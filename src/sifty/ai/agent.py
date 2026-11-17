"""AI agent loop with autonomy levels.

The agent sends the user's request to Ollama with a tool registry. Ollama may
respond with one or more tool calls; the agent dispatches them (subject to the
autonomy level and a confirm callback), appends the results, and re-submits
until the model produces a plain text answer.

Autonomy levels:
  ``ask``           - confirm every ``low`` or ``high`` risk tool before running.
  ``low_risk_auto`` - auto-run ``low`` risk tools; confirm ``high`` ones.
  ``full_auto``     - run all tools automatically (still routes through safety.trash).

The active level is read from a small override file (set via the TUI) layered
over the static ``ai.autonomy`` config default - see :func:`current_autonomy`.

Models that don't emit ``tool_calls`` (not tool-capable) produce a plain reply
on the first iteration; the agent yields that as a :class:`FallbackEvent` so
callers can detect the downgrade.

Events are yielded as the agent progresses so callers (TUI, CLI) can display
each step live instead of waiting for the whole chain.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from ..infra.config import app_data_dir, load_config
from . import policy
from .client import OllamaClient, OllamaUnavailable
from .tools import TOOLS, Tool, ToolResult
from .tools import get as get_tool

logger = logging.getLogger("sifty.ai")

_MAX_ITERATIONS = 10
_VALID_LEVELS = ("ask", "low_risk_auto", "full_auto")

# Read-risk tools whose results are cached within a single run() call so the
# model doesn't re-scan. system_status is excluded - it's a live snapshot.
_NO_CACHE = {"system_status"}

# Appended to the system prompt in agentic mode so the model adds insight rather
# than re-dumping data the UI already renders as tables.
TOOL_USE_NOTE = (
    "\n\nYou can call tools to inspect and maintain this machine. Tool results are "
    "shown to the user directly (large results as tables), so do NOT repeat the raw "
    "data back - give a short, useful interpretation and a clear recommendation. "
    "Before any destructive action (clean_junk, uninstall_app, apply_updates) explain "
    "what you're about to do; the user is asked to approve it."
)


# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------

@dataclass
class ToolCallEvent:
    """The model is requesting a tool call."""
    tool_name: str
    args: dict
    risk: str           # from the Tool definition


@dataclass
class ToolResultEvent:
    """A tool has been executed (or skipped due to a denied confirm)."""
    tool_name: str
    result: str
    skipped: bool = False           # True when the user declined to run it
    table: ToolResult | None = None  # structured output for rich UI rendering


@dataclass
class FinalAnswerEvent:
    """The model produced a plain text reply - the agent is done."""
    text: str


@dataclass
class FallbackEvent:
    """The model doesn't support tools; plain advisory answer returned."""
    text: str


AgentEvent = ToolCallEvent | ToolResultEvent | FinalAnswerEvent | FallbackEvent


# ---------------------------------------------------------------------------
# Autonomy: config default + user override file
# ---------------------------------------------------------------------------

def _override_file():
    return app_data_dir() / "ai_state.json"


def _read_state() -> dict:
    """The full agent-state dict, robust to a missing/bad/unreadable file."""
    try:
        path = _override_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        logger.debug("could not read ai_state.json", exc_info=True)
    return {}


def _write_state(state: dict) -> bool:
    try:
        _override_file().write_text(json.dumps(state), encoding="utf-8")
        return True
    except OSError:
        logger.exception("failed to persist ai_state.json")
        return False


def autonomy_from_config(config=None) -> str:
    config = config or load_config()
    return config.section("ai").get("autonomy", "ask")


def current_autonomy(config=None) -> str:
    """The active autonomy level: user override file wins, else config default."""
    val = _read_state().get("autonomy")
    if val in _VALID_LEVELS:
        return val
    return autonomy_from_config(config)


def set_autonomy(level: str) -> bool:
    """Persist the active autonomy level. Returns False for an invalid level.

    Read-modify-write so it never clobbers per-tool policies in the same file.
    """
    if level not in _VALID_LEVELS:
        return False
    state = _read_state()
    state["autonomy"] = level
    return _write_state(state)


def _needs_confirm(risk: str, autonomy: str) -> bool:
    """Return True if this risk level requires a confirm under the given autonomy."""
    if risk == "read":
        return False
    if autonomy == "full_auto":
        return False
    if autonomy == "low_risk_auto" and risk == "low":
        return False
    return True  # "ask" confirms low+high; "low_risk_auto" confirms high


def _resolve_action(tool: Tool, autonomy: str, tool_policy: str = "default") -> str:
    """Return ``"run"`` | ``"confirm"`` | ``"skip"`` for this tool.

    A per-tool policy overrides the global autonomy; ``"default"`` defers to it.
    """
    if tool_policy == "never":
        return "skip"
    if tool_policy == "auto":
        return "run"
    if tool_policy == "ask":
        return "confirm"
    return "confirm" if _needs_confirm(tool.risk, autonomy) else "run"


def _record_skip(tool: Tool, args: dict) -> None:
    """Best-effort record of a declined tool, for learned preferences."""
    try:
        from ..core.ai_memory import record_skip
        target = str(args.get("categories") or args.get("name") or args.get("id") or "")
        record_skip(tool.name, target)
    except Exception:
        logger.debug("skip not recorded", exc_info=True)


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run(
    client: OllamaClient,
    messages: list[dict],
    *,
    autonomy: str = "ask",
    confirm: Callable[[str], bool] | None = None,
    tools: list[Tool] | None = None,
    cache: dict | None = None,
) -> Iterator[AgentEvent]:
    """Drive an agentic conversation and yield :data:`AgentEvent` instances.

    ``messages`` is the full Ollama-format conversation history (including the
    system message). ``confirm`` is called with a human-readable prompt when a
    tool requires confirmation; return ``True`` to proceed, ``False`` to skip.
    Defaults to always-refuse (safe) when not provided.

    Per-tool policies (see :mod:`sifty.ai.policy`) override the global autonomy.
    ``cache`` is an optional dict reused across read-tool calls to avoid
    re-scanning; a fresh one per call (the default) dedups within this run only.
    """
    if confirm is None:
        confirm = lambda _: False  # noqa: E731 - safe default, not interactive

    active_tools = tools if tools is not None else TOOLS
    schemas = [t.to_ollama() for t in active_tools]
    tool_map = {t.name: t for t in active_tools}
    policies = policy.all_policies()
    cache = {} if cache is None else cache

    current_messages = list(messages)

    for _ in range(_MAX_ITERATIONS):
        try:
            msg = client.chat_once(current_messages, tools=schemas)
        except OllamaUnavailable as exc:
            logger.warning("agent: Ollama unavailable: %s", exc)
            yield FinalAnswerEvent(text=f"(AI unavailable: {exc})")
            return

        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            text = (msg.get("content") or "").strip() or "(no response)"
            # A plain reply on the very first turn means the model ignored tools.
            is_fallback = len(current_messages) == len(messages)
            yield (FallbackEvent(text=text) if is_fallback else FinalAnswerEvent(text=text))
            return

        current_messages.append(msg)

        for call in tool_calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments", {})
            args = raw_args if isinstance(raw_args, dict) else {}

            tool = tool_map.get(name) or get_tool(name)
            if tool is None:
                result_text = f"Unknown tool: {name}"
                yield ToolResultEvent(tool_name=name, result=result_text)
                current_messages.append({"role": "tool", "content": result_text})
                continue

            yield ToolCallEvent(tool_name=name, args=args, risk=tool.risk)

            action = _resolve_action(tool, autonomy, policies.get(name, "default"))
            if action == "skip":
                result_text = f"(skipped {name}: blocked by your 'never' policy)"
                yield ToolResultEvent(tool_name=name, result=result_text, skipped=True)
                current_messages.append({"role": "tool", "content": result_text})
                continue
            if action == "confirm" and not confirm(_confirm_prompt(tool, args)):
                _record_skip(tool, args)
                result_text = f"(user declined to run {name})"
                yield ToolResultEvent(tool_name=name, result=result_text, skipped=True)
                current_messages.append({"role": "tool", "content": result_text})
                continue

            cache_key = (name, json.dumps(args, sort_keys=True)) \
                if tool.risk == "read" and name not in _NO_CACHE else None
            if cache_key is not None and cache_key in cache:
                result = cache[cache_key]
            else:
                try:
                    result = tool.handler(args)
                except Exception as exc:
                    logger.exception("tool %s failed", name)
                    result = ToolResult(summary=f"Error running {name}: {exc}")
                if cache_key is not None and isinstance(result, ToolResult):
                    cache[cache_key] = result

            if isinstance(result, ToolResult):
                result_text = result.summary
                table = result if result.has_table else None
            else:
                result_text = str(result)
                table = None

            yield ToolResultEvent(tool_name=name, result=result_text, table=table)
            current_messages.append({"role": "tool", "content": result_text})

    yield FinalAnswerEvent(text="(agent reached the iteration limit without a final answer)")


def _confirm_prompt(tool: Tool, args: dict) -> str:
    args_str = ", ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
    return f"Run {tool.name}({args_str}) - risk: {tool.risk}"
