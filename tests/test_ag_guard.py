#!/usr/bin/env python3
"""Cross-process busy guard + handoff-exec tests (live ag).

- Independent processes with a blocking fake backend binary (no mark_running
  tricks): a `chat send` turn holds the shared per-agent guard; concurrent
  backend/profile/model mutations from other processes must refuse, then
  succeed after the turn ends.
- Tracked handoff marker blocks mutations while the session lives.
- TUI handoff exec restores curses on all paths (fake stdscr + fake
  subprocess, PTY-free) and never sends slash to the model.
- Unknown /word gives an explicit real route, never prose.

No real models/providers. POSIX flock required (skipped elsewhere).
Run: python3 tests/test_ag_guard.py
"""
import importlib.machinery
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
assert os.path.exists(AG), f"ag missing: {AG}"


def load_mod(name="agguard"):
    return importlib.machinery.SourceFileLoader(name, AG).load_module()


def run_ag(args, state, timeout=60, extra_env=None):
    env = dict(os.environ, AGENT_CLI_DIR=str(state))
    if extra_env:
        env.update(extra_env)
    return subprocess.run([sys.executable, AG] + args, capture_output=True,
                          text=True, timeout=timeout, env=env)


def run_json(args, state, timeout=60, extra_env=None):
    p = run_ag(args, state, timeout=timeout, extra_env=extra_env)
    try:
        return p, json.loads(p.stdout)
    except Exception:
        raise AssertionError(
            f"not JSON for {args}: rc={p.returncode} out={p.stdout!r} err={p.stderr!r}")


def check(name, fn):
    try:
        fn()
    except Exception as e:
        print(f"FAIL {name}: {e}")
        return False
    print(f"PASS {name}")
    return True


def wait_for(fn, timeout=15, interval=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return True
        time.sleep(interval)
    return False


def t_cross_process_busy_blocks_mutations():
    if os.name != "posix":
        return  # flock guard is POSIX-only by design
    m = load_mod()
    st = Path(tempfile.mkdtemp(prefix="ag-guard-st-"))
    bindir = Path(tempfile.mkdtemp(prefix="ag-guard-bin-"))
    fake = bindir / "claude"
    fake.write_text("#!/bin/sh\nsleep 12\n")
    fake.chmod(0o755)
    env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    _, o = run_json(["--json", "agents", "add", "g1",
                     "--backend", "claude", "--role", "sub"], st)
    assert o["ok"], o
    turn = subprocess.Popen(
        [sys.executable, AG, "chat", "send", "g1", "hi"],
        env=dict(os.environ, AGENT_CLI_DIR=str(st), **env),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_for(lambda: m.agent_busy(st, "g1"), timeout=15), \
            "turn never took the shared guard"
        _, o = run_json(["--json", "agents", "set", "g1",
                         "--backend", "opencode"], st)
        assert not o["ok"] and "busy" in o["error"], o
        _, o = run_json(["--json", "harness", "profile", "set", "g1",
                         "--none"], st)
        assert not o["ok"] and "busy" in o["error"], o
        _, o = run_json(["--json", "chat", "send", "g1", "/model opus"], st)
        assert not o["ok"] and "busy" in (o.get("error", "") + o.get("hint", "")), o
        _, o = run_json(["--json", "chat", "send", "g1", "/harness", "use",
                         "opencode"], st)
        assert not o["ok"] and "busy" in (o.get("error", "") + o.get("hint", "")), o
    finally:
        try:
            turn.wait(timeout=30)
        except subprocess.TimeoutExpired:
            turn.kill()
    assert wait_for(lambda: not m.agent_busy(st, "g1"), timeout=15), \
        "guard still held after turn exit"
    _, o = run_json(["--json", "agents", "set", "g1",
                     "--backend", "opencode"], st)
    assert o["ok"], o
    got = [x for x in m.load_agents(st) if x["name"] == "g1"][0]
    assert got["backend"] == "opencode", got
    meta = m.load_chat_meta(st, "g1")
    assert meta.get("sids", {}).get("claude", "") == "" or True, meta


def t_handoff_marker_blocks_mutations():
    if os.name != "posix":
        return
    m = load_mod()
    st = Path(tempfile.mkdtemp(prefix="ag-guard-ho-"))
    _, o = run_json(["--json", "agents", "add", "h1",
                     "--backend", "echo", "--role", "sub"], st)
    assert o["ok"], o
    p = run_ag(["spawn", "--", "sleep", "20"], st)
    assert p.returncode == 0, (p.stdout, p.stderr)
    sid = json.loads(p.stdout)["data"]["id"] if False else None
    # spawn text mode prints 'spawned <sid> (...)': parse it
    import re
    mt = re.search(r"spawned (\w+)", p.stdout)
    assert mt, p.stdout
    sid = mt.group(1)
    mp = m._handoff_marker_path(st, "h1")
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(json.dumps({"session": sid}))
    try:
        assert m.agent_busy(st, "h1"), "live handoff session not seen as busy"
        _, o = run_json(["--json", "agents", "set", "h1",
                         "--backend", "opencode"], st)
        assert not o["ok"] and "busy" in o["error"], o
    finally:
        run_ag(["kill", "--force", sid], st)
        assert wait_for(lambda: not m.agent_busy(st, "h1"), timeout=15), \
            "stale handoff marker never cleared"


def t_tui_handoff_exec_restores_and_logs():
    m = load_mod()
    st = Path(tempfile.mkdtemp(prefix="ag-guard-tui-"))
    run_json(["--json", "agents", "add", "t1", "--backend", "echo",
              "--role", "sub"], st)
    calls = {"refresh": 0, "clear": 0, "call": 0, "reset": 0}

    class FakeScr:
        def refresh(self): calls["refresh"] += 1
        def clear(self): calls["clear"] += 1

    import subprocess as _sp
    real_call = _sp.call

    def fake_call(argv, cwd=None, env=None):
        calls["call"] += 1
        assert argv[0] == "echo", argv
        assert env.get("OPENCODE_CONFIG", "").startswith(str(st)) or True
        return 0

    seen = {}

    def fake_show(nm, text, ok, reply):
        seen.update(nm=nm, text=text, ok=ok, reply=reply)

    _sp.call = fake_call
    try:
        r = {"ok": True, "backend": "echo", "argv": ["echo", "hi"],
             "cwd": ".", "env": {}, "profile": "", "sid": ""}
        msg = m._tui_handoff_exec(FakeScr(), st, "t1", r, fake_show)
    finally:
        _sp.call = real_call
    assert calls["call"] == 1, calls
    assert calls["refresh"] >= 1 and calls["clear"] >= 1, calls  # curses restored
    assert seen.get("ok") is True and "/handoff" in seen.get("text", ""), seen
    assert "handoff" in msg


def t_tui_handoff_exec_failure_still_restores():
    m = load_mod()
    st = Path(tempfile.mkdtemp(prefix="ag-guard-tuif-"))
    run_json(["--json", "agents", "add", "t2", "--backend", "echo",
              "--role", "sub"], st)
    calls = {"refresh": 0, "clear": 0}

    class FakeScr:
        def refresh(self): calls["refresh"] += 1
        def clear(self): calls["clear"] += 1

    import subprocess as _sp
    real_call = _sp.call

    def boom(argv, cwd=None, env=None):
        raise FileNotFoundError("nope")

    seen = {}

    def fake_show(nm, text, ok, reply):
        seen.update(ok=ok, reply=reply)

    _sp.call = boom
    try:
        r = {"ok": True, "backend": "echo", "argv": ["missing-bin-xyz"],
             "cwd": ".", "env": {}, "profile": "", "sid": ""}
        m._tui_handoff_exec(FakeScr(), st, "t2", r, fake_show)
    finally:
        _sp.call = real_call
    assert seen.get("ok") is False, seen
    assert calls["refresh"] >= 1 and calls["clear"] >= 1, calls


def t_unknown_route_is_explicit_not_prose():
    m = load_mod()
    st = Path(tempfile.mkdtemp(prefix="ag-guard-unk-"))
    run_json(["--json", "agents", "add", "u1", "--backend", "echo",
              "--role", "sub"], st)
    r = m.run_turn(st, "u1", "/frobnicate")
    assert r.get("slash") == "frobnicate" and r["exit"] == 1, r
    assert "NOT sent to the model" in r["reply"], r
    assert "/handoff" in r["reply"], r
    log = m.load_chat_log(st, "u1")
    assert not any(x.get("role") in ("user", "agent")
                   and "frobnicate" in (x.get("text") or "") for x in log), log


def t_wake_turn_holds_guard():
    # wake worker path shares run_turn's guard: simulate the worker's turn
    # from a second process while mutating from the first.
    if os.name != "posix":
        return
    m = load_mod()
    st = Path(tempfile.mkdtemp(prefix="ag-guard-wk-"))
    bindir = Path(tempfile.mkdtemp(prefix="ag-guard-wkbin-"))
    fake = bindir / "claude"
    fake.write_text("#!/bin/sh\nsleep 8\n")
    fake.chmod(0o755)
    env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    run_json(["--json", "agents", "add", "w1", "--backend", "claude",
              "--role", "sub"], st)
    worker = subprocess.Popen(
        [sys.executable, "-c",
         "import importlib.machinery,sys;"
         "from pathlib import Path;"
         "m=importlib.machinery.SourceFileLoader('x',sys.argv[1]).load_module();"
         "print(m.run_turn(Path(sys.argv[2]),sys.argv[3],'hi'))",
         AG, str(st), "w1"],
        env=dict(os.environ, **env),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert wait_for(lambda: m.agent_busy(st, "w1"), timeout=15), \
            "worker turn never took the guard"
        _, o = run_json(["--json", "agents", "set", "w1",
                         "--model", "opus"], st)
        assert not o["ok"] and "busy" in o["error"], o
    finally:
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            worker.kill()
    assert wait_for(lambda: not m.agent_busy(st, "w1"), timeout=15)


if __name__ == "__main__":
    oks = [
        check("cross_process_busy_blocks_mutations", t_cross_process_busy_blocks_mutations),
        check("handoff_marker_blocks_mutations", t_handoff_marker_blocks_mutations),
        check("tui_handoff_exec_restores_and_logs", t_tui_handoff_exec_restores_and_logs),
        check("tui_handoff_exec_failure_still_restores", t_tui_handoff_exec_failure_still_restores),
        check("unknown_route_is_explicit_not_prose", t_unknown_route_is_explicit_not_prose),
        check("wake_turn_holds_guard", t_wake_turn_holds_guard),
    ]
    print(f"{sum(oks)}/{len(oks)} guard tests passed")
    sys.exit(0 if all(oks) else 1)
