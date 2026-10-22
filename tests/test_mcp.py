"""MCP shell: raw JSON-RPC behaviour, progress and cancellation in process, and conformance with the official SDK."""

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from vidroute import mcp_server
from vidroute.core.envelope import success

ROOT = Path(__file__).resolve().parents[1]
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _env(**extra):
    return {**os.environ, "PYTHONPATH": str(ROOT / "src"), **extra}


class Proc:
    def __init__(self, **env):
        self.p = subprocess.Popen([sys.executable, "-m", "vidroute.mcp_server"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(**env))

    def send(self, msg):
        line = msg if isinstance(msg, str) else json.dumps(msg)
        self.p.stdin.write(line.encode("utf-8") + b"\n")
        self.p.stdin.flush()

    def recv(self):
        return json.loads(self.p.stdout.readline())

    def call(self, rid, method, params=None):
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, **({"params": params} if params else {})})
        return self.recv()

    def close(self):
        self.p.stdin.close()
        assert self.p.wait(timeout=20) == 0


def test_raw_json_rpc():
    s = Proc()
    init = s.call(1, "initialize", {"protocolVersion": "2099-01-01", "capabilities": {},
                                    "clientInfo": {"name": "t", "version": "0"}})
    assert init["result"]["protocolVersion"] == mcp_server.SUPPORTED_VERSIONS[0]  # counter-offer: our latest
    assert init["result"]["serverInfo"]["name"] == "vidroute" and "Workflow" in init["result"]["instructions"]
    s.send({"jsonrpc": "2.0", "method": "notifications/initialized"})  # no reply
    assert s.call(2, "ping") == {"jsonrpc": "2.0", "id": 2, "result": {}}
    tools = s.call(3, "tools/list")["result"]["tools"]
    assert {t["name"] for t in tools} >= {"doctor", "install", "resolve_llm", "plan_video", "prepare_video",
                                          "drill_video", "read_outputs"}
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    assert s.call(4, "resources/list")["error"]["code"] == -32601
    s.send("{not json")
    assert s.recv()["error"]["code"] == -32700
    assert s.call(5, "tools/call", {"name": "no_such_tool", "arguments": {}})["error"]["code"] == -32602
    missing = s.call(6, "tools/call", {"name": "resolve_llm", "arguments": {}})["result"]
    assert missing["isError"] and missing["structuredContent"]["error"]["code"] == "invalid_input"
    ok = s.call(7, "tools/call", {"name": "resolve_llm", "arguments": {"llm": "claude-opus-5-5", "host": "claude-code"}})
    res = ok["result"]
    assert not res["isError"] and res["structuredContent"]["ok"]
    assert json.loads(res["content"][0]["text"]) == res["structuredContent"]
    assert res["structuredContent"]["data"]["effective"]["image"] is True
    s.close()


def test_progress_and_cancellation_in_process():
    out = io.BytesIO()
    srv = mcp_server.Server(stdout=out)
    started, seen = threading.Event(), {}

    def slow(args, ctx):
        for i in range(3):
            ctx.progress("work", i + 1, 3, f"step {i + 1}")
        started.set()
        seen["cancelled"] = ctx.cancel_event.wait(10)
        return success({})

    srv.handlers["doctor"] = slow
    srv.handle({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                "params": {"name": "doctor", "arguments": {}, "_meta": {"progressToken": "tok"}}})
    assert started.wait(10)
    srv.handle({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 7, "reason": "user"}})
    srv.pool.shutdown(wait=True)
    assert seen["cancelled"] is True
    msgs = [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()]
    progress = [m["params"] for m in msgs if m.get("method") == "notifications/progress"]
    assert progress and all(p["progressToken"] == "tok" for p in progress)
    assert [p["progress"] for p in progress] == sorted({p["progress"] for p in progress})  # strictly increasing
    assert not any(m.get("id") == 7 for m in msgs)  # a cancelled request gets no response


def test_read_outputs_stays_inside_the_output_folder(tmp_path):
    root = tmp_path / "out" / "v-1234"
    (root / "frames").mkdir(parents=True)
    (root / "plan.json").write_text("{}", encoding="utf-8")
    (root / "SUMMARY.md").write_text("# hi", encoding="utf-8")
    (root / "frames" / "001.jpg").write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
    (root / "audio.mp3").write_bytes(b"ID3fake")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    blocks, errors = mcp_server.read_outputs(str(root), ["SUMMARY.md", "frames/001.jpg", "../../secret.txt",
                                                         "audio.mp3"], 20, "2025-11-25")
    assert errors
    assert blocks[0] == {"type": "text", "text": "--- SUMMARY.md ---\n# hi"}
    assert blocks[2]["type"] == "image" and blocks[2]["mimeType"] == "image/jpeg"
    assert "not a file inside" in blocks[3]["text"] and "no" not in blocks[3]["text"].split(":")[0]
    assert blocks[-1]["type"] == "audio"
    old, _ = mcp_server.read_outputs(str(root), ["audio.mp3"], 20, "2024-11-05")
    assert old[0]["type"] == "text" and "attach it from disk" in old[0]["text"]  # no audio content before 2025-03-26
    with pytest.raises(Exception):
        mcp_server.read_outputs(str(tmp_path), ["secret.txt"], 20, "2025-11-25")  # not an output folder


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg/ffprobe not on PATH")
def test_official_sdk_client_end_to_end(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sys.path.insert(0, str(ROOT / "tests"))
    import media

    video = media.make_slides(tmp_path / "talk.mp4")
    out = tmp_path / "out"
    params = StdioServerParameters(command=sys.executable, args=["-m", "vidroute.mcp_server"],
                                   env={"VIDROUTE_HOME": os.environ["VIDROUTE_HOME"], "VIDROUTE_FAKE_ENGINES": "1",
                                        "PYTHONPATH": str(ROOT / "src")})

    async def scenario():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                assert init.server_info.name == "vidroute" and init.protocol_version == "2025-11-25"
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"plan_video", "prepare_video", "read_outputs"} <= names
                plan = await session.call_tool("plan_video", {"video": str(video), "llm": "claude-opus-5-5",
                                                              "host": "claude-code", "out": str(out)})
                assert not plan.is_error and plan.structured_content["data"]["route"] == "frames"
                ticks = []

                async def on_progress(progress, total, message):
                    ticks.append((progress, message))

                prep = await session.call_tool("prepare_video", {"video": str(video), "out": str(out)},
                                               progress_callback=on_progress)
                assert not prep.is_error, prep.content
                data = prep.structured_content["data"]
                assert data["plan_source"] == "out_dir" and ticks
                assert [t[0] for t in ticks] == sorted(t[0] for t in ticks)
                sheet = data["handoff"]["attachments"][0]["path"]
                files = await session.call_tool("read_outputs", {"out_dir": data["out_dir"],
                                                                 "paths": [sheet, "SUMMARY.md"]})
                assert [c.type for c in files.content] == ["text", "image", "text"]
                native = await session.call_tool("prepare_video", {"video": str(video), "llm": "gemini-3.8-flash",
                                                                   "host": "gemini-cli", "out": str(out)})
                assert native.is_error and native.structured_content["error"]["code"] == "raw_video_handoff"

    asyncio.run(scenario())
