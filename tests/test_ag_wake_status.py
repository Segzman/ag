#!/usr/bin/env python3
"""Honest wake status + actionable launch errors. Stdlib only, temp state.

No real models/providers. Fake `claude`/`cursor` binaries on PATH stand in
for backends; echo covers the instant path; flock holdings simulate a busy
agent without timing games.

Run: python3 tests/test_ag_wake_status.py
Env: AG_WAKE_STATUS_BIN overrides binary (default: live repo ag).

Contract under test (see docs/WAKE_STATUS.md):
- queued means waiting (guard not yet held); running means the worker holds
  the per-agent turn guard and the backend is launching/running.
- queued jobs never report backend-stalled; only running jobs age out.
- launch failures distinguish missing project dir from missing executable
  and keep a bounded stderr/reply excerpt in the failed job.
- spawn/daemon chdir failure never executes in a fallback directory: the
  session dies nonzero with the error in its tracked output.
"""
import fcntl
import importlib.machinery
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = os.environ.get("AG_WAKE_STATUS_BIN", _REPO_AG)
assert os.path.exists(AG), f"ag missing: {AG}"


def load_mod(name="agwakestatus"):
    return importlib.machinery.SourceFileLoader(name, AG).load_module()


def run(state, *args, timeout=30, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run([sys.executable, AG, "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=full_env)


def run_json(state, *args, timeout=30, env=None):
    p = run(state, "--json", *args, timeout=timeout, env=env)
    try:
        obj = json.loads(p.stdout or "{}")
    except Exception:
        obj = {}
    return p, obj


def wait_for(pred, timeout=15, interval=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if pred():
                return True
        except Exception:
            pass
        time.sleep(interval)
    return False


def wakes(state, agent=None):
    args = ["wakes"]
    if agent:
        args += ["--agent", agent]
    _, o = run_json(state, *args)
    return o.get("data", {}).get("wakes", [])


def job(state, jid):
    return next((j for j in wakes(state) if j.get("id") == jid), {})


def events(state, limit=100):
    _, o = run_json(state, "events", "--limit", str(limit))
    return o.get("data", {}).get("events", [])


def chat_log(state, name):
    _, o = run_json(state, "chat", "log", name, "--limit", "20")
    return o.get("data", {}).get("log", [])


def hold_agent_lock(state, agent):
    lockdir = Path(state) / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    f = open(lockdir / f"{agent}.lock", "a+b")
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    return f


def release(f):
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    finally:
        f.close()


def make_script(path, body):
    path.write_text(body)
    path.chmod(0o755)
    return path


def test_second_wake_stays_queued_then_runs():
    """Two wakes under a held guard: both stay queued (never running),
    then both complete after release. Same-agent turns serialize."""
    td = Path(tempfile.mkdtemp(prefix="agws-q-"))
    assert run(td, "agents", "add", "q2", "--backend", "echo", "--role", "sub").returncode == 0
    lk = hold_agent_lock(td, "q2")
    try:
        _, o1 = run_json(td, "wake", "q2", "queued-one")
        _, o2 = run_json(td, "wake", "q2", "queued-two")
        assert o1.get("ok") and o2.get("ok"), (o1, o2)
        j1, j2 = o1["data"].get("job"), o2["data"].get("job")
        assert j1 and j2 and j1 != j2, (o1, o2)
        # workers start and block on the guard: wait_reason appears, status
        # stays queued (honest: the guard is not held by them yet).
        ok = wait_for(lambda: job(td, j1).get("wait_reason") and job(td, j2).get("wait_reason"),
            timeout=15)
        assert ok, (job(td, j1), job(td, j2))
        time.sleep(2)
        for jid in (j1, j2):
            j = job(td, jid)
            assert j.get("status") == "queued", j
            assert "running_at" not in j, j
        assert not any(r.get("role") == "agent" for r in chat_log(td, "q2")), chat_log(td, "q2")
    finally:
        release(lk)
    ok = wait_for(lambda: job(td, j1).get("status") == "done"
        and job(td, j2).get("status") == "done", timeout=20)
    assert ok, (job(td, j1), job(td, j2))
    users = [r["text"] for r in chat_log(td, "q2") if r["role"] == "user"]
    assert "queued-one" in users and "queued-two" in users, users
    print("ok test_second_wake_stays_queued_then_runs")


def test_queued_with_timeout_never_stalls():
    """A queued job waiting for the guard is not backend work: no stalled
    flag and no wake_stall event no matter how long it waits."""
    td = Path(tempfile.mkdtemp(prefix="agws-qs-"))
    assert run(td, "agents", "add", "qs", "--backend", "echo", "--role", "sub").returncode == 0
    lk = hold_agent_lock(td, "qs")
    try:
        _, o = run_json(td, "wake", "qs", "patient", "--timeout", "1")
        assert o.get("ok"), o
        jid = o["data"].get("job")
        assert jid, o
        assert wait_for(lambda: job(td, jid).get("status") == "queued", timeout=10), wakes(td)
        time.sleep(3)  # past the 1s timeout while still queued
        j = job(td, jid)
        assert j.get("status") == "queued", j
        assert not j.get("stalled"), j
        assert not any(e["type"] == "wake_stall" and e.get("job") == jid for e in events(td)), \
            events(td)
    finally:
        release(lk)
    ok = wait_for(lambda: job(td, jid).get("status") == "done", timeout=20)
    assert ok, job(td, jid)
    assert not job(td, jid).get("stalled"), job(td, jid)
    print("ok test_queued_with_timeout_never_stalls")


def test_invalid_cwd_never_runs_sentinel():
    """Missing project dir: job fails naming the directory, and the backend
    binary never executes (sentinel untouched)."""
    td = Path(tempfile.mkdtemp(prefix="agws-cwd-"))
    bindir = Path(tempfile.mkdtemp(prefix="agws-cwd-bin-"))
    sentinel = td / "SHOULD_NOT_EXIST_SENTINEL"
    make_script(bindir / "claude",
        '#!/bin/sh\ntouch "$AG_SENTINEL"\n'
        'echo \'{"type":"system","subtype":"init","session_id":"fake"}\'\n'
        'echo \'{"type":"result","subtype":"success","total_cost_usd":0}\'\n')
    assert run(td, "agents", "add", "bad", "--backend", "claude",
        "--role", "sub", "--dir", "/nonexistent-ag-wake-xyz").returncode == 0
    env = {"PATH": str(bindir) + os.pathsep + "/usr/bin:/bin", "AG_SENTINEL": str(sentinel)}
    _, o = run_json(td, "wake", "bad", "will-fail", env=env)
    assert o.get("ok"), o
    jid = o["data"].get("job")
    ok = wait_for(lambda: job(td, jid).get("status") == "failed", timeout=20)
    assert ok, wakes(td)
    err = job(td, jid).get("error") or ""
    assert "project directory" in err, err
    assert not sentinel.exists(), "backend ran despite missing cwd"
    print("ok test_invalid_cwd_never_runs_sentinel")


def test_missing_executable_distinguished():
    """Missing backend binary names the executable, never the directory."""
    td = Path(tempfile.mkdtemp(prefix="agws-bin-"))
    home = Path(tempfile.mkdtemp(prefix="agws-bin-home-"))
    assert run(td, "agents", "add", "nb", "--backend", "cursor", "--role", "sub").returncode == 0
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
    _, o = run_json(td, "wake", "nb", "msg", env=env)
    assert o.get("ok"), o
    jid = o["data"].get("job")
    ok = wait_for(lambda: job(td, jid).get("status") == "failed", timeout=20)
    assert ok, wakes(td)
    err = job(td, jid).get("error") or ""
    assert "binary missing" in err, err
    assert "project directory" not in err, err
    print("ok test_missing_executable_distinguished")


def test_backend_stderr_retained_in_wake_failure():
    """Nonzero backend: the failed job keeps the exit code plus a bounded
    stderr/reply excerpt so remote callers can act."""
    td = Path(tempfile.mkdtemp(prefix="agws-err-"))
    bindir = Path(tempfile.mkdtemp(prefix="agws-err-bin-"))
    make_script(bindir / "claude",
        '#!/bin/sh\necho "boom-stderr-marker-xyz" >&2\nexit 3\n')
    assert run(td, "agents", "add", "se", "--backend", "claude", "--role", "sub").returncode == 0
    env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    _, o = run_json(td, "wake", "se", "blow-up", env=env)
    assert o.get("ok"), o
    jid = o["data"].get("job")
    ok = wait_for(lambda: job(td, jid).get("status") == "failed", timeout=20)
    assert ok, wakes(td)
    err = job(td, jid).get("error") or ""
    assert "boom-stderr-marker-xyz" in err, err
    assert "3" in err, err
    assert len(err) <= 1000, len(err)
    print("ok test_backend_stderr_retained_in_wake_failure")


def test_running_job_still_stalls():
    """Stall detection still applies once the turn actually runs: a blocked
    backend with a short timeout gets stalled=True, then finishes done."""
    td = Path(tempfile.mkdtemp(prefix="agws-run-"))
    bindir = Path(tempfile.mkdtemp(prefix="agws-run-bin-"))
    make_script(bindir / "claude", '#!/bin/sh\nsleep 8\n')
    assert run(td, "agents", "add", "rs", "--backend", "claude", "--role", "sub").returncode == 0
    env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    _, o = run_json(td, "wake", "rs", "slow-backend", "--timeout", "1", env=env)
    assert o.get("ok"), o
    jid = o["data"].get("job")
    ok = wait_for(lambda: job(td, jid).get("stalled") is True, timeout=10)
    assert ok, wakes(td)
    assert job(td, jid).get("status") == "running", job(td, jid)
    ok = wait_for(lambda: job(td, jid).get("status") == "done", timeout=25)
    assert ok, wakes(td)
    print("ok test_running_job_still_stalls")


def test_slash_wake_finishes_done():
    """Slash wake jobs never take the guard yet still terminate done (no
    permanent queued leftover)."""
    td = Path(tempfile.mkdtemp(prefix="agws-slash-"))
    assert run(td, "agents", "add", "sl", "--backend", "echo", "--role", "sub").returncode == 0
    _, o = run_json(td, "wake", "sl", "/help")
    assert o.get("ok"), o
    jid = o["data"].get("job")
    ok = wait_for(lambda: job(td, jid).get("status") == "done", timeout=15)
    assert ok, wakes(td)
    left = [j for j in wakes(td, "sl") if j.get("status") in ("queued", "running")]
    assert not left, left
    print("ok test_slash_wake_finishes_done")


def test_removed_agent_wake_fails_terminal():
    """A queued job whose agent vanished fails closed (no bogus queued)."""
    m = load_mod()
    td = Path(tempfile.mkdtemp(prefix="agws-ghost-"))
    assert run(td, "agents", "add", "real", "--backend", "echo", "--role", "sub").returncode == 0
    sdir = m.state_dir(str(td))
    jid = "ghost001"
    m._wake_write_job(sdir, {"id": jid, "agent": "ghost", "message": "hi",
        "trigger": None, "exit": None, "status": "queued", "ts": m.now_iso(),
        "session": None, "notify": "", "on_done": "", "timeout_s": 0})
    a = types.SimpleNamespace(dir=str(td), jid=jid)
    assert m.do__wake_run(a) == 1
    cur = m._wake_read_job(sdir, jid)
    assert cur.get("status") == "failed", cur
    assert "no such agent" in (cur.get("error") or ""), cur
    print("ok test_removed_agent_wake_fails_terminal")


def test_on_start_fires_only_with_guard():
    """run_turn calls on_start after acquiring the turn guard; local slash
    turns never take the guard so on_start stays silent."""
    m = load_mod()
    td = Path(tempfile.mkdtemp(prefix="agws-cb-"))
    assert run(td, "agents", "add", "e1", "--backend", "echo", "--role", "sub").returncode == 0
    sdir = m.state_dir(str(td))
    fired = []
    r = m.run_turn(sdir, "e1", "hi", on_start=lambda: fired.append(True))
    assert r.get("exit") == 0, r
    assert fired == [True], (r, fired)
    fired2 = []
    r = m.run_turn(sdir, "e1", "/help", on_start=lambda: fired2.append(True))
    assert r.get("exit") == 0, r
    assert fired2 == [], (r, fired2)
    print("ok test_on_start_fires_only_with_guard")


def test_daemon_chdir_failure_never_runs_fallback():
    """spawn with a missing cwd dies nonzero with the chdir error in its
    tracked output; the command never runs in a fallback directory."""
    td = Path(tempfile.mkdtemp(prefix="agws-spawn-"))
    sentinel = td / "SPAWN_SENTINEL"
    p = run(td, "spawn", "--cwd", "/nonexistent-ag-spawn-xyz",
        "--", "touch", str(sentinel), timeout=30)
    assert p.returncode == 0, (p.stdout, p.stderr)
    mt = re.search(r"spawned (\w+)", p.stdout or "")
    assert mt, (p.stdout, p.stderr)
    sid = mt.group(1)

    def finished():
        _, o = run_json(td, "sessions")
        rows = [s for s in o.get("data", {}).get("sessions", []) if s.get("id") == sid]
        return bool(rows) and not rows[0].get("running")

    assert wait_for(finished, timeout=15), "session never exited"

    def snap():
        _, o = run_json(td, "snap", sid, "--clean")
        return o.get("data", {})
    assert wait_for(lambda: snap().get("exit") not in (None,), timeout=15), snap()
    data = snap()
    assert data.get("exit") not in (None, 0), data
    assert "cannot chdir" in (data.get("output") or ""), data
    assert not sentinel.exists(), "command ran in fallback directory"
    print("ok test_daemon_chdir_failure_never_runs_fallback")


if __name__ == "__main__":
    test_second_wake_stays_queued_then_runs()
    test_queued_with_timeout_never_stalls()
    test_invalid_cwd_never_runs_sentinel()
    test_missing_executable_distinguished()
    test_backend_stderr_retained_in_wake_failure()
    test_running_job_still_stalls()
    test_slash_wake_finishes_done()
    test_removed_agent_wake_fails_terminal()
    test_on_start_fires_only_with_guard()
    test_daemon_chdir_failure_never_runs_fallback()
    print("all wake-status tests passed")
