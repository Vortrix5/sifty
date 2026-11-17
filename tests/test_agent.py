"""Tests for the AI agent loop and tool registry."""

from __future__ import annotations

from sifty.ai import policy as ai_policy
from sifty.ai import tools as ai_tools
from sifty.ai.agent import (
    FallbackEvent,
    FinalAnswerEvent,
    ToolCallEvent,
    ToolResultEvent,
    _needs_confirm,
    _resolve_action,
    current_autonomy,
    run,
    set_autonomy,
)
from sifty.ai.client import OllamaClient, OllamaUnavailable
from sifty.ai.tools import Tool, ToolResult

# ---------------------------------------------------------------------------
# Autonomy gating
# ---------------------------------------------------------------------------

def test_read_never_needs_confirm():
    for autonomy in ("ask", "low_risk_auto", "full_auto"):
        assert _needs_confirm("read", autonomy) is False


def test_low_risk_ask_needs_confirm():
    assert _needs_confirm("low", "ask") is True


def test_low_risk_auto_skips_confirm():
    assert _needs_confirm("low", "low_risk_auto") is False


def test_high_always_asks_unless_full_auto():
    assert _needs_confirm("high", "ask") is True
    assert _needs_confirm("high", "low_risk_auto") is True
    assert _needs_confirm("high", "full_auto") is False


# ---------------------------------------------------------------------------
# Fake tools
# ---------------------------------------------------------------------------

def _make_tool(name="do_thing", risk="read", result="done") -> Tool:
    return Tool(
        name=name,
        description="Test tool",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=risk,
        handler=lambda args: result,
    )


def _fake_client_plain(monkeypatch, answer="Hello!"):
    """Client that always returns a plain text reply (no tool_calls)."""
    client = OllamaClient(host="http://localhost:11434", model="test", timeout=5.0)

    def fake_chat_once(messages, tools=None):
        return {"role": "assistant", "content": answer}

    monkeypatch.setattr(client, "chat_once", fake_chat_once)
    return client


def _fake_client_tool_then_answer(monkeypatch, tool_name, args, answer):
    """Client that calls a tool on the first turn, then gives a plain answer."""
    client = OllamaClient(host="http://localhost:11434", model="test", timeout=5.0)
    calls = [0]

    def fake_chat_once(messages, tools=None):
        if calls[0] == 0:
            calls[0] += 1
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": tool_name, "arguments": args}}],
            }
        return {"role": "assistant", "content": answer}

    monkeypatch.setattr(client, "chat_once", fake_chat_once)
    return client


# ---------------------------------------------------------------------------
# Agent loop - fallback (model ignores tools)
# ---------------------------------------------------------------------------

def test_plain_reply_is_fallback(monkeypatch):
    client = _fake_client_plain(monkeypatch, "I can help!")
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    events = list(run(client, msgs, autonomy="ask", tools=[]))
    assert len(events) == 1
    assert isinstance(events[0], FallbackEvent)
    assert events[0].text == "I can help!"


# ---------------------------------------------------------------------------
# Agent loop - single tool call auto-run (read risk)
# ---------------------------------------------------------------------------

def test_read_tool_runs_without_confirm(monkeypatch):
    tool = _make_tool("scan", risk="read", result="scan result")
    client = _fake_client_tool_then_answer(monkeypatch, "scan", {}, "Done, here's the summary.")

    confirmed = []
    events = list(run(client, [{"role": "user", "content": "scan"}],
                      autonomy="ask",
                      confirm=lambda p: confirmed.append(p) or True,
                      tools=[tool]))

    assert confirmed == []  # read tools never confirm
    call_ev = next(e for e in events if isinstance(e, ToolCallEvent))
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    final_ev = next(e for e in events if isinstance(e, FinalAnswerEvent))
    assert call_ev.tool_name == "scan"
    assert result_ev.result == "scan result"
    assert not result_ev.skipped
    assert final_ev.text == "Done, here's the summary."


# ---------------------------------------------------------------------------
# Agent loop - high-risk tool with autonomy=ask, confirm=deny
# ---------------------------------------------------------------------------

def test_high_tool_skipped_when_denied(monkeypatch):
    tool = _make_tool("delete", risk="high", result="deleted!")
    client = _fake_client_tool_then_answer(monkeypatch, "delete", {}, "OK, I skipped it.")

    events = list(run(client, [{"role": "user", "content": "clean"}],
                      autonomy="ask",
                      confirm=lambda p: False,   # always deny
                      tools=[tool]))

    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_ev.skipped is True
    assert "declined" in result_ev.result


# ---------------------------------------------------------------------------
# Agent loop - high-risk tool with autonomy=full_auto runs without confirm
# ---------------------------------------------------------------------------

def test_high_tool_runs_in_full_auto(monkeypatch):
    tool = _make_tool("delete", risk="high", result="deleted!")
    client = _fake_client_tool_then_answer(monkeypatch, "delete", {}, "Done.")

    confirmed = []
    events = list(run(client, [{"role": "user", "content": "clean"}],
                      autonomy="full_auto",
                      confirm=lambda p: confirmed.append(p) or True,
                      tools=[tool]))

    assert confirmed == []
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_ev.result == "deleted!"
    assert not result_ev.skipped


# ---------------------------------------------------------------------------
# Agent loop - Ollama unavailable
# ---------------------------------------------------------------------------

def test_ollama_unavailable_yields_final(monkeypatch):
    client = OllamaClient(host="http://localhost:11434", model="test", timeout=5.0)
    monkeypatch.setattr(client, "chat_once", lambda *a, **kw: (_ for _ in ()).throw(OllamaUnavailable("down")))

    events = list(run(client, [{"role": "user", "content": "hi"}], tools=[]))
    assert len(events) == 1
    assert isinstance(events[0], FinalAnswerEvent)
    assert "unavailable" in events[0].text.lower()


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

def test_all_tools_have_valid_risk():
    for t in ai_tools.TOOLS:
        assert t.risk in ("read", "low", "high"), f"{t.name} has invalid risk"


def test_tool_to_ollama_schema():
    t = _make_tool("mytool", risk="read")
    schema = t.to_ollama()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "mytool"


def test_get_tool_by_name():
    t = ai_tools.get("scan_junk")
    assert t is not None
    assert t.risk == "read"


def test_get_unknown_tool():
    assert ai_tools.get("no_such_tool") is None


def test_ollama_schemas_count():
    schemas = ai_tools.ollama_schemas()
    assert len(schemas) == len(ai_tools.TOOLS)


# ---------------------------------------------------------------------------
# ToolResult (structured table output) flows through the agent
# ---------------------------------------------------------------------------

def test_tool_result_carries_table(monkeypatch):
    tool = Tool(
        name="rep", description="", parameters={"type": "object", "properties": {}},
        risk="read",
        handler=lambda a: ToolResult(summary="3 things", columns=["A"], rows=[["1"], ["2"]]),
    )
    client = _fake_client_tool_then_answer(monkeypatch, "rep", {}, "Here you go.")
    events = list(run(client, [{"role": "user", "content": "go"}], autonomy="ask", tools=[tool]))
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_ev.result == "3 things"          # the concise summary feeds the LLM
    assert result_ev.table is not None and result_ev.table.has_table


# ---------------------------------------------------------------------------
# Autonomy override persistence
# ---------------------------------------------------------------------------

def test_autonomy_override_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert current_autonomy() == "ask"             # no override, config default
    assert set_autonomy("full_auto") is True
    assert current_autonomy() == "full_auto"
    assert set_autonomy("bogus") is False           # invalid rejected
    assert current_autonomy() == "full_auto"        # unchanged


def test_set_autonomy_preserves_tool_policies(tmp_path, monkeypatch):
    """Regression: set_autonomy must not wipe per-tool policies in the shared file."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert ai_policy.set_policy("clean_junk", "never") is True
    assert set_autonomy("full_auto") is True
    assert ai_policy.get_policy("clean_junk") == "never"
    assert current_autonomy() == "full_auto"


# ---------------------------------------------------------------------------
# Per-tool policy resolution
# ---------------------------------------------------------------------------

def test_resolve_action_default_matches_global():
    tool = _make_tool("t", risk="high")
    assert _resolve_action(tool, "ask", "default") == "confirm"
    assert _resolve_action(tool, "full_auto", "default") == "run"


def test_resolve_action_auto_overrides_ask():
    assert _resolve_action(_make_tool("t", risk="high"), "ask", "auto") == "run"


def test_resolve_action_ask_overrides_full_auto():
    assert _resolve_action(_make_tool("t", risk="high"), "full_auto", "ask") == "confirm"


def test_resolve_action_never_skips():
    assert _resolve_action(_make_tool("t", risk="read"), "full_auto", "never") == "skip"


def test_never_policy_skips_and_handler_not_called(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    # Name the fake tool after a real registered tool so set_policy accepts it.
    assert ai_policy.set_policy("clean_junk", "never") is True
    ran = []
    tool = _make_tool("clean_junk", risk="high")
    tool.handler = lambda args: ran.append(1) or "deleted!"
    client = _fake_client_tool_then_answer(monkeypatch, "clean_junk", {}, "Understood.")

    events = list(run(client, [{"role": "user", "content": "clean"}],
                      autonomy="full_auto", tools=[tool]))

    assert ran == []  # handler never invoked
    result_ev = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result_ev.skipped is True
    assert "never" in result_ev.result


def test_read_tool_result_cached_within_run(monkeypatch):
    calls = [0]

    def handler(args):
        calls[0] += 1
        return ToolResult(summary=f"call {calls[0]}")

    tool = _make_tool("scan", risk="read")
    tool.handler = handler

    # Client asks for the same read tool twice, then answers.
    client = OllamaClient(host="http://localhost:11434", model="test", timeout=5.0)
    seq = [0]

    def fake_chat_once(messages, tools=None):
        if seq[0] < 2:
            seq[0] += 1
            return {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": "scan", "arguments": {}}}]}
        return {"role": "assistant", "content": "done"}

    monkeypatch.setattr(client, "chat_once", fake_chat_once)
    list(run(client, [{"role": "user", "content": "scan"}], autonomy="full_auto", tools=[tool]))
    assert calls[0] == 1  # handler ran once; second call served from cache
