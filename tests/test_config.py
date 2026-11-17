"""Tests for config read/write (the engine behind `sifty config`)."""

from __future__ import annotations

from sifty.infra import config as cfg


def test_save_and_read_round_trip(tmp_path):
    path = tmp_path / "config.toml"
    data = {
        "ai": {"model": "llama3.2:3b", "timeout_seconds": 90},
        "junk": {"include_windows_old": True},
        "safety": {"extra_protected_paths": ["D:\\Important", "E:\\Backups"]},
    }
    cfg.save_user_config(data, path)
    assert cfg.read_user_config(path) == data


def test_save_only_persists_overrides(tmp_path):
    path = tmp_path / "config.toml"
    cfg.save_user_config({"ai": {"model": "custom"}}, path)
    loaded = cfg.load_config(path)
    assert loaded.section("ai")["model"] == "custom"          # override applied
    assert loaded.section("ai")["timeout_seconds"] == 60       # default kept
    assert "watch" not in path.read_text(encoding="utf-8")     # untouched sections absent


def test_toml_value_escaping(tmp_path):
    path = tmp_path / "config.toml"
    tricky = 'C:\\Users\\x and a "quote"'
    cfg.save_user_config({"safety": {"extra_protected_paths": [tricky]}}, path)
    assert cfg.read_user_config(path)["safety"]["extra_protected_paths"] == [tricky]


def test_default_template_is_valid_commented_toml():
    import tomllib

    template = cfg.default_template()
    # Every default appears (commented); section headers stay real so the user
    # can just uncomment a key. Parsing yields empty sections - no overrides.
    assert "[ai]" in template and "# model =" in template
    assert tomllib.loads(template) == {section: {} for section in cfg.DEFAULTS}
