<p align="center">
  <img src="docs/logo.png" alt="Sifty" width="200">
</p>

<h1 align="center">Sifty</h1>

<p align="center"><em>Sift the junk from the keep.</em></p>

<p align="center">
  <a href="https://pypi.org/project/sifty/"><img src="https://img.shields.io/pypi/v/sifty" alt="PyPI"></a>
  <img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4" alt="Windows">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License: MIT">
  <img src="https://github.com/Vortrix5/sifty/actions/workflows/ci.yml/badge.svg" alt="CI">
  <a href="https://codecov.io/gh/Vortrix5/sifty"><img src="https://codecov.io/gh/Vortrix5/sifty/branch/main/graph/badge.svg" alt="Coverage"></a>
</p>

<p align="center">
  <a href="https://sifty.tech">Website</a> ·
  <a href="https://sifty.tech/guides/">Guides</a> ·
  <a href="https://github.com/Vortrix5/sifty/blob/main/CHANGELOG.md">Changelog</a>
</p>

**Sifty is an open-source maintenance tool for Windows that is built to be hard
to misuse: it previews every change, deletes only to the Recycle Bin, and can
undo any clean.** It clears junk and caches, analyzes disk usage, finds
duplicate files, and manages apps, startup items and updates. It also cleans the
clutter other cleaners ignore: stray `node_modules` and build output, orphaned
git worktrees, and bloated WSL2 disks. Drive it from a scriptable CLI or a
full-screen terminal UI.

The optional AI assistant runs **locally** via [Ollama]: nothing leaves your
machine, and it only ever sees file *metadata* (names, sizes, paths), never file
contents.

![Sifty demo](docs/demo.gif)

## Safety first

Sifty deletes files and changes system state, so it is built to be hard to misuse:

- **Dry-run by default.** Every destructive command previews what it would do;
  real changes need an explicit `--apply`.
- **Recycle Bin, never permanent delete.** All removals go through one
  `trash()` function backed by Send2Trash, and `sifty undo` restores the last
  clean.
- **Protected paths.** `C:\Windows`, `Program Files`, `ProgramData`, the drive
  root and your profile root are refused even with `--apply --yes`.
- **Audit log.** Every applied deletion is recorded in `%APPDATA%\sifty\audit.log`.
- **The AI never deletes anything.** It is advisory; high-risk tool calls
  always require your approval.

## How it compares

| Feature | Sifty | CCleaner | BleachBit | Revo Uninstaller | WinDirStat |
| --- | --- | --- | --- | --- | --- |
| Junk / cache cleaning | ✅ 14+ categories | ✅ | ✅ | ➖ | ❌ |
| Disk usage analysis | ✅ top-N + volumes | ➖ | ❌ | ❌ | ✅ treemap |
| Duplicate finder | ✅ SHA-256, NTFS-aware | ✅ (paid) | ❌ | ❌ | ❌ |
| App uninstall + leftover scan | ✅ winget + leftovers | ✅ | ❌ | ✅ + leftovers | ❌ |
| App updates | ✅ via winget | ✅ (paid) | ❌ | ❌ | ❌ |
| Startup manager | ✅ reversible | ✅ | ❌ | ✅ | ❌ |
| Dev artifact purge (node_modules, …) | ✅ | ❌ | ❌ | ❌ | ❌ |
| Git worktree / WSL2 VHD cleanup | ✅ | ❌ | ❌ | ❌ | ❌ |
| Local AI assistant | ✅ Ollama | ❌ | ❌ | ❌ | ❌ |
| Scriptable (JSON output) | ✅ | ❌ | ➖ CLI | ❌ | ❌ |
| Recycle Bin + undo for everything | ✅ | ➖ | ❌ permanent | ➖ | n/a |
| Open source | ✅ MIT | ❌ | ✅ GPL | ❌ | ✅ |
| Price | Free, MIT | Freemium | Free | Freemium | Free |

Sifty is built **developer-first**: everything is scriptable, the engine is a
reusable Python library, and it cleans the things developer machines actually
accumulate (build artifacts, orphaned worktrees, bloated WSL2 disks).
[BleachBit](https://sifty.tech/alternatives/bleachbit.html) is the closest
open-source cousin; it is cross-platform and can securely shred files, while
Sifty stays Windows-only, sends everything to the Recycle Bin with full undo,
and adds duplicates, app management and developer cleanups.

## Cleaned junk categories

When running `sifty junk scan` or `sifty junk clean`, the engine targets the following categories extracted directly from `junk_categories()`:

### Always-on categories

- **`user-temp`**: Per-user temporary files (`%TEMP%` or `%TMP%`).
- **`thumbnail-cache`**: Explorer thumbnail and icon cache (rebuilt automatically).
- **`browser-cache`**: On-disk caches for Chrome, Edge, Brave, Vivaldi, and Firefox profiles (never cookies, history, or passwords).
- **`winget-cache`**: Temporary installer files downloaded by WinGet.
- **`onedrive-logs`**: OneDrive sync diagnostic logs (rebuilt automatically).
- **`discord-cache`**: On-disk caches for Discord, PTB, and Canary channels (never login session data).
- **`crash-dumps`**: Per-user crash dumps and error reports (`CrashDumps`, `WER`).
- **`windows-temp`** _(Requires Admin)_: System-wide temporary files (`C:\Windows\Temp`).
- **`windows-update-cache`** _(Requires Admin)_: Downloaded update packages left behind by Windows Update.
- **`event-log-archives`** _(Requires Admin)_: Old Windows event log archives (`Archive-*.evtx`). Active logs are untouched.
- **`defender-history`** _(Requires Admin)_: Windows Defender scan detection history logs.
- **`system-crash-reports`** _(Requires Admin)_: System-wide crash reports and kernel minidumps (`WER`, `Minidump`).

### Off by default (opt-in via configuration)

- **`downloads-installers`**: Installer files (`.exe`/`.msi`) in your Downloads folder. Can be enabled via `sifty config set junk.include_downloads_installers true`.
- **`windows-old`** _(Requires Admin)_: Leftovers from previous Windows installations after a feature update (`C:\Windows.old`). Can be enabled via `sifty config set junk.include_windows_old true`.

## Install

```powershell
pipx install sifty       # recommended (isolated); or: pip install sifty
scoop bucket add sifty https://github.com/Vortrix5/scoop-bucket; scoop install sifty
winget install Vortrix5.Sifty

sifty doctor             # check admin rights, winget, Ollama
```

Prefer no Python install? Download the standalone `sifty.exe` from the
[latest release](https://github.com/Vortrix5/sifty/releases/latest).

Free Windows code signing provided by [SignPath.io](https://about.signpath.io/),
certificate by [SignPath Foundation](https://signpath.org/).

For development:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q
```

## Usage

```powershell
sifty checkup                # one read-only scan of everything: junk, updates,
                             # orphans, stale files, disk space, startup
sifty tui                    # the full-screen interactive app

# Junk
sifty junk scan              # show reclaimable space per category
sifty junk clean             # preview removal (dry-run)
sifty junk clean --apply     # send junk to the Recycle Bin (asks first)

# Disk
sifty disk volumes           # used/free/total per volume
sifty disk analyze C:\Users  # biggest folders/files under a path
sifty disk duplicates D:\    # find duplicate files and wasted space

# Apps & updates
sifty apps list --by-size    # installed apps, largest first
sifty apps orphans           # broken uninstall entries in the registry
sifty apps uninstall "App"   # uninstall via winget (preview, then --apply)
sifty apps leftovers "App"   # what the uninstaller left behind (then --apply)
sifty update check           # available updates (winget)
sifty update apply           # upgrade everything (asks first)

# Developer cleanup
sifty purge clean            # node_modules, dist, __pycache__, target, …
sifty cleanup duplicates D:\Photos   # de-duplicate (keeps one copy each)
sifty cleanup large C:\Users\you     # biggest files under a path
sifty cleanup stale --days 180       # old items in Downloads

# Startup & services
sifty startup list                   # startup programs (enabled/disabled)
sifty startup disable "Spotify"      # reversible (sifty startup enable …)
sifty services list                  # curated optional services + state
sifty --admin services disable DiagTrack   # toggle one (needs admin)

# History & undo
sifty history                # what was cleaned + total space reclaimed
sifty undo                   # restore the most recent clean from the Recycle Bin

# Organize files
sifty organize preview C:\Users\you\Downloads --by type
sifty organize apply   C:\Users\you\Downloads --by date
sifty organize undo          # put the last organize's files back

# Configuration
sifty config                 # all settings + which ones you've overridden
sifty config set ai.model "llama3.2:3b"
sifty config edit            # open config.toml in your editor

# AI (requires Ollama running)
sifty ai status
sifty ai ask "what can I safely delete on my C drive?" --path C:\
sifty ai autonomy low_risk_auto        # how much the agent does before asking
sifty ai policy set clean_junk auto    # per-tool: auto / ask / never
sifty ai policy list                   # see every tool and what it will do

# Proactive agent: checkup + anomaly scan, toast a summary
sifty agent run                        # notify only (never deletes)
sifty agent schedule --sc DAILY --time 09:00   # run it in the background

# Scripting: JSON output on read-only commands (auto-enabled when piped)
sifty --json checkup
sifty --json disk volumes
sifty --json apps list --by-size
```

Some operations (Windows temp, update cache, certain uninstalls) need an
**Administrator** terminal. `sifty doctor` tells you if you're elevated, and
`sifty --admin <cmd>` relaunches elevated via UAC.

## The TUI

`sifty tui` opens a full-screen app with a seven-section sidebar (Home,
Clean, Disk, Apps, Monitor, Reports, AI):

- **Home**: volume gauges and a **Run checkup** button that scans everything
  at once; findings come with buttons that fix them right there (clean junk,
  clean stale downloads, apply updates, each behind a confirm).
- **Clean**: Junk / Purge / Optimize / Smart cleanup under one roof (tabs).
- **Apps**: Installed / Updates / Startup / Services, with fuzzy filter,
  sorting, bulk uninstall, and an automatic leftover scan after uninstalling.
- **AI**: an agentic chat where proposed tool runs show **Run/Skip buttons
  inline in the conversation**, and scan results carry follow-up action
  buttons. The conversation persists across restarts, and Sifty learns which
  categories you routinely clean and which actions you decline so its advice
  fits how you actually use it.

Press **Ctrl+P** for the command palette (jump to any screen), **F2** to
elevate, **Space** to mark rows for bulk actions. The **Reports** screen shows
space reclaimed over time with an **Undo last clean** button.

## AI setup (optional)

1. Install [Ollama] and start it.
2. Pull the configured model: `ollama pull qwen2.5:3b`.
3. `sifty ai status` should report "running".

Configure everything with the `sifty config` command, no need to hand-edit
files:

```powershell
sifty config                                  # show all settings + your overrides
sifty config set ai.model "llama3.2:3b"       # use a different local model
sifty config set ai.host "http://localhost:11434"
sifty config set safety.extra_protected_paths '["D:\\Important"]'
sifty config set junk.include_downloads_installers true
sifty config edit                             # or open config.toml in your editor
```

Settings live in `%APPDATA%\sifty\config.toml`; `sifty config set` only writes
the keys you change, so defaults keep flowing through on upgrades.

## Architecture

Layered: thin frontends over a reusable engine, OS specifics quarantined:

```text
src/sifty/
├── cli/               # Typer entry point + one thin command module per group
├── tui/               # Textual full-screen app (views call core, not cli)
├── core/              # the engine: junk, disk, apps, updates, cleanup,
│   │                  # startup, services, organize, checkup, history, …
│   └── safety.py      # ★ protected paths, dry-run guard, trash(), audit log
├── windows/           # OS primitives: winget, registry, UAC, Recycle Bin, DISM
├── ai/                # Ollama client, advisor prompts, agentic tool loop
└── infra/             # TOML config + rotating diagnostics log
```

Frontends depend on `core`; `core` depends on `windows`/`infra`; nothing
imports upward. A GUI could call the same engine functions (`junk.scan`,
`disk.find_duplicates`, `checkup.run_checkup`) without a rewrite. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design rationale.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q    # 160+ tests, ~20 s
```

The safety guardrails are the most heavily tested code in the repo; the Windows
environment is mocked so the suite also runs on CI. See
[CONTRIBUTING.md](CONTRIBUTING.md) to get involved.

## License

[MIT](LICENSE) © Amine Zouaoui

[Ollama]: https://ollama.com
