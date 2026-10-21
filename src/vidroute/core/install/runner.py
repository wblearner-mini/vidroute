"""Execute a confirmed install plan: ffmpeg (opt-in) -> uv -> Python -> envs -> models -> smoke tests."""

from __future__ import annotations

import shutil
from typing import Any

from vidroute.core import paths
from vidroute.core.context import Ctx
from vidroute.core.errors import RunFailed, VidrouteError
from vidroute.core.install import envs as envmod
from vidroute.core.install.download import Downloader
from vidroute.core.install.manifest import Manifest, load_manifest
from vidroute.core.install.models import install_model
from vidroute.core.util import ffmpeg, proc


def run_smoke(model_id: str, ctx: Ctx, manifest: Manifest | None = None) -> dict[str, Any]:
    from vidroute.core.engines import get_engine, supported

    manifest = manifest or load_manifest()
    if not supported(model_id, manifest):
        return {"status": "skipped", "detail": "no adapter in this version (experimental model)"}
    try:
        return get_engine(model_id, manifest).smoke_test(ctx)
    except VidrouteError as exc:
        return {"status": "failed", "detail": exc.message, "data": exc.data}
    except Exception as exc:  # noqa: BLE001 - smoke failures are reported, not raised
        return {"status": "failed", "detail": f"{type(exc).__name__}: {exc}"}


def execute(plan: dict[str, Any], ctx: Ctx, mirror: str = "auto", smoke: bool = True) -> dict[str, Any]:
    manifest = load_manifest()
    paths.ensure_dir(paths.home())
    results: list[dict[str, Any]] = []
    failed = False

    for item in plan["items"]:
        if item["kind"] != "ffmpeg":
            continue
        if item["action"] == "run_with_yes":
            ctx.progress("ffmpeg", None, None, "installing with winget")
            res = proc.run(item["command"], timeout=1800)
            ok = res.ok and ffmpeg.find_tool("ffmpeg") is not None
            results.append({"kind": "ffmpeg", "status": "installed" if ok else "failed",
                            "detail": (res.stderr or res.stdout).strip()[-400:]})
            if not ok:
                ctx.warn("ffmpeg_install_failed", "winget could not install FFmpeg; install it manually.",
                         ffmpeg.install_hint())
        else:
            results.append({"kind": "ffmpeg", "status": "manual", "detail": item["command"]})
            ctx.warn("ffmpeg_missing", "ffmpeg is required; install it with the command shown.", item["command"])

    if any(i["kind"] == "uv" for i in plan["items"]):
        try:
            uv = envmod.bootstrap_uv(ctx)
            results.append({"kind": "uv", "status": "installed", "path": str(uv)})
        except VidrouteError as exc:
            results.append({"kind": "uv", "status": "failed", "detail": exc.message})
            raise

    for item in plan["items"]:
        if item["kind"] != "env":
            continue
        ctx.check_cancelled()
        try:
            res = envmod.create_env(manifest.env_groups[item["id"]], ctx, variant=item["variant"])
            results.append({"kind": "env", "id": item["id"], "status": res["status"], "variant": item["variant"]})
        except VidrouteError as exc:
            failed = True
            results.append({"kind": "env", "id": item["id"], "status": "failed", "detail": exc.message})
            if item["variant"] == "cuda":
                ctx.warn("gpu_env_failed", f"CUDA runtime for {item['id']} failed to install; trying the CPU variant.")
                try:
                    res = envmod.create_env(manifest.env_groups[item["id"]], ctx, variant="cpu")
                    results.append({"kind": "env", "id": item["id"], "status": res["status"], "variant": "cpu",
                                    "note": "fallback after CUDA failure"})
                    failed = False
                except VidrouteError as exc2:
                    results.append({"kind": "env", "id": item["id"], "status": "failed", "detail": exc2.message})

    downloader = Downloader(manifest.allowed_domains, ctx=ctx)
    installed_ids = []
    for item in plan["items"]:
        if item["kind"] != "model":
            continue
        ctx.check_cancelled()
        entry = manifest.model(item["id"])
        try:
            res = install_model(entry, manifest, ctx, mirror=mirror, reuse_path=item.get("reuse_path"),
                                downloader=downloader, force=bool(plan.get("force")))
            results.append({"kind": "model", **res})
            installed_ids.append(entry.id)
        except VidrouteError as exc:
            if exc.code == "cancelled":
                raise
            failed = True
            results.append({"kind": "model", "id": entry.id, "status": "failed", "detail": exc.message,
                            "errors": (exc.data or {}).get("errors")})

    smoke_results: dict[str, Any] = {}
    if smoke:
        for mid in installed_ids:
            ctx.progress("smoke", None, None, mid)
            smoke_results[mid] = run_smoke(mid, ctx, manifest)
            if smoke_results[mid]["status"] == "failed":
                failed = True
                ctx.warn("smoke_failed", f"Smoke test failed for {mid}: {smoke_results[mid].get('detail')}",
                         f"vidroute install --model {mid} --force --yes")
            elif smoke_results[mid]["status"] == "fallback_cpu":
                ctx.warn("gpu_fallback", f"{mid}: {smoke_results[mid]['detail']}")

    from vidroute.core import doctor

    doctor.invalidate()
    if any(r["kind"] == "env" and r["status"] == "created" for r in results):
        shutil.rmtree(paths.cache_dir() / "uv", ignore_errors=True)  # wheels are copied, the cache is redundant
    out = {"results": results, "smoke": smoke_results, "ok": not failed}
    if failed:
        raise RunFailed("Install finished with errors.", code="install_failed",
                        hint="vidroute install --yes   (re-run resumes; see data.results)", data=out)
    return out
