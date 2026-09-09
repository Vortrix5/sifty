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


def test_ai_status_json_when_ollama_is_reachable(monkeypatch):
    from sifty.cli.commands import ai_group

    monkeypatch.setattr(ai_group.OllamaClient, "is_available", lambda self: True)
    monkeypatch.setattr(ai_group.OllamaClient, "list_models", lambda self: ["qwen2.5:3b"])

    result = runner.invoke(app, ["--json", "ai", "status"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == {
        "host": "http://localhost:11434",
        "model": "qwen2.5:3b",
        "reachable": True,
        "pulled": True,
    }


def test_ai_status_json_when_ollama_is_not_reachable(monkeypatch):
    from sifty.cli.commands import ai_group

    monkeypatch.setattr(ai_group.OllamaClient, "is_available", lambda self: False)
    monkeypatch.setattr(ai_group.OllamaClient, "list_models", lambda self: [])

    result = runner.invoke(app, ["--json", "ai", "status"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == {
        "host": "http://localhost:11434",
        "model": "qwen2.5:3b",
        "reachable": False,
        "pulled": False,
    }


def test_without_json_flag_output_is_not_json():
    result = runner.invoke(app, ["disk", "volumes"])
    assert result.exit_code == 0
    try:
        json.loads(result.stdout)
        is_json = True
    except ValueError:
        is_json = False
    assert not is_json  # human/Rich output, not JSON
