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


# ------------------------------------------------------------------ handlers


def h_version(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.version(ctx=ctx)


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
