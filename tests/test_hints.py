"""Every `vidroute ...` command quoted in the source (error hints, guides) must exist with those flags."""

import ast
import re
from pathlib import Path

from vidroute.cli import build_parser

SRC = Path(__file__).resolve().parents[1] / "src" / "vidroute"


def _strings(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):  # f-strings: placeholders become "{}"
            yield "".join(v.value if isinstance(v, ast.Constant) else "{}" for v in node.values)


def _subparsers(parser):
    for action in parser._actions:  # noqa: SLF001 - argparse has no public accessor
        if action.__class__.__name__ == "_SubParsersAction":
            return action.choices
    return {}


def test_quoted_commands_use_real_subcommands_and_flags():
    commands = _subparsers(build_parser())
    checked = 0
    for path in sorted(SRC.rglob("*.py")):
        for text in _strings(ast.parse(path.read_text(encoding="utf-8"))):
            for m in re.finditer(r"(?<![\w-])vidroute ([a-z][a-z-]*)((?: [^\n;|]*)?)", text):
                cmd, rest = m.group(1), m.group(2)
                if cmd not in commands:
                    continue
                parser = commands[cmd]
                words = rest.split()
                subs = _subparsers(parser)
                if subs and words and words[0] in subs:
                    parser = subs[words[0]]
                known = {opt for action in parser._actions for opt in action.option_strings}  # noqa: SLF001
                for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", rest):
                    assert flag in known, f"{path.name}: '{m.group(0).strip()}' uses unknown flag {flag}"
                checked += 1
    assert checked > 20  # the scan really found the hints
