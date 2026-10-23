# Releasing vidroute

## Checklist (brief §11.5)

| item | status |
|---|---|
| `LICENSE` (MIT, D-001) | ✅ |
| `README.md` + `README.zh-CN.md`: support matrix, 30-second start, privacy, models and licenses, known issues | ✅ |
| `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`, `CHANGELOG.md`, `CITATION.cff`, issue and PR templates | ✅ |
| No model weights or third-party binaries in the repository; `THIRD_PARTY_LICENSES.md` | ✅ (`tests/test_hygiene.py`) |
| Semantic versioning; schema ids in data files (`vidroute/<kind>/<major>`) | ✅ (D-038) |
| Community updates to profiles and rules: `docs/PROFILE_CONTRIBUTING.md` | ✅ |
| Locked dependencies with hashes; CI license and vulnerability scan (`supply-chain.yml`) | ✅ |
| Release artifacts with SHA-256 checksums (`release.yml`); actions pinned by commit SHA | ✅ |
| Name free on PyPI and GitHub (re-checked 2026-10-22) | ✅ |
| Disclaimer on lawful use and raw-video handoff risk | ✅ README "Privacy and safety" |
| Clean Windows install re-test | ✅ `real-install` workflow on `windows-latest` (see PROGRESS.md) |
| `v0.1.0` pre-release on GitHub with sources and checksums | ⏳ needs the owner's go-ahead |
| PyPI release | ⏳ needs the owner's go-ahead and a trusted publisher |

## Steps

1. Update `CHANGELOG.md` (move *Unreleased* to the new version with the date).
2. Set the version in `src/vidroute/__init__.py` (PEP 440, e.g. `0.1.0`), `.claude-plugin/plugin.json`
   (SemVer, e.g. `0.1.0`), `skills/vidroute/SKILL.md` metadata and `CITATION.cff`;
   `pytest tests/test_skill.py` checks the plugin version matches.
3. Make sure CI and **Supply chain** are green on `main`; run **Real install** (Actions → Real install →
   Run workflow) for a clean Windows and Ubuntu install with real models.
4. Tag and push: `git tag -a v0.1.0 -m "vidroute 0.1.0" && git push origin v0.1.0`. The **Release**
   workflow checks that the tag matches the package version, builds, writes `SHA256SUMS.txt` and
   creates the GitHub release (pre-release for `a`/`b`/`rc`/`dev` versions).
5. PyPI (once): create the project's trusted publisher on PyPI for this repository, workflow
   `release.yml`, environment `pypi`. Then Actions → Release → Run workflow with
   `publish_to_pypi = true` on the tag.
6. Users verify downloads with `sha256sum -c SHA256SUMS.txt`.
7. After the first PyPI upload, switch the install source from the git URL to the package name:
   `install.ps1` / `install.sh` (default `Source`), `INSTALL_HINT` in `skills/vidroute/scripts/vidroute.py`
   and `docs/AGENT_SETUP.md` step 2 (`uv tool install vidroute`). The `irm … | iex` / `curl … | sh`
   lines in the README work as soon as the repository is public.
