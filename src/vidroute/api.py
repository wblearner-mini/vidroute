"""Public kernel API. Every function returns an :class:`Envelope`; shells only format it.

Shells (CLI, MCP server, Skill script, desktop app) must not contain business
logic: they parse arguments, call a function here and render the envelope.
"""

from __future__ import annotations

import functools
import traceback
from typing import Any, Callable, TypeVar

from vidroute import __version__
from vidroute.core import config as _config
from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.envelope import Envelope, failure, success
from vidroute.core.errors import RunFailed, VidrouteError
from vidroute.core.log import get_logger

F = TypeVar("F", bound=Callable[..., Envelope])


def command(fn: Callable[..., dict[str, Any]]) -> Callable[..., Envelope]:
    """Wrap a kernel function: inject a Ctx, turn its result/exception into an Envelope."""

    @functools.wraps(fn)
    def wrapper(*args: Any, ctx: Ctx | None = None, **kwargs: Any) -> Envelope:
        ctx = ctx or Ctx()
        try:
            data = fn(*args, ctx=ctx, **kwargs)
        except VidrouteError as err:
            return failure(err, warnings=ctx.warnings)
        except KeyboardInterrupt:
            return failure(RunFailed("Interrupted", code="cancelled"), warnings=ctx.warnings)
        except Exception as exc:  # noqa: BLE001 - last-resort envelope
            get_logger().debug("unhandled error:\n%s", traceback.format_exc())
            err = RunFailed(
                f"Unexpected error: {type(exc).__name__}: {exc}",
                hint="vidroute doctor --refresh",
            )
            return failure(err, warnings=ctx.warnings)
        return success(data, warnings=ctx.warnings)

    return wrapper


# --------------------------------------------------------------------- misc


@command
def version(*, ctx: Ctx) -> dict[str, Any]:
    return {"version": __version__, "home": str(paths.home())}


# ------------------------------------------------------------------- config


@command
def config_get(key: str | None = None, *, ctx: Ctx) -> dict[str, Any]:
    if key is None:
        return {"config": _config.effective(), "path": str(paths.config_path())}
    return {"key": key, "value": _config.get(key)}


@command
def config_set(key: str, value: Any, *, ctx: Ctx) -> dict[str, Any]:
    _config.set_value(key, value)
    return {"key": key, "value": _config.get(key), "path": str(paths.config_path())}


@command
def config_unset(key: str, *, ctx: Ctx) -> dict[str, Any]:
    _config.unset(key)
    return {"key": key, "value": _config.get(key) if key in _config.KNOWN_KEYS else None}
