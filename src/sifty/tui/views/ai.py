"""AI screen: an agentic chat panel backed by the local Ollama agent/advisor.

The transcript is a scrollable column of widgets so streaming replies, tool-call
steps, and tool-result tables all render in place as they arrive.

What this view wires together:
- **Agentic loop** (:mod:`sifty.ai.agent`): tool calls + results stream in live;
  tools that need approval render **inline Run/Skip buttons in the chat** (no
  modal) so the AI's proposal reads like a suggestion the user can accept.
- **Autonomy** dropdown (ask / low_risk_auto / full_auto), persisted immediately.
- **Memory**: the conversation persists on the app, so leaving the screen and
  coming back keeps the history; the model also sees prior turns each request.
- **Context**: a metadata-only machine snapshot is built in the worker (never on
  mount) and cached, so opening the screen is instant.
"""

from __future__ import annotations

import logging
import threading

from rich.markdown import Markdown
from rich.markup import escape
from rich.table import Table
from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Select, Static

from ...ai import context as ai_context
from ...ai.advisor import SYSTEM_PROMPT
from ...ai.agent import (
    TOOL_USE_NOTE,
    FallbackEvent,
    FinalAnswerEvent,
    ToolCallEvent,
    ToolResultEvent,
    current_autonomy,
    set_autonomy,
)
from ...ai.agent import (
    run as agent_run,
)
from ...ai.client import OllamaClient, OllamaUnavailable
from ...ai.tools import TOOLS as ALL_TOOLS
from ...core import ai_memory
from .base import BaseView

logger = logging.getLogger("sifty.tui")

_AUTONOMY_OPTIONS = [
    ("Ask before acting", "ask"),
    ("Auto low-risk", "low_risk_auto"),
    ("Full auto", "full_auto"),
]

_RISK_COLOR = {"read": "cyan", "low": "yellow", "high": "red"}

# Read-only tool -> a deterministic follow-up action rendered as a button under
# the result (label, nav key). Deliberately NOT model-generated: a small local
# model can't reliably emit action schemas, and the mapped screens already
# guard every destructive step behind preview + confirm.
_TOOL_ACTIONS: dict[str, tuple[str, str]] = {
    "scan_junk": ("Clean junk…", "junk"),
    "analyze_disk": ("Open Disk…", "disk"),
    "find_duplicates": ("Review duplicates…", "cleanup"),
    "list_apps": ("Open Apps…", "apps"),
    "list_updates": ("Review updates…", "updates"),
    "find_orphan_apps": ("Review orphans…", "apps"),
    "scan_project_artifacts": ("Purge artifacts…", "purge"),
    "system_status": ("Open Monitor…", "monitor"),
}


class AIView(BaseView):
    def compose(self) -> ComposeResult:
        yield Static("Ask Sifty", classes="title")
        yield Static("Checking Ollama…", id="ai-status", classes="subtle")
        with Horizontal(id="ai-controls"):
            yield Static("Autonomy:", classes="ctl-label")
            yield Select(
                _AUTONOMY_OPTIONS, id="autonomy",
                value=current_autonomy(), allow_blank=False,
            )
        yield VerticalScroll(id="chat-log")
        yield Input(
            placeholder="Ask about cleanup, disk usage, safety…  (Enter to send)",
            id="ask",
        )

    def on_mount(self) -> None:
        self._client = OllamaClient.from_config()
        self._online = False
        self._live: Static | None = None       # in-progress streaming reply
        self._thinking: Static | None = None    # "Sifty is thinking…" placeholder
        # In-flight inline approval: (worker wake event, result holder, chat row).
        self._pending: tuple[threading.Event, dict, Horizontal] | None = None
        self._autonomy = current_autonomy()
        # Conversation persists on the app across navigation and, seeded once per
        # session from ai_memory.db, across restarts too.
        if getattr(self.app, "_ai_messages", None) is None:
            self.app._ai_messages = ai_memory.recent_messages(20)
        self._messages: list[dict] = self.app._ai_messages
        self._system: str | None = getattr(self.app, "_ai_system", None)
        self._replay_history()
        if self.workers_enabled():
            self.check_status()

    # ------------------------------------------------------------------ status
    @work(thread=True, exclusive=True, group="ai-status")
    def check_status(self) -> None:
        ok = self._client.is_available()
        self.app.call_from_thread(self._set_online, ok)

    def _set_online(self, ok: bool) -> None:
        self._online = ok
        self._refresh_status()

    def _refresh_status(self) -> None:
        status = self.query_one("#ai-status", Static)
        if self._online:
            status.update(f"[green]●[/green] Ollama connected · model [b]{self._client.model}[/b]")
        else:
            status.update(
                f"[yellow]●[/yellow] Ollama not reachable at {self._client.host} - "
                f"start it and run `ollama pull {self._client.model}`"
            )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "autonomy" and event.value not in (None, Select.BLANK):
            self._autonomy = str(event.value)
            set_autonomy(self._autonomy)

    # ------------------------------------------------------------------ input
    async def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#ask", Input).value = ""
        self._submit(event.value)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.has_class("approve") or event.button.has_class("deny"):
            self._resolve_approval(approved=event.button.has_class("approve"))
            return
        nav_key = getattr(event.button, "_nav_key", None)
        if nav_key:  # a tool-result follow-up action - jump to the screen
            await self.app.show(nav_key)

    def _submit(self, question: str) -> None:
        question = question.strip()
        if not question:
            return
        log = self.query_one("#chat-log", VerticalScroll)
        log.mount(Static(f"[b cyan]You[/b cyan]  {escape(question)}", classes="msg-user"))
        self._messages.append({"role": "user", "content": question})
        ai_memory.append_message("user", question)
        self._thinking = Static("[dim]Sifty is thinking…[/dim]", classes="msg-thinking")
        log.mount(self._thinking)
        log.scroll_end(animate=False)
        self.ask(list(self._messages))

    # ------------------------------------------------------------------ worker
    @work(thread=True, exclusive=True, group="ai-chat")
    def ask(self, messages: list[dict]) -> None:
        if not self._client.is_available():
            self.app.call_from_thread(self._show_error, "AI unavailable - is Ollama running?")
            return
        system = self._ensure_system()
        full = [{"role": "system", "content": system}] + messages

        try:
            for event in agent_run(
                self._client, full,
                autonomy=self._autonomy,
                confirm=self._confirm_blocking,
                tools=ALL_TOOLS,
            ):
                if isinstance(event, ToolCallEvent):
                    self.app.call_from_thread(self._show_tool_call, event)
                elif isinstance(event, ToolResultEvent):
                    self.app.call_from_thread(self._show_tool_result, event)
                elif isinstance(event, FallbackEvent):
                    answer = self._stream_fallback(full) or event.text
                    self.app.call_from_thread(self._finish_reply, answer)
                    self._remember(answer)
                elif isinstance(event, FinalAnswerEvent):
                    self.app.call_from_thread(self._finish_reply, event.text)
                    self._remember(event.text)
        except OllamaUnavailable as exc:
            self.app.call_from_thread(self._show_error, str(exc))
        except Exception as exc:  # never let a worker die silently
            logger.exception("AI agent failed")
            self.app.call_from_thread(self._show_error, str(exc))

    def _ensure_system(self) -> str:
        """Build (once) the system prompt + machine context, off the UI thread."""
        if self._system is None:
            base = SYSTEM_PROMPT + TOOL_USE_NOTE
            ctx = ai_context.build()
            self._system = f"{base}\n\n{ctx}" if ctx else base
            self.app._ai_system = self._system
        return self._system

    def _stream_fallback(self, full: list[dict]) -> str:
        """A tool-less model: stream a plain reply into a live widget."""
        self.app.call_from_thread(self._begin_reply)
        parts: list[str] = []
        try:
            for chunk in self._client.chat_stream("", "", messages=full):
                parts.append(chunk)
                self.app.call_from_thread(self._stream, "".join(parts))
        except OllamaUnavailable as exc:
            self.app.call_from_thread(self._show_error, str(exc))
            return ""
        return "".join(parts).strip()

    def _confirm_blocking(self, prompt: str) -> bool:
        """Ask the user to approve a tool from the worker thread (blocks until answered).

        Renders inline Run/Skip buttons in the chat - the AI's proposal reads
        like a suggestion the user accepts in place, not a modal interruption.
        """
        done = threading.Event()
        holder = {"ok": False}
        self.app.call_from_thread(self._mount_approval, prompt, done, holder)
        done.wait()
        return holder["ok"]

    def _mount_approval(self, prompt: str, done: threading.Event, holder: dict) -> None:
        row = Horizontal(classes="approval-row")
        row._prompt = prompt
        self._pending = (done, holder, row)
        self._remove_thinking()
        log = self._log()
        log.mount(row)
        row.mount(Static(f"[b]?[/b] {escape(prompt)}", classes="approval-text"))
        row.mount(Button("Run", variant="warning", classes="approve"))
        row.mount(Button("Skip", classes="deny"))
        log.scroll_end(animate=False)

    def _resolve_approval(self, *, approved: bool) -> None:
        if self._pending is None:
            return
        done, holder, row = self._pending
        self._pending = None
        holder["ok"] = approved
        verdict = "[green]✓ approved[/green]" if approved else "[dim]✗ skipped[/dim]"
        prompt = getattr(row, "_prompt", "")
        self._log().mount(Static(f"{verdict} [dim]{escape(prompt)}[/dim]", classes="msg-tool"))
        row.remove()
        done.set()  # wake the agent worker

    def on_unmount(self) -> None:
        # Navigating away with an approval pending: deny it (no widget work -
        # the view is going away) so the agent worker doesn't block forever.
        if self._pending is not None:
            done, holder, _row = self._pending
            self._pending = None
            holder["ok"] = False
            done.set()

    def _remember(self, answer: str) -> None:
        if answer:
            self._messages.append({"role": "assistant", "content": answer})
            ai_memory.append_message("assistant", answer)

    # --------------------------------------------------- UI helpers (main thread)
    def _log(self) -> VerticalScroll:
        return self.query_one("#chat-log", VerticalScroll)

    def _remove_thinking(self) -> None:
        if self._thinking is not None:
            self._thinking.remove()
            self._thinking = None

    def _replay_history(self) -> None:
        """Re-render the stored conversation when the view is re-mounted."""
        log = self._log()
        for msg in self._messages:
            role, content = msg.get("role"), msg.get("content", "")
            if role == "user":
                log.mount(Static(f"[b cyan]You[/b cyan]  {escape(content)}", classes="msg-user"))
            elif role == "assistant":
                log.mount(Static("[b green]Sifty[/b green]", classes="msg-label"))
                log.mount(Static(Markdown(content), classes="msg"))

    def _show_tool_call(self, event: ToolCallEvent) -> None:
        self._remove_thinking()
        color = _RISK_COLOR.get(event.risk, "cyan")
        args = ", ".join(f"{k}={v!r}" for k, v in event.args.items()) if event.args else ""
        suffix = f" [dim]{escape(args)}[/dim]" if args else ""
        label = f"[{color}]⚙ {event.tool_name}[/{color}]{suffix}  [dim]· {event.risk}[/dim]"
        self._log().mount(Static(label, classes="msg-tool"))
        self._log().scroll_end(animate=False)

    def _show_tool_result(self, event: ToolResultEvent) -> None:
        self._remove_thinking()
        log = self._log()
        if event.skipped:
            log.mount(Static(f"[red]✗ {escape(event.tool_name)} skipped[/red]", classes="msg-tool"))
        elif event.table is not None:
            log.mount(Static(self._build_table(event.table), classes="msg-table"))
        else:
            log.mount(Static(f"[green]✓[/green] [dim]{escape(event.result)}[/dim]", classes="msg-tool"))
        self._mount_follow_up(event)
        log.scroll_end(animate=False)

    def _mount_follow_up(self, event: ToolResultEvent) -> None:
        """Offer a one-tap action button when a read-only scan found something."""
        if event.skipped or event.table is None or not event.table.rows:
            return
        action = _TOOL_ACTIONS.get(event.tool_name)
        if action is None:
            return
        label, nav_key = action
        button = Button(label, classes="ai-action")
        button._nav_key = nav_key
        self._log().mount(button)

    def _build_table(self, tr) -> Table:
        table = Table(title=tr.title or None, title_style="bold", expand=False, pad_edge=False)
        for col in tr.columns:
            table.add_column(col, overflow="fold")
        for row in tr.rows[:50]:
            table.add_row(*[escape(str(c)) for c in row])
        if len(tr.rows) > 50:
            table.add_row(*[f"… +{len(tr.rows) - 50} more"] + [""] * (len(tr.columns) - 1))
        return table

    def _begin_reply(self) -> None:
        self._remove_thinking()
        log = self._log()
        log.mount(Static("[b green]Sifty[/b green]", classes="msg-label"))
        self._live = Static("[dim]…[/dim]", classes="msg")
        log.mount(self._live)
        log.scroll_end(animate=False)

    def _stream(self, text: str) -> None:
        if self._live is not None:
            self._live.update(Markdown(text))
            self._log().scroll_end(animate=False)

    def _finish_reply(self, answer: str) -> None:
        self._remove_thinking()
        log = self._log()
        if self._live is not None:
            self._live.update(Markdown(answer))
            self._live = None
        else:
            log.mount(Static("[b green]Sifty[/b green]", classes="msg-label"))
            log.mount(Static(Markdown(answer), classes="msg"))
        log.scroll_end(animate=False)

    def _show_error(self, err: str) -> None:
        self._remove_thinking()
        if self._live is not None:
            self._live.update(f"[yellow](error: {escape(err)})[/yellow]")
            self._live = None
        else:
            self._log().mount(Static(f"[yellow](error: {escape(err)})[/yellow]", classes="msg"))
        self._log().scroll_end(animate=False)
