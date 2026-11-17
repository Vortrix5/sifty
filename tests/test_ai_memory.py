"""Tests for the AI memory store (conversation + learned preferences)."""

from __future__ import annotations

import pytest

from sifty.core import ai_memory, history


@pytest.fixture
def temp_appdata(monkeypatch, tmp_path):
    """Point ai_memory.db and history.db at a temp APPDATA."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return tmp_path


# --- conversation transcript ------------------------------------------------

def test_append_and_recent_messages(temp_appdata):
    ai_memory.append_message("user", "hi")
    ai_memory.append_message("assistant", "hello")
    ai_memory.append_message("user", "clean junk")
    msgs = ai_memory.recent_messages(2)
    # Oldest-first, limited to the last 2.
    assert msgs == [
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "clean junk"},
    ]


def test_recent_messages_empty(temp_appdata):
    assert ai_memory.recent_messages() == []


def test_clear_messages(temp_appdata):
    ai_memory.append_message("user", "x")
    ai_memory.clear_messages()
    assert ai_memory.recent_messages() == []


# --- skip events -> avoided tools / often skipped ---------------------------

def test_avoided_tools_needs_min_count(temp_appdata):
    ai_memory.record_skip("uninstall_app")
    prefs = ai_memory.learned_preferences(min_count=2)
    assert "uninstall_app" not in prefs.avoided_tools  # only one skip
    ai_memory.record_skip("uninstall_app")
    prefs = ai_memory.learned_preferences(min_count=2)
    assert prefs.avoided_tools == ["uninstall_app"]


def test_often_skipped_pairs(temp_appdata):
    ai_memory.record_skip("clean_junk", "browser-cache")
    ai_memory.record_skip("clean_junk", "browser-cache")
    prefs = ai_memory.learned_preferences(min_count=2)
    assert prefs.often_skipped == ["clean_junk:browser-cache"]


# --- always_clean derived from history --------------------------------------

def test_always_clean_from_history(temp_appdata):
    history.record_clean("junk", "browser-cache,user-temp", 10, 1, [])
    history.record_clean("junk", "browser-cache", 10, 1, [])
    prefs = ai_memory.learned_preferences(min_count=2)
    assert prefs.always_clean == ["browser-cache"]  # user-temp only once


def test_always_clean_ignores_sentinels(temp_appdata):
    history.record_clean("junk", "all", 10, 1, [])
    history.record_clean("junk", "checkup", 10, 1, [])
    prefs = ai_memory.learned_preferences(min_count=1)
    assert prefs.always_clean == []


def test_learned_preferences_empty(temp_appdata):
    prefs = ai_memory.learned_preferences()
    assert prefs.is_empty
