"""Shell 1: the command-line interface.

Thin wrapper over :mod:`vidroute.api`: parse arguments, call the kernel, print
the envelope. With ``--json`` stdout carries exactly one JSON document; all
human-readable logging and progress goes to stderr.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable

from vidroute import __version__, api
from vidroute.core.context import Ctx
from vidroute.core.envelope import Envelope, failure
from vidroute.core.errors import UsageError
from vidroute.core.log import get_logger, set_verbosity
from vidroute.core.util.jsonio import dumps

Handler = Callable[[argparse.Namespace, Ctx], Envelope]
Renderer = Callable[[Envelope, argparse.Namespace], str]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(f"{self.prog}: {message}", hint=f"{self.prog} --help")


def _common_flags(parser: argparse.ArgumentParser, suppress: bool) -> None:
    default: Any = argparse.SUPPRESS if suppress else False
    parser.add_argument("--json", action="store_true", default=default, help="print the JSON envelope on stdout")
    parser.add_argument("-v", "--verbose", action="store_true", default=default, help="debug logging on stderr")
    parser.add_argument("-q", "--quiet", action="store_true", default=default, help="only warnings and errors on stderr")


# ----------------------------------------------------------------- renderers


def render_generic(env: Envelope, args: argparse.Namespace) -> str:
    return dumps(env.data)


def render_config(env: Envelope, args: argparse.Namespace) -> str:
    data = env.data
    if "config" in data:
        lines = [f"# {data['path']}"]
        lines += [f"{k} = {dumps(v, indent=None)}" for k, v in sorted(data["config"].items())]
        return "\n".join(lines)
    return f"{data['key']} = {dumps(data.get('value'), indent=None)}"


def _yes(flag: object) -> str:
    return "yes" if flag else ("no" if flag is not None else "?")


def render_doctor(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    cpu = d.get("cpu") or {}
    ff = d.get("ffmpeg") or {}
    lines = [
        f"vidroute doctor  ({'cached' if d.get('cached') else 'fresh'}, {d.get('probe_seconds')}s)",
        f"  system   {d.get('os', {}).get('system')} {d.get('os', {}).get('release')} {d.get('os', {}).get('machine')}"
        f" | python {d.get('os', {}).get('python')}",
        f"  cpu      {cpu.get('model') or '?'} | {cpu.get('logical_cores')} threads | AVX2 {_yes(cpu.get('avx2'))}",
        f"  memory   {d.get('ram_gb')} GB (available {d.get('ram_available_gb')} GB) | disk free {d.get('disk_free_gb')} GB",
        f"  ffmpeg   {('found ' + str(ff.get('version'))) if ff.get('found') else 'MISSING -> ' + str(ff.get('install_hint'))}",
    ]
    gpus = d.get("gpus") or []
    if gpus:
        for g in gpus:
            vram = g.get("vram_gb") if g.get("vram_gb") is not None else g.get("vram_approx_gb")
            lines.append(f"  gpu      {g.get('name')} | VRAM {vram if vram is not None else '?'} GB ({g.get('vram_source')})")
    else:
        lines.append("  gpu      none detected")
    uv = (d.get("runtimes") or {}).get("uv") or {}
    lines.append(f"  uv       {uv.get('version') if uv.get('found') else 'not found (installed on demand)'}")
    for name, e in ((d.get("runtimes") or {}).get("envs") or {}).items():
        status = "ready" if e.get("exists") and e.get("import_ok") else ("broken" if e.get("exists") else "not installed")
        lines.append(f"  env      {name}: {status}")
    lines.append(f"  tier     {d.get('tier')}  ({'; '.join(d.get('tier_reasons') or [])})")
    for cap, info in (d.get("capabilities") or {}).items():
        state_txt = ", ".join(info.get("models") or []) or "missing"
        lines.append(f"  {cap:<8} {state_txt}" + (f"  -> {info['hint']}" if info.get("hint") else ""))
    ext = d.get("external") or {}
    local = [k for k, v in ext.items() if v.get("running")]
    lines.append(f"  local    {', '.join(local) if local else 'no Ollama / LM Studio server running'}")
    net = d.get("network") or {}
    if net.get("checked"):
        mir = ", ".join(f"{k} {'ok' if v.get('ok') else 'unreachable'}" for k, v in (net.get("mirrors") or {}).items())
        lines.append(f"  network  {mir}")
    return "\n".join(lines)


def render_install(env: Envelope, args: argparse.Namespace) -> str:
    from vidroute.core.install.planner import describe

    d = env.data
    lines = [describe(d["plan"])] if d.get("plan") else []
    if d.get("message"):
        lines.append(d["message"])
    if d.get("executed"):
        lines.append("Results:")
        for r in d.get("results", []):
            lines.append(f"  {r.get('kind'):<6} {r.get('id', ''):<34} {r.get('status')}"
                         + (f"  {r.get('source')}" if r.get("source") else ""))
        for mid, sm in (d.get("smoke") or {}).items():
            lines.append(f"  smoke  {mid:<34} {sm.get('status')}  {sm.get('detail', '')}")
    elif d.get("plan") and not d.get("message"):
        lines.append(f"Dry run only. To install: {d['plan']['confirm_command']}")
    return "\n".join(lines)


def render_models(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    if "models" in d:
        lines = [f"{'id':<36} {'cap':<5} {'status':<12} {'size':>8}  {'license':<28} installed"]
        for m in d["models"]:
            lines.append(f"{m['id']:<36} {m['capability']:<5} {m['status']:<12} {m['size_mb']:>6.0f}MB  "
                         f"{m['license']:<28} {'yes' if m['installed'] else '-'}")
        return "\n".join(lines)
    if "results" in d:
        return "\n".join(f"{r['id']:<36} {'ok' if r['ok'] else 'FAILED: ' + '; '.join(r.get('problems', []))}"
                         for r in d["results"]) or "nothing installed"
    return dumps(d)


# ------------------------------------------------------------------ handlers


def h_version(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.version(ctx=ctx)


def h_doctor(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.doctor(refresh=args.refresh, offline=True if args.offline else None, ctx=ctx)


def _confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def h_install(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    from vidroute.core.install.planner import describe

    kwargs = dict(capabilities=args.capability, profile=args.profile, models=args.model, mirror=args.mirror,
                  accept_licenses=args.accept_license, dry_run=args.dry_run, force=args.force, smoke=not args.no_smoke)
    env = api.install(yes=args.yes, ctx=ctx, **kwargs)
    # Interactive terminals get a prompt instead of an exit-3 envelope; the kernel stays the decision point.
    if (not env.ok and env.error and env.error.get("code") == "needs_confirmation" and ctx.interactive
            and env.data.get("plan")):
        sys.stderr.write(describe(env.data["plan"]) + "\n")
        if _confirm("Proceed with this install?"):
            ctx.warnings.clear()
            env = api.install(yes=True, ctx=ctx, **kwargs)
    return env


def h_models(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    action = args.action or "list"
    if action == "list":
        return api.models_list(ctx=ctx)
    if action == "verify":
        return api.models_verify(args.ids or None, ctx=ctx)
    yes = args.yes or (ctx.interactive and _confirm(f"Remove {', '.join(args.ids)}?"))
    return api.models_remove(args.ids, yes=yes, ctx=ctx)


def h_clean(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.clean(outputs=args.outputs, yes=args.yes, ctx=ctx)


def h_config(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    from vidroute.core.config import parse_value

    action = args.action or "get"
    if action == "get":
        return api.config_get(getattr(args, "key", None), ctx=ctx)
    if action == "set":
        return api.config_set(args.key, parse_value(args.value), ctx=ctx)
    return api.config_unset(args.key, ctx=ctx)


# -------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="vidroute",
        description=(
            "Give coding agents eyes and ears for local video: decide what to hand the "
            "agent's LLM (native video, keyframes + transcript, or text) and prepare it locally."
        ),
    )
    _common_flags(parser, suppress=False)
    parser.add_argument("--version", action="version", version=f"vidroute {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", parser_class=_Parser)

    def add(name: str, help_text: str, handler: Handler, render: Renderer = render_generic) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        _common_flags(p, suppress=True)
        p.set_defaults(handler=handler, render=render)
        return p

    add("version", "Print version and data directory", h_version)

    p = add("doctor", "Detect OS, CPU/GPU, memory, ffmpeg, runtimes and installed models; decide the local tier",
            h_doctor, render_doctor)
    p.add_argument("--refresh", action="store_true", help="ignore the cached result and probe again")
    p.add_argument("--offline", action="store_true", help="skip download-mirror reachability checks")

    p = add("install", "Plan and (after confirmation) install local models and runtimes", h_install, render_install)
    p.add_argument("--capability", action="append", choices=["asr", "vad", "ocr", "scene"], help="capability to install (repeatable)")
    p.add_argument("--profile", choices=["auto", "lite", "full"], default="auto", help="package set when no --capability/--model is given")
    p.add_argument("--model", action="append", help="exact model id from `vidroute models list` (repeatable)")
    p.add_argument("--mirror", choices=["auto", "github", "hf", "modelscope"], help="preferred download source")
    p.add_argument("--accept-license", action="append", metavar="ID", help="accept a restricted model license (repeatable)")
    p.add_argument("--yes", "-y", action="store_true", help="confirm the plan non-interactively")
    p.add_argument("--dry-run", "--plan", dest="dry_run", action="store_true", help="only show the plan")
    p.add_argument("--force", action="store_true", help="reinstall even if already installed")
    p.add_argument("--no-smoke", action="store_true", help="skip smoke tests after installing")

    p = add("models", "List, verify (sha256) or remove installed models", h_models, render_models)
    msub = p.add_subparsers(dest="action", metavar="ACTION", parser_class=_Parser)
    m = msub.add_parser("list", help="all known models and their install state")
    _common_flags(m, suppress=True)
    m = msub.add_parser("verify", help="re-hash installed files")
    _common_flags(m, suppress=True)
    m.add_argument("ids", nargs="*")
    m = msub.add_parser("remove", help="delete installed models")
    _common_flags(m, suppress=True)
    m.add_argument("ids", nargs="+")
    m.add_argument("--yes", "-y", action="store_true")

    p = add("clean", "Delete download caches and logs inside VIDROUTE_HOME", h_clean)
    p.add_argument("--outputs", action="store_true", help="also delete $VIDROUTE_HOME/out")
    p.add_argument("--yes", "-y", action="store_true")

    p = add("config", "Get or set user configuration ($VIDROUTE_HOME/config.json)", h_config, render_config)
    csub = p.add_subparsers(dest="action", metavar="ACTION", parser_class=_Parser)
    g = csub.add_parser("get", help="show one key or the whole effective config")
    _common_flags(g, suppress=True)
    g.add_argument("key", nargs="?")
    s = csub.add_parser("set", help="set a key (value parsed as JSON when possible)")
    _common_flags(s, suppress=True)
    s.add_argument("key")
    s.add_argument("value")
    u = csub.add_parser("unset", help="remove a key (back to default)")
    _common_flags(u, suppress=True)
    u.add_argument("key")

    return parser


# ---------------------------------------------------------------------- main


class _StderrProgress:
    """Throttled progress lines on stderr."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self.log = get_logger()

    def __call__(self, stage: str, done: float | None, total: float | None, message: str | None) -> None:
        now = time.monotonic()
        final = done is not None and total is not None and done >= total
        if not final and now - self.last.get(stage, 0.0) < 1.0:
            return
        self.last[stage] = now
        parts = [f"[{stage}]"]
        if done is not None and total:
            parts.append(f"{done:.0f}/{total:.0f}")
        if message:
            parts.append(message)
        self.log.info(" ".join(parts))


def _emit(env: Envelope, args: argparse.Namespace | None, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(dumps(env.to_dict()) + "\n")
        return
    log = get_logger()
    for w in env.to_dict()["warnings"]:
        log.warning("warning: %s%s", w["message"], f" (try: {w['hint']})" if w.get("hint") else "")
    if env.ok:
        render: Renderer = getattr(args, "render", render_generic) if args is not None else render_generic
        text = render(env, args) if args is not None else dumps(env.data)
        if text:
            sys.stdout.write(text + "\n")
    else:
        err = env.error or {}
        log.error("error: %s", err.get("message"))
        if err.get("hint"):
            log.error("hint: %s", err["hint"])


def _configure_stdio() -> None:
    # Windows consoles may default to a legacy code page; never die on non-ASCII paths.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as err:
        env = failure(err)
        _emit(env, None, as_json)
        return int(env.exit_code)
    set_verbosity(getattr(args, "verbose", False), getattr(args, "quiet", False))
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 1
    ctx = Ctx(on_progress=_StderrProgress(), interactive=sys.stdin.isatty() and not args.json)
    env = handler(args, ctx)
    _emit(env, args, bool(args.json))
    return int(env.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
