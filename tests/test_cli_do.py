"""Tests for `sifty do` (one-shot agent). The model and agent loop are faked."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from sifty.ai import agent
from sifty.ai.agent import FallbackEvent, FinalAnswerEvent, ToolCallEvent, ToolResultEvent
from sifty.ai.client import OllamaClient
from sifty.ai.tools import ToolResult
from sifty.cli.app import app

runner = CliRunner()


def _fake_client(monkeypatch, available=True):
    client = SimpleNamespace(host="http://localhost:11434", is_available=lambda: available)
    monkeypatch.setattr(OllamaClient, "from_config", classmethod(lambda cls: client))
    monkeypatch.setattr(agent, "build_system_prompt", lambda: "system")
    monkeypatch.setattr(agent, "current_autonomy", lambda config=None: "ask")
    return client


def test_do_needs_ollama(monkeypatch):
    _fake_client(monkeypatch, available=False)
    result = runner.invoke(app, ["do", "clean up"])
    assert result.exit_code == 1


def test_do_renders_tool_steps_and_answer(monkeypatch):
    _fake_client(monkeypatch)
    seen = {}

    def fake_run(client, messages, *, autonomy, confirm, tools):
        seen["messages"] = messages
        yield ToolCallEvent("scan_junk", {}, "read")
        yield ToolResultEvent("scan_junk", "found junk", table=ToolResult(
            summary="s", title="Junk by category", columns=["Key", "Size"], rows=[["user-temp", "1 GB"]]))
        yield FinalAnswerEvent("Clean **user-temp** first.")

    monkeypatch.setattr(agent, "run", fake_run)
    result = runner.invoke(app, ["do", "what can I clean?"])
    assert result.exit_code == 0
    assert "scan_junk" in result.stdout
    assert "user-temp" in result.stdout
    assert "Clean user-temp first." in result.stdout
    assert seen["messages"][-1] == {"role": "user", "content": "what can I clean?"}


def test_do_without_terminal_declines_changes(monkeypatch):
    _fake_client(monkeypatch)
    answers = []

    def fake_run(client, messages, *, autonomy, confirm, tools):
        yield ToolCallEvent("clean_junk", {"categories": ["user-temp"]}, "high")
        answers.append(confirm("Run clean_junk - risk: high"))
        yield ToolResultEvent("clean_junk", "(user declined to run clean_junk)", skipped=True)
        yield FinalAnswerEvent("ok")

    monkeypatch.setattr(agent, "run", fake_run)
    result = runner.invoke(app, ["do", "clean temp"])
    assert result.exit_code == 0
    assert answers == [False]       # CliRunner has no TTY, so nothing is approved
    assert "high risk" in result.stdout


def test_do_read_only_offers_only_read_tools(monkeypatch):
    _fake_client(monkeypatch)
    offered = {}

    def fake_run(client, messages, *, autonomy, confirm, tools):
        offered["risks"] = {t.risk for t in tools}
        yield FinalAnswerEvent("done")

    monkeypatch.setattr(agent, "run", fake_run)
    result = runner.invoke(app, ["do", "--read-only", "look around"])
    assert result.exit_code == 0
    assert offered["risks"] == {"read"}


def test_do_fallback_model_warns(monkeypatch):
    _fake_client(monkeypatch)
    monkeypatch.setattr(agent, "run", lambda *a, **k: iter([FallbackEvent("Try cleaning temp.")]))
    result = runner.invoke(app, ["do", "help"])
    assert result.exit_code == 0
    assert "Try cleaning temp." in result.stdout


def test_read_only_tools_filters_by_risk():
    assert agent.read_only_tools()
    assert all(t.risk == "read" for t in agent.read_only_tools())
