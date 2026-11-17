"""Tests for the `sifty agent` CLI apply-decision boundary."""

from __future__ import annotations

import pytest

from sifty.cli.commands import agent as agent_cli
from sifty.core.agent_run import AgentRunResult


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


def _empty_result() -> AgentRunResult:
    return AgentRunResult([], [], None, False, "", [])


@pytest.fixture
def capture_apply(monkeypatch):
    captured = {}
    monkeypatch.setattr(agent_cli.agent_run, "run_agent",
                        lambda **kw: captured.update(kw) or _empty_result())
    return captured


def test_foreground_without_apply_is_read_only(temp_appdata, capture_apply):
    agent_cli.run_cmd(apply=False, background=False)
    assert capture_apply["apply"] is False


def test_foreground_apply_flag_opts_in(temp_appdata, capture_apply):
    agent_cli.run_cmd(apply=True, background=False)
    assert capture_apply["apply"] is True


def test_background_defaults_to_config_autofix_off(temp_appdata, capture_apply):
    # No config file -> auto_fix defaults to False, so background does not delete.
    agent_cli.run_cmd(apply=False, background=True)
    assert capture_apply["apply"] is False


def test_background_honours_config_autofix_on(temp_appdata, capture_apply, monkeypatch):
    from sifty.infra.config import DEFAULTS, Config, _deep_merge
    monkeypatch.setattr(agent_cli, "load_config",
                        lambda: Config(data=_deep_merge(DEFAULTS, {"agent": {"auto_fix": True}})))
    agent_cli.run_cmd(apply=False, background=True)
    assert capture_apply["apply"] is True
