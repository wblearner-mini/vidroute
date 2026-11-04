"""Concurrent runs, failure messages and resource limits."""

import errno
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from vidroute import api, cli
from vidroute.core.context import Ctx
from vidroute.core.doctor import system
from vidroute.core.errors import RunFailed
from vidroute.core.integrate import skills
from vidroute.core.prepare.layout import Layout
from vidroute.core.util import ffmpeg, limits
from vidroute.core.util.filelock import exclusive


def test_lock_keeps_a_second_run_out_until_the_first_is_done(tmp_path):
    lock = tmp_path / ".work" / "lock"
    with exclusive(lock, Ctx(), "busy"):
        with pytest.raises(RunFailed) as exc:
            with exclusive(lock, Ctx(), "busy", wait_seconds=0.1, poll=0.02):
                pytest.fail("two holders")
        assert exc.value.code == "output_busy" and "--out" in exc.value.message
    with exclusive(lock, Ctx(), "busy", wait_seconds=0):
        pass


def test_a_waiting_run_goes_on_when_the_lock_is_released(tmp_path):
    lock = tmp_path / "lock"
    order = []

    def second():
        with exclusive(lock, Ctx(), "busy", wait_seconds=5, poll=0.01):
            order.append("second")

    with exclusive(lock, Ctx(), "busy"):
        t = threading.Thread(target=second)
        t.start()
        t.join(0.1)
        order.append("first")
    t.join(5)
    assert order == ["first", "second"]


def test_lock_dies_with_a_killed_process(tmp_path):
    lock = tmp_path / "lock"
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = ("import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from pathlib import Path; from vidroute.core.context import Ctx;"
            "from vidroute.core.util.filelock import exclusive\n"
            "with exclusive(Path(sys.argv[2]), Ctx(), 'x'):\n    print('held', flush=True); time.sleep(60)")
    holder = subprocess.Popen([sys.executable, "-c", code, src, str(lock)], stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    with pytest.raises(RunFailed):
        with exclusive(lock, Ctx(), "busy", wait_seconds=0.05, poll=0.01):
            pass
    holder.kill()
    holder.wait()
    with exclusive(lock, Ctx(), "busy", wait_seconds=5, poll=0.01):
        pass


@pytest.mark.parametrize("name,content,stderr,expected", [
    ("empty.mp4", b"", "moov atom not found", "the file is empty"),
    ("cut.mp4", b"x", "x: moov atom not found", "incomplete or damaged"),
    ("junk.mp4", b"x", "x: Invalid data found when processing input", "damaged"),
    ("other.mp4", b"x", "something rare", "something rare"),
])
def test_unreadable_files_are_explained(tmp_path, name, content, stderr, expected):
    path = tmp_path / name
    path.write_bytes(content)
    assert expected in ffmpeg.unreadable_message(path, stderr)


def test_broken_ffmpeg_is_not_reported_as_missing(monkeypatch):
    info = ffmpeg.FFmpegInfo(False, "/usr/bin/ffmpeg", "/usr/bin/ffprobe",
                             error="error while loading shared libraries: libx.so")
    monkeypatch.setattr(ffmpeg, "_cached_detect", type("C", (), {"__call__": lambda s: info, "cache_clear": lambda s: None})())
    monkeypatch.setenv("LD_LIBRARY_PATH", "/bad")
    with pytest.raises(Exception) as exc:
        ffmpeg.require()
    assert exc.value.code == "ffmpeg_broken" and "LD_LIBRARY_PATH" in exc.value.message
    assert exc.value.hint.startswith("env -u LD_LIBRARY_PATH")


@pytest.mark.parametrize("number,code", [(errno.ENOSPC, "disk_full"), (errno.EACCES, "not_writable"), (errno.EROFS, "not_writable")])
def test_system_errors_get_their_own_message(number, code):
    err = api._unexpected(OSError(number, os.strerror(number), "/x/y"))  # noqa: SLF001
    assert err.code == code and "/x/y" in err.message and err.hint


def test_real_surprises_ask_for_a_report_not_a_pointless_command():
    err = api._unexpected(ValueError("boom"))  # noqa: SLF001
    assert "boom" in err.message and "-v" in err.message and err.hint is None


def test_output_folder_that_cannot_be_written(tmp_path, monkeypatch):
    from vidroute.core.prepare import layout as lay

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    with pytest.raises(Exception) as exc:
        lay.layout_for(video, str(blocker / "sub"))
    assert exc.value.code == "output_not_writable" and "--out" in exc.value.message


def test_a_failed_run_on_an_unreadable_file_leaves_no_folder(tmp_path):
    bad = tmp_path / "bad.mp4"
    bad.write_bytes(os.urandom(2000))
    out = tmp_path / "out"
    env = api.run(str(bad), "claude-opus-5-5", "claude-code", out=str(out), ctx=Ctx())
    assert not env.ok and env.error["code"] == "probe_failed" and env.error["hint"] is None
    assert list(out.iterdir()) == []


def test_a_failure_keeps_earlier_results(tmp_path):
    from vidroute.core.prepare import _discard_if_empty

    keep = Layout(tmp_path / "x").ensure()
    (keep.root / "SUMMARY.md").write_text("old result")
    _discard_if_empty(keep)
    assert (keep.root / "SUMMARY.md").exists()
    empty = Layout(tmp_path / "y").ensure()
    (empty.work / "lock").write_text("")
    _discard_if_empty(empty)
    assert not empty.root.exists()


def test_cgroup_v2_limits_take_the_tightest_parent(tmp_path):
    root = tmp_path / "cg"
    leaf = root / "a" / "b"
    leaf.mkdir(parents=True)
    (root / "a" / "cpu.max").write_text("150000 100000\n")
    (leaf / "cpu.max").write_text("max 100000\n")
    (root / "a" / "memory.max").write_text(str(2 * 2**30))
    (root / "a" / "memory.current").write_text(str(2**30))
    (leaf / "memory.max").write_text("max\n")
    proc = tmp_path / "cgroup"
    proc.write_text("0::/a/b\n")
    assert limits.cgroup_cpus(root, proc) == 1.5
    assert limits.cgroup_memory(root, proc) == (2 * 2**30, 2**30)


def test_cgroup_v1_and_no_limit(tmp_path):
    root = tmp_path / "cg"
    (root / "cpu").mkdir(parents=True)
    (root / "memory").mkdir()
    proc = tmp_path / "cgroup"
    proc.write_text("0::/\n")
    assert limits.cgroup_cpus(root, proc) is None and limits.cgroup_memory(root, proc) is None
    (root / "cpu" / "cpu.cfs_quota_us").write_text("-1")
    (root / "cpu" / "cpu.cfs_period_us").write_text("100000")
    assert limits.cgroup_cpus(root, proc) is None
    (root / "cpu" / "cpu.cfs_quota_us").write_text("200000")
    (root / "memory" / "memory.limit_in_bytes").write_text(str(2**30))
    assert limits.cgroup_cpus(root, proc) == 2 and limits.cgroup_memory(root, proc) == (2**30, 0)


def test_memory_limit_replaces_the_hosts_numbers_only_when_smaller():
    host = {"total_gb": 32.0, "available_gb": 20.0}
    limited = system.apply_memory_limit(host, (2 * 2**30, 2**30))
    assert limited == {"total_gb": 2.0, "available_gb": 1.0, "limited": True}
    assert system.apply_memory_limit(host, None) == host
    assert system.apply_memory_limit(host, (64 * 2**30, 0)) == host


def test_threads_follow_the_usable_cpus(monkeypatch):
    for usable, threads in ((1, 1), (2, 2), (3, 3), (4, 2), (16, 8), (64, 8)):
        monkeypatch.setattr(limits, "usable_cpus", lambda u=usable: u)
        assert limits.engine_threads() == threads


def test_only_a_real_copy_counts_as_the_vidroute_skill(tmp_path):
    target = tmp_path / "vidroute"
    target.mkdir()
    (target / "SKILL.md").write_text("my own notes")
    assert not skills.is_vidroute_skill(target)
    (target / "SKILL.md").write_text("---\nname: vidroute\n---\n")
    (target / "scripts").mkdir()
    (target / "scripts" / "vidroute").write_text("#!/bin/sh\n")
    assert skills.is_vidroute_skill(target)


def test_setup_warns_when_another_skill_blocks_a_host(user_home, capsys):
    target = user_home / ".claude" / "skills" / "vidroute"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("my own skill")
    assert cli.main(["setup", "--host", "claude-code", "--no-install", "--no-selftest", "--yes", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    warned = [w for w in out["warnings"] if w["code"] == "skill_conflict"]
    assert warned and "Claude Code" not in warned[0]["message"].split("holds")[1]
    assert warned[0]["hint"] == "vidroute setup --force --no-install --host claude-code"
    assert (target / "SKILL.md").read_text() == "my own skill"


def test_plan_is_shown_without_a_terminal(user_home, capsys):
    assert cli.main(["setup", "--host", "claude-code", "--no-install", "--no-selftest"]) == 3
    err = capsys.readouterr().err
    assert "skill" in err and "create" in err and "Review the setup plan" in err
