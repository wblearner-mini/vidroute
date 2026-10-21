"""Error types and the process exit codes they map to.

Exit codes are part of the public contract (see README "Exit codes"):

* 0 success
* 1 invalid usage or input
* 2 needs install (a capability, model, runtime or ffmpeg is missing)
* 3 needs user confirmation (install plan, restricted license, raw video handoff)
* 4 insufficient hardware
* 5 run failure
"""

from __future__ import annotations

from enum import IntEnum
from typing import Any


class ExitCode(IntEnum):
    OK = 0
    USAGE = 1
    NEEDS_INSTALL = 2
    NEEDS_CONFIRMATION = 3
    INSUFFICIENT_HARDWARE = 4
    RUN_FAILED = 5


class VidrouteError(Exception):
    """Base error. ``hint`` should be a command the user or agent can run as-is."""

    exit_code: ExitCode = ExitCode.RUN_FAILED
    default_code = "run_failed"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code
        self.hint = hint
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint}


class UsageError(VidrouteError):
    exit_code = ExitCode.USAGE
    default_code = "invalid_input"


class NotFoundError(UsageError):
    default_code = "not_found"


class NeedsInstall(VidrouteError):
    exit_code = ExitCode.NEEDS_INSTALL
    default_code = "needs_install"


class NeedsConfirmation(VidrouteError):
    exit_code = ExitCode.NEEDS_CONFIRMATION
    default_code = "needs_confirmation"


class InsufficientHardware(VidrouteError):
    exit_code = ExitCode.INSUFFICIENT_HARDWARE
    default_code = "insufficient_hardware"


class RunFailed(VidrouteError):
    exit_code = ExitCode.RUN_FAILED
    default_code = "run_failed"


class Cancelled(RunFailed):
    default_code = "cancelled"


class DataFileError(RunFailed):
    """A bundled or user-supplied data file (profile, manifest, rules) is invalid."""

    default_code = "invalid_data_file"
