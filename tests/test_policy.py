"""Tests for per-tool AI action policies (ai/policy.py)."""

from __future__ import annotations

import pytest

from sifty.ai import policy


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def test_set_and_get_policy(temp_appdata):
    assert policy.get_policy("clean_junk") is None
    assert policy.set_policy("clean_junk", "auto") is True
    assert policy.get_policy("clean_junk") == "auto"
    assert policy.all_policies() == {"clean_junk": "auto"}


def test_invalid_policy_rejected(temp_appdata):
    assert policy.set_policy("clean_junk", "sometimes") is False
    assert policy.get_policy("clean_junk") is None


def test_unknown_tool_rejected(temp_appdata):
    assert policy.set_policy("no_such_tool", "auto") is False


def test_default_clears_entry(temp_appdata):
    policy.set_policy("clean_junk", "never")
    assert policy.set_policy("clean_junk", "default") is True
    assert policy.get_policy("clean_junk") is None


def test_reset_one_and_all(temp_appdata):
    policy.set_policy("clean_junk", "auto")
    policy.set_policy("uninstall_app", "never")
    policy.reset_policy("clean_junk")
    assert policy.all_policies() == {"uninstall_app": "never"}
    policy.reset_policy()
    assert policy.all_policies() == {}


def test_autonomy_and_policies_coexist(temp_appdata):
    """The clobber regression: setting one must not wipe the other."""
    from sifty.ai.agent import current_autonomy, set_autonomy

    set_autonomy("full_auto")
    policy.set_policy("clean_junk", "never")
    # Both survive in the shared ai_state.json.
    assert current_autonomy() == "full_auto"
    assert policy.get_policy("clean_junk") == "never"
    # And setting autonomy again keeps the policy.
    set_autonomy("ask")
    assert policy.get_policy("clean_junk") == "never"


def test_renamed_tool_keeps_old_policy(temp_appdata):
    # A policy saved before schedule_maintenance became schedule_cleanup.
    state = policy.load_state()
    state["tool_policies"] = {"schedule_maintenance": "never"}
    policy.save_state(state)
    assert policy.get_policy("schedule_cleanup") == "never"

    # Setting it under either name replaces the old key instead of duplicating it.
    assert policy.set_policy("schedule_maintenance", "auto") is True
    assert policy.load_state()["tool_policies"] == {"schedule_cleanup": "auto"}

    policy.reset_policy("schedule_maintenance")
    assert policy.all_policies() == {}
