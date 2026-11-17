"""Orphaned app data: AppData folders that no installed app seems to own.

Uninstallers leave settings and caches behind in ``%APPDATA%`` and
``%LOCALAPPDATA%``. When you no longer remember the app's name,
:mod:`sifty.core.leftovers` can't help, so this scans the other way round: it
lists top-level AppData folders and keeps the ones that

- don't match anything installed (app names, publishers, uninstaller paths,
  Program Files folders, Start Menu shortcuts, running processes), and
- haven't been touched in ``min_age_days``.

It is read-only and deliberately conservative: matching is loose, so a folder
that even partly resembles something installed is treated as owned. Missing a
real orphan is fine; flagging live data is not. Removal goes through
``sifty apps leftovers "<name>"``, which has its own preview and confirm.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from .leftovers import _NEVER_MATCH
from .purge import _dir_size

__all__ = ["OrphanedData", "find_orphaned_app_data", "known_names"]

DEFAULT_MIN_AGE_DAYS = 90
DEFAULT_MIN_SIZE = 1024 * 1024   # empty or tiny folders aren't worth a row

# Top-level AppData folders that belong to Windows or are shared, never flagged.
_SYSTEM_DIRS = _NEVER_MATCH | {
    "packages", "programs", "crashdumps", "d3dscache", "connecteddevicesplatform",
    "comms", "peerdistrepub", "publishers", "virtualstore", "history",
    "microsoft_corporation", "placeholdertilelogofolder", "fontcache",
    "iconcache", "squirreltemp", "elevateddiagnostics", "identities",
    "media center programs", "sun", "gtk-3.0", "fontconfig", "pip",
    "isolatedstorage", "toastnotificationmanagercompat", "programs (x86)",
}

# Caches and state of developer tools that are often installed without a
# registry entry (npm, scoop, rustup...). They're caches, not orphans.
_DEV_TOOL_DIRS = {
    "nuget", "pypa", "pnpm", "pnpm-state", "pnpm-cache", "yarn", "npm-cache",
    "node-gyp", "go-build", "turborepo", "nextjs-nodejs", "deno", "bun", "uv",
    "pypoetry", "poetry", "jedi", "ms-playwright", "electron", "electron-builder",
    "cypress", "prisma-nodejs", "typescript", "corepack", "fnm", "fnm_multishells",
    "nvm", "volta", "rustup", "cargo", "conda", "jupyter", "ipython", "gradle",
    "maven", "vcpkg", "docker", "kubernetes", "terraform.d", "azure", "gcloud",
}

# Words too generic to say who owns a folder.
_GENERIC = {
    "software", "update", "updater", "tools", "tool", "data", "files", "system",
    "service", "services", "helper", "client", "setup", "installer", "launcher",
    "desktop", "studio", "player", "manager", "driver", "drivers", "runtime",
    "the", "and", "for", "inc", "corp", "corporation", "ltd", "llc", "gmbh",
}

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass
class OrphanedData:
    path: Path
    size_bytes: int
    last_modified: float   # newest mtime of the folder and its direct children


def _compact(text: str) -> str:
    return _NON_ALNUM.sub("", text.lower())


def _tokens(name: str) -> set[str]:
    """The compact full name plus each meaningful word (4+ chars, not generic)."""
    out: set[str] = set()
    full = _compact(name)
    if len(full) >= 3:
        out.add(full)
    for word in _NON_ALNUM.split(name.lower()):
        if len(word) >= 4 and word not in _GENERIC and not word.isdigit():
            out.add(word)
    return out


def _child_names(root: Path, depth: int = 2) -> list[str]:
    """Directory names under ``root``, up to ``depth`` levels (permission-tolerant)."""
    names: list[str] = []
    level = [root]
    for _ in range(depth):
        nxt: list[Path] = []
        for d in level:
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if e.is_dir(follow_symlinks=False):
                            names.append(e.name)
                            nxt.append(Path(e.path))
            except OSError:
                continue
        level = nxt
    return names


def _shortcut_names() -> list[str]:
    names: list[str] = []
    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if not base:
            continue
        root = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for _dirpath, dirs, files in os.walk(root, onerror=lambda _e: None):
            names.extend(dirs)
            names.extend(Path(f).stem for f in files)
    return names


def _process_names() -> list[str]:
    try:
        import psutil
        return [Path(p.info["name"] or "").stem for p in psutil.process_iter(["name"])]
    except Exception:
        return []


def _path_tool_names() -> list[str]:
    """Executables and folder names on PATH (tools installed outside the registry)."""
    names: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        d = Path(entry)
        names += list(d.parts[-2:])
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.name.lower().endswith((".exe", ".cmd", ".bat", ".ps1")):
                        names.append(Path(e.name).stem)
        except OSError:
            continue
    return names


def _package_manager_names() -> list[str]:
    """Apps installed by scoop, winget (portable) and npm -g."""
    names: list[str] = []
    home = Path.home()
    names += _child_names(Path(os.environ.get("SCOOP", home / "scoop")) / "apps", depth=1)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        names += _child_names(Path(local) / "Microsoft" / "WinGet" / "Packages", depth=1)
    roaming = os.environ.get("APPDATA")
    if roaming:
        names += _child_names(Path(roaming) / "npm" / "node_modules", depth=1)
    return names


def known_names() -> set[str]:
    """Tokens for everything that looks installed or in use on this machine."""
    from .apps import installed_apps

    raw: list[str] = []
    try:
        for app in installed_apps():
            raw += [app.name, app.publisher]
            # The uninstaller usually sits in the app's own folder.
            exe = app.uninstall_string.strip().strip('"').split('"')[0]
            raw += list(Path(exe).parts[-3:-1])
    except Exception:
        pass
    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            raw += _child_names(Path(base))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        raw += _child_names(Path(local) / "Programs")
    raw += _shortcut_names()
    raw += _process_names()
    raw += _path_tool_names()
    raw += _package_manager_names()

    tokens: set[str] = set()
    for name in raw:
        if name:
            tokens |= _tokens(str(name))
    return tokens


def _is_owned(folder: str, known: set[str]) -> bool:
    """Loose match: any overlap with a known name counts as owned."""
    compact = _compact(folder)
    if compact in known:
        return True
    for token in _tokens(folder):
        if token in known:
            return True
    for k in known:
        # "Discord" owns "discordptb"; "obs-studio" owns "obs".
        if len(k) >= 5 and k in compact:
            return True
        if len(compact) >= 5 and compact in k:
            return True
    return False


def _last_modified(path: Path) -> float:
    newest = 0.0
    try:
        newest = path.stat().st_mtime
        with os.scandir(path) as it:
            for e in it:
                try:
                    newest = max(newest, e.stat(follow_symlinks=False).st_mtime)
                except OSError:
                    continue
    except OSError:
        pass
    return newest


def _default_roots() -> list[Path]:
    roots: list[Path] = []
    for var in ("APPDATA", "LOCALAPPDATA"):
        base = os.environ.get(var)
        if base and Path(base).is_dir():
            roots.append(Path(base))
    return roots


def find_orphaned_app_data(
    min_age_days: int = DEFAULT_MIN_AGE_DAYS,
    *,
    min_size: int = DEFAULT_MIN_SIZE,
    roots: list[Path] | None = None,
    known: set[str] | None = None,
    now: float | None = None,
) -> list[OrphanedData]:
    """AppData folders no installed app seems to own, largest first.

    ``roots``, ``known`` and ``now`` exist for tests; by default the roots are
    Roaming and Local AppData and ``known`` comes from :func:`known_names`.
    """
    known = known_names() if known is None else known
    cutoff = (now if now is not None else time.time()) - min_age_days * 86400
    found: list[OrphanedData] = []
    for root in (roots if roots is not None else _default_roots()):
        try:
            entries = [e for e in os.scandir(root) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if (name.startswith(".") or len(_compact(name)) < 3
                    or name.lower() in _SYSTEM_DIRS or name.lower() in _DEV_TOOL_DIRS
                    or _is_owned(name, known)):
                continue
            path = Path(entry.path)
            modified = _last_modified(path)
            if modified >= cutoff:
                continue   # used recently: something still owns it
            size = _dir_size(path)
            if size >= min_size:
                found.append(OrphanedData(path, size, modified))
    found.sort(key=lambda o: o.size_bytes, reverse=True)
    return found
