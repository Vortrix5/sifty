# Releasing Sifty

Sifty ships two artifacts per release, both automated by
[`.github/workflows/release.yml`](../.github/workflows/release.yml):

1. **PyPI package** (`pip install sifty`), built and uploaded via PyPI
   **Trusted Publishing** (OIDC) against the repo's `pypi` environment, so no
   API token is ever stored in the repo.
2. **`sifty.exe`**, a standalone Windows executable attached to the GitHub
   Release (no Python needed to run it).

## Version policy

Sifty follows [Semantic Versioning](https://semver.org). Versions are
`MAJOR.MINOR.PATCH`, tagged `vMAJOR.MINOR.PATCH`. Which part to bump:

- **PATCH** (`0.6.0` → `0.6.1`): bug fixes, copy and docs, and safe internal
  refactors. No new commands and no changed user-facing behavior.
- **MINOR** (`0.6.x` → `0.7.0`): new capabilities (a command, a junk category,
  a scanner) or changed user-facing behavior. This is what every release so far
  has been.
- **MAJOR** (`0.x` → `1.0.0`): the deliberate "Sifty is stable" milestone.

Sifty is **pre-1.0**, so the CLI and behavior are still allowed to change in a
minor bump; a breaking change does not force a major bump yet. After `1.0.0`,
breaking changes to the CLI or behavior require a major bump.

## Cutting a release

1. Bump the version and update the changelog. `src/sifty/__init__.py`
   (`__version__`) is the single source of truth, `pyproject.toml` reads it
   dynamically, so you only edit it once:
   ```powershell
   python scripts/bump_version.py 0.6.1   # edits __version__ + syncs the rest
   ```
   This also updates the files that can't read the package metadata (the website
   JSON-LD, the Inno Setup script, the issue template). Then add a section to
   `CHANGELOG.md`. To verify nothing drifted later: `python scripts/bump_version.py --check`.
2. Commit, then tag and push:
   ```powershell
   git tag v0.6.1
   git push origin main --tags
   ```
3. On GitHub: **Releases → Draft a new release**, choose the tag (e.g.
   `v0.6.1`), paste the changelog section as the notes, and **Publish**.
4. Publishing the release triggers `release.yml`, which:
   - builds the wheel + sdist and uploads them to PyPI, and
   - builds `sifty.exe` and attaches it to the release.

## Verifying a release

```powershell
pip install --upgrade sifty
sifty version          # should match the tag
```

The exe appears under the release's "Assets". `sifty selfupdate` compares the
installed version against PyPI and upgrades via pipx.

## Community installers (winget / scoop)

- **scoop**: the bucket at <https://github.com/Vortrix5/scoop-bucket> uses
  `checkver` + `autoupdate`, so new releases are picked up automatically by
  `scoop update` / excavator. No per-release action needed.
- **winget**: bump the manifests in [`packaging/winget/`](../packaging/winget/)
  to the new version + SHA256 and submit with
  `wingetcreate update Vortrix5.Sifty --version <v> --urls <exe-url>` (it
  computes the hash and opens the winget-pkgs PR). Do this *after* the release,
  since it needs the exe URL + hash.

## Code signing (optional)

`sifty.exe` is currently **unsigned**, so Windows SmartScreen shows an
"unknown publisher" prompt on first run (click *More info → Run anyway*). To
remove it, sign the exe in the `windows-exe` job between the build and upload
steps. Options, cheapest first:

- **[SignPath](https://about.signpath.io/product/open-source)**: free
  certificate + GitHub Action for OSS projects. Best fit here.
- **[Azure Trusted Signing](https://learn.microsoft.com/azure/trusted-signing/)**:
  ~$10/month, Microsoft-run, has an official GitHub Action.
- An **EV code-signing certificate**: instant SmartScreen reputation, but
  pricey and needs a hardware/cloud HSM. OV certs are cheaper but still have to
  accrue reputation.

Self-signing does **not** help with SmartScreen. Whichever you pick, the
signing credential goes in a GitHub Actions secret and the signing step runs in
CI, so the release stays fully automated.

## Manual build (local, for testing)

```powershell
.\.venv\Scripts\python.exe -m pip install build
.\.venv\Scripts\python.exe -m build          # -> dist/*.whl, *.tar.gz
.\.venv\Scripts\python.exe -m twine check dist/*
```

For a local exe build, mirror what CI does (see `release.yml`):

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --onefile --name sifty --paths src --console `
  --collect-submodules win32com --hidden-import win32timezone `
  packaging/exe_entry.py
.\dist\sifty.exe version     # smoke test
```
