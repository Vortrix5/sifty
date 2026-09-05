"""Junk scanning and cleanup (engine).

Each junk *category* points at one or more directories whose top-level entries
are safe to send to the Recycle Bin. The directory itself is registered as an
allowed subtree so :func:`sifty.core.safety.trash` permits its contents even when
the directory sits inside a protected root (e.g. ``C:\\Windows\\Temp``).
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..infra.config import load_config
from .models import CategoryScan, CleanResult, JunkCategory
from .safety import ProtectedPathError, trash

__all__ = ["JunkCategory", "CategoryScan", "CleanResult", "junk_categories", "scan", "clean"]


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _local_appdata() -> Path | None:
    return _env_path("LOCALAPPDATA")


def _roaming_appdata() -> Path | None:
    return _env_path("APPDATA")


def _browser_cache_roots(local: Path) -> list[Path]:
    """Cache directories across ALL profiles of the installed browsers.

    Chromium-family browsers keep one cache per profile under ``User Data``
    (Default, Profile 1, …); Firefox keeps ``cache2`` per profile. Only cache
    dirs are returned - never cookies, history, or passwords.
    """
    roots: list[Path] = []

    chromium_user_data = [
        local / "Google" / "Chrome" / "User Data",
        local / "Microsoft" / "Edge" / "User Data",
        local / "BraveSoftware" / "Brave-Browser" / "User Data",
        local / "Vivaldi" / "User Data",
    ]
    for user_data in chromium_user_data:
        if not user_data.is_dir():
            continue
        try:
            profiles = [
                p for p in user_data.iterdir()
                if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile"))
            ]
        except OSError:
            continue
        for profile in profiles:
            for sub in ("Cache", "Code Cache", "GPUCache"):
                cand = profile / sub
                if cand.is_dir():
                    roots.append(cand)

    # Opera and Opera GX store their User Data under %APPDATA% (roaming), not %LOCALAPPDATA%.
    appdata = _roaming_appdata()
    if appdata:
        opera_user_data = [
            appdata / "Opera Software" / "Opera Stable",
            appdata / "Opera Software" / "Opera GX Stable",
        ]
        for user_data in opera_user_data:
            if not user_data.is_dir():
                continue
            try:
                profiles = [
                    p for p in user_data.iterdir()
                    if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile"))
                ]
            except OSError:
                continue
            for profile in profiles:
                for sub in ("Cache", "Code Cache", "GPUCache"):
                    cand = profile / sub
                    if cand.is_dir():
                        roots.append(cand)

    firefox_profiles = local / "Mozilla" / "Firefox" / "Profiles"
    if firefox_profiles.is_dir():
        try:
            for profile in firefox_profiles.iterdir():
                cand = profile / "cache2"
                if cand.is_dir():
                    roots.append(cand)
        except OSError:
            pass

    return roots

def _discord_cache_roots() -> list[Path]:
    """Cache directories for Discord, Discord PTB, and Discord Canary.

    Only returns specific Chromium cache paths - never login session,
    local storage, or settings data.
    """
    roots: list[Path] = []
    appdata = _env_path("APPDATA")
    if not appdata:
        return roots

    flavors = ["discord", "discordptb", "discordcanary"]
    cache_subs = ["Cache", "Code Cache", "GPUCache"]

    for flavor in flavors:
        flavor_dir = appdata / flavor
        if not flavor_dir.is_dir():
            continue
        for sub in cache_subs:
            cand = flavor_dir / sub
            if cand.is_dir():
                roots.append(cand)

    return roots

def junk_categories(config=None) -> list[JunkCategory]:
    """Build the list of junk categories from the environment + config."""
    config = config or load_config()
    cats: list[JunkCategory] = []

    user_temp = _env_path("TEMP") or _env_path("TMP")
    if user_temp:
        cats.append(
            JunkCategory(
                "user-temp", "User temp files",
                "Per-user temporary files (%TEMP%).", [user_temp],
            )
        )

    win_temp = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Temp"
    cats.append(
        JunkCategory(
            "windows-temp", "Windows temp files",
            "System-wide temp files (C:\\Windows\\Temp).", [win_temp],
            requires_admin=True,
        )
    )

    local = _local_appdata()
    if local:
        thumb = local / "Microsoft" / "Windows" / "Explorer"
        cats.append(
            JunkCategory(
                "thumbnail-cache", "Thumbnail & icon cache",
                "Explorer thumbnail/icon cache (rebuilt automatically).", [thumb],
            )
        )
        cats.append(
            JunkCategory(
                "browser-cache", "Browser caches",
                "On-disk caches for every Chrome/Edge/Brave/Vivaldi/Opera profile and "
                "Firefox (never cookies, history, or passwords).",
                _browser_cache_roots(local),
            )
        )

    wu = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "SoftwareDistribution" / "Download"
    cats.append(
        JunkCategory(
            "windows-update-cache", "Windows Update cache",
            "Downloaded update packages (re-downloaded if needed).", [wu],
            requires_admin=True,
        )
    )

    if config.section("junk").get("include_downloads_installers"):
        downloads = Path.home() / "Downloads"
        cats.append(
            JunkCategory(
                "downloads-installers", "Leftover installers",
                "Installer files (.exe/.msi) in Downloads.", [downloads],
                file_filter=_downloads_installer_filter,
            )
        )

    # ---- optional / off-by-default categories --------------------------------

    if config.section("junk").get("include_windows_old"):
        sys_drive = os.environ.get("SystemDrive", "C:")
        win_old = Path(sys_drive + "\\Windows.old")
        cats.append(
            JunkCategory(
                "windows-old", "Windows.old (post-upgrade)",
                "Previous Windows installation left after a feature update (often 15-30 GB).",
                [win_old],
                requires_admin=True,
            )
        )

    # ---- always-available additional categories ------------------------------

    local = _local_appdata()

    winget_dl = Path(os.environ.get("LOCALAPPDATA", "")) / "Temp" / "WinGet" if local else None
    if winget_dl and winget_dl.parent.exists():
        cats.append(
            JunkCategory(
                "winget-cache", "WinGet download cache",
                "Temporary installer files downloaded by WinGet.", [winget_dl],
            )
        )

    winevt = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "winevt" / "Logs"
    cats.append(
        JunkCategory(
            "event-log-archives", "Archived event logs",
            "Old Windows event log archives (Archive-*.evtx). Active logs are untouched.",
            [winevt],
            requires_admin=True,
            file_filter=_event_log_archive_filter,
        )
    )

    defender_history = Path(os.environ.get("ProgramData", r"C:\ProgramData")) / (
        "Microsoft" / Path("Windows Defender") / "Scans" / "History" / "Service" / "DetectionHistory"
    )
    cats.append(
        JunkCategory(
            "defender-history", "Defender detection history",
            "Windows Defender scan detection history logs (safe to remove).",
            [defender_history],
            requires_admin=True,
        )
    )

    if local:
        od_logs = local / "Microsoft" / "OneDrive" / "logs"
        cats.append(
            JunkCategory(
                "onedrive-logs", "OneDrive sync logs",
                "OneDrive diagnostic logs (rebuilt automatically).", [od_logs],
            )
        )

    # Discord caches (Chromium style, safe to clear)
    discord_roots = _discord_cache_roots()
    if discord_roots:
        cats.append(
            JunkCategory(
                "discord-cache", "Discord cache",
                "On-disk caches for Discord, PTB, and Canary channels (never login session data).",
                discord_roots,
            )
        )

    if local:
        cats.append(
            JunkCategory(
                "crash-dumps", "App crash dumps",
                "Per-user crash dumps and error reports (CrashDumps, WER).",
                [
                    local / "CrashDumps",
                    local / "Microsoft" / "Windows" / "WER" / "ReportQueue",
                    local / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                ],
            )
        )

    sysroot = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    progdata = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    cats.append(
        JunkCategory(
            "system-crash-reports", "System crash reports",
            "System-wide crash reports and kernel minidumps (WER, Minidump).",
            [
                progdata / "Microsoft" / "Windows" / "WER" / "ReportQueue",
                progdata / "Microsoft" / "Windows" / "WER" / "ReportArchive",
                sysroot / "Minidump",
            ],
            requires_admin=True,
        )
    )

    return cats


def _dir_size(path: Path) -> tuple[int, int]:
    """Return (total_bytes, file_count) for everything under ``path``."""
    total = 0
    count = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in files:
            fp = Path(root) / name
            try:
                total += fp.stat(follow_symlinks=False).st_size
                count += 1
            except OSError:
                continue
    return total, count


def _filtered_size(root: Path, file_filter) -> tuple[int, int]:
    """Like ``_dir_size`` but only counts files that pass ``file_filter``."""
    total = 0
    count = 0
    try:
        for entry in root.iterdir():
            if entry.is_file() and file_filter(entry):
                try:
                    total += entry.stat(follow_symlinks=False).st_size
                    count += 1
                except OSError:
                    pass
    except OSError:
        pass
    return total, count


def _measure_category(cat: JunkCategory) -> CategoryScan:
    """Size-scan one category (pure, thread-safe)."""
    total = 0
    files = 0
    present: list[Path] = []
    for root in cat.roots:
        try:
            if not root.exists():
                continue
        except OSError:
            continue
        present.append(root)
        try:
            if cat.file_filter is not None:
                size, count = _filtered_size(root, cat.file_filter)
            else:
                size, count = _dir_size(root)
        except OSError:
            size, count = 0, 0
        total += size
        files += count
    return CategoryScan(cat, total, files, present)


def scan(config=None, only: set[str] | None = None) -> list[CategoryScan]:
    """Measure each junk category concurrently. ``only`` filters by key."""
    cats = [c for c in junk_categories(config) if not only or c.key in only]
    if not cats:
        return []
    workers = min(len(cats), 8, os.cpu_count() or 1)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_measure_category, cats))


def _downloads_installer_filter(path: Path) -> bool:
    return path.suffix.lower() in {".exe", ".msi"}


# Common suffix patterns stripped before matching an installer filename to an app name.
_INSTALLER_SUFFIXES = re.compile(
    r"[_\-\s]*(setup|installer|install|_x64|_x86|_amd64|_win|_win64|"
    r"_windows|portable|\d+[\.\d]*)$",
    re.IGNORECASE,
)


def _installer_app_hint(path: Path) -> str:
    """Extract a normalised app-name hint from an installer filename."""
    stem = path.stem
    stem = _INSTALLER_SUFFIXES.sub("", stem).strip()
    return stem.lower().replace("-", " ").replace("_", " ")


def _is_installed_app_installer(path: Path, installed_names: frozenset[str]) -> bool:
    """Return True if ``path`` looks like an installer for an already-installed app."""
    hint = _installer_app_hint(path)
    if len(hint) < 3:
        return False
    return any(hint in name or name in hint for name in installed_names)


def _event_log_archive_filter(path: Path) -> bool:
    return path.name.startswith("Archive-") and path.suffix.lower() == ".evtx"


def clean(
    config=None,
    only: set[str] | None = None,
    *,
    dry_run: bool = True,
    extra_protected: list[str] | None = None,
) -> CleanResult:
    """Trash the contents of selected junk categories.

    Returns a :class:`CleanResult`. ``trashed`` holds the original paths sent to
    the Recycle Bin (empty on a dry-run) - used to record an undoable session.
    ``extra_protected`` extends the built-in denylist for this call only.
    """
    config = config or load_config()
    cfg_protected = config.section("safety").get("extra_protected_paths", [])
    extra_protected = list(cfg_protected) + list(extra_protected or [])
    bytes_freed = 0
    items = 0
    skipped: list[str] = []
    trashed: list[Path] = []

    # Build installed-app name set once for smart installer matching.
    _installed_names: frozenset[str] | None = None
    if config.section("junk").get("include_downloads_installers"):
        try:
            from .apps import installed_apps
            _installed_names = frozenset(a.name.lower() for a in installed_apps())
        except Exception:
            _installed_names = frozenset()

    for cat_scan in scan(config, only):
        cat = cat_scan.category
        for root in cat_scan.existing_roots:
            try:
                entries = list(root.iterdir())
            except OSError as exc:
                # e.g. C:\Windows\Temp without Administrator rights.
                skipped.append(f"{root}: {exc}")
                continue
            for entry in entries:
                if cat.file_filter is not None and not (
                    entry.is_file() and cat.file_filter(entry)
                ):
                    continue
                # Smart installer check: only flag installers for apps that are
                # already installed (conservative - skip if we can't match).
                if (
                    cat.key == "downloads-installers"
                    and _installed_names is not None
                    and not _is_installed_app_installer(entry, _installed_names)
                ):
                    continue
                try:
                    if entry.is_dir():
                        size, _ = _dir_size(entry)
                    else:
                        size = entry.stat(follow_symlinks=False).st_size
                    trash(
                        entry,
                        allow_subtrees=[root],
                        extra_protected=extra_protected,  # merged above
                        dry_run=dry_run,
                    )
                    bytes_freed += size
                    items += 1
                    if not dry_run:
                        trashed.append(entry)
                except ProtectedPathError as exc:
                    skipped.append(str(exc))
                except OSError as exc:
                    skipped.append(f"{entry}: {exc}")

    return CleanResult(bytes_freed, items, skipped, trashed)
