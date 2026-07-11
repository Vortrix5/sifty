# Sifty architecture

This document explains *why* Sifty is shaped the way it is. For contributor
ground rules see [CONTRIBUTING.md](../CONTRIBUTING.md); for usage see
[README.md](../README.md).

## Design goals

1. **Safe by construction.** A maintenance tool that can delete files must make
   destruction the hard path, not the default. Mistakes should be recoverable.
2. **Testable without a real Windows victim.** Core logic runs against sandboxes,
   so we can iterate fast and prove safety on any machine.
3. **CLI now, GUI later, no rewrite.** Logic lives in plain functions; the CLI is
   a thin shell over them. A future GUI/TUI imports the same functions.
4. **Private AI.** The AI is local (Ollama) and sees only metadata. No file
   contents and nothing personal leave the machine.

## Layered structure

```text
            ┌──────────────────────┬──────────────────────┐
   user →   │ cli/ (Typer)         │ tui/ (Textual)       │  thin frontends
            │ commands/*.py        │ views/*.py           │
            ├──────────────────────┴──────────────────────┤
            │ core/  - engine (pure-ish), the gatekeeper   │  testable core
            │   models · safety · junk · disk · apps ·     │
            │   updates · organize                         │
            ├───────────────────────┬──────────────────────┤
            │ windows/ - OS calls    │ infra/ - config, log │  primitives
            │ admin·recyclebin·winget│                      │
            ├───────────────────────┴──────────────────────┤
            │ ai/  - advisory (+ agentic), degrades to no-op │  optional
            └──────────────────────────────────────────────┘
```

The split is now **across packages**, not within a file:

- **`core/<domain>.py`**: pure-ish functions that return data (`scan() ->
  list[CategoryScan]`, `find_duplicates() -> dict`, `plan_organization() ->
  list[Move]`). No Typer/Textual; OS access goes through `windows/`. This is what
  tests, the TUI, and the AI agent all call.
- **`cli/commands/*.py`** and **`tui/views/*.py`**: thin frontends that parse
  input / render and call core, handling the dry-run/confirm flow. No business
  logic worth testing.
- **`windows/`**: every direct OS call (Send2Trash, winget, registry, UAC).
- **`infra/`**: config + logging. `console.py` holds the shared Rich helpers.

## The safety model (the heart of the system)

All destruction funnels through one function so there is a single place to audit
and a single place that can refuse. `safety.trash()`:

1. Calls `assert_safe()` → `is_protected()`.
2. If dry-run, returns without touching disk.
3. Otherwise sends the path to the Recycle Bin and appends to the audit log.

`is_protected()` uses **two tiers of roots** because "protect the whole drive"
and "protect the Windows directory" need different rules:

| Tier | Examples | Rule |
|---|---|---|
| Contents-protected | `C:\Windows`, `Program Files`, `Program Files (x86)`, `ProgramData`, plus user `extra_protected_paths` | Refuse the root, anything inside it, or any ancestor of it. |
| Self-protected | drive root (`C:\`), user profile root | Refuse only the root itself (or an ancestor). Contents stay deletable; otherwise the whole disk would be off-limits. |

Callers that legitimately need to delete inside a contents-protected root pass
`allow_subtrees` to vouch for a specific path (e.g. junk cleaning passes
`C:\Windows\Temp`). This keeps the broad denylist strict while permitting the few
known-safe carve-outs.

**Invariant:** the only deletion primitive in the codebase is `safety.trash()`.
Anything else (`os.remove`, `shutil.rmtree`, …) is a bug.

## Capability notes

- **junk**: categories are data (`JunkCategory`) built from the environment, each
  with `roots` and an `allow_subtrees` carve-out. Scanning measures sizes; cleaning
  trashes top-level entries. The Downloads-installers category is config-gated off
  by default (those files are often wanted).
- **disk**: `psutil` for volumes; size-then-SHA-256 two-pass for duplicates (hash
  only files that share a size, so most files are never hashed).
- **apps**: reads the registry Uninstall + Run keys via `winreg` (read-only, no
  admin needed) and the Startup folder; uninstalls shell out to `winget`.
- **updates**: `winget` has no stable machine output, so we parse its fixed-column
  table by header offsets. This is the most brittle code in the repo and is the
  reason `test_updates.py` exists.
- **organize**: plans `(src → dest)` moves and previews them; moves are reversible
  (into subfolders) so they don't go through the Recycle Bin, but still default to
  dry-run. `_unique_dest()` avoids clobbering.

## AI layer

`ai/client.py` wraps the Ollama HTTP API. `is_available()` is checked before every
use so a missing/stopped Ollama degrades to "no AI" rather than an error.
`ai/advisor.py` builds prompts from **metadata only** and is the sole place prompts
live; the advisor itself only explains and recommends, it never acts.

`ai/agent.py` is the agentic loop: the model may call tools from `ai/tools.py`,
each tagged `read` / `low` / `high`. Whether a call runs, prompts for confirmation,
or is blocked follows the global autonomy level plus optional per-tool policies
(`ai/policy.py`, stored in `ai_state.json`). The AI has no privileged path around
safety - every tool that deletes routes through `core.safety.trash()`, so protected
paths are refused and everything is audited whether a human or the agent triggered it.

`ai/context.py` builds the metadata-only machine snapshot, now including lightweight
learned preferences. `core/ai_memory.py` (a separate `ai_memory.db`, kept out of the
`history.db` undo ledger) persists the chat transcript and the tool skips those
preferences are derived from.

## Proactive agent

`core/agent_run.py` is what a scheduled `sifty agent run` executes: a read-only
`checkup` + `anomaly.detect()`, then a toast summarizing what it found. It may also
auto-clean, but only under strict rails - this is the one place Sifty can delete
with no human present. The auto-fix set is a **hardcoded allowlist** of per-user
cache/temp categories (config can only narrow it), every admin/system category is
excluded, and the LLM is never asked what to delete here - the background path calls
`junk.clean()` directly and records the run so `sifty undo` still works. It is off by
default (`[agent].auto_fix`): the scheduled run only notifies unless you opt in.

`core/anomaly.py` keeps a small time-series baseline (`snapshots.db`, separate from
`history.db` and `ai_memory.db`) of disk-free, junk totals, and startup names, and
flags notable change - a fast free-space drop, junk growth, a new startup entry. Its
findings feed the toast, the AI context, and the Home checkup.

## Testing strategy

- `pyproject.toml` sets `pythonpath = ["src"]` so tests import `sifty` without an
  install step.
- Safety/junk tests `monkeypatch` the protected-root environment variables and
  `Path.home`, then build fixtures under `tmp_path`. This makes the
  Windows-specific path logic deterministic and runnable on any OS.
- `core.safety.send_to_trash` is monkeypatched in tests so nothing is ever
  really deleted; `windows/` primitives are mocked.
- The fragile winget parser is tested against a captured sample table.

## Extension points

- **New capability**: add `core/<name>.py` (logic), `cli/commands/<name>.py`
  (handler) and a `tui/views/<name>.py`, wired in `cli/app.py` and the TUI nav.
  The junk and disk modules are the pattern to copy.
- **New junk source**: a `JunkCategory` in `junk.py` with its `allow_subtrees`
  carve-out and a sandbox test in `tests/test_junk.py`.
- **GUI**: import the core functions; no logic needs to move.
- **Packaging**: PyInstaller single-file exe; the entry point is
  `packaging/exe_entry.py` (see `packaging/README.md`).
