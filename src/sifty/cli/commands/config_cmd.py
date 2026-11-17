"""`sifty config` - view and edit the config without hunting through AppData."""

from __future__ import annotations

import os
import tomllib

import typer

from ...console import console, error, success
from ...infra.config import (
    DEFAULTS,
    config_path,
    default_template,
    load_config,
    read_user_config,
    save_user_config,
)
from .. import output

app = typer.Typer(help="View and edit Sifty's configuration (config.toml).")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """With no subcommand, show the resolved configuration."""
    if ctx.invoked_subcommand is None:
        show_cmd()


@app.command("show")
def show_cmd() -> None:
    """Show every setting with its resolved value (defaults + your overrides)."""
    cfg = load_config()
    overrides = read_user_config()

    if output.json_enabled():
        output.emit({"path": str(config_path()), "config": cfg.data})
        return

    console.print(f"[dim]{config_path()}[/dim]\n")
    for section in DEFAULTS:
        console.print(f"[bold cyan]\\[{section}][/bold cyan]")
        for key, value in cfg.section(section).items():
            overridden = key in overrides.get(section, {})
            marker = " [yellow](set by you)[/yellow]" if overridden else ""
            console.print(f"  {key} = {value!r}{marker}")
    console.print("\n[dim]Change one with: sifty config set <section.key> <value>[/dim]")


@app.command("path")
def path_cmd() -> None:
    """Print the config file location."""
    console.print(str(config_path()))


@app.command("edit")
def edit_cmd() -> None:
    """Open the config file in your default editor (creates a template first run)."""
    path = config_path()
    if not path.exists():
        path.write_text(default_template(), encoding="utf-8")
        console.print(f"[dim]Created a commented template at {path}[/dim]")
    os.startfile(str(path))  # noqa: S606 - opening the user's own config file


@app.command("get")
def get_cmd(
    key: str = typer.Argument(..., help="Setting to read, as section.key (e.g. ai.model)."),
) -> None:
    """Print one resolved setting."""
    section, _, name = key.partition(".")
    value = load_config().section(section).get(name)
    if value is None and name not in DEFAULTS.get(section, {}):
        error(f"Unknown setting '{key}'. See `sifty config show` for valid keys.")
        raise typer.Exit(1)
    if output.json_enabled():
        output.emit({key: value})
    else:
        console.print(repr(value))


@app.command("set")
def set_cmd(
    key: str = typer.Argument(..., help="Setting to change, as section.key (e.g. ai.model)."),
    value: str = typer.Argument(..., help='New value (TOML literal: true, 5, "text", ["a","b"]).'),
) -> None:
    """Change one setting (validates the key, keeps everything else untouched)."""
    section, _, name = key.partition(".")
    if name not in DEFAULTS.get(section, {}):
        valid = ", ".join(f"{s}.{k}" for s, ks in DEFAULTS.items() for k in ks)
        error(f"Unknown setting '{key}'. Valid keys: {valid}")
        raise typer.Exit(1)

    # Parse the value as a TOML literal so booleans/numbers/lists round-trip;
    # anything that doesn't parse is treated as a plain string.
    try:
        parsed = tomllib.loads(f"v = {value}")["v"]
    except tomllib.TOMLDecodeError:
        parsed = value

    default = DEFAULTS[section][name]
    if type(parsed) is not type(default) and not (
        isinstance(default, (int, float)) and isinstance(parsed, (int, float))
    ):
        error(
            f"'{key}' expects {type(default).__name__} "
            f"(default: {default!r}), got {type(parsed).__name__} ({parsed!r})."
        )
        raise typer.Exit(1)

    overrides = read_user_config()
    overrides.setdefault(section, {})[name] = parsed
    save_user_config(overrides)
    success(f"{key} = {parsed!r}  [dim]({config_path()})[/dim]")


@app.command("reset")
def reset_cmd(
    key: str = typer.Argument(..., help="Setting to reset to its default, as section.key."),
) -> None:
    """Remove one override so the setting goes back to its default."""
    section, _, name = key.partition(".")
    overrides = read_user_config()
    if name not in overrides.get(section, {}):
        console.print(f"'{key}' is not overridden - already at its default.")
        return
    del overrides[section][name]
    if not overrides[section]:
        del overrides[section]
    save_user_config(overrides)
    default = DEFAULTS.get(section, {}).get(name)
    success(f"{key} reset to default ({default!r}).")
