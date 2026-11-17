"""Configuration loading and the per-user app data directory.

Config lives at ``%APPDATA%\\sifty\\config.toml``. Anything not set there
falls back to :data:`DEFAULTS`. The config holds the AI model name, optional
extra protected paths, and feature preferences - never anything secret.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_NAME = "sifty"

DEFAULTS: dict[str, Any] = {
    "ai": {
        # Ollama HTTP endpoint and the local model to use.
        "host": "http://localhost:11434",
        "model": "qwen2.5:3b",
        # Per-chunk wait while streaming a reply (and the initial model load),
        # not the total answer length - see ai/client.py.
        "timeout_seconds": 60,
        # How long Ollama keeps the model resident after a request, so the next
        # question doesn't pay the cold-load cost again.
        "keep_alive": "10m",
        # Agentic autonomy level: "ask" | "low_risk_auto" | "full_auto".
        # "ask"           - confirm every mutating tool call (safe default).
        # "low_risk_auto" - auto-run low-risk tools; confirm high-risk ones.
        # "full_auto"     - run all tools without prompting (still uses Recycle Bin).
        "autonomy": "ask",
    },
    "safety": {
        # User-supplied extra paths that must never be touched, on top of the
        # built-in system denylist in safety.py.
        "extra_protected_paths": [],
    },
    "junk": {
        # Whether to offer leftover installers in Downloads as junk (off by
        # default - those are often wanted).
        "include_downloads_installers": False,
        # Whether to offer Windows.old (post-upgrade leftover) - off by default
        # since it is a one-time cleanup and can be very large.
        "include_windows_old": False,
    },
    "watch": {
        # Free-space threshold (GB) below which `sifty watch check` warns/toasts.
        "threshold_gb": 5,
    },
    "agent": {
        # Whether the background `sifty agent run` may DELETE unattended. Off by
        # default - it only notifies. Turn on to auto-clean low-risk junk.
        "auto_fix": False,
        # Junk categories the unattended run may clean when auto_fix is on. This
        # can only NARROW the hardcoded cache/temp allowlist in core/agent_run.py,
        # never widen it, and admin-only categories are always excluded.
        "auto_fix_categories": [
            "user-temp", "thumbnail-cache", "browser-cache",
            "winget-cache", "onedrive-logs", "discord-cache", "crash-dumps",
        ],
        # Don't auto-clean unless at least this many bytes are reclaimable
        # (default 500 MB); below it, just notify.
        "auto_fix_min_bytes": 524288000,
    },
    "anomaly": {
        # Flag a volume that lost at least this much free space since last check.
        "disk_drop_gb": 10,
        # Flag junk that grew by at least this much since last check.
        "junk_growth_gb": 5,
        # Flag programs newly added to Windows startup.
        "flag_new_startup": True,
    },
    "purge": {
        # Extra artifact directory names beyond the built-in ARTIFACT_DIRS set.
        "extra_patterns": [],
    },
    "monitor": {
        # How often the Monitor TUI view refreshes (seconds).
        "refresh_interval_seconds": 2,
        # CPU % at which the monitor highlights a process in red.
        "cpu_alert_threshold_percent": 85,
    },
}


def app_data_dir() -> Path:
    """Return (and create) the per-user app data directory."""
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return app_data_dir() / "config.toml"


def audit_log_path() -> Path:
    return app_data_dir() / "audit.log"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` onto a copy of ``base``."""
    result = {k: (dict(v) if isinstance(v, dict) else v) for k, v in base.items()}
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass
class Config:
    data: dict[str, Any] = field(default_factory=lambda: _deep_merge(DEFAULTS, {}))

    def section(self, name: str) -> dict[str, Any]:
        return self.data.get(name, {})


def load_config(path: Path | None = None) -> Config:
    """Load config from disk, merged over defaults. Missing file → defaults."""
    target = path or config_path()
    if target.exists():
        with target.open("rb") as fh:
            user_data = tomllib.load(fh)
        return Config(data=_deep_merge(DEFAULTS, user_data))
    return Config()


# ---------------------------------------------------------------------------
# Writing user overrides (for `sifty config set/edit`)
# ---------------------------------------------------------------------------

def read_user_config(path: Path | None = None) -> dict[str, Any]:
    """The raw user override file (NOT merged with defaults); {} if missing."""
    target = path or config_path()
    if not target.exists():
        return {}
    with target.open("rb") as fh:
        return tomllib.load(fh)


def _toml_value(value: Any) -> str:
    """Serialize one scalar/list value as a TOML literal."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def save_user_config(data: dict[str, Any], path: Path | None = None) -> None:
    """Write the user override dict back as TOML (two-level: sections of scalars).

    Only the *overrides* are persisted - anything absent keeps its default, so
    upgrading Sifty can still change defaults the user never touched.
    """
    target = path or config_path()
    lines: list[str] = []
    for section, values in data.items():
        if not isinstance(values, dict) or not values:
            continue
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")


def default_template() -> str:
    """A fully-commented config template showing every setting and its default."""
    lines = [
        "# Sifty configuration - uncomment a line to override its default.",
        "# Run `sifty config show` to see the resolved values.",
        "",
    ]
    for section, values in DEFAULTS.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"# {key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)
