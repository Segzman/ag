#!/usr/bin/env python3
"""Regression tests for bounded CLI reliability fixes (defects 1,2,4).
Isolated only: uses temp AGENT_CLI_DIR + safe local children (echo/printf/cat/false).
Target: live repo ag (override with AG_BIN env).
Run: python3 tests/test_ag_cli.py
"""
import json, os, signal, subprocess, sys, tempfile, time
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
assert os.path.exists(AG), f"ag missing: {AG}"

def run_ag(args, state, timeout=20):
    env = dict(os.environ, AGENT_CLI_DIR=str(state))
    return subprocess.run([sys.executable, AG]+args, capture_output=True,
        text=True, timeout=timeout, env=env)

def run_json(args, state, timeout=20):
    p = run_ag(args, state, timeout=timeout)
    try:
        return p, json.loads(p.stdout)
    except Exception:
        raise AssertionError(f"not JSON for {args}: rc={p.returncode} out={p.stdout!r} err={p.stderr!r}")

def wait_for(fn, timeout=10, interval=0.2):
    t0 = time.time()
    while time.time()-t0 < timeout:
        v = fn()
        if v: return v
        time.sleep(interval)
    return None

def sessions_json(state):
    p, o = run_json(["--json", "sessions"], state)
    assert o["ok"], o
    return {s["id"]: s for s in o["data"]["sessions"]}

def status_json(state):
    p, o = run_json(["--json", "status"], state)
    assert o["ok"], o
    return {s["id"]: s for s in o["data"]["sessions"]}

def read_status(state, sid):
    return json.loads((Path(state)/"sessions"/sid/"status.json").read_text())

def wait_child_pid(state, sid, timeout=8):
    ok = wait_for(lambda: read_status(state, sid).get("child_pid"), timeout=timeout)
    assert ok, f"no child_pid for {sid}"
    # ensure daemon fully started; child_pid present => reconcile grace bypassed
    time.sleep(0.5)

def test_json_boundary_child_preserved():
    with tempfile.TemporaryDirectory() as td:
        p = run_ag(["run", "--", "echo", "--json"], td)
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert "--json" in p.stdout, f"child --json lost: {p.stdout!r} {p.stderr!r}"
        try:
            obj = json.loads(p.stdout)
            raise AssertionError(f"wrongly enabled JSON mode: {obj}")
        except json.JSONDecodeError:
            pass
        hist = (Path(td)/"history.jsonl").read_text()
        assert "echo --json" in hist, hist

def test_json_before_boundary_enabled():
    with tempfile.TemporaryDirectory() as td:
        p, o = run_json(["run", "--json", "--", "echo", "hi"], td)
        assert o["ok"] and o["data"]["stdout"].strip() == "hi", o
        p2, o2 = run_json(["--json", "run", "--", "echo", "hi"], td)
        assert o2["ok"] and o2["data"]["stdout"].strip() == "hi", o2

def test_spawn_child_json_preserved():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        p = run_ag(["spawn", "--", "echo", "--json"], td)
        assert p.returncode == 0, (p.stdout, p.stderr)
        mp = sessions_json(td)
        assert len(mp) == 1
        st = list(mp.values())[0]
        assert "--json" in (st["cmd"] or ""), st
        # drain
        sid = st["id"]
        run_ag(["wait", sid, "--timeout", "8"], td)

def test_argv_quoting():
    with tempfile.TemporaryDirectory() as td:
        p = run_ag(["run", "--", "printf", "%s\\n", "a b"], td)
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert "a b" in p.stdout and "a\nb" not in p.stdout, f"quoting broken: {p.stdout!r}"
        # single-string shell commands stay byte-identical (documented)
        p2 = run_ag(["run", "--", "echo hi | grep hi"], td)
        assert p2.returncode == 0 and "hi" in p2.stdout, (p2.stdout, p2.stderr)
        p3 = run_ag(["run", "--", "echo hello"], td)
        assert "hello" in p3.stdout, (p3.stdout, p3.stderr)

def test_live_session_compat():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        p = run_ag(["spawn", "--", "echo", "hello-pty"], td)
        assert p.returncode == 0, (p.stdout, p.stderr)
        sid = list(sessions_json(td))[0]
        t0 = time.time()
        p = run_ag(["wait", sid, "--timeout", "8"], td, timeout=15)
        assert time.time()-t0 < 7, "live wait too slow"
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert "exit=0" in p.stdout, p.stdout
        st = read_status(td, sid)
        assert st["running"] is False and st["exit"] == 0 and not st.get("stale"), st
        p, o = run_json(["snap", "--json", sid], td)
        assert o["data"]["running"] is False and o["data"]["exit"] == 0 and "stale" not in o["data"], o

def test_on_exit_compat():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        marker = str(Path(td)/"onexit.marker")
        p = run_ag(["spawn", "--on-exit", f"touch {marker}", "--", "echo", "hi"], td)
        assert p.returncode == 0, (p.stdout, p.stderr)
        sid = list(sessions_json(td))[0]
        run_ag(["wait", sid, "--timeout", "8"], td, timeout=15)
        ok = wait_for(lambda: os.path.exists(marker), timeout=6)
        assert ok, "on_exit did not run for live session"
        st = read_status(td, sid)
        assert st["exit"] == 0 and not st.get("stale"), st

def _kill_daemon(state, sid):
    st = read_status(state, sid)
    dp = st.get("daemon_pid")
    assert dp, st
    os.kill(dp, signal.SIGKILL)
    time.sleep(0.7)  # let kernel reap; child_pid present so no grace delay
    return dp

def test_stale_recovery():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        p = run_ag(["spawn", "--", "cat"], td)
        assert p.returncode == 0, (p.stdout, p.stderr)
        sid = list(sessions_json(td))[0]
        wait_child_pid(td, sid)
        # ensure session old enough that grace cannot mask the kill
        time.sleep(2.2)
        _kill_daemon(td, sid)
        # snap: prompt, stale, not live
        t0 = time.time()
        p, o = run_json(["snap", "--json", sid], td)
        dt = time.time()-t0
        assert dt < 5, f"snap slow after daemon death: {dt}"
        assert o["data"]["running"] is False and o["data"]["exit"] == -1 and o["data"].get("stale") is True, o
        assert "stale" in p.stdout.lower() or o["data"].get("stale"), p.stdout
        # wait: prompt, no full-timeout burn
        t0 = time.time()
        p2 = run_ag(["wait", sid, "--timeout", "10"], td, timeout=15)
        dt2 = time.time()-t0
        assert dt2 < 5, f"wait burned timeout after daemon death: {dt2}"
        assert p2.returncode == 0 and "stale" in p2.stdout.lower(), (p2.stdout, p2.stderr)
        # sessions + status claim dead, carry stale
        mp = sessions_json(td)
        assert mp[sid]["running"] is False and mp[sid]["exit"] == -1 and mp[sid].get("stale") is True, mp[sid]
        ms = status_json(td)
        assert ms[sid]["running"] is False and ms[sid]["exit"] == -1, ms[sid]
        # exactly-once stale event, no phantom exit
        evs = (Path(td)/"events.jsonl").read_text().splitlines()
        stale = [json.loads(l) for l in evs if json.loads(l).get("type") == "stale"]
        mine = [e for e in stale if e.get("session") == sid]
        assert len(mine) == 1, f"stale events !=1: {mine}"
        exits = [json.loads(l) for l in evs if json.loads(l).get("type") == "exit" and json.loads(l).get("session") == sid]
        assert not exits, f"phantom exit after daemon kill: {exits}"
        # second snap: no duplicate event
        run_json(["snap", "--json", sid], td)
        evs2 = (Path(td)/"events.jsonl").read_text().splitlines()
        mine2 = [json.loads(l) for l in evs2 if json.loads(l).get("type") == "stale" and json.loads(l).get("session") == sid]
        assert len(mine2) == 1, f"duplicate stale on re-poll: {mine2}"
        # on_exit field preserved for wake integration (not cleared, not claimed)
        st = read_status(td, sid)
        assert "on_exit" in st, st

def test_stale_preserves_real_exit():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        run_ag(["spawn", "--", "echo", "bye"], td)
        sid = list(sessions_json(td))[0]
        run_ag(["wait", sid, "--timeout", "8"], td, timeout=15)
        time.sleep(0.5)  # daemon reaped normally; pid dead but exit real
        p, o = run_json(["snap", "--json", sid], td)
        assert o["data"]["exit"] == 0 and "stale" not in o["data"], o

def test_no_startup_race():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        run_ag(["spawn", "--", "cat"], td)
        sid = list(sessions_json(td))[0]
        p, o = run_json(["snap", "--json", sid], td)  # immediate, daemon alive
        assert "stale" not in o["data"], f"false stale at startup: {o}"
        assert o["data"]["running"] is True, o
        run_ag(["kill", "--force", sid], td)

def test_kill_and_send_stale():
    if os.name != "posix": return
    with tempfile.TemporaryDirectory() as td:
        run_ag(["spawn", "--", "cat"], td)
        sid = list(sessions_json(td))[0]
        wait_child_pid(td, sid)
        time.sleep(2.2)
        _kill_daemon(td, sid)
        t0 = time.time()
        p = run_ag(["kill", sid], td, timeout=15)
        assert time.time()-t0 < 6, "kill hung on stale"
        assert p.returncode == 0 and "stale" in p.stdout.lower(), (p.stdout, p.stderr)
    with tempfile.TemporaryDirectory() as td:
        run_ag(["spawn", "--", "cat"], td)
        sid = list(sessions_json(td))[0]
        wait_child_pid(td, sid)
        time.sleep(2.2)
        _kill_daemon(td, sid)
        p = run_ag(["send", sid, "hi"], td)
        assert p.returncode != 0 and "daemon gone" in (p.stdout + p.stderr).lower(), (p.stdout, p.stderr)
        _, o = run_json(["snap", "--json", sid], td)
        assert o["data"].get("stale") is True, o

TESTS = [test_json_boundary_child_preserved, test_json_before_boundary_enabled,
    test_spawn_child_json_preserved, test_argv_quoting, test_live_session_compat,
    test_on_exit_compat, test_stale_recovery, test_stale_preserves_real_exit,
    test_no_startup_race, test_kill_and_send_stale]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            fails += 1
            print(f"FAIL {fn.__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"{len(TESTS)-fails}/{len(TESTS)} passed")
    sys.exit(1 if fails else 0)
