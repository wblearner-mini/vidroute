"""A strict, standard-library-only loader for the YAML subset vidroute's data files use.

Why not PyYAML: the orchestrator must stay dependency-free (it is installed
with ``pipx``/``uv tool`` on machines we know nothing about). Profiles, rules
and the model manifest are human-edited YAML, so we parse a well-defined
subset and fail loudly on anything outside it. CI cross-checks every bundled
file against PyYAML to make sure both agree.

Supported:
  * block mappings and block sequences (including "indentless" sequences
    as mapping values and ``- key: value`` items);
  * flow sequences ``[a, b]`` and flow mappings ``{a: 1}`` on a single line;
  * plain, single-quoted and double-quoted scalars (single line);
  * literal ``|`` and folded ``>`` block scalars with ``-``/``+`` chomping;
  * comments, a leading ``---`` document marker.

Scalars follow the YAML 1.2 core schema: ``null``/``~``/empty, ``true``/
``false``, decimal/hex/octal integers and decimal floats. ``yes``/``no``/``on``/
``off`` stay strings. Not supported (raise ``YamlError``): anchors, aliases,
tags, directives, multi-document streams, multi-line plain/quoted scalars and
multi-line flow collections.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class YamlError(ValueError):
    def __init__(self, message: str, source: str = "<string>", line: int | None = None) -> None:
        where = f"{source}:{line}" if line else source
        super().__init__(f"{where}: {message}")
        self.source = source
        self.line = line


@dataclass
class _Token:
    indent: int
    text: str
    line: int  # 1-based physical line
    dash: bool = False


_INT_DEC = re.compile(r"[-+]?[0-9]+$")
_INT_OCT = re.compile(r"0o[0-7]+$")
_INT_HEX = re.compile(r"0x[0-9a-fA-F]+$")
_FLOAT = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$")


def resolve_plain(text: str) -> Any:
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if _INT_DEC.match(text):
        return int(text)
    if _INT_OCT.match(text):
        return int(text[2:], 8)
    if _INT_HEX.match(text):
        return int(text[2:], 16)
    if _FLOAT.match(text):
        return float(text)
    if text in (".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF"):
        return math.inf
    if text in ("-.inf", "-.Inf", "-.INF"):
        return -math.inf
    if text in (".nan", ".NaN", ".NAN"):
        return math.nan
    return text


_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n", "v": "\v",
    "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"', "/": "/", "\\": "\\",
    "N": "\x85", "_": "\xa0", "L": " ", "P": " ",
}
_HEX_LEN = {"x": 2, "u": 4, "U": 8}


def _strip_comment(raw: str) -> str:
    """Remove a trailing ``# comment`` that is not inside quotes."""
    quote = None
    i = 0
    while i < len(raw):
        ch = raw[i]
        if quote == '"':
            if ch == "\\":
                i += 2
                continue
            if ch == '"':
                quote = None
        elif quote == "'":
            if ch == "'":
                if i + 1 < len(raw) and raw[i + 1] == "'":
                    i += 2
                    continue
                quote = None
        else:
            if ch in "\"'" and (i == 0 or raw[i - 1] in " \t[{,:-?"):
                quote = ch
            elif ch == "#" and (i == 0 or raw[i - 1] in " \t"):
                return raw[:i]
        i += 1
    return raw


class _Parser:
    def __init__(self, text: str, source: str) -> None:
        if text.startswith("﻿"):
            text = text[1:]
        self.raw = text.splitlines()
        self.source = source
        self.pos = 0  # next physical line to tokenize
        self.pending: deque[_Token] = deque()
        self.started = False  # seen any content line yet (for the leading '---')

    # -- errors ---------------------------------------------------------
    def err(self, message: str, line: int | None = None) -> YamlError:
        return YamlError(message, self.source, line)

    # -- tokenizer ------------------------------------------------------
    def _fill(self) -> None:
        while not self.pending and self.pos < len(self.raw):
            lineno = self.pos + 1
            raw = self.raw[self.pos]
            self.pos += 1
            body = _strip_comment(raw).rstrip()
            if not body.strip():
                continue
            lead = len(body) - len(body.lstrip(" \t"))
            if "\t" in body[:lead]:
                raise self.err("tabs are not allowed for indentation", lineno)
            stripped = body.strip()
            if not self.started:
                self.started = True
                if stripped.startswith("%"):
                    raise self.err("YAML directives are not supported", lineno)
                if stripped == "---":
                    continue
            if stripped in ("---", "..."):
                raise self.err("multi-document streams are not supported", lineno)
            indent = lead
            content = body[lead:]
            # Split "- - value" / "- key: v" into a dash token plus a virtual line.
            while content == "-" or content.startswith("- "):
                self.pending.append(_Token(indent, "-", lineno, dash=True))
                rest = content[1:]
                stripped_rest = rest.lstrip(" ")
                indent += 1 + (len(rest) - len(stripped_rest))
                content = stripped_rest
            if content:
                self.pending.append(_Token(indent, content, lineno))

    def peek(self) -> _Token | None:
        self._fill()
        return self.pending[0] if self.pending else None

    def next(self) -> _Token:
        tok = self.peek()
        if tok is None:  # pragma: no cover - guarded by callers
            raise self.err("unexpected end of document")
        return self.pending.popleft()

    # -- structure ------------------------------------------------------
    def parse_document(self) -> Any:
        tok = self.peek()
        if tok is None:
            return None
        value = self.parse_block(tok.indent, parent_indent=-1)
        extra = self.peek()
        if extra is not None:
            raise self.err(f"unexpected content {extra.text!r} (check indentation)", extra.line)
        return value

    def parse_block(self, indent: int, parent_indent: int) -> Any:
        tok = self.peek()
        assert tok is not None
        if tok.dash:
            return self.parse_seq(tok.indent)
        if self._split_entry(tok) is not None:
            return self.parse_map(tok.indent)
        self.next()
        value = self.parse_inline(tok.text, tok, parent_indent)
        nxt = self.peek()
        # A complete scalar node cannot be followed by anything indented deeper
        # than its parent; YAML would read that as a multi-line plain scalar.
        if nxt is not None and nxt.indent > parent_indent:
            raise self.err(
                "unexpected indented content after a scalar (multi-line plain scalars are not "
                "supported; quote the value or use a | block)",
                nxt.line,
            )
        return value

    def parse_seq(self, indent: int) -> list[Any]:
        items: list[Any] = []
        while True:
            tok = self.peek()
            if tok is None or tok.indent < indent:
                break
            if tok.indent > indent:
                raise self.err("bad indentation in sequence", tok.line)
            if not tok.dash:
                break
            self.next()
            nxt = self.peek()
            if nxt is not None and nxt.indent > indent:
                items.append(self.parse_block(nxt.indent, parent_indent=indent))
            else:
                items.append(None)
        return items

    def parse_map(self, indent: int) -> dict[Any, Any]:
        result: dict[Any, Any] = {}
        while True:
            tok = self.peek()
            if tok is None or tok.indent < indent:
                break
            if tok.indent > indent:
                raise self.err("bad indentation in mapping", tok.line)
            if tok.dash:
                raise self.err("sequence item where a mapping key was expected", tok.line)
            entry = self._split_entry(tok)
            if entry is None:
                raise self.err(f"expected 'key: value', got {tok.text!r}", tok.line)
            key, rest = entry
            self.next()
            try:
                hash(key)
            except TypeError:
                raise self.err("mapping keys must be scalars", tok.line) from None
            if key in result:
                raise self.err(f"duplicate key {key!r}", tok.line)
            if rest == "":
                nxt = self.peek()
                if nxt is not None and (nxt.indent > indent or (nxt.indent == indent and nxt.dash)):
                    if nxt.dash and nxt.indent == indent:
                        result[key] = self.parse_seq(indent)
                    else:
                        result[key] = self.parse_block(nxt.indent, parent_indent=indent)
                else:
                    result[key] = None
            else:
                result[key] = self.parse_inline(rest, tok, indent)
        return result

    def _split_entry(self, tok: _Token) -> tuple[Any, str] | None:
        text = tok.text
        if tok.dash or text[0] in "[{|>":
            return None
        if text[0] in "\"'":
            value, end = self._quoted(text, 0, tok)
            rest = text[end:].lstrip(" ")
            if not rest.startswith(":"):
                return None
            after = rest[1:]
            if after and not after.startswith(" "):
                return None
            return value, after.strip()
        idx = 0
        while True:
            idx = text.find(":", idx)
            if idx < 0:
                return None
            if idx == len(text) - 1 or text[idx + 1] == " ":
                key_text = text[:idx].rstrip()
                if not key_text:
                    raise self.err("empty mapping key", tok.line)
                if key_text[0] in "&*!?":
                    raise self.err("anchors, aliases, tags and complex keys are not supported", tok.line)
                return resolve_plain(key_text), text[idx + 1 :].strip()
            idx += 1

    # -- scalars --------------------------------------------------------
    def parse_inline(self, text: str, tok: _Token, parent_indent: int) -> Any:
        head = text[0]
        if head in "&*!":
            raise self.err("anchors, aliases and tags are not supported", tok.line)
        if head in "[{":
            value, end = self._flow(text, 0, tok)
            if text[end:].strip():
                raise self.err(f"unexpected text after flow collection: {text[end:].strip()!r}", tok.line)
            return value
        if head in "\"'":
            value, end = self._quoted(text, 0, tok)
            if text[end:].strip():
                raise self.err(f"unexpected text after quoted scalar: {text[end:].strip()!r}", tok.line)
            return value
        if head in "|>":
            return self._block_scalar(text, tok, parent_indent)
        if ": " in text or text.endswith(":"):
            raise self.err(f"plain scalar may not contain ': ' — quote it: {text!r}", tok.line)
        return resolve_plain(text)

    def _quoted(self, text: str, start: int, tok: _Token) -> tuple[str, int]:
        quote = text[start]
        i = start + 1
        out: list[str] = []
        while i < len(text):
            ch = text[i]
            if quote == "'":
                if ch == "'":
                    if i + 1 < len(text) and text[i + 1] == "'":
                        out.append("'")
                        i += 2
                        continue
                    return "".join(out), i + 1
                out.append(ch)
                i += 1
                continue
            if ch == '"':
                return "".join(out), i + 1
            if ch == "\\":
                if i + 1 >= len(text):
                    break
                esc = text[i + 1]
                if esc in _ESCAPES:
                    out.append(_ESCAPES[esc])
                    i += 2
                    continue
                if esc in _HEX_LEN:
                    n = _HEX_LEN[esc]
                    digits = text[i + 2 : i + 2 + n]
                    if len(digits) != n or not all(c in "0123456789abcdefABCDEF" for c in digits):
                        raise self.err(f"bad \\{esc} escape", tok.line)
                    out.append(chr(int(digits, 16)))
                    i += 2 + n
                    continue
                raise self.err(f"unknown escape \\{esc}", tok.line)
            out.append(ch)
            i += 1
        raise self.err("unterminated quoted scalar (multi-line quoted scalars are not supported)", tok.line)

    def _flow(self, text: str, i: int, tok: _Token) -> tuple[Any, int]:
        opener = text[i]
        closer = "]" if opener == "[" else "}"
        i += 1
        seq: list[Any] = []
        mapping: dict[Any, Any] = {}
        while True:
            i = _skip_ws(text, i)
            if i >= len(text):
                raise self.err("unterminated flow collection (must fit on one line)", tok.line)
            if text[i] == closer:
                return (seq if opener == "[" else mapping), i + 1
            node, i = self._flow_node(text, i, tok, in_map_key=opener == "{")
            i = _skip_ws(text, i)
            if opener == "{":
                if i < len(text) and text[i] == ":":
                    i = _skip_ws(text, i + 1)
                    if i < len(text) and text[i] in ",}":
                        value = None
                    else:
                        value, i = self._flow_node(text, i, tok, in_map_key=False)
                        i = _skip_ws(text, i)
                else:
                    value = None
                if node in mapping:
                    raise self.err(f"duplicate key {node!r}", tok.line)
                mapping[node] = value
            else:
                seq.append(node)
            if i < len(text) and text[i] == ",":
                i += 1
                continue
            if i < len(text) and text[i] == closer:
                continue
            raise self.err("expected ',' or closing bracket in flow collection", tok.line)

    def _flow_node(self, text: str, i: int, tok: _Token, in_map_key: bool) -> tuple[Any, int]:
        ch = text[i]
        if ch in "[{":
            return self._flow(text, i, tok)
        if ch in "\"'":
            return self._quoted(text, i, tok)
        if ch in "&*!|>":
            raise self.err("anchors, aliases, tags and block scalars are not supported in flow context", tok.line)
        j = i
        while j < len(text):
            c = text[j]
            if c in ",[]{}":
                break
            if c == ":" and (j + 1 == len(text) or text[j + 1] in " ,]}"):
                break
            j += 1
        return resolve_plain(text[i:j].strip()), j

    def _block_scalar(self, header: str, tok: _Token, parent_indent: int) -> str:
        style = header[0]
        chomp = "clip"
        for ch in header[1:].strip():
            if ch == "-":
                chomp = "strip"
            elif ch == "+":
                chomp = "keep"
            else:
                raise self.err(f"unsupported block scalar header {header!r}", tok.line)
        if self.pending:
            raise self.err("block scalar indicator must end its line", tok.line)
        lines: list[str] = []
        content_indent: int | None = None
        while self.pos < len(self.raw):
            raw = self.raw[self.pos]
            if raw.strip() == "":
                lines.append("")
                self.pos += 1
                continue
            lead = len(raw) - len(raw.lstrip(" "))
            if lead <= parent_indent:
                break
            if content_indent is None:
                content_indent = lead
            if lead < content_indent:
                break
            lines.append(raw[content_indent:])
            self.pos += 1
        # trailing blank lines belong to chomping, not content
        body = lines
        trailing = 0
        while body and body[-1] == "":
            body = body[:-1]
            trailing += 1
        if style == "|":
            text = "\n".join(body)
        else:
            text = _fold(body)
        if not body:
            return "" if chomp != "keep" else "\n" * trailing
        if chomp == "strip":
            return text
        if chomp == "keep":
            return text + "\n" * (trailing + 1)
        return text + "\n"


def _fold(lines: list[str]) -> str:
    out = ""
    prev_blank = False
    for idx, line in enumerate(lines):
        more_indented = line.startswith(" ")
        if idx == 0:
            out = line
        elif line == "":
            out += "\n"
            prev_blank = True
            continue
        elif prev_blank or more_indented or lines[idx - 1].startswith(" "):
            out += ("" if prev_blank else "\n") + line
        else:
            out += " " + line
        prev_blank = False
    return out


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t":
        i += 1
    return i


def loads(text: str, source: str = "<string>") -> Any:
    return _Parser(text, source).parse_document()


def load_file(path: Path) -> Any:
    return loads(Path(path).read_text(encoding="utf-8"), source=str(path))
