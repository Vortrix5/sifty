"""Tests for the OllamaClient HTTP layer and the advisor prompt helpers.

`httpx` is monkeypatched throughout, so nothing touches the network.
The streaming happy-path is covered in test_ai_context.py; this covers the
rest of the client surface plus all of advisor.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from sifty.ai import advisor
from sifty.ai.client import OllamaClient, OllamaUnavailable


def _client() -> OllamaClient:
    return OllamaClient(host="http://localhost:11434", model="test", timeout=5.0)


# --- from_config -----------------------------------------------------------


def test_from_config_uses_defaults():
    cfg = SimpleNamespace(section=lambda name: {})
    c = OllamaClient.from_config(cfg)
    assert c.host == "http://localhost:11434"
    assert c.model == "qwen2.5:3b"
    assert c.timeout == 60.0
    assert c.keep_alive == "10m"


def test_from_config_reads_overrides():
    cfg = SimpleNamespace(
        section=lambda name: {
            "host": "http://x:1",
            "model": "llama3.2:3b",
            "timeout_seconds": 30,
            "keep_alive": "5m",
        }
    )
    c = OllamaClient.from_config(cfg)
    assert (c.host, c.model, c.timeout, c.keep_alive) == ("http://x:1", "llama3.2:3b", 30.0, "5m")


# --- is_available / list_models --------------------------------------------


def test_is_available_true(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: SimpleNamespace(status_code=200))
    assert _client().is_available() is True


def test_is_available_false_non_200(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: SimpleNamespace(status_code=503))
    assert _client().is_available() is False


def test_is_available_false_on_http_error(monkeypatch):
    def boom(url, timeout=None):
        raise httpx.HTTPError("connection refused")

    monkeypatch.setattr(httpx, "get", boom)
    assert _client().is_available() is False


def test_list_models_returns_names(monkeypatch):
    resp = SimpleNamespace(
        status_code=200,
        json=lambda: {"models": [{"name": "a"}, {"name": "b"}, {}]},
    )
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: resp)
    assert _client().list_models() == ["a", "b", ""]


def test_list_models_empty_on_non_200(monkeypatch):
    resp = SimpleNamespace(status_code=500, json=lambda: {})
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None: resp)
    assert _client().list_models() == []


def test_list_models_empty_on_http_error(monkeypatch):
    def boom(url, timeout=None):
        raise httpx.HTTPError("down")

    monkeypatch.setattr(httpx, "get", boom)
    assert _client().list_models() == []


# --- _timeout / _payload ---------------------------------------------------


def test_timeout_is_split():
    t = _client()._timeout()
    assert isinstance(t, httpx.Timeout)
    assert t.connect == 5.0
    assert t.read == 5.0  # == self.timeout


def test_payload_includes_tools_when_given():
    p = _client()._payload([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert p["tools"] == [{"type": "function"}]
    assert p["stream"] is True
    assert p["model"] == "test"


def test_payload_omits_tools_when_none():
    p = _client()._payload([], None)
    assert "tools" not in p


# --- chat_stream branches --------------------------------------------------


class _FakeStreamResp:
    def __init__(self, lines, status_error=None):
        self._lines = lines
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_chat_stream_skips_blank_and_unparseable_lines(monkeypatch):
    lines = [
        "",  # blank → skip
        "not json",  # JSONDecodeError → skip
        json.dumps({"message": {"content": "hi"}, "done": False}),
        json.dumps({"message": {"content": ""}}),  # empty chunk → no yield
        json.dumps({"done": True}),  # done → break
        json.dumps({"message": {"content": "unreached"}}),
    ]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStreamResp(lines))
    assert list(_client().chat_stream("s", "u")) == ["hi"]


def test_chat_stream_exhausts_without_done_flag(monkeypatch):
    # No {"done": True} line → the loop ends by exhausting the iterator.
    lines = [
        json.dumps({"message": {"content": "a"}, "done": False}),
        json.dumps({"message": {"content": "b"}, "done": False}),
    ]
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStreamResp(lines))
    assert list(_client().chat_stream("s", "u")) == ["a", "b"]


def test_chat_stream_timeout_raises_unavailable(monkeypatch):
    def boom(*a, **k):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "stream", boom)
    with pytest.raises(OllamaUnavailable):
        list(_client().chat_stream("s", "u"))


def test_chat_stream_http_error_raises_unavailable(monkeypatch):
    err = httpx.HTTPError("bad status")
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStreamResp([], status_error=err))
    with pytest.raises(OllamaUnavailable):
        list(_client().chat_stream("s", "u"))


def test_chat_joins_and_strips(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "chat_stream", lambda s, u, messages=None: iter(["  hello ", "world  "]))
    assert c.chat("s", "u") == "hello world"


# --- chat_once -------------------------------------------------------------


def test_chat_once_returns_message_and_sets_stream_false(monkeypatch):
    captured = {}
    resp = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"message": {"role": "assistant", "content": "hi", "tool_calls": []}},
    )

    def fake_post(url, json=None, timeout=None):
        captured["payload"] = json
        return resp

    monkeypatch.setattr(httpx, "post", fake_post)
    msg = _client().chat_once([{"role": "user", "content": "x"}], tools=[{"t": 1}])
    assert msg["content"] == "hi"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["tools"] == [{"t": 1}]


def test_chat_once_defaults_message_when_absent(monkeypatch):
    resp = SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: resp)
    assert _client().chat_once([]) == {"role": "assistant", "content": ""}


def test_chat_once_timeout_raises_unavailable(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(OllamaUnavailable):
        _client().chat_once([])


def test_chat_once_http_error_raises_unavailable(monkeypatch):
    def boom(url, json=None, timeout=None):
        raise httpx.HTTPError("down")

    monkeypatch.setattr(httpx, "post", boom)
    with pytest.raises(OllamaUnavailable):
        _client().chat_once([])


# --- advisor ---------------------------------------------------------------


def test_safe_returns_none_when_unavailable(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "is_available", lambda: False)
    assert advisor._safe(c, "prompt") is None


def test_safe_returns_chat_result(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "is_available", lambda: True)
    monkeypatch.setattr(c, "chat", lambda system, user: "answer")
    assert advisor._safe(c, "prompt") == "answer"


def test_safe_swallows_unavailable_during_chat(monkeypatch):
    c = _client()
    monkeypatch.setattr(c, "is_available", lambda: True)

    def boom(system, user):
        raise OllamaUnavailable("dropped")

    monkeypatch.setattr(c, "chat", boom)
    assert advisor._safe(c, "prompt") is None


def test_explain_item_builds_prompt(monkeypatch):
    captured = {}

    def fake_safe(client, prompt):
        captured["prompt"] = prompt
        return "explained"

    monkeypatch.setattr(advisor, "_safe", fake_safe)
    out = advisor.explain_item(_client(), "temp.tmp", "C:\\Temp", "10 MB")
    assert out == "explained"
    assert "temp.tmp" in captured["prompt"]
    assert "C:\\Temp" in captured["prompt"]
    assert "10 MB" in captured["prompt"]


def test_summarize_disk_builds_listing(monkeypatch):
    captured = {}

    def fake_safe(client, prompt):
        captured["prompt"] = prompt
        return "summary"

    monkeypatch.setattr(advisor, "_safe", fake_safe)
    out = advisor.summarize_disk(
        _client(), [("big.iso", "4 GB"), ("logs", "1 GB")], "what can I delete?"
    )
    assert out == "summary"
    assert "big.iso: 4 GB" in captured["prompt"]
    assert "what can I delete?" in captured["prompt"]


def test_suggest_organization_samples_first_40(monkeypatch):
    captured = {}

    def fake_safe(client, prompt):
        captured["prompt"] = prompt
        return "scheme"

    monkeypatch.setattr(advisor, "_safe", fake_safe)
    names = [f"file{i}.txt" for i in range(50)]
    out = advisor.suggest_organization(_client(), names)
    assert out == "scheme"
    assert "file39.txt" in captured["prompt"]
    assert "file40.txt" not in captured["prompt"]  # capped at 40
