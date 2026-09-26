#!/usr/bin/env python3
"""Wake cancellation + hard max-runtime. Stdlib only, fake backends, temp state.

Run: python3 tests/test_ag_wake_control.py
Env: AG_WAKE_BIN overrides binary (default: live repo ag).
"""
import fcntl
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = Path(os.environ.get("AG_WAKE_BIN", _REPO_AG))
assert AG.exists(), f"missing ag binary: {AG}"


def run(state, *args, timeout=20, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    p = subprocess.run([sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=full_env)
    return p


def run_json(state, *args, timeout=20, env=None):
    p = run(state, "--json", *args, timeout=timeout, env=env)
    try:
        obj = json.loads(p.stdout or "{}")
    except Exception:
        obj = {}
    return p, obj


def wait_for(state, pred, timeout=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.2)
    return False


def wakes(state, agent=None):
    args = ["wakes"]
    if agent:
        args += ["--agent", agent]
    _, o = run_json(state, *args)
    return o.get("data", {}).get("wakes", [])


def job(state, jid):
    return next((j for j in wakes(state) if j.get("id") == jid), {})


def events(state):
    _, o = run_json(state, "events", "--limit", "100")
    return o.get("data", {}).get("events", [])


def chat_log(state, name):
    _, o = run_json(state, "chat", "log", name, "--limit", "30")
    return o.get("data", {}).get("log", [])


def make_slow_printf(bindir, pidfile, sleep_s=60):
    """Fake `printf` (echo backend argv[0]) that ignores TERM and spawns a
    TERM-ignoring child. Records backend + child pids to pidfile."""
    bindir.mkdir(parents=True, exist_ok=True)
    pidfile = str(pidfile)
    script = bindir / "printf"
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import os, signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"PF = {pidfile!r}\n"
        "child = subprocess.Popen([sys.executable, '-c',\n"
        " 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'])\n"
        "open(PF, 'w').write(f\"{os.getpid()} {child.pid}\\n\")\n"
        f"time.sleep({int(sleep_s)})\n"
        "print('fake-backend-done')\n"
    )
    script.chmod(0o755)
    return bindir


def fake_env(bindir):
    return {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}


def read_pids(pidfile):
    try:
        parts = Path(pidfile).read_text().strip().split()
        return [int(x) for x in parts]
    except Exception:
        return []


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_cancel_active_no_replay():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "c1", "--backend", "echo", "--role", "sub").returncode == 0
    bindir = Path(tempfile.mkdtemp(prefix="agwc-bin-"))
    pidfile = td / "backend.pids"
    make_slow_printf(bindir, pidfile)
    env = fake_env(bindir)
    _, o = run_json(td, "wake", "c1", "slow-work", env=env)
    assert o.get("ok"), o
    jid = o["data"].get("job")
    assert jid, o
    assert wait_for(td, lambda: job(td, jid).get("status") == "running"
        and read_pids(pidfile), timeout=15), wakes(td)
    p, o = run_json(td, "wakes", "--cancel", jid)
    assert p.returncode == 0, (p.stdout, p.stderr)
    assert wait_for(td, lambda: job(td, jid).get("status") == "failed", timeout=15), wakes(td)
    j = job(td, jid)
    assert j.get("cancelled") is True, j
    assert "cancel" in (j.get("error") or "").lower(), j
    pids = read_pids(pidfile)
    assert pids, "fake backend never recorded pids"
    assert wait_for(td, lambda: not any(alive(x) for x in read_pids(pidfile)), timeout=15), pids
    time.sleep(2)  # replay window: cancelled work must not land later
    j2 = job(td, jid)
    assert j2.get("status") == "failed" and j2.get("cancelled") is True, j2
    assert not any(r.get("role") == "agent" and "slow-work" in (r.get("text") or "")
        for r in chat_log(td, "c1")), chat_log(td, "c1")
    print("ok test_cancel_active_no_replay")


def test_cancel_queued_keeps_holder():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "q1", "--backend", "echo", "--role", "sub").returncode == 0
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "q1.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o1 = run_json(td, "wake", "q1", "holder-msg")
        _, o2 = run_json(td, "wake", "q1", "queued-msg")
        assert o1.get("ok") and o2.get("ok"), (o1, o2)
        j1, j2 = o1["data"].get("job"), o2["data"].get("job")
        assert j1 and j2 and j1 != j2, (o1, o2)
        time.sleep(3)  # both workers blocked on the guard
        p, o = run_json(td, "wakes", "--cancel", j2)
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert wait_for(td, lambda: job(td, j2).get("status") == "failed", timeout=10), wakes(td)
        assert job(td, j2).get("cancelled") is True, job(td, j2)
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    assert wait_for(td, lambda: job(td, j1).get("status") == "done", timeout=15), wakes(td)
    assert job(td, j2).get("status") == "failed", job(td, j2)
    users = [r["text"] for r in chat_log(td, "q1") if r["role"] == "user"]
    assert "holder-msg" in users, users
    assert "queued-msg" not in users, users
    print("ok test_cancel_queued_keeps_holder")


def test_cancel_terminal_noop():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "t1", "--backend", "echo", "--role", "sub").returncode == 0
    _, o = run_json(td, "wake", "t1", "quick")
    jid = o["data"].get("job")
    assert wait_for(td, lambda: job(td, jid).get("status") == "done", timeout=15), wakes(td)
    fails_before = len([e for e in events(td) if e["type"] == "wake_fail"])
    p, o = run_json(td, "wakes", "--cancel", jid)
    assert p.returncode == 0, (p.stdout, p.stderr)
    j = job(td, jid)
    assert j.get("status") == "done", j
    assert j.get("cancelled") is not True, j
    fails_after = len([e for e in events(td) if e["type"] == "wake_fail"])
    assert fails_after == fails_before, (fails_before, fails_after)
    print("ok test_cancel_terminal_noop")


def test_max_runtime_kills_tree_and_releases_guard():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "m1", "--backend", "echo", "--role", "sub").returncode == 0
    bindir = Path(tempfile.mkdtemp(prefix="agwc-bin-"))
    pidfile = td / "backend.pids"
    make_slow_printf(bindir, pidfile)
    env = fake_env(bindir)
    _, o = run_json(td, "wake", "m1", "slow-deadline", "--max-runtime", "1", env=env)
    assert o.get("ok"), o
    jid = o["data"].get("job")
    assert wait_for(td, lambda: job(td, jid).get("status") == "failed", timeout=15), wakes(td)
    j = job(td, jid)
    assert j.get("timed_out") is True, j
    assert "runtime" in (j.get("error") or "").lower() or "max" in (j.get("error") or "").lower(), j
    assert wait_for(td, lambda: not any(alive(x) for x in read_pids(pidfile)), timeout=15), read_pids(pidfile)
    # guard released: next turn on same agent succeeds
    _, o2 = run_json(td, "wake", "m1", "after-timeout")
    assert o2.get("ok"), o2
    j2 = o2["data"].get("job")
    assert wait_for(td, lambda: job(td, j2).get("status") == "done", timeout=15), wakes(td)
    print("ok test_max_runtime_kills_tree_and_releases_guard")


def test_max_runtime_waits_for_guard():
    """Runtime budget starts after guard acquire: queued time doesn't burn it."""
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "g1", "--backend", "echo", "--role", "sub").returncode == 0
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "g1.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o = run_json(td, "wake", "g1", "guarded-fast", "--max-runtime", "2")
        assert o.get("ok"), o
        jid = o["data"].get("job")
        time.sleep(4)  # queued longer than the runtime budget
        j = job(td, jid)
        assert j.get("status") in ("queued", "running"), j
        assert j.get("timed_out") is not True, j
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    assert wait_for(td, lambda: job(td, jid).get("status") == "done", timeout=15), wakes(td)
    assert job(td, jid).get("timed_out") is not True, job(td, jid)
    print("ok test_max_runtime_waits_for_guard")


def test_stall_behavior_unchanged():
    """Queued jobs waiting for the turn guard never stall (status track):
    hold the guard past the timeout, see queued + no stall, release to done."""
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "s1", "--backend", "echo", "--role", "sub").returncode == 0
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "s1.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o = run_json(td, "wake", "s1", "slow-stall", "--timeout", "1")
        jid = o["data"].get("job")
        assert jid, o
        assert wait_for(td, lambda: job(td, jid).get("status") == "queued", timeout=10), wakes(td)
        time.sleep(3)  # past the 1s timeout while still queued
        j = job(td, jid)
        assert j.get("status") == "queued" and not j.get("stalled"), j
        assert not any(e["type"] == "wake_stall" and e.get("job") == jid
            for e in events(td)), events(td)
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    assert wait_for(td, lambda: job(td, jid).get("status") == "done", timeout=15), wakes(td)
    assert not job(td, jid).get("stalled"), wakes(td)
    print("ok test_stall_behavior_unchanged")


def test_invalid_max_runtime_rejected():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "v1", "--backend", "echo", "--role", "sub").returncode == 0
    for bad in ("-1", "nan", "inf", "-inf"):
        p = run(td, "wake", "v1", "msg", "--max-runtime", bad)
        assert p.returncode != 0, (bad, p.stdout, p.stderr)
        assert "max-runtime" in (p.stderr or p.stdout).lower(), (bad, p.stdout, p.stderr)
    assert wakes(td, "v1") == [], wakes(td, "v1")
    print("ok test_invalid_max_runtime_rejected")


def test_cancel_wait_conflict():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "x1", "--backend", "echo", "--role", "sub").returncode == 0
    p = run(td, "wakes", "--wait", "abc123", "--cancel", "abc123")
    assert p.returncode != 0, (p.stdout, p.stderr)
    print("ok test_cancel_wait_conflict")


def test_chat_send_timeout_kills_tree_and_releases_guard():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "f1", "--backend", "echo", "--role", "sub").returncode == 0
    bindir = Path(tempfile.mkdtemp(prefix="agwc-bin-"))
    pidfile = td / "backend.pids"
    make_slow_printf(bindir, pidfile)
    env = fake_env(bindir)
    p, o = run_json(td, "chat", "send", "f1", "--timeout", "1", "slow-fg", env=env)
    assert p.returncode == 124, (p.stdout, p.stderr)
    assert o.get("ok") is False, o
    assert o["data"].get("timed_out") is True and o["data"].get("exit") == 124, o
    assert "max-runtime" in (o["data"].get("error") or "").lower(), o
    assert wait_for(td, lambda: not any(alive(x) for x in read_pids(pidfile))
        and read_pids(pidfile), timeout=15), read_pids(pidfile)
    p2 = run(td, "chat", "send", "f1", "after-fg")
    assert p2.returncode == 0, (p2.stdout, p2.stderr)
    print("ok test_chat_send_timeout_kills_tree_and_releases_guard")


def test_chat_send_timeout_validation():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "f2", "--backend", "echo", "--role", "sub").returncode == 0
    for bad in ("-1", "nan", "inf", "-inf"):
        p = run(td, "chat", "send", "f2", "--timeout", bad, "msg")
        assert p.returncode != 0, (bad, p.stdout, p.stderr)
        assert "timeout" in (p.stderr or p.stdout).lower(), (bad, p.stdout, p.stderr)
    p = run(td, "chat", "send", "f2", "--timeout", "0", "fast-ok")
    assert p.returncode == 0, (p.stdout, p.stderr)
    print("ok test_chat_send_timeout_validation")


def test_delegate_timeout_parity():
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "orch", "--backend", "echo", "--role", "orchestrator").returncode == 0
    assert run(td, "agents", "add", "d1", "--backend", "echo", "--role", "sub").returncode == 0
    bindir = Path(tempfile.mkdtemp(prefix="agwc-bin-"))
    pidfile = td / "backend.pids"
    make_slow_printf(bindir, pidfile)
    env = fake_env(bindir)
    p, o = run_json(td, "delegate", "d1", "--timeout", "1", "slow-task", env=env)
    assert p.returncode == 124, (p.stdout, p.stderr)
    assert o.get("ok") is False, o
    assert o["data"].get("timed_out") is True and o["data"].get("exit") == 124, o
    assert "max-runtime" in (o["data"].get("error") or "").lower(), o
    assert wait_for(td, lambda: not any(alive(x) for x in read_pids(pidfile))
        and read_pids(pidfile), timeout=15), read_pids(pidfile)
    for bad in ("-1", "nan", "inf"):
        p = run(td, "delegate", "d1", "--timeout", bad, "msg")
        assert p.returncode != 0, (bad, p.stdout, p.stderr)
    print("ok test_delegate_timeout_parity")


def test_cancel_before_running_never_resurrects():
    """Cancel lands between enqueue and the worker publishing running: the
    job must stay failed/cancelled and never run or replay."""
    td = Path(tempfile.mkdtemp(prefix="agwc-"))
    assert run(td, "agents", "add", "z1", "--backend", "echo", "--role", "sub").returncode == 0
    lockdir = td / "wake" / "locks"
    lockdir.mkdir(parents=True, exist_ok=True)
    lockf = open(lockdir / "z1.lock", "a+b")
    fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
    try:
        _, o = run_json(td, "wake", "z1", "early-msg")
        assert o.get("ok"), o
        jid = o["data"].get("job")
        time.sleep(1)  # worker spawned, blocked before guard
        p, o = run_json(td, "wakes", "--cancel", jid)
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert wait_for(td, lambda: job(td, jid).get("status") == "failed", timeout=10), wakes(td)
    finally:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)
        lockf.close()
    time.sleep(3)  # resurrect window: worker must not flip back to running
    j = job(td, jid)
    assert j.get("status") == "failed" and j.get("cancelled") is True, j
    users = [r["text"] for r in chat_log(td, "z1") if r["role"] == "user"]
    assert "early-msg" not in users, users
    print("ok test_cancel_before_running_never_resurrects")


if __name__ == "__main__":
    test_cancel_active_no_replay()
    test_cancel_queued_keeps_holder()
    test_cancel_terminal_noop()
    test_max_runtime_kills_tree_and_releases_guard()
    test_max_runtime_waits_for_guard()
    test_stall_behavior_unchanged()
    test_invalid_max_runtime_rejected()
    test_cancel_wait_conflict()
    test_chat_send_timeout_kills_tree_and_releases_guard()
    test_chat_send_timeout_validation()
    test_delegate_timeout_parity()
    test_cancel_before_running_never_resurrects()
    print("all wake-control tests passed")
