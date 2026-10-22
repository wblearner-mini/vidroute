# Contributing to vidroute

Thanks for helping! vidroute is small on purpose: a deterministic routing kernel with thin shells.
Please read [docs/DECISIONS.md](docs/DECISIONS.md) before larger changes — most "why not X?"
questions are answered there with evidence.

## Ground rules

- **Kernel + shells.** Logic lives in `src/vidroute/core` and `src/vidroute/api.py`; the CLI, MCP
  server and skill launcher only translate arguments and envelopes.
- **No runtime dependencies** in the orchestrator (`dependencies = []`). Heavy libraries go into a
  worker environment (`envs/*.in` + hash-locked `envs/*.lock.txt`) and talk JSON lines.
- **Local only, consent first.** No telemetry, no cloud calls, no downloads without a confirmed
  plan, no writes outside `VIDROUTE_HOME` and `--out`, never `shell=True`.
- **Windows is first-class.** Use `pathlib`, argument lists for subprocesses, UTF-8 everywhere; CI
  must pass on `windows-latest`.
- **Evidence over memory.** Model limits, host behaviour and model licenses need an official
  source and a check date (see [docs/PROFILE_CONTRIBUTING.md](docs/PROFILE_CONTRIBUTING.md)).

## Development setup

```bash
git clone https://github.com/wblearner-mini/vidroute && cd vidroute
python -m venv .venv
.venv/bin/pip install --require-hashes -r requirements-dev.lock.txt   # Windows: .venv\Scripts\pip
.venv/bin/pip install --no-deps -e .
.venv/bin/pytest
```

The default test run is offline: it uses ffmpeg (skipped when missing) and fake engines
(`VIDROUTE_FAKE_ENGINES=1`). With models installed (`vidroute install ... --yes`), run
`VIDROUTE_REAL_MODELS=1 pytest tests/test_real_models.py`.

Dependencies are locked with hashes:

```bash
uv pip compile --universal --generate-hashes --python-version 3.10 requirements-dev.in -o requirements-dev.lock.txt
uv pip compile --universal --generate-hashes --python-version 3.12 envs/ocr.in -o envs/ocr.lock.txt
```

After changing a lock, run `python tools/license_report.py --check <lock files>` and refresh
`THIRD_PARTY_LICENSES.md`.

## Pull requests

- One topic per PR, with tests. Routing changes need a table-driven case in `tests/test_routing.py`.
- Run `pytest` locally; CI runs Windows and Ubuntu on Python 3.10 and 3.13.
- Update `CHANGELOG.md` (Unreleased) and, for behaviour changes, `docs/DECISIONS.md`.
- Commit messages: conventional style (`feat(prepare): ...`, `fix(probe): ...`).
- By contributing you agree that your contribution is licensed under the MIT license.

## Reporting problems

- Bugs and profile corrections: GitHub issues (templates provided). Include `vidroute doctor --json`
  output, never the video itself or anything private.
- Security issues: see [SECURITY.md](SECURITY.md) — please do not open a public issue.
