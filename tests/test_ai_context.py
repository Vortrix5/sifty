"""Tests for ai/context.py and the updated OllamaClient messages API."""

from __future__ import annotations

import json

import httpx
import pytest

from sifty.ai import context as ai_context
from sifty.ai.client import OllamaClient
from sifty.core import disk, history
from sifty.core.models import VolumeUsage

# ---------------------------------------------------------------------------
# context.build()
# ---------------------------------------------------------------------------

_GB = 1024 ** 3


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_context_includes_volumes(monkeypatch, temp_appdata):
    vols = [VolumeUsage("C", "C:\\", "NTFS", 100 * _GB, 60 * _GB, 40 * _GB)]
    monkeypatch.setattr(disk, "volumes", lambda: vols)
    ctx = ai_context.build(include_junk=False, include_history=False)
    assert "C:\\" in ctx
    assert "NTFS" in ctx


def test_context_skips_failed_volume_call(monkeypatch, temp_appdata):
    monkeypatch.setattr(disk, "volumes", lambda: (_ for _ in ()).throw(OSError("denied")))
    ctx = ai_context.build(include_junk=False, include_history=False)
    # Should not raise; empty string or just the header is fine.
    assert isinstance(ctx, str)


def test_context_empty_when_all_disabled(temp_appdata):
    ctx = ai_context.build(include_junk=False, include_volumes=False, include_history=False)
    assert ctx == ""


def test_context_includes_history(monkeypatch, temp_appdata):
    from sifty.core.models import Run
    fake_run = Run(1, "2026-05-01T10:00:00", "junk", "temp", 500_000, 12, True, 0)
    monkeypatch.setattr(history, "recent_runs", lambda n: [fake_run])
    monkeypatch.setattr(history, "summary", lambda: {"runs": 1, "bytes_freed": 500_000, "items": 12})
    ctx = ai_context.build(include_junk=False, include_volumes=False, include_preferences=False)
    assert "2026-05-01" in ctx
    assert "junk" in ctx


def test_context_includes_preferences(monkeypatch, temp_appdata):
    from sifty.core import ai_memory
    prefs = ai_memory.Preferences(always_clean=["browser-cache"], avoided_tools=["uninstall_app"])
    monkeypatch.setattr(ai_memory, "learned_preferences", lambda *a, **k: prefs)
    ctx = ai_context.build(include_junk=False, include_volumes=False, include_history=False)
    assert "browser-cache" in ctx
    assert "uninstall_app" in ctx


def test_context_preferences_degrade(monkeypatch, temp_appdata):
    from sifty.core import ai_memory
    monkeypatch.setattr(ai_memory, "learned_preferences",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    # Must not raise even if the preferences lookup fails.
    ctx = ai_context.build(include_junk=False, include_volumes=False, include_history=False)
    assert isinstance(ctx, str)


# ---------------------------------------------------------------------------
# OllamaClient.chat_stream with messages=
# ---------------------------------------------------------------------------

def _fake_chunks(texts: list[str]) -> bytes:
    """Build a fake Ollama streaming response body."""
    lines = [json.dumps({"message": {"content": t}, "done": False}) for t in texts]
    lines.append(json.dumps({"done": True}))
    return b"\n".join(line.encode() for line in lines)


def _mock_client(monkeypatch) -> OllamaClient:
    return OllamaClient(host="http://localhost:11434", model="test", timeout=5.0)


def test_chat_stream_with_messages_list(monkeypatch):
    """chat_stream(messages=...) uses the provided list verbatim."""
    client = _mock_client(monkeypatch)
    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def iter_lines(self):
            yield json.dumps({"message": {"content": "hello"}, "done": False})
            yield json.dumps({"done": True})
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_stream(method, url, json=None, timeout=None):
        captured["messages"] = json.get("messages")
        return FakeResp()

    monkeypatch.setattr(httpx, "stream", fake_stream)

    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "hi"},
    ]
    result = list(client.chat_stream("", "", messages=msgs))
    assert result == ["hello"]
    assert captured["messages"] == msgs


def test_chat_stream_fallback_to_system_user(monkeypatch):
    """chat_stream without messages= builds the list from system + user."""
    client = _mock_client(monkeypatch)
    captured = {}

    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def iter_lines(self):
            yield json.dumps({"message": {"content": "ok"}, "done": False})
            yield json.dumps({"done": True})
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_stream(method, url, json=None, timeout=None):
        captured["messages"] = json.get("messages")
        return FakeResp()

    monkeypatch.setattr(httpx, "stream", fake_stream)
    list(client.chat_stream("sys", "usr"))
    assert captured["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "usr"},
    ]
