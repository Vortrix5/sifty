"""Per-tool AI action policies, and the shared agent-state store.

The agent decides whether to run a tool from a *global* autonomy level (see
:mod:`sifty.ai.agent`) plus optional *per-tool* overrides. Both live in one
small JSON file, ``ai_state.json``, in the app-data dir. This module owns that
file so there's a single writer; :mod:`sifty.ai.agent` reads/writes the
``autonomy`` key through :func:`load_state` / :func:`save_state`.

Per-tool policy values:

- ``"auto"``    - run this tool without asking, whatever the global level.
- ``"ask"``     - always confirm this tool, whatever the global level.
- ``"never"``   - never run this tool; the agent skips it.
- ``"default"`` - defer to the global autonomy level (stored as *absence*).

A per-tool ``auto`` only suppresses the confirm prompt; it never bypasses
``core.safety.trash()`` or the protected-path checks in the tool handlers.
"""

from __future__ import annotations

import json
import logging

from ..infra.config import app_data_dir
from .tools import get as get_tool

logger = logging.getLogger("sifty.ai")

VALID_POLICIES = ("auto", "ask", "never", "default")


def state_file():
    return app_data_dir() / "ai_state.json"


def load_state() -> dict:
    """The full agent-state dict (``{}`` if missing/unreadable)."""
    path = state_file()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (ValueError, OSError):
            logger.debug("could not read ai_state.json", exc_info=True)
    return {}


def save_state(state: dict) -> bool:
    """Persist the full agent-state dict. Returns False on write failure."""
    try:
        state_file().write_text(json.dumps(state), encoding="utf-8")
        return True
    except OSError:
        logger.exception("failed to persist ai_state.json")
        return False


def all_policies() -> dict[str, str]:
    """Every set per-tool policy, filtered to valid values."""
    raw = load_state().get("tool_policies", {})
    if not isinstance(raw, dict):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, str) and v in VALID_POLICIES}


def get_policy(name: str) -> str | None:
    """The per-tool policy for ``name``, or None if unset (defers to global)."""
    return all_policies().get(name)


def set_policy(name: str, policy: str) -> bool:
    """Set (or clear, with ``"default"``) a tool's policy.

    Returns False for an unknown tool or an invalid policy value.
    """
    if policy not in VALID_POLICIES or get_tool(name) is None:
        return False
    state = load_state()
    policies = state.get("tool_policies")
    if not isinstance(policies, dict):
        policies = {}
    if policy == "default":
        policies.pop(name, None)
    else:
        policies[name] = policy
    state["tool_policies"] = policies
    return save_state(state)


def reset_policy(name: str | None = None) -> bool:
    """Clear one tool's policy, or all of them when ``name`` is None."""
    state = load_state()
    if name is None:
        state.pop("tool_policies", None)
    else:
        policies = state.get("tool_policies")
        if isinstance(policies, dict):
            policies.pop(name, None)
            state["tool_policies"] = policies
    return save_state(state)
