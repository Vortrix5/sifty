"""Tabbed group views - host several sub-views under one sidebar entry.

Consolidates the formerly separate "free up space" screens (Junk / Purge /
Optimize / Smart cleanup) and "what's installed" screens (Installed apps /
Startup / Services) into two tabbed screens, so the sidebar stays short.

Sub-views are **lazily mounted**: only the active tab's view is instantiated, so
each sub-view's ``on_mount`` (and its scan worker) fires only when that tab is
first shown - opening a group never kicks off several scans at once.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane

from .apps import AppsView
from .base import BaseView
from .cleanup import CleanupView
from .junk import JunkView
from .optimize import OptimizeView
from .purge import PurgeView
from .services import ServicesView
from .startup import StartupView
from .updates import UpdatesView


class TabGroupView(BaseView):
    """Hosts several sub-views as lazily-mounted ``TabPane``s."""

    # (tab_id, label, view_cls) - order defines the tab strip.
    TABS: list[tuple[str, str, type[BaseView]]] = []

    def __init__(self, initial_tab: str | None = None) -> None:
        super().__init__()
        self._initial = f"pane-{initial_tab}" if initial_tab else ""

    def compose(self) -> ComposeResult:
        with TabbedContent(id="group-tabs", initial=self._initial):
            for tab_id, label, _cls in self.TABS:
                yield TabPane(label, id=f"pane-{tab_id}")

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        # Fires for the initial tab on mount and on every later tab switch.
        pane_id = event.pane.id or ""
        self._ensure_mounted(pane_id.removeprefix("pane-"))

    def _ensure_mounted(self, tab_id: str) -> None:
        pane = self.query_one(f"#pane-{tab_id}", TabPane)
        if pane.query(BaseView):  # already mounted once
            return
        cls = next((c for t, _l, c in self.TABS if t == tab_id), None)
        if cls is not None:
            pane.mount(cls())

    def activate_tab(self, tab_id: str) -> None:
        """Switch to a tab programmatically (mounts its view on demand)."""
        self.query_one("#group-tabs", TabbedContent).active = f"pane-{tab_id}"


class CleanView(TabGroupView):
    """Everything that frees up space, under one roof."""

    TABS = [
        ("junk", "Junk", JunkView),
        ("purge", "Purge", PurgeView),
        ("optimize", "Optimize", OptimizeView),
        ("cleanup", "Smart", CleanupView),
    ]


class AppsSystemView(TabGroupView):
    """Installed software and what runs on the machine."""

    TABS = [
        ("apps", "Installed", AppsView),
        ("updates", "Updates", UpdatesView),
        ("startup", "Startup", StartupView),
        ("services", "Services", ServicesView),
    ]


# Old (sub-view) nav key -> (group nav key, tab id). Lets every former screen
# stay directly reachable - via the command palette and via ``app.show(key)`` -
# now deep-linking into the right tab of its group.
SUBVIEW_ROUTES: dict[str, tuple[str, str]] = {
    "junk": ("clean", "junk"),
    "purge": ("clean", "purge"),
    "optimize": ("clean", "optimize"),
    "cleanup": ("clean", "cleanup"),
    "updates": ("apps", "updates"),
    "startup": ("apps", "startup"),
    "services": ("apps", "services"),
}

# Friendly labels for the deep-link palette entries.
SUBVIEW_LABELS: dict[str, str] = {
    "junk": "Junk",
    "purge": "Purge",
    "optimize": "Optimize",
    "cleanup": "Smart cleanup",
    "updates": "Updates",
    "startup": "Startup",
    "services": "Services",
}
