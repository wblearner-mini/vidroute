"""The uniform JSON envelope returned by every kernel command.

    {"ok": true, "schema": "vidroute/1", "data": {...}, "warnings": [...], "error": null}

On failure ``ok`` is false and ``error`` is ``{"code", "message", "hint"}``,
where ``hint`` is a command that can be executed as-is. Warnings are objects
``{"code", "message", "hint"?}`` so both agents and programs can consume them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vidroute import SCHEMA
from vidroute.core.errors import ExitCode, VidrouteError


def warning(code: str, message: str, hint: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if hint:
        item["hint"] = hint
    return item


def dedupe_warnings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out = []
    for item in items:
        key = (item.get("code", ""), item.get("message", ""))
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


@dataclass
class Envelope:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None
    exit_code: ExitCode = ExitCode.OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema": SCHEMA,
            "data": self.data,
            "warnings": dedupe_warnings(self.warnings),
            "error": self.error,
        }


def success(data: dict[str, Any] | None = None, warnings: list[dict[str, Any]] | None = None) -> Envelope:
    return Envelope(ok=True, data=data or {}, warnings=list(warnings or []))


def failure(
    err: VidrouteError,
    warnings: list[dict[str, Any]] | None = None,
    data: dict[str, Any] | None = None,
) -> Envelope:
    payload = dict(data or {})
    if err.data:
        payload.update(err.data)
    return Envelope(
        ok=False,
        data=payload,
        warnings=list(warnings or []),
        error=err.to_dict(),
        exit_code=err.exit_code,
    )
