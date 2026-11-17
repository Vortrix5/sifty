"""Sifty TUI - full-screen interactive app.

A thin frontend over the same core functions as the CLI. The sidebar selects a
content view (see ``views/``); views load their data in background workers and
route destructive actions through confirm modals + ``safety.trash()``.
"""

from __future__ import annotations

import logging

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.theme import Theme
from textual.widgets import Footer, Header, Label, ListItem, ListView
from textual.worker import Worker, WorkerState

from ..windows.admin import is_admin, relaunch_as_admin
from .commands import SiftyCommands
from .views import VIEWS

logger = logging.getLogger("sifty.tui")

# A controlled palette so the look doesn't depend on the terminal's own scheme.
# (In a truecolor terminal like Windows Terminal these render exactly; legacy
# conhost will approximate them.)
SIFTY_THEME = Theme(
    name="sifty",
    primary="#7aa2f7",     # blue
    secondary="#9aa5ce",
    accent="#7dcfff",      # cyan
    foreground="#c0caf5",
    background="#16161e",  # deep navy-black
    surface="#1a1b26",
    panel="#1f2335",
    success="#9ece6a",
    warning="#e0af68",
    error="#f7768e",
    dark=True,
)

# (nav key, sidebar label) - order defines the menu. No emoji: legacy consoles
# render them as tofu boxes; Windows Terminal users still get a clean look.
# "Clean" and "Apps" are tabbed groups (see views/group.py) that fold in the
# former Junk/Purge/Optimize/Cleanup and Startup/Services screens.
SECTIONS: list[tuple[str, str]] = [
    ("home", "Home"),
    ("clean", "Clean"),
    ("disk", "Disk"),
    ("apps", "Apps"),
    ("monitor", "Monitor"),
    ("reports", "Reports"),
    ("ai", "AI"),
]


class SiftyApp(App):
    """The top-level Sifty terminal application."""

    CSS_PATH = "styles.tcss"
    COMMANDS = App.COMMANDS | {SiftyCommands}
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("f2", "elevate", "Admin"),
    ]
    TITLE = "Sifty"
    SUB_TITLE = "Windows maintenance"

    def __init__(self, start_workers: bool = True) -> None:
        super().__init__()
        # Tests set this False to mount views without firing the slow workers.
        self.start_workers = start_workers

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield ListView(
                *[ListItem(Label(label), id=f"nav-{key}") for key, label in SECTIONS],
                id="sidebar",
            )
            yield VerticalScroll(id="content")
        yield Footer()

    async def on_mount(self) -> None:
        self.register_theme(SIFTY_THEME)
        self.theme = "sifty"
        self.sub_title = (
            "Administrator" if is_admin() else "standard user · F2 to elevate"
        )
        await self.show("home")

    def action_elevate(self) -> None:
        """Relaunch Sifty with administrator rights (UAC), or report status."""
        if is_admin():
            self.notify("Already running as administrator.", title="Admin")
            return
        if relaunch_as_admin():
            self.exit(message="Relaunching Sifty as administrator…")
        else:
            self.notify(
                "Could not elevate - the UAC prompt was dismissed.",
                title="Admin",
                severity="warning",
            )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        key = (event.item.id or "nav-home").removeprefix("nav-")
        await self.show(key)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        """Log any worker that fails, so TUI errors land in the diagnostics log."""
        if event.state is WorkerState.ERROR:
            logger.error(
                "Worker %r failed", event.worker.name, exc_info=event.worker.error
            )

    async def show(self, key: str) -> None:
        # A sub-view key (e.g. "junk", "startup") deep-links into its group's
        # tab; a top-level key maps straight to its view class.
        from .views import SUBVIEW_ROUTES, TabGroupView

        group_key, tab_id = SUBVIEW_ROUTES.get(key, (key, None))
        view_cls = VIEWS.get(group_key)
        if view_cls is None:
            return
        content = self.query_one("#content", VerticalScroll)
        await content.remove_children()
        if issubclass(view_cls, TabGroupView):
            await content.mount(view_cls(initial_tab=tab_id))
        else:
            await content.mount(view_cls())


def run() -> None:
    """Entry point used by the ``sifty tui`` command."""
    from ..infra.logging import get_logger, setup_logging

    setup_logging()
    try:
        SiftyApp().run()
    except Exception:
        get_logger("sifty.tui").exception("TUI crashed")
        raise
