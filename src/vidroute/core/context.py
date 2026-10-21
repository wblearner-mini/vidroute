"""Per-command run context: warnings, progress reporting and cooperative cancellation.

Shells construct a Ctx and pass it to vidroute.api functions. The CLI reports
progress on stderr; the MCP server turns it into ``notifications/progress`` and
sets the cancel event on ``notifications/cancelled``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from vidroute.core.envelope import warning
from vidroute.core.errors import Cancelled

ProgressFn = Callable[[str, "float | None", "float | None", "str | None"], None]


@dataclass
class Ctx:
    warnings: list[dict[str, Any]] = field(default_factory=list)
    on_progress: ProgressFn | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    interactive: bool = False

    def warn(self, code: str, message: str, hint: str | None = None) -> None:
        self.warnings.append(warning(code, message, hint))

    def progress(self, stage: str, done: float | None = None, total: float | None = None, message: str | None = None) -> None:
        if self.on_progress is not None:
            try:
                self.on_progress(stage, done, total, message)
            except Exception:  # noqa: BLE001 - progress must never break work
                pass

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled("Cancelled by request", hint=None)
