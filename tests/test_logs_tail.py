"""`sifty logs --tail 0` must show no lines (not the whole file)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import sifty.cli.app as cli_app
from sifty.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _sandbox_appdata(monkeypatch, tmp_path):
    # Keep config/profiles/history/logs out of the real %APPDATA%.
    monkeypatch.setenv("APPDATA", str(tmp_path))


def _write_log(tmp_path, count):
    log = tmp_path / "sifty.log"
    log.write_text("".join(f"line {i}\n" for i in range(count)))
    return log


def test_logs_tail_zero_shows_no_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_app, "log_file", lambda: _write_log(tmp_path, 5))
    result = runner.invoke(app, ["logs", "--tail", "0"])
    assert result.exit_code == 0
    assert "line 0" not in result.stdout
    assert "line 4" not in result.stdout


def test_logs_tail_still_shows_last_lines(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_app, "log_file", lambda: _write_log(tmp_path, 5))
    result = runner.invoke(app, ["logs", "--tail", "2"])
    assert result.exit_code == 0
    assert "line 3" in result.stdout
    assert "line 4" in result.stdout
    assert "line 0" not in result.stdout
