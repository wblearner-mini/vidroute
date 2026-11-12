"""Shell 1: the command-line interface.

Thin wrapper over :mod:`vidroute.api`: parse arguments, call the kernel, print
the envelope. With ``--json`` stdout carries exactly one JSON document; all
human-readable logging and progress goes to stderr.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable

from vidroute import __version__, api
from vidroute.core.context import Ctx
from vidroute.core.envelope import Envelope, failure
from vidroute.core.errors import UsageError
from vidroute.core.log import get_logger, set_verbosity
from vidroute.core.util.jsonio import dumps

_ARGPARSE_ZH = {
    "usage: ": "用法：",
    "%(heading)s:": "%(heading)s：",
    "positional arguments": "位置参数",
    "options": "选项",
    "optional arguments": "选项",
    "subcommands": "子命令",
    "show this help message and exit": "显示帮助并退出",
    "show program's version number and exit": "显示版本号并退出",
    "the following arguments are required: %s": "缺少必需的参数：%s",
    "unrecognized arguments: %s": "无法识别的参数：%s",
    "invalid choice: %(value)r (choose from %(choices)s)": "无效的选项：%(value)r（可选：%(choices)s）",
    "invalid %(type)s value: %(value)r": "无效的 %(type)s 值：%(value)r",
    "expected one argument": "需要一个参数",
    "argument %(argument_name)s: %(message)s": "参数 %(argument_name)s：%(message)s",
}
_argparse_gettext = getattr(argparse, "_", None)
if callable(_argparse_gettext):
    # argparse looks its messages up through the module-level gettext alias at call time.
    argparse._ = lambda message: _ARGPARSE_ZH.get(message) or _argparse_gettext(message)  # type: ignore[attr-defined]

Handler = Callable[[argparse.Namespace, Ctx], Envelope]
Renderer = Callable[[Envelope, argparse.Namespace], str]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise UsageError(f"{self.prog}: {message}", hint=f"{self.prog} --help")


def _common_flags(parser: argparse.ArgumentParser, suppress: bool) -> None:
    default: Any = argparse.SUPPRESS if suppress else False
    parser.add_argument("--json", action="store_true", default=default, help="在 stdout 输出 JSON 外壳")
    parser.add_argument("-v", "--verbose", action="store_true", default=default, help="在 stderr 输出调试日志")
    parser.add_argument("-q", "--quiet", action="store_true", default=default, help="stderr 只输出警告和错误")


# ----------------------------------------------------------------- renderers


def render_generic(env: Envelope, args: argparse.Namespace) -> str:
    return dumps(env.data)


def render_config(env: Envelope, args: argparse.Namespace) -> str:
    data = env.data
    if "config" in data:
        lines = [f"# {data['path']}"]
        lines += [f"{k} = {dumps(v, indent=None)}" for k, v in sorted(data["config"].items())]
        return "\n".join(lines)
    return f"{data['key']} = {dumps(data.get('value'), indent=None)}"


def _yes(flag: object) -> str:
    return "是" if flag else ("否" if flag is not None else "?")


def _usable(cpu: dict[str, Any]) -> str:
    usable = cpu.get("usable_cores")
    return f"（可用 {usable}）" if usable and usable != cpu.get("logical_cores") else ""


def render_doctor(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    cpu = d.get("cpu") or {}
    ff = d.get("ffmpeg") or {}
    lines = [
        f"vidroute doctor  （{'缓存' if d.get('cached') else '最新'}，{d.get('probe_seconds')} 秒）",
        f"  系统     {d.get('os', {}).get('system')} {d.get('os', {}).get('release')} {d.get('os', {}).get('machine')}"
        f" | python {d.get('os', {}).get('python')}",
        f"  CPU      {cpu.get('model') or '?'} | {cpu.get('logical_cores')} 线程{_usable(cpu)} | AVX2 {_yes(cpu.get('avx2'))}",
        f"  内存     {d.get('ram_gb')} GB{'（容器限额）' if d.get('ram_limited') else ''}（可用 {d.get('ram_available_gb')} GB）| 磁盘剩余 {d.get('disk_free_gb')} GB",
        f"  ffmpeg   {('已找到 ' + str(ff.get('version'))) if ff.get('found') else '缺失 -> ' + str(ff.get('install_hint'))}",
    ]
    gpus = d.get("gpus") or []
    if gpus:
        for g in gpus:
            vram = g.get("vram_gb") if g.get("vram_gb") is not None else g.get("vram_approx_gb")
            lines.append(f"  GPU      {g.get('name')} | 显存 {vram if vram is not None else '?'} GB（{g.get('vram_source')}）")
    else:
        lines.append("  GPU      未检测到")
    uv = (d.get("runtimes") or {}).get("uv") or {}
    lines.append(f"  uv       {uv.get('version') if uv.get('found') else '未找到（需要时自动安装）'}")
    for name, e in ((d.get("runtimes") or {}).get("envs") or {}).items():
        status = "就绪" if e.get("exists") and e.get("import_ok") else ("已损坏" if e.get("exists") else "未安装")
        lines.append(f"  环境     {name}：{status}")
    lines.append(f"  档位     {d.get('tier')}  （{'；'.join(d.get('tier_reasons') or [])}）")
    for cap, info in (d.get("capabilities") or {}).items():
        state_txt = ", ".join(info.get("models") or []) or "缺失"
        lines.append(f"  {cap:<8} {state_txt}" + (f"  -> {info['hint']}" if info.get("hint") else ""))
    ext = d.get("external") or {}
    local = [k for k, v in ext.items() if v.get("running")]
    lines.append(f"  本地服务 {', '.join(local) if local else '没有运行中的 Ollama / LM Studio 服务'}")
    net = d.get("network") or {}
    if net.get("checked"):
        mir = ", ".join(f"{k} {'可用' if v.get('ok') else '不可达'}" for k, v in (net.get("mirrors") or {}).items())
        lines.append(f"  网络     {mir}")
    for h in (d.get("integrations") or {}).get("hosts") or []:
        skill = h.get("skill")
        if skill is None:
            seen = "没有 vidroute 技能 -> vidroute setup"
        elif skill.get("plugin"):
            seen = "技能来自 vidroute 插件"
        else:
            if skill.get("managed"):
                state_txt = "最新" if skill.get("current") else "已过时 -> vidroute setup"
            elif skill.get("is_vidroute"):
                state_txt = "手动复制的，setup 不会更新它"
            else:
                state_txt = f"不是 vidroute：另一个技能占用了这个名字 -> vidroute setup --force --no-install --host {h['id']}"
            seen = f"技能 {skill['path']}（{state_txt}）"
        mcp = f"；MCP 方式 {h['mcp']['method']}" if h.get("mcp") else ""
        lines.append(f"  Agent    {h['name']}：{seen}{mcp}" + ("" if h.get("detected") else "（未找到该宿主）"))
    return "\n".join(lines)


def render_install(env: Envelope, args: argparse.Namespace) -> str:
    from vidroute.core.install.planner import describe

    d = env.data
    lines = [describe(d["plan"])] if d.get("plan") else []
    if d.get("message"):
        lines.append(d["message"])
    if d.get("executed"):
        lines.append("结果：")
        for r in d.get("results", []):
            lines.append(f"  {r.get('kind'):<6} {r.get('id', ''):<34} {r.get('status')}"
                         + (f"  {r.get('source')}" if r.get("source") else ""))
        for mid, sm in (d.get("smoke") or {}).items():
            lines.append(f"  冒烟   {mid:<34} {sm.get('status')}  {sm.get('detail', '')}")
    elif d.get("plan") and not d.get("message"):
        lines.append(f"仅预览，未执行。要安装请运行：{d['plan']['confirm_command']}")
    return "\n".join(lines)


def _describe_integration(integ: dict[str, Any]) -> list[str]:
    lines = []
    names = ", ".join(h["name"] for h in integ.get("hosts") or []) or "无"
    lines.append(f"Agent 宿主（{integ.get('mode')}）：{names}")
    for i in integ.get("items") or []:
        if i["kind"] == "skill":
            lines.append(f"  技能   {i['action']:<8} {i['path']}  [{', '.join(i['hosts'])}]  {i['reason']}")
        else:
            lines.append(f"  mcp    {i['action']:<8} {i['host']}: {i.get('target') or i['method']}  {i['reason']}")
            if i.get("manual"):
                lines.append(f"         手动操作：{i['manual']}")
    for n in integ.get("notes") or []:
        lines.append(f"  注意：{n}")
    return lines


def describe_setup(plan: dict[str, Any]) -> str:
    from vidroute.core.install.planner import describe

    lines = []
    if plan.get("install"):
        lines.append(describe(plan["install"]))
    elif plan.get("install") is None:
        lines.append("安装：已跳过（--no-install）")
    lines += _describe_integration(plan["integration"])
    if plan.get("selftest"):
        lines.append("然后：用生成的 6 秒样片做一次端到端自检。")
    return "\n".join(lines)


def render_setup(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    if "integration" not in (d.get("plan") or {}):  # setup --remove
        rows = d.get("results") or (d.get("plan") or {}).get("items") or []
        return "\n".join(f"  {r['kind']:<6} {r.get('status') or r['action']:<8} {r.get('path') or r.get('host')}"
                         f"  {r.get('reason', '')}" for r in rows) or "vidroute 还没有向任何 Agent 宿主添加内容。"
    lines = [describe_setup(d["plan"])] if d.get("plan") else []
    if not d.get("executed") and d.get("plan") and not d["plan"].get("nothing_to_do") and "selftest" not in d:
        lines.append(f"仅预览，未执行。要执行请运行：{d['plan']['confirm_command']}")
    inst = d.get("install")
    if isinstance(inst, dict):
        lines.append("安装结果：")
        for r in inst.get("results", []):
            lines.append(f"  {r.get('kind'):<6} {r.get('id', ''):<34} {r.get('status')}")
    integ = d.get("integration")
    if isinstance(integ, dict) and integ.get("results"):
        lines.append("Agent 宿主：")
        for r in integ["results"]:
            where = r.get("path") if r["kind"] == "skill" else f"{r['host']} ({r.get('target') or r['method']})"
            lines.append(f"  {r['kind']:<6} {r.get('status'):<10} {where}")
    st = d.get("selftest")
    if isinstance(st, dict):
        lines.append(f"自检：{st['status']}" + (f"，用时 {st.get('seconds')} 秒" if st.get("seconds") is not None else "")
                     + (f"（{st['reason']}）" if st.get("reason") else ""))
        for c in st.get("checks") or []:
            lines.append(f"  {'通过' if c['ok'] else '失败'} {c['check']:<10} {c['detail']}")
    for step in d.get("next_steps") or []:
        lines.append(f"下一步：{step}")
    return "\n".join(lines)


def render_models(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    if "models" in d:
        lines = [f"{'id':<36} {'能力':<5} {'状态':<12} {'大小':>8}  {'许可':<28} 已安装"]
        for m in d["models"]:
            lines.append(f"{m['id']:<36} {m['capability']:<5} {m['status']:<12} {m['size_mb']:>6.0f}MB  "
                         f"{m['license']:<28} {'是' if m['installed'] else '-'}")
        return "\n".join(lines)
    if "results" in d:
        return "\n".join(f"{r['id']:<36} {'正常' if r['ok'] else '失败：' + '；'.join(r.get('problems', []))}"
                         for r in d["results"]) or "没有安装任何模型"
    return dumps(d)


def render_resolve(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    if "effective" not in d:
        return dumps(d)
    llm, host, eff = d["llm"], d["host"], d["effective"]
    flags = [m for m in ("image", "video", "audio") if eff.get(m)]
    lines = [
        f"LLM   {llm['model']} -> 档案 {llm['matched']}（{', '.join(llm['modalities_in'])}；"
        f"上下文 {llm['context_tokens']:,}；{'未核实' if llm['unverified'] else '核实于 ' + str(llm['verified_at'])}）",
        f"宿主  {host['id']}（来源 {host['source']}，可信度 {host.get('confidence')}）：image={host.get('can_attach_image')} "
        f"video={host.get('can_attach_video')}/{host.get('video_delivery')} audio={host.get('can_attach_audio')}",
        f"有效能力  text + {', '.join(flags) or '无其他'}" + ("  （视频含音频）" if eff.get("audio_in_video") else ""),
    ]
    for b in eff.get("blocked_by_host") or []:
        lines.append(f"  被挡住：{b['modality']}：{b['why']}")
    if eff.get("video_limits"):
        v = eff["video_limits"]
        lines.append(f"  视频限额：{v['max_seconds']} 秒，每个文件 {v['max_file_mb']} MB，每次请求 {v['max_files']} 个文件")
    if eff.get("image_limits"):
        i = eff["image_limits"]
        lines.append(f"  图片限额：{i['max_images']} 张，每张 {i['max_file_mb']} MB")
    lines.append("  来源：" + ", ".join(f"{k}={v}" for k, v in llm["sources"].items()))
    return "\n".join(lines)


def render_probe(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    v = d.get("video") or {}
    lines = [
        f"{d['name']}：{d['duration']:.1f} 秒，{d['size_mb']} MB，{d.get('format')}",
        f"  视频    {v.get('codec')} {v.get('width')}x{v.get('height')} @ {v.get('fps')} fps" if v else "  视频    无",
        f"  音频    {'有' if d['has_audio'] else '无'}；speech_ratio {d['speech_ratio']}（{d['speech']['method']}）",
        f"  文字    text_density {d['text_density']}（{d['pictures'].get('method', 'n/a')}）；visual_change {d['visual_change']}",
        f"  字幕    {', '.join(s['codec'] + '/' + str(s.get('language')) for s in d['subtitles']) or '无'}",
        f"  探测    {d['probe_seconds']} 秒{'（缓存）' if d.get('cached') else ''}",
    ]
    return "\n".join(lines)


def render_plan(env: Envelope, args: argparse.Namespace) -> str:
    from vidroute.core.plan import summarize

    d = env.data
    lines = summarize(d)
    lines.append("步骤：")
    for st in d["steps"]:
        lines.append(f"  {st['id']:<16} {st['status']:<13} {str(st['engine'] or '-'):<28} ~{st['est_seconds']:>6}s  {st['reason']}")
    for n in d.get("needs_confirmation") or []:
        lines.append(f"需要确认：{n['message']}")
    for n in d.get("needs_install") or []:
        lines.append(f"需要安装（{n['capability']}）：{n['hint']}")
    if d.get("plan_path"):
        lines.append(f"计划已写入 {d['plan_path']}")
    return "\n".join(lines)


def render_prepare(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    h = d["handoff"]
    lines = [f"路线：{d['route']}  ->  {d['out_dir']}"]
    for st in d["steps"]:
        lines.append(f"  {st['id']:<17} {st['status']:<8} {st['detail']}")
    attach = [a["path"] for a in h["attachments"]] + [t["path"] for t in h["text_context"] if t["attach"]]
    lines.append(f"上传原始视频：{'是' if h['uploads_raw_video'] else '否'}")
    lines.append(f"附带：{', '.join(attach) or '（无）'}  （约 {h['budget']['est_input_tokens']:,} 输入 token）")
    lines.append(f"下一步：阅读 {d['summary_path']}")
    return "\n".join(lines)


def render_drill(env: Envelope, args: argparse.Namespace) -> str:
    d = env.data
    lines = [f"钻取 {d['range'][0]:.1f}-{d['range'][1]:.1f} 秒  ->  {d['drill_dir']}",
             f"  帧：{len(d['frames'])}（{d['sampling'].get('method', '-')}）"]
    for a in d["attachments"]:
        lines.append(f"  附带：{a['path']}  （{a['note']}）")
    for t in d["text_context"]:
        lines.append(f"  {t['type']}：{t['path']}" + ("" if t["attach"] else "  （可选）"))
    return "\n".join(lines)


# ------------------------------------------------------------------ handlers


def h_version(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.version(ctx=ctx)


def h_doctor(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.doctor(refresh=args.refresh, offline=True if args.offline else None, ctx=ctx)


def _confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes", "是", "好")


def _unconfirmed_plan(env: Envelope, args: argparse.Namespace) -> bool:
    """No terminal to ask on and no JSON to read it from: the plan must still be shown."""
    return (not env.ok and bool(env.error) and env.error.get("code") == "needs_confirmation"
            and bool(env.data.get("plan")) and not getattr(args, "json", False))


def h_install(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    from vidroute.core.install.planner import describe

    kwargs = dict(capabilities=args.capability, profile=args.profile, models=args.model, mirror=args.mirror,
                  accept_licenses=args.accept_license, dry_run=args.dry_run, force=args.force, smoke=not args.no_smoke)
    env = api.install(yes=args.yes, ctx=ctx, **kwargs)
    # Interactive terminals get a prompt instead of an exit-3 envelope; the kernel stays the decision point.
    if (not env.ok and env.error and env.error.get("code") == "needs_confirmation" and ctx.interactive
            and env.data.get("plan")):
        sys.stderr.write(describe(env.data["plan"]) + "\n")
        if _confirm("按这个计划安装吗？"):
            ctx.warnings.clear()
            env = api.install(yes=True, ctx=ctx, **kwargs)
    elif _unconfirmed_plan(env, args):
        sys.stderr.write(describe(env.data["plan"]) + "\n")  # scripts cannot confirm what they cannot see
    return env


def h_setup(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    if args.remove:
        env = api.setup_remove(args.host, yes=args.yes, dry_run=args.dry_run, ctx=ctx)
        if (not env.ok and env.error and env.error.get("code") == "needs_confirmation" and ctx.interactive):
            sys.stderr.write(render_setup(Envelope(True, env.data), args) + "\n")
            if _confirm("从你的 Agent 宿主中移除这些内容吗？"):
                ctx.warnings.clear()
                env = api.setup_remove(args.host, yes=True, ctx=ctx)
        return env
    kwargs = dict(hosts=args.host, skill=not args.no_skill, mcp=args.mcp, install=not args.no_install,
                  capabilities=args.capability, profile=args.profile, models=args.model, mirror=args.mirror,
                  accept_licenses=args.accept_license, selftest=not args.no_selftest, dry_run=args.dry_run,
                  force=args.force)
    env = api.setup(yes=args.yes, ctx=ctx, **kwargs)
    if (not env.ok and env.error and env.error.get("code") == "needs_confirmation" and ctx.interactive
            and env.data.get("plan")):
        sys.stderr.write(describe_setup(env.data["plan"]) + "\n")
        if _confirm("按这个计划执行吗？"):
            ctx.warnings.clear()
            env = api.setup(yes=True, ctx=ctx, **kwargs)
    elif _unconfirmed_plan(env, args):
        sys.stderr.write(describe_setup(env.data["plan"]) + "\n")
    return env


def h_models(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    action = args.action or "list"
    if action == "list":
        return api.models_list(ctx=ctx)
    if action == "verify":
        return api.models_verify(args.ids or None, ctx=ctx)
    yes = args.yes or (ctx.interactive and _confirm(f"删除 {', '.join(args.ids)} 吗？"))
    return api.models_remove(args.ids, yes=yes, ctx=ctx)


def h_clean(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.clean(outputs=args.outputs, yes=args.yes, ctx=ctx)


def h_llm(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    action = args.action or "list"
    if action == "resolve":
        return api.llm_resolve(args.llm, args.host, args.modalities, args.audio_in_video, ctx=ctx)
    if action == "set":
        return api.llm_set(args.model_id, modalities=args.modalities, audio_in_video=args.audio_in_video,
                           video_max_seconds=args.video_max_seconds, video_max_mb=args.video_max_mb,
                           video_tokens_per_second=args.video_tokens_per_second, max_images=args.max_images,
                           context_tokens=args.context_tokens, notes=args.notes, ctx=ctx)
    if action == "unset":
        return api.llm_unset(args.model_id, ctx=ctx)
    if action == "sync":
        return api.llm_sync(args.provider, ctx=ctx)
    return api.llm_list(ctx=ctx)


def h_host(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    action = args.action or "list"
    if action == "set":
        return api.host_set(args.host_id, attach_image=args.attach_image, attach_video=args.attach_video,
                            attach_audio=args.attach_audio, video_delivery=args.video_delivery,
                            video_includes_audio=args.video_includes_audio, max_attachment_mb=args.max_attachment_mb,
                            notes=args.notes, ctx=ctx)
    if action == "unset":
        return api.host_unset(args.host_id, ctx=ctx)
    return api.host_list(ctx=ctx)


def h_probe(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.probe(args.video, refresh=args.refresh, ctx=ctx)


def h_plan(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.plan(args.video, args.llm, args.host, args.task, args.prefer, args.language, args.modalities,
                    args.audio_in_video, args.ocr, args.route, args.force_asr, args.out, args.diarize, ctx=ctx)


def _with_handoff_consent(call: Callable[[bool], Envelope], ctx: Ctx, accepted: bool) -> Envelope:
    env = call(accepted)
    # Interactive terminals get a prompt instead of an exit-3 envelope; agents must ask their user themselves.
    if (not env.ok and env.error and env.error.get("code") == "raw_video_handoff" and ctx.interactive):
        sys.stderr.write((env.error.get("message") or "") + "\n")
        if _confirm("把原始视频交给 LLM 服务商吗？"):
            ctx.warnings.clear()
            env = call(True)
    return env


def h_prepare(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    def call(accept: bool) -> Envelope:
        return api.prepare(args.video, args.plan, args.route, args.out, accept, args.llm, args.host, args.task,
                           args.prefer, args.language, args.modalities, args.audio_in_video, args.ocr, args.force_asr,
                           args.diarize, ctx=ctx)

    return _with_handoff_consent(call, ctx, args.accept_raw_video_handoff)


def h_run(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    def call(accept: bool) -> Envelope:
        return api.run(args.video, args.llm, args.host, args.task, args.prefer, args.language, args.modalities,
                       args.audio_in_video, args.ocr, args.route, args.force_asr, args.out, accept, args.diarize, ctx=ctx)

    return _with_handoff_consent(call, ctx, args.accept_raw_video_handoff)


def h_drill(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.drill(args.out_dir, args.range, fps=args.fps, asr=args.asr, ocr=args.ocr, max_frames=args.max_frames,
                     ctx=ctx)


def h_skill(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    return api.skill_path(ctx=ctx)


def render_skill(env: Envelope, args: argparse.Namespace) -> str:
    return env.data["path"]


def h_config(args: argparse.Namespace, ctx: Ctx) -> Envelope:
    from vidroute.core.config import parse_value

    action = args.action or "get"
    if action == "get":
        return api.config_get(getattr(args, "key", None), ctx=ctx)
    if action == "set":
        return api.config_set(args.key, parse_value(args.value), ctx=ctx)
    return api.config_unset(args.key, ctx=ctx)


# -------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="vidroute",
        description=(
            "让编程 Agent 能看、能听本地视频：决定把什么交给 Agent 的 LLM"
            "（原生视频、关键帧 + 转写，或纯文本），并在本机准备好。"
        ),
    )
    _common_flags(parser, suppress=False)
    parser.add_argument("--version", action="version", version=f"vidroute {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="命令", parser_class=_Parser)

    def add(name: str, help_text: str, handler: Handler, render: Renderer = render_generic) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        _common_flags(p, suppress=True)
        p.set_defaults(handler=handler, render=render)
        return p

    add("version", "打印版本和数据目录", h_version)

    p = add("doctor", "检测系统、CPU/GPU、内存、ffmpeg、运行时和已装模型，确定本地档位",
            h_doctor, render_doctor)
    p.add_argument("--refresh", action="store_true", help="忽略缓存结果，重新检测")
    p.add_argument("--offline", action="store_true", help="跳过下载镜像的连通性检查")

    p = add("install", "生成计划，并（经确认后）安装本地模型和运行时", h_install, render_install)
    p.add_argument("--capability", action="append", choices=["asr", "vad", "ocr", "scene", "diarize"], help="要安装的能力（可重复）")
    p.add_argument("--profile", choices=["auto", "lite", "full"], default="auto", help="未指定 --capability/--model 时使用的套餐")
    p.add_argument("--model", action="append", help="`vidroute models list` 中的确切模型 id（可重复）")
    p.add_argument("--mirror", choices=["auto", "cn", "github", "hf", "modelscope"],
                   help="首选下载来源（cn：软件包、Python 和模型使用中国大陆镜像）")
    p.add_argument("--accept-license", action="append", metavar="ID", help="接受受限的模型许可（可重复）")
    p.add_argument("--yes", "-y", action="store_true", help="以非交互方式确认计划")
    p.add_argument("--dry-run", "--plan", dest="dry_run", action="store_true", help="只显示计划")
    p.add_argument("--force", action="store_true", help="已安装也重新安装")
    p.add_argument("--no-smoke", action="store_true", help="安装后跳过冒烟测试")

    p = add("setup", "一步装好 vidroute：ffmpeg、本地模型和运行时、Agent 宿主中的技能"
                     "（加 --mcp 还有 MCP），最后自检——只需确认一次", h_setup, render_setup)
    p.add_argument("--host", action="append", metavar="ID",
                   help="要接入的 Agent 宿主（可重复）：auto（默认：找到的所有宿主）、none、claude-code、"
                        "codex-cli、gemini-cli、qwen-code、opencode、cursor、cline、roo、kilo、openclaw")
    p.add_argument("--mcp", action="store_true", help="同时在这些宿主中注册 vidroute MCP 服务")
    p.add_argument("--no-skill", action="store_true", help="不把 Agent Skill 复制到宿主中")
    p.add_argument("--no-install", action="store_true", help="跳过 ffmpeg、运行时和模型")
    p.add_argument("--capability", action="append", choices=["asr", "vad", "ocr", "scene", "diarize"],
                   help="要安装的能力（默认：asr、vad、ocr、scene）")
    p.add_argument("--profile", choices=["auto", "lite", "full"], default="auto", help="用套餐代替 --capability")
    p.add_argument("--model", action="append", help="`vidroute models list` 中的确切模型 id（可重复）")
    p.add_argument("--mirror", choices=["auto", "cn", "github", "hf", "modelscope"],
                   help="首选下载来源（cn：中国大陆镜像）")
    p.add_argument("--accept-license", action="append", metavar="ID", help="接受受限的模型许可")
    p.add_argument("--no-selftest", action="store_true", help="跳过端到端自检")
    p.add_argument("--remove", action="store_true",
                   help="把 vidroute 从 Agent 宿主中撤下（模型和运行时保留）")
    p.add_argument("--yes", "-y", action="store_true", help="以非交互方式确认计划")
    p.add_argument("--dry-run", "--plan", dest="dry_run", action="store_true", help="只显示计划")
    p.add_argument("--force", action="store_true",
                   help="重新安装，并把不是 vidroute 创建的技能目录或 MCP 配置项挪到一边")

    p = add("models", "列出、校验（sha256）或删除已安装的模型", h_models, render_models)
    msub = p.add_subparsers(dest="action", metavar="操作", parser_class=_Parser)
    m = msub.add_parser("list", help="所有已知模型及其安装状态")
    _common_flags(m, suppress=True)
    m = msub.add_parser("verify", help="重新计算已安装文件的哈希")
    _common_flags(m, suppress=True)
    m.add_argument("ids", nargs="*")
    m = msub.add_parser("remove", help="删除已安装的模型")
    _common_flags(m, suppress=True)
    m.add_argument("ids", nargs="+")
    m.add_argument("--yes", "-y", action="store_true")

    p = add("clean", "删除 VIDROUTE_HOME 中的下载缓存和日志", h_clean)
    p.add_argument("--outputs", action="store_true", help="同时删除 $VIDROUTE_HOME/out")
    p.add_argument("--yes", "-y", action="store_true")

    def sub_parser(subs: Any, name: str, help_text: str) -> argparse.ArgumentParser:
        sp = subs.add_parser(name, help=help_text, description=help_text)
        _common_flags(sp, suppress=True)
        return sp

    def llm_flags(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--modalities", help="用逗号分隔的列表覆盖档案，例如 text,image,video,audio")
        sp.add_argument("--audio-in-video", choices=["yes", "no"], help="模型能否听到视频中的音轨？")

    p = add("llm", "解析 LLM × 宿主的能力，或覆盖/同步 LLM 档案", h_llm, render_resolve)
    lsub = p.add_subparsers(dest="action", metavar="操作", parser_class=_Parser)
    sp = sub_parser(lsub, "resolve", "某个 LLM 在某个宿主上的有效能力")
    sp.add_argument("--llm", required=True, help="Agent 使用的模型 id（例如 claude-opus-5-5）")
    sp.add_argument("--host", help="Agent 宿主 id（claude-code、codex-cli、gemini-cli、qwen-code、opencode……）")
    llm_flags(sp)
    sp = sub_parser(lsub, "set", "在 config.json 中覆盖某个模型的档案")
    sp.add_argument("model_id", help="模型 id 或通配符（通配符请加引号）")
    llm_flags(sp)
    sp.add_argument("--video-max-seconds", type=float)
    sp.add_argument("--video-max-mb", type=float, help="每个视频原始文件的最大大小")
    sp.add_argument("--video-tokens-per-second", type=float)
    sp.add_argument("--max-images", type=int)
    sp.add_argument("--context-tokens", type=int)
    sp.add_argument("--notes")
    sp = sub_parser(lsub, "unset", "删除用户覆盖")
    sp.add_argument("model_id")
    sub_parser(lsub, "list", "内置档案和用户档案")
    sp = sub_parser(lsub, "sync", "下载公开的模型列表以了解模态（需显式执行）")
    sp.add_argument("--provider", default="openrouter", choices=["openrouter"])

    p = add("host", "覆盖或列出 Agent 宿主档案", h_host)
    hsub = p.add_subparsers(dest="action", metavar="操作", parser_class=_Parser)
    sp = sub_parser(hsub, "set", "覆盖宿主能附带的内容")
    sp.add_argument("host_id")
    sp.add_argument("--attach-image", choices=["yes", "no"])
    sp.add_argument("--attach-video", choices=["yes", "no"])
    sp.add_argument("--attach-audio", choices=["yes", "no"])
    sp.add_argument("--video-delivery", choices=["native", "frame-sampled", "preprocessed", "none"])
    sp.add_argument("--video-includes-audio", choices=["yes", "no"])
    sp.add_argument("--max-attachment-mb", type=float)
    sp.add_argument("--notes")
    sp = sub_parser(hsub, "unset", "删除用户覆盖")
    sp.add_argument("host_id")
    sub_parser(hsub, "list", "内置宿主档案和用户宿主档案")

    p = add("probe", "快速获取视频信息：流、语音占比、文字密度、画面变化", h_probe, render_probe)
    p.add_argument("video")
    p.add_argument("--refresh", action="store_true", help="忽略缓存的探测结果")

    p = add("plan", "为视频生成两层路由计划（加 --out 时写出 plan.json）", h_plan, render_plan)
    p.add_argument("video")
    p.add_argument("--llm", help="Agent 使用的模型 id（或配置 default_llm）")
    p.add_argument("--host", help="Agent 宿主 id（或配置 default_host / 自动检测）")
    p.add_argument("--task", default="summary", choices=["minutes", "summary", "qa", "transcript"])
    p.add_argument("--prefer", default="quality", choices=["quality", "privacy", "cost"])
    p.add_argument("--language", default="auto", help="说话的语言（zh、en……）或 auto")
    p.add_argument("--ocr", default="auto", choices=["auto", "on", "off"])
    p.add_argument("--route", default="auto", choices=["auto", "native-av", "native-v", "frames", "text"])
    p.add_argument("--force-asr", action="store_true", help="即使有字幕/没检测到语音也运行 ASR")
    p.add_argument("--diarize", choices=["auto", "on", "off"], default="auto", help="在转写中标出说话人；auto：仅 --task minutes 时")
    p.add_argument("--out", help="输出根目录；plan.json 写到 <out>/<slug>/plan.json")
    llm_flags(p)

    def routing_flags(sp: argparse.ArgumentParser, task_default: str | None = "summary") -> None:
        sp.add_argument("--llm", help="Agent 使用的模型 id（或配置 default_llm）")
        sp.add_argument("--host", help="Agent 宿主 id（或配置 default_host / 自动检测）")
        sp.add_argument("--task", default=task_default, choices=["minutes", "summary", "qa", "transcript"])
        sp.add_argument("--prefer", default="quality", choices=["quality", "privacy", "cost"])
        sp.add_argument("--language", default="auto", help="说话的语言（zh、en……）或 auto")
        sp.add_argument("--ocr", default="auto", choices=["auto", "on", "off"])
        sp.add_argument("--route", default="auto", choices=["auto", "native-av", "native-v", "frames", "text"])
        sp.add_argument("--force-asr", action="store_true", help="即使有字幕/没检测到语音也运行 ASR")
        sp.add_argument("--diarize", choices=["auto", "on", "off"], default="auto", help="在转写中标出说话人；auto：仅 --task minutes 时")
        sp.add_argument("--out", help="输出根目录（默认：配置 out_root 或 $VIDROUTE_HOME/out）；"
                                      "文件写到 <out>/<slug>/")
        sp.add_argument("--accept-raw-video-handoff", action="store_true",
                        help="用户已同意把原始视频/音频交给 LLM 服务商")
        llm_flags(sp)

    p = add("prepare", "准备 Agent 要交给 LLM 的材料（帧、转写、OCR、handoff.json）", h_prepare,
            render_prepare)
    p.add_argument("video")
    p.add_argument("--plan", help="由 'vidroute plan --out' 生成的 plan.json（默认：存在时用 <out>/<slug>/plan.json）")
    routing_flags(p)

    p = add("run", "一次完成 plan + prepare", h_run, render_prepare)
    p.add_argument("video")
    routing_flags(p)

    p = add("drill", "对已准备视频的某个时间段补充更多帧 / 重新转写 / OCR", h_drill,
            render_drill)
    p.add_argument("out_dir", help="该视频的输出目录（<out>/<slug>，即 handoff.json 所在处）")
    p.add_argument("--range", required=True, help="全局时间的 开始-结束，例如 01:20-02:00 或 80-120")
    p.add_argument("--fps", type=float, help="用固定帧率代替按变化取帧（最大 10）")
    p.add_argument("--asr", action="store_true", help="重新转写该时间段")
    p.add_argument("--ocr", action="store_true", help="读取钻取帧上的屏幕文字")
    p.add_argument("--max-frames", type=int, help="该时间段的帧预算（默认 24，用 --fps 时为 120）")

    p = add("skill", "打印内置 Agent Skill 的目录（可复制到宿主的技能目录）",
            h_skill, render_skill)
    ssub = p.add_subparsers(dest="action", metavar="操作", parser_class=_Parser)
    sp = ssub.add_parser("path", help="打印技能目录")
    _common_flags(sp, suppress=True)

    p = add("config", "读取或设置用户配置（$VIDROUTE_HOME/config.json）", h_config, render_config)
    csub = p.add_subparsers(dest="action", metavar="操作", parser_class=_Parser)
    g = csub.add_parser("get", help="显示某个键或全部有效配置")
    _common_flags(g, suppress=True)
    g.add_argument("key", nargs="?")
    s = csub.add_parser("set", help="设置某个键（值尽量按 JSON 解析）")
    _common_flags(s, suppress=True)
    s.add_argument("key")
    s.add_argument("value")
    u = csub.add_parser("unset", help="删除某个键（恢复默认）")
    _common_flags(u, suppress=True)
    u.add_argument("key")

    return parser


# ---------------------------------------------------------------------- main


class _StderrProgress:
    """Throttled progress lines on stderr."""

    def __init__(self) -> None:
        self.last: dict[str, float] = {}
        self.log = get_logger()

    def __call__(self, stage: str, done: float | None, total: float | None, message: str | None) -> None:
        now = time.monotonic()
        final = done is not None and total is not None and done >= total
        if not final and now - self.last.get(stage, 0.0) < 1.0:
            return
        self.last[stage] = now
        parts = [f"[{stage}]"]
        if done is not None and total:
            parts.append(f"{done:.0f}/{total:.0f}")
        if message:
            parts.append(message)
        self.log.info(" ".join(parts))


def _emit(env: Envelope, args: argparse.Namespace | None, as_json: bool) -> None:
    if as_json:
        sys.stdout.write(dumps(env.to_dict()) + "\n")
        return
    log = get_logger()
    for w in env.to_dict()["warnings"]:
        log.warning("警告：%s%s", w["message"], f"（可尝试：{w['hint']}）" if w.get("hint") else "")
    if env.ok:
        render: Renderer = getattr(args, "render", render_generic) if args is not None else render_generic
        text = render(env, args) if args is not None else dumps(env.data)
        if text:
            sys.stdout.write(text + "\n")
    else:
        err = env.error or {}
        log.error("错误：%s", err.get("message"))
        if err.get("hint"):
            log.error("提示：%s", err["hint"])


def _configure_stdio() -> None:
    # Windows consoles may default to a legacy code page; never die on non-ASCII paths.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except UsageError as err:
        env = failure(err)
        _emit(env, None, as_json)
        return int(env.exit_code)
    set_verbosity(getattr(args, "verbose", False), getattr(args, "quiet", False))
    handler: Handler | None = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 1
    ctx = Ctx(on_progress=_StderrProgress(), interactive=sys.stdin.isatty() and not args.json)
    env = handler(args, ctx)
    _emit(env, args, bool(args.json))
    return int(env.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())
