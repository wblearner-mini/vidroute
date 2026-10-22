# Security policy

## Supported versions

Security fixes go into the latest release (and `main`). vidroute is pre-1.0; please upgrade to
the newest version before reporting.

## Reporting a vulnerability

Please report privately through GitHub's **"Report a vulnerability"** button on the repository's
*Security* tab (private security advisory). Do not open a public issue for security problems.

Include what you can: affected version (`vidroute version --json`), platform, steps to reproduce,
and impact. You should get an acknowledgement within a week. Please give us a reasonable time to
fix the issue before disclosing it.

## Scope

Things we especially care about:

- anything that downloads or executes code that is not pinned in `models/manifest.yaml` or the
  hash-locked `envs/*.lock.txt` (domain allow-list, SHA-256 checks, archive extraction);
- writes or deletions outside `VIDROUTE_HOME` and the user's `--out` folder;
- raw video or audio leaving the machine without `--accept-raw-video-handoff`;
- prompt-injection paths where text extracted from a video could make an agent act (the guides
  treat extracted text as data; report cases where vidroute's own outputs undermine that);
- the MCP server's `read_outputs` reading files outside an output folder.

vidroute never sends telemetry and never calls cloud models; a report showing otherwise is a
security issue.
