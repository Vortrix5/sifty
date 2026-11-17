# Security Policy

## Supported versions

Only the latest release receives fixes.

## Sifty's security model

- Sifty never permanently deletes: removals go to the Recycle Bin through a
  single audited code path with protected-path checks.
- Destructive commands are dry-run by default and require `--apply` plus a
  confirmation.
- The AI integration is local-only (Ollama) and metadata-only: file names,
  sizes and paths, never file contents. The AI cannot delete anything
  itself; high-risk tool calls require explicit user approval.
- Elevation (UAC) is on-demand and never silent.

## Reporting a vulnerability

If you find a way to make Sifty delete something it shouldn't (bypass the
protected-path checks, escape the Recycle Bin routing, escalate privileges, or
inject commands through file names / registry values / winget output), please
**do not open a public issue**. Email amine.zouaoui@ieee.org with the details
and steps to reproduce. You'll get a response within a week, and credit in the
release notes once a fix ships.
