# Changelog

All notable changes to Sifty. The format loosely follows
[Keep a Changelog](https://keepachangelog.com); versions before the first
public release were development milestones.

## [0.9.0] - 2026-07

### Added

- **Proactive maintenance agent**: `sifty agent run` does a full read-only
  checkup plus anomaly detection and toasts a short summary. Schedule it with
  `sifty agent schedule` to have Sifty keep an eye on things in the background.
- **Anomaly detection**: Sifty now notices change over time - a drive that lost
  a lot of free space, junk piling up fast, or a new program added to Windows
  startup - and surfaces it in the checkup, the Home screen, and the AI's
  context. Baselines are stored locally (sizes and startup names only).
- **Opt-in unattended auto-fix**: with `[agent].auto_fix` turned on, the
  scheduled run may auto-clean a small, fixed allowlist of per-user cache/temp
  junk (never system or admin locations, never your files), all to the Recycle
  Bin and undoable from Reports. Off by default: it only notifies.
- **AI summary on Home**: when the local model is available, the Home checkup
  shows a short plain-language read on what matters most and what to do first.
- **Run agent from Home**: a "Run agent" button runs the whole proactive pass
  and cleans the low-risk allowlist to the Recycle Bin behind a confirm, showing
  the checkup, any anomalies, and what it cleaned right there.

### Changed

- The Home checkup now lists notable changes as their own reviewable rows, each
  linking to the screen where you can act on them.

## [0.8.0] - 2026-07

### Added

- **The AI remembers your machine.** The chat now persists across restarts,
  and Sifty learns your habits - the categories you routinely clean and the
  actions you tend to decline feed into the AI's context so its advice matches
  how you actually use the tool. Everything stays local: names, sizes and
  counts only, never file contents.
- **Per-tool AI policies**: set any agent tool to `auto` (run without asking),
  `ask` (always confirm), or `never` (block it) with
  `sifty ai policy set <tool> <auto|ask|never>`; `sifty ai policy list` shows
  every tool, its risk, and what will happen. These layer on top of the global
  autonomy level, so you can auto-clean junk but always confirm an uninstall.
- **`sifty ai autonomy`**: show or set the global autonomy level from the CLI
  (previously only the TUI dropdown).

### Changed

- The agent no longer re-runs the same read-only scan twice within a single
  request, so multi-step answers come back faster.

## [0.7.0] - 2026-07

### Added

- **Discord cache cleanup**: the junk cleaner now covers Discord's `Cache`,
  `Code Cache`, and `GPUCache` directories - cache only, never tokens or
  message history.
- **Windows installer**: a graphical `sifty-setup.exe` (Inno Setup) with a
  Welcome / License / Install / Finish wizard that adds Sifty to PATH and
  registers an Add/Remove Programs entry, alongside the existing portable exe.

### Changed

- **Item counts read with thousands separators** across the CLI (e.g.
  `12,480 files` instead of `12480`).
- **Worktree cleanup** is now described by what it detects - any prunable git
  worktree, not just coding-agent leftovers - so it also matches CI checkouts
  and abandoned experiments.

### Fixed

- **`sifty tui` no longer crashes in the packaged exe.** Textual imports its
  widgets lazily, so PyInstaller left `textual.widgets._tab_pane` (and the
  `styles.tcss` stylesheet) out of the build and the TUI died on launch. Both
  are now bundled. Affected winget / scoop / installer users only; pip and
  pipx installs were fine.
- **Optimize no longer crashes when you navigate away mid-run.** The
  background worker now no-ops once its view is unmounted instead of raising.
- **Self-update is disabled on editable (`-e`) installs** instead of offering
  a phantom upgrade that then fails and can half-rebuild the venv.

## [0.6.0] - 2026-06

### Added

- **Uninstall leftovers scanner**: `sifty apps leftovers "App"` finds the
  directories and Start Menu shortcuts an uninstaller left behind in
  AppData/ProgramData (conservative exact-name matching, never inside
  Windows/Program Files). The CLI reports leftovers automatically after an
  uninstall; the Apps TUI offers to clean them right after a bulk uninstall.
- **`sifty config`**: show / get / set / reset / edit the configuration
  without hunting through `%APPDATA%` (writes only your overrides).
- **Browser cache coverage**: every Chrome/Edge/Brave/Vivaldi profile
  (Cache, Code Cache, GPUCache) plus Firefox `cache2`; cache dirs only,
  never cookies/history/passwords.
- **Crash-report junk categories**: per-user crash dumps + WER queues, and
  system WER/kernel minidumps (admin).
- **`sifty organize undo`**: moves the last organize session's files back
  and removes the emptied folders.

### Changed

- **Home** is now the checkup: findings carry direct fix buttons (clean junk,
  clean stale downloads, apply updates, each behind the usual confirm) and
  the redundant stat-card grid is gone.
- **AI chat approvals are inline**: Run/Skip buttons in the transcript
  instead of a pop-up modal; quick-action buttons removed.
- **Startup screen** matches the Services pattern: highlight a row, then
  Enable/Disable buttons; clicking a row no longer toggles it instantly.
- Bare group commands (`sifty junk`, `sifty disk`, …) print their full help
  instead of a "try --help" hint.

### Fixed

- Junk clean now reports how many items were skipped and why (in use / need
  admin) instead of silently looking like it did nothing on a re-clean.
- Self-update version-check failures are logged instead of swallowed.

## [0.5.0] - 2026-06

### Added

- **Checkup**: a read-only full-suite health scan (`sifty checkup` and a
  Home-screen hero button): junk, pending updates, registry orphans, stale
  downloads, low disk space, and startup bloat in one report, each finding
  with a one-tap action.
- **AI follow-up actions**: when an AI tool scan finds something, the result
  card offers a button that jumps to the screen that can act on it.
- **Self-update** (`sifty selfupdate`) via PyPI + pipx.
- **Git worktree cleanup**: detect and prune orphaned git worktrees
  (`Worktrees` mode in Smart cleanup).
- **VHD compaction**: compact WSL2/Hyper-V virtual disks via DISM.
- Installer intelligence: the Downloads-installers junk category only flags
  installers whose app is already installed.

### Changed

- TUI navigation consolidated into tabbed groups: **Clean** (Junk, Purge,
  Optimize, Smart) and **Apps** (Installed, Updates, Startup, Services); every
  former screen stays reachable via the command palette.
- Home redesigned: clickable stat cards, summaries cached between visits,
  compact admin status line.
- Browse… folder picker on the Purge and Smart cleanup screens.

### Fixed

- Worktree pruning honors the row selection and no longer crashes when an
  orphan directory exists on disk.
- Registry orphan scanner filters SystemComponent and winget-managed entries.

## [0.4.0] - 2026

- NTFS-aware duplicate detection (hardlinks counted once), concurrent junk
  scanning and hashing, registry orphan detection (read-only), apps-screen
  orphan panel.

## [0.3.0] - 2026

- Dev artifact purge (`node_modules`, `dist`, `__pycache__`, …), system optimize
  (DNS flush, thumbnail/prefetch/update-cache rebuild, DISM component
  cleanup), five new junk categories.

## [0.2.0] - 2026

- Layered architecture (core / windows / infra / cli / tui), smart cleanup
  (duplicates, large files, stale downloads), startup manager (reversible),
  curated services manager, cleanup profiles + Task Scheduler integration,
  low-disk watch with toast alerts, clean history (SQLite) + undo, `--json`
  scripting output, agentic AI with autonomy levels, live system monitor,
  on-demand UAC elevation, command palette, crash logging.

## [0.1.0] - 2026

- Initial release: junk scan/clean, disk analysis, duplicate finder, app
  list/uninstall (winget), updates via winget, file organization, local-AI
  advisor (Ollama), safety layer (Recycle Bin only, dry-run default,
  protected paths, audit log), Textual TUI.
