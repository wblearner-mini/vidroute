"""Public kernel API. Every function returns an :class:`Envelope`; shells only format it.

Shells (CLI, MCP server, Skill script, desktop app) must not contain business
logic: they parse arguments, call a function here and render the envelope.
"""

from __future__ import annotations

import errno
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


def _unexpected(exc: Exception) -> RunFailed:
    """Name the problem when it is the machine's (full disk, no permission); never send people on a wild goose chase."""
    if isinstance(exc, OSError):
        where = exc.filename or "a file"
        if exc.errno == errno.ENOSPC:
            return RunFailed(f"The disk is full (writing {where}).", code="disk_full", hint="vidroute clean")
        if exc.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            return RunFailed(f"No permission to write {where}: {exc.strerror}.", code="not_writable",
                             hint="vidroute config set out_root <a folder you can write to>")
    return RunFailed(f"Unexpected error: {type(exc).__name__}: {exc}. Run again with -v for details; this may be a "
                     "bug, please report it: https://github.com/wblearner-mini/vidroute/issues")


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
            return failure(_unexpected(exc), warnings=ctx.warnings)
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
def setup(
    hosts: list[str] | None = None,
    skill: bool = True,
    mcp: bool = False,
    install: bool = True,
    capabilities: list[str] | None = None,
    profile: str = "auto",
    models: list[str] | None = None,
    mirror: str | None = None,
    accept_licenses: list[str] | None = None,
    selftest: bool = True,
    yes: bool = False,
    dry_run: bool = False,
    force: bool = False,
    *,
    ctx: Ctx,
) -> dict[str, Any]:
    """Install plan + connecting agent hosts + self-test, behind one confirmation (exit 3 without ``yes``)."""
    from vidroute.core import setup as _setup
    from vidroute.core.errors import InsufficientHardware, NeedsConfirmation, NeedsInstall

    mirror = mirror or _config.get("mirror") or "auto"
    the_plan = _setup.plan(hosts, skill, mcp, install, capabilities, profile, models, mirror, accept_licenses,
                           selftest, force)
    inst = the_plan["install"]
    if dry_run:
        return {"plan": the_plan, "executed": False}
    blocker = _setup.ffmpeg_blocker(the_plan)
    if blocker:  # say so now, not after a long download
        raise NeedsInstall("Install ffmpeg first, then run vidroute setup again: vidroute cannot read videos without it "
                           "and cannot install it here. Nothing has been downloaded or changed.",
                           code="ffmpeg_missing", hint=blocker, data={"plan": the_plan})
    if inst and inst["license_acceptance_required"]:
        raise NeedsConfirmation(
            "Some models have restricted licenses that must be accepted explicitly: "
            + ", ".join(inst["license_acceptance_required"]),
            code="license_not_accepted", hint=inst["accept_command"], data={"plan": the_plan})
    if inst and not inst["disk_ok"]:
        raise InsufficientHardware(
            f"Not enough disk space: ~{inst['disk_needed_gb']} GB needed, {inst['disk_free_gb']} GB free.",
            hint="Free some space or set VIDROUTE_HOME to a larger drive", data={"plan": the_plan})
    if not the_plan["nothing_to_do"] and not yes:
        raise NeedsConfirmation(
            "Review the setup plan and confirm (nothing has been downloaded or changed).",
            code="needs_confirmation", hint=the_plan["confirm_command"], data={"plan": the_plan})
    return _setup.execute(the_plan, ctx, mirror=mirror)


@command
def setup_remove(hosts: list[str] | None = None, yes: bool = False, dry_run: bool = False, *,
                 ctx: Ctx) -> dict[str, Any]:
    """Take out what ``setup`` added to agent hosts (skill folders, MCP entries); models stay installed."""
    from vidroute.core import integrate
    from vidroute.core.errors import NeedsConfirmation

    the_plan = integrate.plan_remove(hosts)
    if dry_run or not the_plan["changes"]:
        return {"plan": the_plan, "executed": False}
    if not yes:
        hint = "vidroute setup --remove" + "".join(f" --host {h}" for h in hosts or []) + " --yes"
        raise NeedsConfirmation("Review what would be removed from your agent hosts and confirm.",
                                code="needs_confirmation", hint=hint, data={"plan": the_plan})
    return {"plan": the_plan, "executed": True, **integrate.remove(the_plan, ctx)}


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


# --------------------------------------------------------- llm / host / plan


@command
def llm_resolve(llm: str | None, host: str | None = None, modalities: str | None = None,
                audio_in_video: str | None = None, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.llm.resolve import resolve

    return resolve(llm, host, modalities, audio_in_video, ctx)


@command
def llm_set(model_id: str, *, ctx: Ctx, **opts: Any) -> dict[str, Any]:
    from vidroute.core.llm import manage

    return {"profile": manage.llm_set(model_id, **opts)}


@command
def llm_unset(model_id: str, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.llm import manage

    return {"removed": manage.unset("llm_profiles", model_id), "id": model_id}


@command
def llm_list(*, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.llm import manage

    return manage.llm_list()


@command
def llm_sync(provider: str = "openrouter", *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.errors import UsageError
    from vidroute.core.llm import manage

    if provider != "openrouter":
        raise UsageError("Only --provider openrouter is supported")
    return manage.sync_openrouter()


@command
def host_set(host_id: str, *, ctx: Ctx, **opts: Any) -> dict[str, Any]:
    from vidroute.core.llm import manage

    return {"profile": manage.host_set(host_id, **opts)}


@command
def host_unset(host_id: str, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.llm import manage

    return {"removed": manage.unset("host_profiles", host_id), "id": host_id}


@command
def host_list(*, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.llm import manage

    return manage.host_list()


@command
def probe(video: str, refresh: bool = False, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.probe import probe as _probe

    return _probe(video, ctx, refresh=refresh)


@command
def plan(video: str, llm: str | None, host: str | None = None, task: str = "summary", prefer: str = "quality",
         language: str = "auto", modalities: str | None = None, audio_in_video: str | None = None, ocr: str = "auto",
         route: str = "auto", force_asr: bool = False, out: str | None = None, diarize: str = "auto", *,
         ctx: Ctx) -> dict[str, Any]:
    from pathlib import Path

    from vidroute.core.plan import make_plan
    from vidroute.core.util.jsonio import write_json_atomic

    result = make_plan(video, llm, host, task, prefer, language, modalities, audio_in_video, ocr, route, force_asr, ctx,
                       diarize=diarize)
    if out:
        target = Path(out).expanduser().resolve() / paths.video_slug(Path(video)) / "plan.json"
        write_json_atomic(target, result)
        result = {**result, "plan_path": str(target)}
    return result


@command
def prepare(video: str, plan: str | None = None, route: str = "auto", out: str | None = None,
            accept_raw_video_handoff: bool = False, llm: str | None = None, host: str | None = None,
            task: str = "summary", prefer: str = "quality", language: str = "auto", modalities: str | None = None,
            audio_in_video: str | None = None, ocr: str = "auto", force_asr: bool = False, diarize: str = "auto", *,
            ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.prepare import prepare as _prepare

    return _prepare(video, plan, route, out, accept_raw_video_handoff, llm, host, task, prefer, language, modalities,
                    audio_in_video, ocr, force_asr, ctx=ctx, diarize=diarize)


@command
def run(video: str, llm: str | None = None, host: str | None = None, task: str = "summary", prefer: str = "quality",
        language: str = "auto", modalities: str | None = None, audio_in_video: str | None = None, ocr: str = "auto",
        route: str = "auto", force_asr: bool = False, out: str | None = None, accept_raw_video_handoff: bool = False,
        diarize: str = "auto", *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.prepare import run as _run

    return _run(video, llm, host=host, task=task, prefer=prefer, language=language, modalities=modalities,
                audio_in_video=audio_in_video, ocr=ocr, route=route, force_asr=force_asr, out=out,
                accept_raw_video_handoff=accept_raw_video_handoff, diarize=diarize, ctx=ctx)


@command
def drill(out_dir: str, range: str, fps: float | None = None, asr: bool = False, ocr: bool = False,  # noqa: A002
          max_frames: int | None = None, *, ctx: Ctx) -> dict[str, Any]:
    from vidroute.core.prepare.drill import drill as _drill

    return _drill(out_dir, range, fps=fps, asr=asr, ocr=ocr, max_frames=max_frames, ctx=ctx)


@command
def skill_path(*, ctx: Ctx) -> dict[str, Any]:
    """Where the bundled Agent Skill lives (copy or link it into your host's skills folder)."""
    from vidroute.core.resources import data_file

    skill_md = data_file("skills/vidroute/SKILL.md")
    return {"path": str(skill_md.parent), "skill_md": str(skill_md)}


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
