"""Extra agent-loop coverage: autonomy persistence + tool-dispatch edge cases."""

from __future__ import annotations

import json
from types import SimpleNamespace

from sifty.ai import agent
from sifty.ai.tools import Tool, ToolResult


def _tool(name="t", risk="read", handler=None):
    return Tool(
        name=name,
        description="test tool",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=risk,
        handler=handler or (lambda args: ToolResult(summary="ok")),
    )


class _ScriptedClient:
    """Returns canned chat_once responses in order."""

    def __init__(self, responses):
        self._responses = list(responses)

    def chat_once(self, messages, tools=None):
        return self._responses.pop(0)


def _tool_call(name, args=None):
    return {"role": "assistant", "tool_calls": [{"function": {"name": name, "arguments": args or {}}}]}


# --- current_autonomy / set_autonomy ---------------------------------------


def test_current_autonomy_valid_override(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    f = agent._override_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"autonomy": "full_auto"}), encoding="utf-8")
    assert agent.current_autonomy() == "full_auto"


def test_current_autonomy_invalid_override_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    f = agent._override_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"autonomy": "bogus"}), encoding="utf-8")
    cfg = SimpleNamespace(section=lambda name: {"autonomy": "ask"})
    assert agent.current_autonomy(cfg) == "ask"


def test_current_autonomy_malformed_json_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    f = agent._override_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("{ not valid json", encoding="utf-8")
    cfg = SimpleNamespace(section=lambda name: {"autonomy": "low_risk_auto"})
    assert agent.current_autonomy(cfg) == "low_risk_auto"


def test_set_autonomy_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert agent.set_autonomy("full_auto") is True
    assert agent.current_autonomy() == "full_auto"


def test_set_autonomy_rejects_invalid_level():
    assert agent.set_autonomy("bogus") is False


def test_set_autonomy_write_error(monkeypatch):
    class _BadPath:
        def write_text(self, *a, **k):
            raise OSError("disk full")

    monkeypatch.setattr(agent, "_override_file", lambda: _BadPath())
    assert agent.set_autonomy("ask") is False


# --- run() dispatch edge cases ---------------------------------------------


def test_run_unknown_tool(monkeypatch):
    client = _ScriptedClient([
        _tool_call("does_not_exist"),
        {"role": "assistant", "content": "done"},
    ])
    events = list(agent.run(client, [{"role": "user", "content": "hi"}], autonomy="full_auto", tools=[]))
    assert any(
        isinstance(e, agent.ToolResultEvent) and "Unknown tool" in e.result for e in events
    )


def test_run_confirm_accepted_runs_tool():
    ran = []

    def handler(args):
        ran.append(1)
        return ToolResult(summary="did it")

    client = _ScriptedClient([
        _tool_call("risky"),
        {"role": "assistant", "content": "finished"},
    ])
    events = list(
        agent.run(
            client,
            [{"role": "user", "content": "hi"}],
            autonomy="ask",
            confirm=lambda prompt: True,
            tools=[_tool("risky", risk="high", handler=handler)],
        )
    )
    assert ran == [1]
    assert any(isinstance(e, agent.ToolResultEvent) and "did it" in e.result for e in events)


def test_run_tool_handler_exception():
    def boom(args):
        raise RuntimeError("kaboom")

    client = _ScriptedClient([
        _tool_call("boomtool"),
        {"role": "assistant", "content": "recovered"},
    ])
    events = list(
        agent.run(
            client,
            [{"role": "user", "content": "hi"}],
            autonomy="full_auto",
            tools=[_tool("boomtool", risk="read", handler=boom)],
        )
    )
    assert any(
        isinstance(e, agent.ToolResultEvent) and "Error running boomtool" in e.result for e in events
    )


def test_run_hits_iteration_limit():
    class _AlwaysToolClient:
        def chat_once(self, messages, tools=None):
            return _tool_call("loop")

    events = list(
        agent.run(
            _AlwaysToolClient(),
            [{"role": "user", "content": "hi"}],
            autonomy="full_auto",
            tools=[_tool("loop", risk="read")],
        )
    )
    assert isinstance(events[-1], agent.FinalAnswerEvent)
    assert "iteration limit" in events[-1].text
