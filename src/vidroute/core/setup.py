"""``vidroute setup``: everything needed to use vidroute from an agent, behind one confirmation.

1. the install plan — ffmpeg, uv, the Python runtime, worker environments and
   models — exactly as ``vidroute install`` computes it (default: speech, voice
   activity and on-screen text, about 0.8 GB);
2. connecting vidroute to the agent hosts on this machine (skill folders and,
   with ``--mcp``, MCP servers), see :mod:`vidroute.core.integrate`;
3. an end-to-end self-test on a generated clip (:mod:`vidroute.core.selftest`).

Steps 1 and 2 write files (step 2 outside VIDROUTE_HOME), so nothing runs
before the user confirms the combined plan. A machine that is already set up
gets the self-test only.
"""

from __future__ import annotations

from typing import Any

from vidroute.core import integrate
from vidroute.core.context import Ctx
from vidroute.core.errors import NeedsInstall, RunFailed, VidrouteError

DEFAULT_CAPABILITIES = ("asr", "vad", "ocr", "scene")


def plan(hosts: list[str] | None = None, skill: bool = True, mcp: bool = False, install: bool = True,
         capabilities: list[str] | None = None, profile: str = "auto", models: list[str] | None = None,
         mirror: str = "auto", accept_licenses: list[str] | None = None, selftest: bool = True,
         force: bool = False) -> dict[str, Any]:
    from vidroute.core.install import planner

    install_plan = None
    if install:
        caps = list(capabilities or [])
        if not caps and not models and profile == "auto":
            caps = list(DEFAULT_CAPABILITIES)
        install_plan = planner.plan_install(caps or None, profile, models, accept_licenses, mirror, force=force)
    integration = integrate.plan(hosts, skill=skill, mcp=mcp, force=force)
    changes = (install_plan is not None and not install_plan["nothing_to_do"]) or integration["changes"] > 0
    return {
        "install": install_plan,
        "integration": integration,
        "selftest": selftest,
        "nothing_to_do": not changes,
        "confirm_command": "vidroute setup" + _cli_args(hosts, skill, mcp, install, capabilities, profile, models,
                                                        mirror, accept_licenses, selftest, force) + " --yes",
    }


def _cli_args(hosts: list[str] | None, skill: bool, mcp: bool, install: bool, capabilities: list[str] | None,
              profile: str, models: list[str] | None, mirror: str, accept_licenses: list[str] | None,
              selftest: bool, force: bool) -> str:
    parts = [f" --host {h}" for h in hosts or [] if h != "auto"]
    parts += [" --no-skill"] if not skill else []
    parts += [" --mcp"] if mcp else []
    parts += [" --no-install"] if not install else []
    parts += [f" --capability {c}" for c in capabilities or []]
    parts += [f" --model {m}" for m in models or []]
    parts += [f" --profile {profile}"] if profile != "auto" else []
    parts += [f" --mirror {mirror}"] if mirror != "auto" else []
    parts += [f" --accept-license {a}" for a in accept_licenses or []]
    parts += [" --no-selftest"] if not selftest else []
    parts += [" --force"] if force else []
    return "".join(parts)


def execute(the_plan: dict[str, Any], ctx: Ctx, mirror: str = "auto") -> dict[str, Any]:
    from vidroute.core import doctor, selftest
    from vidroute.core.install import runner
    from vidroute.core.util import ffmpeg

    out: dict[str, Any] = {"plan": the_plan, "executed": not the_plan["nothing_to_do"]}
    problems: list[str] = []
    inst = the_plan.get("install")
    if inst is not None and not inst["nothing_to_do"]:
        try:
            out["install"] = runner.execute(inst, ctx, mirror=mirror)
        except VidrouteError as exc:
            if exc.code == "cancelled":
                raise
            out["install"] = {**(exc.data or {}), "ok": False, "error": exc.message}
            problems.append("installing runtimes or models")
    out["integration"] = integrate.apply(the_plan["integration"], ctx)
    if not out["integration"]["ok"]:
        problems.append("connecting an agent host")
    ffmpeg.reset_cache()
    doctor.invalidate()
    if the_plan.get("selftest"):
        out["selftest"] = selftest.run(ctx)
        if out["selftest"]["status"] == "failed":
            problems.append("the self-test")
    out["next_steps"] = out["integration"].get("next_steps", [])
    if not ffmpeg.detect().found:
        hint = ffmpeg.install_hint()
        raise NeedsInstall("ffmpeg/ffprobe are still missing: vidroute cannot read videos without them.",
                           code="ffmpeg_missing", hint=hint, data=out)
    if problems:
        raise RunFailed("Setup finished with problems in " + ", ".join(problems) + ".", code="setup_failed",
                        hint="vidroute doctor --refresh   (then run vidroute setup again: finished parts are kept)",
                        data=out)
    return out
