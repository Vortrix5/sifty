"""Tests for --json output on read-only CLI commands."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from sifty.cli.app import app

runner = CliRunner()


def test_disk_volumes_json_is_parseable():
    result = runner.invoke(app, ["--json", "disk", "volumes"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    if data:  # at least the system drive on a real machine
        assert {"drive", "used", "free", "total"} <= set(data[0])


def test_junk_scan_json_shape():
    result = runner.invoke(app, ["--json", "junk", "scan"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert all({"key", "size_bytes", "requires_admin"} <= set(c) for c in data)


def test_doctor_json_keys():
    result = runner.invoke(app, ["--json", "doctor"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert {"administrator", "winget", "ollama_reachable"} <= set(data)


def _pin_ollama_client(monkeypatch, *, reachable: bool, models: list[str]) -> None:
    """Pin the client so the assertions don't depend on the local config.toml."""
    from sifty.ai.client import OllamaClient
    from sifty.cli.commands import ai_group

    pinned = OllamaClient(host="http://ollama.test:11434", model="test-model", timeout=1.0)
    monkeypatch.setattr(
        ai_group.OllamaClient, "from_config", classmethod(lambda cls, config=None: pinned)
    )
    monkeypatch.setattr(ai_group.OllamaClient, "is_available", lambda self: reachable)
    monkeypatch.setattr(ai_group.OllamaClient, "list_models", lambda self: models)


def test_ai_status_json_when_ollama_is_reachable(monkeypatch):
    _pin_ollama_client(monkeypatch, reachable=True, models=["test-model"])

    result = runner.invoke(app, ["--json", "ai", "status"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == {
        "host": "http://ollama.test:11434",
        "model": "test-model",
        "reachable": True,
        "pulled": True,
    }


def test_ai_status_json_when_ollama_is_not_reachable(monkeypatch):
    _pin_ollama_client(monkeypatch, reachable=False, models=[])

    result = runner.invoke(app, ["--json", "ai", "status"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == {
        "host": "http://ollama.test:11434",
        "model": "test-model",
        "reachable": False,
        "pulled": False,
    }


def test_ai_status_json_when_model_not_pulled(monkeypatch):
    """Reachable, but the configured model isn't among the pulled ones."""
    _pin_ollama_client(monkeypatch, reachable=True, models=["some-other-model"])

    result = runner.invoke(app, ["--json", "ai", "status"])

    assert result.exit_code == 0
    assert json.loads(result.stdout)["pulled"] is False


def test_ai_status_human_mode_does_not_list_models(monkeypatch):
    """`pulled` is JSON-only, so human mode must not pay the extra round-trip."""
    from sifty.cli.commands import ai_group

    _pin_ollama_client(monkeypatch, reachable=True, models=["test-model"])
    calls: list[int] = []
    monkeypatch.setattr(
        ai_group.OllamaClient, "list_models", lambda self: calls.append(1) or ["test-model"]
    )

    result = runner.invoke(app, ["ai", "status"])

    assert result.exit_code == 0
    assert calls == []


def test_without_json_flag_output_is_not_json():
    result = runner.invoke(app, ["disk", "volumes"])
    assert result.exit_code == 0
    try:
        json.loads(result.stdout)
        is_json = True
    except ValueError:
        is_json = False
    assert not is_json  # human/Rich output, not JSON
