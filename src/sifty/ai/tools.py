"""Tool registry for the AI agent.

Each tool has a name, Ollama-compatible JSON-schema descriptor, a risk tag
(``"read"`` | ``"low"`` | ``"high"``), and a handler.

Handlers return a :class:`ToolResult` (a concise ``summary`` for the model plus
optional tabular data the UI renders as a real table) or a plain ``str``.  The
summary is what gets fed back to the LLM - it is deliberately short so the model
adds *insight* instead of re-dumping the raw data (which the UI already shows).

Risk levels:
  - ``read``  - no side effects, always runs automatically.
  - ``low``   - reversible / low-impact (e.g. toggle a startup entry).
  - ``high``  - destructive or hard to reverse (delete files, uninstall, update).

The autonomy level decides which tiers need a confirm (see :mod:`sifty.ai.agent`).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..console import human_size

# ---------------------------------------------------------------------------
# Tool result + Tool dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """A tool's output: a short ``summary`` for the LLM + optional table for UI."""
    summary: str
    title: str = ""
    columns: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @property
    def has_table(self) -> bool:
        return bool(self.columns and self.rows)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict          # JSON Schema for the function's arguments
    risk: str                 # "read" | "low" | "high"
    handler: Callable[[dict], ToolResult | str]

    def to_ollama(self) -> dict:
        """Return the Ollama tool-descriptor for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# Handlers (call core functions; return ToolResult)
# ---------------------------------------------------------------------------

def _handler_scan_junk(_args: dict) -> ToolResult:
    from ..core import junk
    cats = [c for c in junk.scan() if c.size > 0]
    if not cats:
        return ToolResult(summary="No junk found - the machine is already tidy.")
    total = sum(c.size for c in cats)
    rows = [[c.category.key, c.category.label, human_size(c.size), f"{c.file_count:,}"]
            for c in cats]
    summary = (
        f"Found {human_size(total)} of removable junk across {len(cats)} categories "
        f"(keys: {', '.join(c.category.key for c in cats)}). Shown to the user as a table."
    )
    return ToolResult(summary=summary, title="Junk by category",
                      columns=["Key", "Category", "Size", "Files"], rows=rows)


def _handler_analyze_disk(args: dict) -> ToolResult:
    from ..core import disk
    raw = args.get("path", "")
    path = Path(raw).expanduser()
    if not path.exists():
        return ToolResult(summary=f"Path does not exist: {path}")
    try:
        items = disk.biggest(path, 20)
    except OSError as exc:
        return ToolResult(summary=f"Could not read {path}: {exc}")
    if not items:
        return ToolResult(summary=f"No files found in {path}.")
    rows = [[entry.name, human_size(size)] for entry, size in items]
    top = ", ".join(f"{e.name} ({human_size(s)})" for e, s in items[:3])
    return ToolResult(
        summary=f"Largest items in {path}: {top}. Full list shown to the user as a table.",
        title=f"Largest items in {path}", columns=["Item", "Size"], rows=rows,
    )


def _handler_find_duplicates(args: dict) -> ToolResult:
    from ..core import disk
    raw = args.get("path", "")
    path = Path(raw).expanduser()
    if not path.exists():
        return ToolResult(summary=f"Path does not exist: {path}")
    try:
        groups = [g for g in disk.find_duplicates(path).values() if len(g) > 1]
    except OSError as exc:
        return ToolResult(summary=f"Could not scan {path}: {exc}")
    if not groups:
        return ToolResult(summary=f"No duplicate files found in {path}.")
    wasted = 0
    rows = []
    for g in groups:
        try:
            size = g[0].stat().st_size
        except OSError:
            size = 0
        wasted += size * (len(g) - 1)
        rows.append([str(len(g)), g[0].name, human_size(size * (len(g) - 1))])
    rows.sort(key=lambda r: r[1])
    return ToolResult(
        summary=f"Found {len(groups)} duplicate groups wasting ~{human_size(wasted)} in {path}. "
                f"Suggest the user review them in the Cleanup screen (keep-one, trash the rest).",
        title=f"Duplicates in {path}", columns=["Copies", "File", "Wasted"], rows=rows,
    )


def _handler_list_apps(_args: dict) -> ToolResult:
    from ..core.apps import installed_apps
    apps = sorted(installed_apps(), key=lambda a: a.size_bytes, reverse=True)
    if not apps:
        return ToolResult(summary="No installed apps found.")
    rows = [[a.name, a.version or "-", human_size(a.size_bytes) if a.size_bytes else "-"]
            for a in apps[:25]]
    biggest = ", ".join(f"{a.name} ({human_size(a.size_bytes)})" for a in apps[:3] if a.size_bytes)
    return ToolResult(
        summary=f"{len(apps)} apps installed. Largest: {biggest}. "
                f"Full list shown to the user as a table - do not repeat it.",
        title="Installed apps (largest first)", columns=["Name", "Version", "Size"], rows=rows,
    )


def _handler_list_updates(_args: dict) -> ToolResult:
    from ..core.updates import list_upgrades
    ups = list_upgrades()
    if not ups:
        return ToolResult(summary="All apps are up to date - no updates pending.")
    rows = [[u.name, u.current, u.available] for u in ups]
    names = ", ".join(u.name for u in ups[:5])
    return ToolResult(
        summary=f"{len(ups)} update(s) available: {names}"
                f"{' …' if len(ups) > 5 else ''}. Shown to the user as a table. "
                f"To apply one, use apply_updates with the package id.",
        title="Pending updates", columns=["App", "Current", "Available"], rows=rows,
    )


def _handler_clean_junk(args: dict) -> ToolResult:
    from ..core import junk
    categories = args.get("categories") or []
    only = set(categories) if categories else None
    result = junk.clean(only=only, dry_run=False)
    if not result.items:
        return ToolResult(summary="Nothing was cleaned (no matching junk found).")
    return ToolResult(
        summary=f"Cleaned {result.items} items ({human_size(result.bytes_freed)}) "
                f"to the Recycle Bin. The user can undo this from the Reports screen."
    )


def _handler_uninstall_app(args: dict) -> ToolResult:
    from ..core.apps import uninstall_app
    name = args.get("name", "")
    ok, message = uninstall_app(name)
    return ToolResult(summary=f"Uninstall of '{name}' {'succeeded' if ok else 'failed'}: {message}")


def _handler_toggle_startup(args: dict) -> ToolResult:
    from ..core import startup
    name = args.get("name", "")
    enable = bool(args.get("enable", True))
    verb = "enabled" if enable else "disabled"
    ok = startup.set_enabled(name, enable)
    if ok:
        return ToolResult(summary=f"Startup entry '{name}' {verb}.")
    return ToolResult(
        summary=f"Could not {('enable' if enable else 'disable')} '{name}' - "
                f"it may not exist or is already in that state."
    )


def _handler_schedule_cleanup(args: dict) -> ToolResult:
    from ..core import schedule
    name = args.get("name", "sifty-auto")
    profile = args.get("profile", "")
    frequency = (args.get("frequency") or "DAILY").upper()
    day = (args.get("day") or "MON").upper()
    time_str = args.get("time", "03:00")
    if not profile:
        return ToolResult(summary="Cannot schedule: no profile name provided. "
                          "Ask the user which profile to use (see sifty profile list).")
    command = schedule.sifty_command(profile)
    ok, msg = schedule.add(name, profile, command, sc=frequency, day=day, time=time_str)
    if ok:
        when = f"{frequency.title()} {day} at {time_str}" if frequency == "WEEKLY" else f"Daily at {time_str}"
        return ToolResult(
            summary=f"Scheduled '{name}' to run profile '{profile}' {when}. "
                    f"Use `sifty schedule list` to confirm."
        )
    return ToolResult(summary=f"Failed to create scheduled task: {msg}")


def _handler_prune_worktrees(args: dict) -> ToolResult:
    from ..core.vcs import find_orphan_worktrees, prune_worktrees
    raw = args.get("path", "")
    path = Path(raw).expanduser()
    if not path.exists():
        return ToolResult(summary=f"Path does not exist: {path}")
    orphans = find_orphan_worktrees(path)
    if not orphans:
        return ToolResult(summary=f"No orphaned worktrees found in {path}.")
    result = prune_worktrees(path, dry_run=False)
    return ToolResult(
        summary=f"Pruned {result.items} orphaned worktree(s) ({human_size(result.bytes_freed)}) "
                f"from {path}. Directories sent to Recycle Bin."
                + (f" {len(result.skipped)} skipped." if result.skipped else "")
    )


def _handler_find_orphan_apps(_args: dict) -> ToolResult:
    from ..core.registry_scan import find_orphan_uninstall_entries
    entries = find_orphan_uninstall_entries()
    if not entries:
        return ToolResult(summary="No orphaned uninstall entries found - the registry looks clean.")
    rows = [[e.display_name, e.reason, e.hive] for e in entries]
    return ToolResult(
        summary=f"Found {len(entries)} orphaned uninstall entries with broken or missing uninstallers. "
                f"Full list shown to the user as a table. These are read-only findings; "
                f"removal must be done manually via regedit or a registry cleaner.",
        title="Orphaned uninstall entries",
        columns=["Application", "Reason", "Hive"],
        rows=rows,
    )


def _handler_scan_artifacts(args: dict) -> ToolResult:
    from ..core.purge import scan_artifacts
    raw = args.get("path", "")
    path = Path(raw).expanduser()
    if not path.exists():
        return ToolResult(summary=f"Path does not exist: {path}")
    artifacts = scan_artifacts(path)
    if not artifacts:
        return ToolResult(summary=f"No artifact directories found under {path}.")
    total = sum(a.size_bytes for a in artifacts)
    rows = [[a.pattern, human_size(a.size_bytes), str(a.path)] for a in artifacts]
    top = ", ".join(f"{a.pattern} ({human_size(a.size_bytes)})" for a in artifacts[:3])
    return ToolResult(
        summary=f"Found {len(artifacts)} artifact directories ({human_size(total)}) in {path}. "
                f"Largest: {top}. Full list shown as table.",
        title=f"Artifact directories in {path}",
        columns=["Pattern", "Size", "Path"],
        rows=rows,
    )


def _handler_purge_artifacts(args: dict) -> ToolResult:
    from ..core.purge import purge_artifacts, scan_artifacts
    raw = args.get("path", "")
    path = Path(raw).expanduser()
    if not path.exists():
        return ToolResult(summary=f"Path does not exist: {path}")
    artifacts = scan_artifacts(path)
    if not artifacts:
        return ToolResult(summary=f"No artifact directories found under {path}.")
    result = purge_artifacts(artifacts, dry_run=False)
    return ToolResult(
        summary=f"Purged {result.items} artifact directories ({human_size(result.bytes_freed)}) "
                f"to the Recycle Bin under {path}."
                + (f" {len(result.skipped)} skipped." if result.skipped else "")
    )


def _handler_optimize_system(_args: dict) -> ToolResult:
    from ..core.optimize import list_operations, run_op
    from ..windows.admin import is_admin
    admin = is_admin()
    ops = [
        op for op in list_operations()
        if op.key != "compact-vhd" and (not op.requires_admin or admin)
    ]
    if not ops:
        return ToolResult(summary="No operations available without administrator rights.")
    results = []
    for op in ops:
        ok, msg = run_op(op, dry_run=False)
        results.append([op.label, "ok" if ok else "failed", msg])
    summary = f"Ran {len(ops)} optimization operations: " + ", ".join(
        op.label for op in ops
    ) + "."
    return ToolResult(
        summary=summary,
        title="Optimization results",
        columns=["Operation", "Status", "Detail"],
        rows=results,
    )


def _handler_system_status(_args: dict) -> ToolResult:
    from ..core.monitor import fmt_rate, snapshot
    snap = snapshot()   # blocks ~1 s - cpu_percent(interval=1) inside
    rows = [
        [p.name, str(p.pid), f"{p.cpu_percent:.1f}%", f"{p.memory_mb:.0f} MB"]
        for p in snap.processes
    ]
    top_str = (
        f"Top process: {snap.processes[0].name} ({snap.processes[0].cpu_percent:.1f}% CPU). "
        if snap.processes else ""
    )
    summary = (
        f"CPU: {snap.cpu_percent:.0f}%, "
        f"Memory: {snap.memory_percent:.0f}% "
        f"({snap.memory_used_gb:.1f}/{snap.memory_total_gb:.1f} GB used). "
        f"Disk: {fmt_rate(snap.disk_read_bytes, 1)} read / {fmt_rate(snap.disk_write_bytes, 1)} write. "
        f"Network: {fmt_rate(snap.net_sent_bytes, 1)} sent / {fmt_rate(snap.net_recv_bytes, 1)} recv. "
        f"{top_str}Full process list shown to the user as a table."
    )
    return ToolResult(
        summary=summary,
        title="Live system status",
        columns=["Process", "PID", "CPU %", "Memory"],
        rows=rows,
    )


# Stale-download cleanup never goes below this, so "days=0" can't empty Downloads.
_MIN_STALE_DAYS = 30


def _stale_days(args: dict) -> int:
    try:
        days = int(args.get("days") or 180)
    except (TypeError, ValueError):
        days = 180
    return max(days, _MIN_STALE_DAYS)


def _handler_find_stale_downloads(args: dict) -> ToolResult:
    from ..core.cleanup import find_stale_downloads
    days = _stale_days(args)
    items = find_stale_downloads(days)
    if not items:
        return ToolResult(summary=f"Nothing in Downloads is older than {days} days.")
    total = sum(size for _p, size, _m in items)
    rows = [[p.name, human_size(size)] for p, size, _m in items[:50]]
    return ToolResult(
        summary=f"{len(items)} item(s) in Downloads untouched for {days}+ days, "
                f"{human_size(total)} in total. Shown to the user as a table. "
                f"clean_stale_downloads with the same days value would move them to the Recycle Bin.",
        title=f"Downloads older than {days} days", columns=["Item", "Size"], rows=rows,
    )


def _handler_clean_stale_downloads(args: dict) -> ToolResult:
    from ..core import history
    from ..core.cleanup import find_stale_downloads, trash_paths
    days = _stale_days(args)
    items = find_stale_downloads(days)
    if not items:
        return ToolResult(summary=f"Nothing in Downloads is older than {days} days.")
    result = trash_paths([p for p, _s, _m in items], dry_run=False)
    history.record_clean("cleanup-stale", f"Downloads >{days}d", result.bytes_freed,
                         result.items, result.trashed)
    return ToolResult(
        summary=f"Moved {result.items} old download(s) ({human_size(result.bytes_freed)}) "
                f"to the Recycle Bin. The user can undo this from the Reports screen."
                + (f" {len(result.skipped)} skipped." if result.skipped else "")
    )


def _handler_find_large_files(args: dict) -> ToolResult:
    from ..core.cleanup import find_large_files
    path = Path(args.get("path", "")).expanduser()
    if not path.exists():
        return ToolResult(summary=f"Path does not exist: {path}")
    try:
        min_mb = max(int(args.get("min_mb") or 100), 1)
    except (TypeError, ValueError):
        min_mb = 100
    items = find_large_files(path, min_mb * 1024 * 1024, top=30)
    if not items:
        return ToolResult(summary=f"No files of {min_mb} MB or more under {path} "
                                  f"(files changed in the last week are left out).")
    rows = [[human_size(size), str(fp)] for fp, size in items]
    return ToolResult(
        summary=f"{len(items)} file(s) of {min_mb} MB+ under {path}, "
                f"{human_size(sum(s for _f, s in items))} in total. Shown to the user as a table. "
                f"Only suggest removing ones that are clearly disposable (installers, old "
                f"archives, disk images); pass their exact paths to clean_large_files.",
        title=f"Large files under {path}", columns=["Size", "Path"], rows=rows,
    )


def _handler_clean_large_files(args: dict) -> ToolResult:
    from ..core import history
    from ..core.cleanup import trash_paths
    raw = args.get("paths") or []
    if isinstance(raw, str):
        raw = [raw]
    files = [Path(str(p)).expanduser() for p in raw]
    # Files only: a folder here would be a much bigger blast radius than asked for.
    not_files = [f for f in files if not f.is_file()]
    files = [f for f in files if f.is_file()]
    if not files:
        return ToolResult(summary="Nothing removed: none of the given paths is an existing file.")
    result = trash_paths(files, dry_run=False)
    history.record_clean("cleanup-large", f"{len(files)} large file(s)", result.bytes_freed,
                         result.items, result.trashed)
    skipped = len(result.skipped) + len(not_files)
    return ToolResult(
        summary=f"Moved {result.items} file(s) ({human_size(result.bytes_freed)}) to the "
                f"Recycle Bin. The user can undo this from the Reports screen."
                + (f" {skipped} skipped (missing, not a file, or protected)." if skipped else "")
    )


def _handler_list_services(_args: dict) -> ToolResult:
    from ..core.services import list_services
    rows = [[s.name, s.label, s.start_type] for s in list_services() if s.present]
    if not rows:
        return ToolResult(summary="None of the optional services Sifty manages are installed.")
    return ToolResult(
        summary=f"{len(rows)} optional service(s) Sifty can change, with their start types. "
                f"Shown to the user as a table. Only these can be changed with manage_service.",
        title="Optional services", columns=["Service", "Description", "Start type"], rows=rows,
    )


def _handler_manage_service(args: dict) -> ToolResult:
    from ..core import services
    from ..windows.admin import is_admin
    name = str(args.get("name", ""))
    mode = str(args.get("mode", "")).lower()
    if not services.can_manage(name):
        return ToolResult(summary=f"'{name}' is not on Sifty's list of optional services, "
                                  f"so it can't be changed from here. See list_services.")
    if not services.is_present(name):
        return ToolResult(summary=f"The service '{name}' isn't installed on this machine.")
    if not is_admin():
        return ToolResult(summary="Changing a service needs administrator rights. Ask the user "
                                  "to restart Sifty as admin (sifty --admin, or F2 in the TUI).")
    if services.set_start_type(name, mode):
        return ToolResult(summary=f"Set '{name}' to {mode}. This can be changed back at any time.")
    return ToolResult(summary=f"Could not set '{name}' to '{mode}' (valid modes: auto, manual, disabled).")


def _handler_compact_virtual_disks(_args: dict) -> ToolResult:
    from ..core.optimize import list_operations, run_op
    from ..windows.admin import is_admin
    from ..windows.hyperv import list_vhdx_files
    vhds = list_vhdx_files()
    if not vhds:
        return ToolResult(summary="No Hyper-V or WSL2 virtual disks (.vhdx) found.")
    if not is_admin():
        total = sum(size for _p, size in vhds)
        return ToolResult(summary=f"Found {len(vhds)} virtual disk(s) ({human_size(total)}), "
                                  f"but compacting needs administrator rights. Ask the user to "
                                  f"restart Sifty as admin and shut down WSL first (wsl --shutdown).")
    op = next(o for o in list_operations() if o.key == "compact-vhd")
    ok, msg = run_op(op, dry_run=False)
    return ToolResult(summary=f"Compacting virtual disks {'finished' if ok else 'failed'}: {msg}. "
                              f"Disks in use (a running WSL distro or VM) can't be compacted.")


def _handler_apply_updates(args: dict) -> ToolResult:
    from ..core.updates import apply_upgrades
    pkg_id = args.get("id") or None
    code = apply_upgrades(pkg_id)
    target = pkg_id or "all pending updates"
    if code == 0:
        return ToolResult(summary=f"Update of {target} completed successfully.")
    return ToolResult(summary=f"Update of {target} exited with code {code} (it may have failed).")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="scan_junk",
        description="Scan for junk files (temp files, caches, old logs) and return a breakdown by category with sizes and category keys.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="read",
        handler=_handler_scan_junk,
    ),
    Tool(
        name="analyze_disk",
        description="List the largest files and folders in a given directory path.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute directory path, e.g. C:\\Users\\User\\Downloads"}
            },
            "required": ["path"],
        },
        risk="read",
        handler=_handler_analyze_disk,
    ),
    Tool(
        name="find_duplicates",
        description="Find duplicate files inside a given directory path.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to scan for duplicates"}
            },
            "required": ["path"],
        },
        risk="read",
        handler=_handler_find_duplicates,
    ),
    Tool(
        name="list_apps",
        description="List installed applications (name, version, size), largest first.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="read",
        handler=_handler_list_apps,
    ),
    Tool(
        name="list_updates",
        description="List applications that have updates available (via winget). Use this when the user asks what needs updating.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="read",
        handler=_handler_list_updates,
    ),
    Tool(
        name="clean_junk",
        description="Remove junk files from specific categories by moving them to the Recycle Bin.",
        parameters={
            "type": "object",
            "properties": {
                "categories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Junk category keys to clean, e.g. ['user-temp']. Call scan_junk first to get valid keys.",
                }
            },
            "required": ["categories"],
        },
        risk="high",
        handler=_handler_clean_junk,
    ),
    Tool(
        name="uninstall_app",
        description="Uninstall an application by its display name or winget package id.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Application display name or winget package id"}
            },
            "required": ["name"],
        },
        risk="high",
        handler=_handler_uninstall_app,
    ),
    Tool(
        name="toggle_startup",
        description="Enable or disable a Windows startup entry by name (reversible).",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Startup entry name"},
                "enable": {"type": "boolean", "description": "True to enable, False to disable"},
            },
            "required": ["name", "enable"],
        },
        risk="low",
        handler=_handler_toggle_startup,
    ),
    Tool(
        name="apply_updates",
        description="Apply a pending software update by its winget package id (omit id to update everything).",
        parameters={
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "winget package id, e.g. 'Microsoft.PowerToys'"}
            },
            "required": [],
        },
        risk="high",
        handler=_handler_apply_updates,
    ),
    Tool(
        name="system_status",
        description="Get a live snapshot of CPU usage, memory usage, disk I/O, network I/O, and the top processes by CPU.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="read",
        handler=_handler_system_status,
    ),
    Tool(
        name="schedule_cleanup",
        description="Schedule a recurring automatic cleanup. Use when the user asks to automate or schedule a cleanup. Extract the schedule from their request and provide the profile name.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Short name for the task, e.g. 'weekly-cleanup'"},
                "profile": {"type": "string", "description": "Cleanup profile name to run (use list_profiles first if unsure)"},
                "frequency": {"type": "string", "enum": ["DAILY", "WEEKLY"], "description": "DAILY or WEEKLY"},
                "day": {"type": "string", "description": "Day of week for WEEKLY schedules: MON TUE WED THU FRI SAT SUN"},
                "time": {"type": "string", "description": "Time in HH:MM 24h format, e.g. '03:00'"},
            },
            "required": ["name", "profile", "frequency", "time"],
        },
        risk="low",
        handler=_handler_schedule_cleanup,
    ),
    Tool(
        name="prune_worktrees",
        description="Find and remove orphaned git worktrees in a repository (stale checkouts whose branch or directory is gone).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Git repository root path to inspect"}
            },
            "required": ["path"],
        },
        risk="high",
        handler=_handler_prune_worktrees,
    ),
    Tool(
        name="find_orphan_apps",
        description="Scan the Windows registry for orphaned uninstall entries whose executable no longer exists on disk.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="read",
        handler=_handler_find_orphan_apps,
    ),
    Tool(
        name="scan_project_artifacts",
        description="Scan a directory for dev artifact directories (node_modules, dist, __pycache__, target, etc.) and report their sizes.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root directory to scan, e.g. C:\\Users\\User\\Projects"}
            },
            "required": ["path"],
        },
        risk="read",
        handler=_handler_scan_artifacts,
    ),
    Tool(
        name="purge_artifacts",
        description="Remove dev artifact directories (node_modules, dist, __pycache__, etc.) under a path by sending them to the Recycle Bin.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Root directory whose artifacts should be purged"}
            },
            "required": ["path"],
        },
        risk="high",
        handler=_handler_purge_artifacts,
    ),
    Tool(
        name="optimize_system",
        description="Run non-destructive system optimization: flush DNS cache, rebuild thumbnail cache, and other safe cache-clearing operations.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="low",
        handler=_handler_optimize_system,
    ),
    Tool(
        name="find_stale_downloads",
        description="List items in the Downloads folder that haven't been touched in a number of days.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Age threshold in days (minimum 30, default 180)"}
            },
            "required": [],
        },
        risk="read",
        handler=_handler_find_stale_downloads,
    ),
    Tool(
        name="clean_stale_downloads",
        description="Move items in Downloads older than a number of days to the Recycle Bin. Call find_stale_downloads first so the user sees what goes.",
        parameters={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Age threshold in days (minimum 30, default 180)"}
            },
            "required": [],
        },
        risk="high",
        handler=_handler_clean_stale_downloads,
    ),
    Tool(
        name="find_large_files",
        description="List the largest files under a directory (files changed in the last week are left out).",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to search, e.g. C:\\Users\\User"},
                "min_mb": {"type": "integer", "description": "Minimum file size in MB (default 100)"},
            },
            "required": ["path"],
        },
        risk="read",
        handler=_handler_find_large_files,
    ),
    Tool(
        name="clean_large_files",
        description="Move specific files to the Recycle Bin by exact path. Only files, never folders. Use paths from find_large_files.",
        parameters={
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Exact file paths to remove",
                }
            },
            "required": ["paths"],
        },
        risk="high",
        handler=_handler_clean_large_files,
    ),
    Tool(
        name="list_services",
        description="List the optional Windows services Sifty can change (telemetry, Xbox, Fax, SysMain, etc.) and their start types.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="read",
        handler=_handler_list_services,
    ),
    Tool(
        name="manage_service",
        description="Change the start type of one optional Windows service from list_services. Reversible. Needs administrator rights.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Service name from list_services, e.g. 'DiagTrack'"},
                "mode": {"type": "string", "enum": ["auto", "manual", "disabled"], "description": "New start type"},
            },
            "required": ["name", "mode"],
        },
        risk="high",
        handler=_handler_manage_service,
    ),
    Tool(
        name="compact_virtual_disks",
        description="Reclaim unused space inside WSL2 / Hyper-V .vhdx virtual disks without touching their contents. Needs administrator rights and WSL shut down.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk="high",
        handler=_handler_compact_virtual_disks,
    ),
]

_TOOL_MAP: dict[str, Tool] = {t.name: t for t in TOOLS}

# Old tool names still accepted, so saved policies and older chats keep working.
ALIASES: dict[str, str] = {"schedule_maintenance": "schedule_cleanup"}


def canonical(name: str) -> str:
    """The current name for ``name`` (resolves renamed tools)."""
    return ALIASES.get(name, name)


def get(name: str) -> Tool | None:
    """Look up a tool by name (old names resolve to their replacement)."""
    return _TOOL_MAP.get(canonical(name))


def ollama_schemas() -> list[dict]:
    """Return all tools formatted for Ollama's ``tools`` field."""
    return [t.to_ollama() for t in TOOLS]
