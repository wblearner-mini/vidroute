"""Public kernel API. Every function returns an :class:`Envelope`; shells only format it.

Shells (CLI, MCP server, Skill script, desktop app) must not contain business
logic: they parse arguments, call a function here and render the envelope.
"""

from __future__ import annotations

import functools
import traceback
from typing import Any, Callable

from vidroute import __version__
from vidroute.core import config as _config
from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.envelope import Envelope, failure, success
from vidroute.core.errors import RunFailed, VidrouteError
from vidroute.core.log import get_logger


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


# ------------------------------------------------------------------- doctor


@command
def doctor(refresh: bool = False, offline: bool | None = None, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core import doctor as _doctor

    return _doctor.run(refresh=refresh, offline=offline, ctx=ctx)


# ------------------------------------------------------------------ install


@command
def install(
    capabilities: list[str] | None = None,
    profile: str = "auto",
    models: list[str] | None = None,
    mirror: str | None = None,
    accept_licenses: list[str] | None = None,
    yes: bool = False,
    dry_run: bool = False,
    force: bool = False,
    smoke: bool = True,
    *,
    ctx: Ctx,
) -> dict[str, Any]:
    """Plan (always) and execute (only with yes=True) an install.

    Without ``yes`` the plan is returned with error ``needs_confirmation``
    (exit 3); shells show it to the user and call again with ``yes=True``.
    """
    from vidroute.core.errors import NeedsConfirmation
    from vidroute.core.install import planner, runner

    mirror = mirror or _config.get("mirror") or "auto"
    plan = planner.plan_install(capabilities, profile, models, accept_licenses, mirror, force=force)
    if dry_run:
        return {"plan": plan, "executed": False}
    if plan["license_acceptance_required"]:
        raise NeedsConfirmation(
            "Some models have restricted licenses that must be accepted explicitly: "
            + ", ".join(plan["license_acceptance_required"]),
            code="license_not_accepted",
            hint=plan["accept_command"],
            data={"plan": plan},
        )
    if not plan["disk_ok"]:
        from vidroute.core.errors import InsufficientHardware

        raise InsufficientHardware(
            f"Not enough disk space: ~{plan['disk_needed_gb']} GB needed, {plan['disk_free_gb']} GB free.",
            hint="Free some space or set VIDROUTE_HOME to a larger drive", data={"plan": plan})
    if plan["nothing_to_do"]:
        return {"plan": plan, "executed": False, "message": "Everything requested is already installed."}
    if not yes:
        raise NeedsConfirmation(
            "Review the install plan and confirm (nothing has been downloaded).",
            code="needs_confirmation",
            hint=plan["confirm_command"],
            data={"plan": plan},
        )
    outcome = runner.execute(plan, ctx, mirror=mirror, smoke=smoke)
    return {"plan": plan, "executed": True, **outcome}


@command
def models_list(*, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.engines import supported
    from vidroute.core.install.manifest import load_manifest

    manifest = load_manifest()
    rows = []
    for m in manifest.models.values():
        st = m.installed_status()
        rows.append({
            "id": m.id, "capability": m.capability, "status": m.status, "package": m.package,
            "installed": bool(st["installed"]), "size_mb": m.size_mb, "languages": list(m.languages),
            "license": m.license.spdx, "restricted": m.license.needs_acceptance, "gpu": m.requires_gpu,
            "adapter": supported(m.id, manifest), "path": str(m.model_dir) if not m.builtin else None,
        })
    return {"models": rows}


@command
def models_verify(model_ids: list[str] | None = None, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.install.manifest import load_manifest
    from vidroute.core.install.models import verify_model

    manifest = load_manifest()
    targets = model_ids or [m.id for m in manifest.models.values() if not m.builtin and m.installed_status().get("installed_at")]
    results = []
    for mid in targets:
        ctx.progress("verify", None, None, mid)
        results.append(verify_model(manifest.model(mid)))
    bad = [r["id"] for r in results if not r["ok"]]
    if bad:
        ctx.warn("verify_failed", f"Integrity check failed: {', '.join(bad)}",
                 "vidroute install " + " ".join(f"--model {b}" for b in bad) + " --force --yes")
    return {"results": results, "ok": not bad}


@command
def models_remove(model_ids: list[str], yes: bool = False, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core import doctor as _doctor
    from vidroute.core.errors import NeedsConfirmation
    from vidroute.core.install.manifest import load_manifest
    from vidroute.core.install.models import remove_model

    manifest = load_manifest()
    entries = [manifest.model(mid) for mid in model_ids]
    if not yes:
        raise NeedsConfirmation(f"Remove {', '.join(model_ids)} from {paths.models_dir()}?",
                                hint="vidroute models remove " + " ".join(model_ids) + " --yes")
    removed = [e.id for e in entries if remove_model(e)]
    _doctor.invalidate()
    return {"removed": removed}


@command
def clean(downloads: bool = True, logs: bool = True, outputs: bool = False, yes: bool = False, *, ctx: Ctx) -> dict[str, Any]:
    """Remove caches inside VIDROUTE_HOME (never anything outside it)."""
    import shutil

    from vidroute.core import state as _state
    from vidroute.core.errors import NeedsConfirmation

    targets = []
    if downloads:
        targets += [paths.downloads_dir(), paths.cache_dir() / "uv"]
    if logs:
        targets.append(paths.cache_dir() / "logs")
    if outputs:
        targets.append(paths.default_out_root())
    if outputs and not yes:
        raise NeedsConfirmation(f"This deletes all outputs under {paths.default_out_root()}.",
                                hint="vidroute clean --outputs --yes")
    removed = []
    for t in targets:
        if t.exists() and paths.is_within(t, paths.home()):
            shutil.rmtree(t, ignore_errors=True)
            removed.append(str(t))
    _state.cache_clear()
    return {"removed": removed}


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
