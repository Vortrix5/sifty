# Contributing to Sifty

Thanks for your interest! Sifty is a Windows maintenance tool, so most changes
need a Windows machine to test on (the unit tests themselves are
cross-platform; the Windows environment is mocked).

## The prime directive: safety

Sifty deletes files and changes system state. Every change must preserve:

- **All deletion goes through `safety.trash()`** ([src/sifty/core/safety.py](src/sifty/core/safety.py)).
  No `os.remove`, `os.unlink`, `shutil.rmtree`, or `Path.unlink` anywhere else.
- **Dry-run is the default.** Destructive commands preview by default and only
  act with an explicit `--apply`, after a confirm prompt.
- **Protected paths are refused** even with `--apply --yes`.
- **Applied deletions are audited** to `%APPDATA%\sifty\audit.log`.

If you touch deletion logic, run the safety tests first:
`pytest tests/test_safety.py`.

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q     # should be green (~20s)
```

### Git hooks (optional)

If you'd like ruff to lint your changes before each commit (the same check CI
runs), enable the [pre-commit](https://pre-commit.com) hook after the dev
install:

```powershell
pre-commit install
```

It's entirely optional, nothing runs unless you enable it, and you can skip it
on a given commit with `git commit --no-verify`. Either way, CI runs the full
lint and test suite on your PR.

## Project layout

Layered: `cli`/`tui` (thin frontends) → `core` (engine) → `windows` (OS
primitives) / `infra` (config, logging). `ai` is advisory. Never import a
frontend from `core`; keep OS calls in `windows/`. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Conventions

- Logic lives in plain testable functions; Typer/Textual layers stay thin.
- Output goes through `console.py` helpers, not `print()`.
- `from __future__ import annotations` + type hints in every module.
- Subprocess calls capture text as UTF-8 (`encoding="utf-8", errors="replace"`).
- Filesystem walks tolerate permission errors.
- Lint with `ruff check .` before pushing.

## Commits & PRs

- Subject format: `(feat|fix|enhance|refactor|docs|test|chore) short imperative summary`.
- One coherent change per commit.
- PRs need green tests and, for new core functions, a matching test.

## Reporting bugs

Open an issue with: Windows version, Python version, the command you ran, and
the tail of `sifty logs`. Never include your audit log if it contains paths
you'd rather keep private.
