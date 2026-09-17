#!/usr/bin/env python3
"""Tests for ag wake capability. Stdlib only, echo backend + temp state.
Run: python3 tests/test_ag_wake.py
Env: AG_WAKE_BIN overrides binary (default: live repo ag).

Design under test: each `wake` enqueues a durable job (wake/jobs/<jid>.json)
and spawns one tracked private worker session (`__wake-<agent>-<jid>`,
visible in sessions/status, stoppable via `kill`). The worker takes a
per-agent POSIX flock around run_turn (crash-releases), so two rapid wakes
to the same agent serialize instead of overlapping turns.
"""
import fcntl
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = Path(os.environ.get("AG_WAKE_BIN", _REPO_AG))
assert AG.exists(), f"missing ag binary: {AG}"

def run(state, *args, timeout=20):
    p = subprocess.run([sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout)
    return p

def run_json(state, *args, timeout=20):
    p = run(state, "--json", *args, timeout=timeout)
    try: obj = json.loads(p.stdout or "{}")
    except Exception: obj = {}
    return p, obj

def wait_for(state, pred, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred(): return True
        time.sleep(0.2)
    return False

def events(state):
    _, o = run_json(state, "events", "--limit", "50")
    return o.get("data", {}).get("events", [])

def chat_log(state, name):
    _, o = run_json(state, "chat", "log", name, "--limit", "20")
    return o.get("data", {}).get("log", [])

def sessions(state):
    _, o = run_json(state, "sessions")
    return o.get("data", {}).get("sessions", [])

def wakes(state, agent=None):
    args = ["wakes"]
    if agent: args += ["--agent", agent]
    _, o = run_json(state, *args)
    return o.get("data", {}).get("wakes", [])

def test_wake_explicit():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    assert run(td, "agents", "add", "w1", "--backend", "echo", "--role", "sub").returncode == 0
    p = run(td, "wake", "w1", "hello-wake")
    assert p.returncode == 0, p.stderr
    ok = wait_for(td, lambda: any(r.get("role") == "agent" for r in chat_log(td, "w1")))
    assert ok, "wake background chat never landed"
    log = chat_log(td, "w1")
    assert log[0]["text"] == "hello-wake"
    evs = events(td)
    assert any(e["type"] == "wake" and e.get("agent") == "w1" for e in evs), evs
    assert any(e["type"] == "chat" for e in evs)
    print("ok test_wake_explicit")

def test_spawn_wake_completion_context():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "w2", "--backend", "echo", "--role", "sub")
    p = run(td, "spawn", "--wake", "w2", "--wake-message", "please summarize", "--", "echo hi-spawn")
    assert p.returncode == 0, p.stderr
    sid = p.stdout.strip().split()[1]
    ok = wait_for(td, lambda: any(r.get("role") == "agent" for r in chat_log(td, "w2")))
    assert ok, "spawn --wake never fired"
    log = chat_log(td, "w2")
    user_txt = [r["text"] for r in log if r["role"] == "user"][0]
    assert sid in user_txt and "exited 0" in user_txt and "please summarize" in user_txt, user_txt
    assert "echo hi-spawn" in user_txt
    ss = [s for s in sessions(td) if s["id"] == sid]
    assert ss and ss[0]["wake_agent"] == "w2" and ss[0]["wake_fired"] is True, ss
    print("ok test_spawn_wake_completion_context")

def test_spawn_wake_default_message():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "w", "--backend", "echo", "--role", "sub")
    run(td, "spawn", "--wake", "w", "--", "echo x")
    ok = wait_for(td, lambda: any(r.get("role") == "agent" for r in chat_log(td, "w")))
    assert ok
    txt = [r["text"] for r in chat_log(td, "w") if r["role"] == "user"][0]
    assert "Follow up on the completed work." in txt, txt
    print("ok test_spawn_wake_default_message")

def test_validation():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "v", "--backend", "echo", "--role", "sub")
    p = run(td, "wake", "nosuch", "hi")
    assert p.returncode != 0 and "no such agent" in (p.stderr or p.stdout)
    p = run(td, "spawn", "--wake", "nosuch", "--", "echo hi")
    assert p.returncode != 0 and "no such agent" in (p.stderr or p.stdout)
    p = run(td, "wake", "v")
    assert p.returncode != 0 and "empty message" in (p.stderr or p.stdout)
    _, o = run_json(td, "wake", "nosuch", "hi")
    assert o.get("ok") is False
    assert any(e["type"] == "wake_fail" for e in events(td)), events(td)
    print("ok test_validation")

def test_no_duplicate():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "d", "--backend", "echo", "--role", "sub")
    p = run(td, "spawn", "--wake", "d", "--", "echo dup")
    sid = p.stdout.strip().split()[1]
    ok = wait_for(td, lambda: any(r.get("role") == "agent" for r in chat_log(td, "d")), timeout=10)
    assert ok
    time.sleep(2)
    n = len([e for e in events(td) if e["type"] == "wake" and e.get("session") == sid])
    assert n == 1, f"duplicate wake: {n}"
    print("ok test_no_duplicate")

def test_on_exit_compat():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "c", "--backend", "echo", "--role", "sub")
    marker = td / "onexit-ok"
    p = run(td, "spawn", "--on-exit", f"touch {marker}", "--wake", "c",
        "--wake-message", "m", "--", "echo compat")
    assert p.returncode == 0
    ok = wait_for(td, lambda: marker.exists() and any(r.get("role") == "agent" for r in chat_log(td, "c")))
    assert ok, "on-exit and wake should both fire"
    print("ok test_on_exit_compat")

def test_safe_argv_no_shell():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "s", "--backend", "echo", "--role", "sub")
    evil = "hi; touch SHOULD_NOT_EXIST_XYZ"
    run(td, "wake", "s", evil)
    ok = wait_for(td, lambda: any(r.get("role") == "agent" for r in chat_log(td, "s")))
    assert ok
    assert not (td / "SHOULD_NOT_EXIST_XYZ").exists()
    assert not Path("SHOULD_NOT_EXIST_XYZ").exists()
    txt = [r["text"] for r in chat_log(td, "s") if r["role"] == "user"][0]
    assert evil in txt
    print("ok test_safe_argv_no_shell")

def test_kill_fires_wake_once():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "k", "--backend", "echo", "--role", "sub")
    p = run(td, "spawn", "--wake", "k", "--", "sleep 30")
    sid = p.stdout.strip().split()[1]
    time.sleep(1)
    assert run(td, "kill", sid, "--force").returncode == 0
    ok = wait_for(td, lambda: any(r.get("role") == "agent" for r in chat_log(td, "k")), timeout=10)
    assert ok, "kill should fire wake"
    time.sleep(2)
    n = len([e for e in events(td) if e["type"] == "wake" and e.get("session") == sid])
    assert n == 1, f"kill duplicate wake: {n}"
    print("ok test_kill_fires_wake_once")

def test_status_visibility():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "st", "--backend", "echo", "--role", "sub")
    p = run(td, "spawn", "--wake", "st", "--", "echo vis")
    sid = p.stdout.strip().split()[1]
    _, o = run_json(td, "sessions")
    row = [s for s in o["data"]["sessions"] if s["id"] == sid][0]
    assert row["wake_agent"] == "st", row
    _, o = run_json(td, "status")
    srow = [s for s in o["data"]["sessions"] if s["id"] == sid][0]
    assert srow["wake_agent"] == "st", srow
    print("ok test_status_visibility")

def test_two_rapid_wakes_complete():
    """Two rapid wakes to same agent: both serialize and complete, no lost turn."""
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "r2", "--backend", "echo", "--role", "sub")
    _, o1 = run_json(td, "wake", "r2", "rapid-one")
    _, o2 = run_json(td, "wake", "r2", "rapid-two")
    assert o1.get("ok") and o2.get("ok"), (o1, o2)
    j1 = o1["data"].get("job")
    j2 = o2["data"].get("job")
    assert j1 and j2 and j1 != j2, (o1, o2)
    ok = wait_for(td, lambda: len([r for r in chat_log(td, "r2") if r.get("role") == "agent"]) >= 2,
        timeout=20)
    assert ok, "both rapid wakes should complete"
    users = [r["text"] for r in chat_log(td, "r2") if r["role"] == "user"]
    assert "rapid-one" in users and "rapid-two" in users, users
    ok = wait_for(td, lambda: all(
        (j.get("status") == "done") for j in wakes(td, "r2")
        if j.get("id") in (j1, j2)) and len(
        [j for j in wakes(td, "r2") if j.get("id") in (j1, j2)]) == 2, timeout=10)
    assert ok, wakes(td, "r2")
    evs = events(td)
    assert len([e for e in evs if e["type"] == "wake" and e.get("agent") == "r2"]) == 2, evs
    assert len([e for e in evs if e["type"] == "wake_done" and e.get("agent") == "r2"]) == 2, evs
    print("ok test_two_rapid_wakes_complete")

def test_wake_serializes_on_agent_lock():
    """Holding the per-agent lock blocks the wake turn: job stays queued,
    worker session stays running/visible; releasing unblocks to done."""
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "lk", "--backend", "echo", "--role", "sub")
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "lk.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o = run_json(td, "wake", "lk", "blocked-msg")
        assert o.get("ok"), o
        jid = o["data"].get("job")
        wsid = o["data"].get("session")
        assert jid and wsid, o
        time.sleep(3)  # worker should be stuck on flock, not done
        js = [j for j in wakes(td, "lk") if j.get("id") == jid]
        assert js and js[0]["status"] in ("queued", "running"), js
        assert js[0]["status"] != "done", js
        assert not any(r.get("role") == "agent" for r in chat_log(td, "lk")), chat_log(td, "lk")
        ss = [s for s in sessions(td) if s["id"] == wsid]
        assert ss and ss[0]["running"] is True, ss
        assert ss[0].get("wake_job") == jid and ss[0].get("wake_worker_for") == "lk", ss
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    ok = wait_for(td, lambda: any(
        j.get("id") == jid and j.get("status") == "done" for j in wakes(td, "lk")), timeout=15)
    assert ok, wakes(td, "lk")
    assert any(r.get("role") == "agent" for r in chat_log(td, "lk"))
    print("ok test_wake_serializes_on_agent_lock")

def test_wake_job_visibility():
    """wakes + sessions expose queued/running/done and the worker link."""
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "vis", "--backend", "echo", "--role", "sub")
    _, o = run_json(td, "wake", "vis", "see-me")
    jid, wsid = o["data"].get("job"), o["data"].get("session")
    assert jid and wsid, o
    ok = wait_for(td, lambda: any(
        j.get("id") == jid and j.get("status") == "done" for j in wakes(td, "vis")), timeout=15)
    assert ok, wakes(td, "vis")
    js = [j for j in wakes(td, "vis") if j.get("id") == jid][0]
    assert js["agent"] == "vis" and js["session"] == wsid, js
    assert js["sess_exit"] == 0 and js["sess_running"] is False, js
    ss = [s for s in sessions(td) if s["id"] == wsid]
    assert ss and ss[0].get("wake_job") == jid, ss
    assert "__wake-vis-" in (ss[0].get("name") or ""), ss
    print("ok test_wake_job_visibility")

def test_wake_backend_failure_visible():
    """Echo agent with an impossible cwd fails the turn: job failed,
    wake_fail event, non-zero chat event. Echo backend only."""
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "bad", "--backend", "echo", "--role", "sub",
        "--dir", "/nonexistent-ag-wake-xyz")
    _, o = run_json(td, "wake", "bad", "will-fail")
    assert o.get("ok"), o
    jid = o["data"].get("job")
    ok = wait_for(td, lambda: any(
        j.get("id") == jid and j.get("status") == "failed" for j in wakes(td, "bad")), timeout=15)
    assert ok, wakes(td, "bad")
    evs = events(td)
    assert any(e["type"] == "wake_fail" and e.get("job") == jid for e in evs), evs
    assert any(e["type"] == "chat" and e.get("exit") not in (0, None) for e in evs), evs
    print("ok test_wake_backend_failure_visible")

def _job(state, jid):
    return next((j for j in wakes(state) if j.get("id") == jid), {})

def test_wakes_wait_done():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "ww", "--backend", "echo", "--role", "sub")
    _, o = run_json(td, "wake", "ww", "wait-for-me")
    jid = o["data"].get("job")
    assert jid, o
    p, o = run_json(td, "wakes", "--wait", jid, "--timeout", "15")
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert o["data"].get("status") == "done", o
    print("ok test_wakes_wait_done")

def test_wake_notify_push():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "wn", "--backend", "echo", "--role", "sub")
    run(td, "agents", "add", "orch", "--backend", "echo", "--role", "orchestrator")
    _, o = run_json(td, "wake", "wn", "do-it", "--notify", "orch")
    jid = o["data"].get("job")
    assert jid, o
    ok = wait_for(td, lambda: _job(td, jid).get("status") == "done", timeout=15)
    assert ok, wakes(td)
    log = chat_log(td, "orch")
    notes = [r for r in log if r.get("role") == "tool" and jid in r.get("text", "")]
    assert notes and "done" in notes[0]["text"], log
    print("ok test_wake_notify_push")

def test_wake_on_done_hook():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "wh", "--backend", "echo", "--role", "sub")
    marker = td / "hook.env"
    run(td, "wake", "wh", "hook-it",
        "--on-done", f"echo $AG_WAKE_STATUS:$AG_WAKE_AGENT > {marker}")
    ok = wait_for(td, lambda: marker.exists(), timeout=15)
    assert ok, "on-done hook never ran"
    assert marker.read_text().strip() == "done:wh", marker.read_text()
    print("ok test_wake_on_done_hook")

def test_dead_worker_marked_failed():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "dw", "--backend", "echo", "--role", "sub")
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "dw.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o = run_json(td, "wake", "dw", "doomed")
        jid, wsid = o["data"].get("job"), o["data"].get("session")
        assert jid and wsid, o
        ok = wait_for(td, lambda: _job(td, jid).get("status") == "running", timeout=10)
        assert ok, wakes(td)
        assert run(td, "kill", wsid, "--force").returncode == 0
        ok = wait_for(td, lambda: _job(td, jid).get("status") == "failed", timeout=10)
        assert ok, wakes(td)
        assert "worker gone" in (_job(td, jid).get("error") or ""), _job(td, jid)
        assert any(e["type"] == "wake_fail" and e.get("job") == jid
            for e in events(td)), events(td)
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    print("ok test_dead_worker_marked_failed")

def test_timeout_marks_stalled():
    td = Path(tempfile.mkdtemp(prefix="agw-"))
    run(td, "agents", "add", "to", "--backend", "echo", "--role", "sub")
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "to.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o = run_json(td, "wake", "to", "slow-one", "--timeout", "1")
        jid = o["data"].get("job")
        assert jid, o
        ok = wait_for(td, lambda: _job(td, jid).get("stalled") is True, timeout=10)
        assert ok, wakes(td)
        assert any(e["type"] == "wake_stall" and e.get("job") == jid
            for e in events(td)), events(td)
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    ok = wait_for(td, lambda: _job(td, jid).get("status") == "done", timeout=15)
    assert ok, wakes(td)
    print("ok test_timeout_marks_stalled")

if __name__ == "__main__":
    test_wake_explicit()
    test_spawn_wake_completion_context()
    test_spawn_wake_default_message()
    test_validation()
    test_no_duplicate()
    test_on_exit_compat()
    test_safe_argv_no_shell()
    test_kill_fires_wake_once()
    test_status_visibility()
    test_two_rapid_wakes_complete()
    test_wake_serializes_on_agent_lock()
    test_wake_job_visibility()
    test_wake_backend_failure_visible()
    test_wakes_wait_done()
    test_wake_notify_push()
    test_wake_on_done_hook()
    test_dead_worker_marked_failed()
    test_timeout_marks_stalled()
    print("all wake tests passed")
