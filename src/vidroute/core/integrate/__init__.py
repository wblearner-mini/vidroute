"""Connect vidroute to the agent hosts on this machine (part of ``vidroute setup``).

The skill is copied into the fewest user-level skill folders that reach every
selected host (``~/.agents/skills`` serves most of them); the MCP server is
registered only on request. Everything here writes outside VIDROUTE_HOME, so it
runs only as part of a plan the user confirmed, and state.json records what was
added (``integrations``) so that removal touches nothing else.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from vidroute.core import state
from vidroute.core.context import Ctx
from vidroute.core.integrate import hosts as hostmod
from vidroute.core.integrate import mcp as mcpmod
from vidroute.core.integrate import skills
from vidroute.core.util import proc
from vidroute.core.util.jsonio import read_json

STATE_KEY = "integrations"
CHANGES = ("create", "update", "replace", "add", "remove")

Which = Callable[[str], "Path | None"]


def _records() -> dict[str, Any]:
    rec = state.load().get(STATE_KEY)
    rec = rec if isinstance(rec, dict) else {}
    rec.setdefault("skills", {})
    rec.setdefault("mcp", {})
    return rec


def _save(rec: dict[str, Any]) -> None:
    state.update(STATE_KEY, rec)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def claude_plugin_installed() -> bool:
    """The vidroute Claude Code plugin (from any marketplace) already brings the skill."""
    data = read_json(hostmod.claude_dir() / "plugins" / "installed_plugins.json")
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return isinstance(plugins, dict) and any(str(k).split("@")[0] == skills.SKILL_NAME for k in plugins)


def select_hosts(requested: list[str] | None, which: Which = proc.which) -> tuple[list[str], str]:
    """Host ids to connect: the detected ones for ``auto`` (default), none for ``none``, else the named ones."""
    req = [r.strip().lower() for r in (requested or ["auto"]) if r and r.strip()]
    if req == ["none"]:
        return [], "none"
    ids: list[str] = []
    if "auto" in req:
        ids += hostmod.detected_ids(which)
    ids += [hostmod.get(r).id for r in req if r not in ("auto", "none")]
    order = [h.id for h in hostmod.HOSTS]
    return sorted(set(ids), key=order.index), "auto" if "auto" in req else "explicit"


def choose_roots(host_ids: list[str], skip: tuple[str, ...] = ()) -> dict[str, list[str]]:
    """Fewest skill folders that reach every host: single-folder hosts first, the others join one already chosen."""
    chosen: dict[str, list[str]] = {}
    flexible = []
    for hid in host_ids:
        if hid in skip:
            continue
        roots = hostmod.BY_ID[hid].skill_roots
        if len(roots) == 1:
            chosen.setdefault(roots[0], []).append(hid)
        else:
            flexible.append(hid)
    for hid in flexible:
        roots = hostmod.BY_ID[hid].skill_roots
        root = next((r for r in roots if r in chosen), roots[0])
        chosen.setdefault(root, []).append(hid)
    return {root: sorted(served, key=host_ids.index) for root, served in chosen.items()}


def _plan_skill(root: str, served: list[str], force: bool) -> dict[str, Any]:
    target = hostmod.SKILL_ROOTS[root]() / skills.SKILL_NAME
    st = skills.inspect(target)
    item = {"kind": "skill", "path": str(target), "hosts": served}
    if st.get("foreign"):
        if force:
            return {**item, "action": "replace",
                    "reason": "a vidroute folder vidroute did not create is there; it is renamed to vidroute.bak-*"}
        return {**item, "action": "skip",
                "reason": "a vidroute folder vidroute did not create is already there (rerun with --force to put it "
                          "aside and install the current skill)"}
    if st["ours"] and st["current"]:
        return {**item, "action": "keep", "reason": f"up to date (vidroute {st.get('version')})"}
    if st["ours"]:
        return {**item, "action": "update", "reason": f"installed by vidroute {st.get('version')}; refreshing"}
    return {**item, "action": "create", "reason": "skill folder for " + ", ".join(served)}


def _plan_mcp(host: hostmod.Host, cmd: list[str], record: dict[str, Any] | None, force: bool,
              which: Which) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": "mcp", "host": host.id, "method": host.mcp, "command": cmd}
    if host.mcp is None:
        return {**item, "action": "unsupported",
                "reason": f"{host.name}: no supported way to register MCP servers; the skill covers it"}
    if host.mcp in mcpmod.CLI_TARGETS:
        exe = mcpmod.cli_exe(host.mcp, which)
        if exe is None:
            return {**item, "action": "manual", "reason": f"`{mcpmod.CLI_TARGETS[host.mcp]['exe']}` is not on PATH",
                    "manual": mcpmod.snippet(host.mcp, cmd)}
        item["target"] = f"{exe} ({mcpmod.CLI_TARGETS[host.mcp]['where']})"
        exists = mcpmod.cli_has_server(host.mcp, exe)
        if not exists:
            return {**item, "action": "add", "reason": "register the vidroute MCP server"}
        if record is None:
            return {**item, "action": "update" if force else "skip",
                    "reason": "an MCP server named vidroute is already configured (not by vidroute)"}
        if record.get("command") == cmd:
            return {**item, "action": "keep", "reason": "already registered by vidroute"}
        return {**item, "action": "update", "reason": "registered by vidroute with an older command"}
    st = mcpmod.json_state(host.mcp)
    item["target"] = st["file"]
    if not st["editable"]:
        return {**item, "action": "manual", "reason": st["problem"], "manual": mcpmod.snippet(host.mcp, cmd)}
    want = mcpmod.JSON_TARGETS[host.mcp][2](cmd)
    entry = st["entry"]
    if entry is None:
        return {**item, "action": "add", "reason": "add the vidroute MCP server"}
    if entry == want:
        return {**item, "action": "keep", "reason": "already registered"}
    if (record is not None and entry == record.get("entry")) or force:
        return {**item, "action": "update", "reason": "replace the vidroute entry with the current command"}
    return {**item, "action": "skip", "reason": "a vidroute entry configured by hand is already there"}


def plan(requested: list[str] | None = None, skill: bool = True, mcp: bool = False, force: bool = False,
         which: Which = proc.which) -> dict[str, Any]:
    host_ids, mode = select_hosts(requested, which)
    plugin = "claude-code" in host_ids and claude_plugin_installed()
    items: list[dict[str, Any]] = []
    notes: list[str] = []
    if skill:
        roots = choose_roots(host_ids, skip=("claude-code",) if plugin else ())
        items += [_plan_skill(root, served, force) for root, served in roots.items()]
        if plugin:
            notes.append("Claude Code: the vidroute plugin is installed and already provides the skill.")
    if mcp:
        cmd = mcpmod.server_command()
        records = _records()["mcp"]
        items += [_plan_mcp(hostmod.BY_ID[hid], cmd, records.get(hid), force, which) for hid in host_ids]
    if not host_ids and mode == "auto":
        notes.append("No agent host was found on this machine; name one with --host (for example --host claude-code).")
    return {
        "hosts": [{"id": hid, "name": hostmod.BY_ID[hid].name, "reload": hostmod.BY_ID[hid].reload} for hid in host_ids],
        "mode": mode,
        "items": items,
        "notes": notes,
        "changes": sum(1 for i in items if i["action"] in CHANGES),
    }


def _apply_mcp(item: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    method, host, cmd = item["method"], item["host"], item["command"]
    if method in mcpmod.CLI_TARGETS:
        exe = mcpmod.cli_exe(method)
        if exe is None:
            return {**item, "status": "failed", "detail": "command not found", "manual": mcpmod.snippet(method, cmd)}
        if item["action"] == "update":
            mcpmod.cli_remove(method, exe)
        res = mcpmod.cli_add(method, exe, cmd)
        if not res.ok:
            tail = (res.stderr or res.stdout).strip()[-300:]
            return {**item, "status": "failed", "detail": tail, "manual": mcpmod.snippet(method, cmd)}
        rec["mcp"][host] = {"method": method, "command": cmd, "target": item.get("target"), "installed_at": _now()}
        return {**item, "status": "registered"}
    try:
        entry = mcpmod.json_set(method, cmd)
    except (OSError, ValueError) as exc:
        return {**item, "status": "failed", "detail": str(exc), "manual": mcpmod.snippet(method, cmd)}
    rec["mcp"][host] = {"method": method, "command": cmd, "file": item.get("target"), "entry": entry,
                        "installed_at": _now()}
    return {**item, "status": "registered"}


def apply(the_plan: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    rec = _records()
    results = []
    for item in the_plan["items"]:
        if item["action"] not in CHANGES:
            results.append({**item, "status": item["action"]})
            continue
        ctx.check_cancelled()
        if item["kind"] == "skill":
            ctx.progress("integrate", None, None, f"skill -> {item['path']}")
            try:
                res = skills.install(Path(item["path"]), force=item["action"] == "replace")
            except OSError as exc:
                results.append({**item, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
                continue
            if res["status"] in ("created", "updated"):
                rec["skills"][item["path"]] = {"hosts": item["hosts"], "version": res["version"], "installed_at": _now()}
            results.append({**item, **res})
        else:
            ctx.progress("integrate", None, None, f"MCP -> {item['host']}")
            results.append(_apply_mcp(item, rec))
    _save(rec)
    failed = [r for r in results if r.get("status") == "failed"]
    for r in failed:
        ctx.warn("integration_failed", f"Could not connect {r.get('host') or r.get('path')}: {r.get('detail')}",
                 r.get("manual"))
    touched = {h for r in results if r.get("status") in ("created", "updated", "registered")
               for h in (r.get("hosts") or [r.get("host")])}
    next_steps = [f"{h['name']}: {h['reload']}" for h in the_plan.get("hosts", []) if h["id"] in touched]
    return {"results": results, "ok": not failed, "next_steps": next_steps}


# ------------------------------------------------------------------- removal


def plan_remove(requested: list[str] | None = None) -> dict[str, Any]:
    """Every skill copy and MCP entry vidroute added (optionally only for the named hosts)."""
    only = {hostmod.get(r).id for r in requested or [] if r not in ("auto", "all")} or None
    rec = _records()
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root, getter in hostmod.SKILL_ROOTS.items():
        target = getter() / skills.SKILL_NAME
        served = rec["skills"].get(str(target), {}).get("hosts") or [
            h.id for h in hostmod.HOSTS if root in h.skill_roots]
        if only is not None and not (set(served) & only):
            continue
        if skills.read_marker(target) is not None:
            items.append({"kind": "skill", "path": str(target), "hosts": served, "action": "remove",
                          "reason": "skill folder created by vidroute"})
            seen.add(str(target))
    for path, info in rec["skills"].items():
        if path not in seen and (only is None or set(info.get("hosts") or []) & only) and Path(path).exists():
            if skills.read_marker(Path(path)) is not None:
                items.append({"kind": "skill", "path": path, "hosts": info.get("hosts") or [], "action": "remove",
                              "reason": "skill folder created by vidroute"})
    for host, info in rec["mcp"].items():
        if only is not None and host not in only:
            continue
        item = {"kind": "mcp", "host": host, "method": info.get("method"), "command": info.get("command"),
                "target": info.get("file") or info.get("target")}
        if info.get("method") in mcpmod.JSON_TARGETS:
            st = mcpmod.json_state(info["method"])
            if st["entry"] is None:
                items.append({**item, "action": "gone", "reason": "entry already removed"})
            elif st["entry"] != info.get("entry"):
                items.append({**item, "action": "skip", "reason": "the vidroute entry was changed by hand; left as is"})
            else:
                items.append({**item, "action": "remove", "reason": "MCP entry added by vidroute"})
        else:
            items.append({**item, "action": "remove", "reason": "MCP server registered by vidroute"})
    return {"items": items, "changes": sum(1 for i in items if i["action"] == "remove")}


def remove(the_plan: dict[str, Any], ctx: Ctx) -> dict[str, Any]:
    rec = _records()
    results = []
    for item in the_plan["items"]:
        if item["action"] != "remove":
            if item["action"] == "gone":
                rec["mcp"].pop(item["host"], None)
            results.append({**item, "status": item["action"]})
            continue
        if item["kind"] == "skill":
            res = skills.remove(Path(item["path"]))
            rec["skills"].pop(item["path"], None)
            results.append({**item, **res})
            continue
        method = item["method"]
        if method in mcpmod.CLI_TARGETS:
            exe = mcpmod.cli_exe(method)
            res_ok = exe is not None and mcpmod.cli_remove(method, exe).ok
            status = "removed" if res_ok else "failed"
        else:
            status = "removed" if mcpmod.json_remove(method) else "absent"
        if status != "failed":
            rec["mcp"].pop(item["host"], None)
        results.append({**item, "status": status})
    _save(rec)
    for r in results:
        if r.get("status") == "failed":
            ctx.warn("integration_remove_failed", f"Could not remove the vidroute MCP server from {r['host']}.")
    return {"results": results, "ok": all(r.get("status") != "failed" for r in results)}


# -------------------------------------------------------------------- status


def status(which: Which = proc.which) -> dict[str, Any]:
    """What doctor reports: detected hosts, the skill each one sees, MCP entries vidroute manages."""
    rec = _records()
    plugin = claude_plugin_installed()
    rows = []
    for h in hostmod.HOSTS:
        det = hostmod.detect(h, which)
        skill = None
        for root in h.skill_roots:
            target = hostmod.SKILL_ROOTS[root]() / skills.SKILL_NAME
            if target.exists():
                st = skills.inspect(target)
                skill = {"path": str(target), "managed": st["ours"], "current": st["current"],
                         "version": st.get("version"), "python_ok": st.get("python_ok")}
                break
        if skill is None and h.id == "claude-code" and plugin:
            skill = {"path": "plugin vidroute", "managed": False, "current": True, "version": None, "plugin": True}
        mcp = rec["mcp"].get(h.id)
        if det["detected"] or mcp:
            rows.append({"id": h.id, "name": h.name, "detected": det["detected"], "skill": skill,
                         "mcp": {"method": mcp["method"], "since": mcp.get("installed_at")} if mcp else None})
    return {"hosts": rows, "claude_plugin": plugin}
