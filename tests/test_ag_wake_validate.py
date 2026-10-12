#!/usr/bin/env python3
"""Wake-validate: reject unknown agents at call time + max-runtime chat hint."""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = Path(os.environ.get("AG_WAKE_BIN", _REPO_AG))
assert AG.exists(), f"missing ag binary: {AG}"

def run(state, *args, timeout=20, env=None):
    full_env = dict(os.environ)
    if env: full_env.update(env)
    return subprocess.run([sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=full_env)

def run_json(state, *args, timeout=20, env=None):
    p = run(state, "--json", *args, timeout=timeout, env=env)
    try: obj = json.loads(p.stdout or "{}")
    except Exception: obj = {}
    return p, obj

def events(state):
    _, o = run_json(state, "events", "--limit", "100")
    return o.get("data", {}).get("events", [])

def chat_log(state, name):
    _, o = run_json(state, "chat", "log", name, "--limit", "30")
    return o.get("data", {}).get("log", [])

def jobs(state):
    d = Path(state) / "wake" / "jobs"
    return list(d.glob("*.json")) if d.exists() else []

def test_wake_unknown_rejects_no_job():
    td = Path(tempfile.mkdtemp(prefix="agwv-"))
    assert run(td, "agents", "add", "alpha", "--backend", "echo", "--role", "sub").returncode == 0
    p = run(td, "wake", "alpah", "hi")
    assert p.returncode != 0, (p.stdout, p.stderr)
    out = (p.stderr or "") + (p.stdout or "")
    assert "error: no such agent: alpah" in out, out
    assert "alpha" in out and "ag agents list" in out, out
    _, o = run_json(td, "wake", "alpah", "hi")
    assert o.get("ok") is False and "no such agent" in o.get("error", ""), o
    assert "alpha" in (o.get("hint") or "") and "ag agents list" in (o.get("hint") or ""), o
    assert jobs(td) == [], jobs(td)
    assert not any(e.get("type") in ("wake", "wake_fail") for e in events(td)), events(td)
    print("ok test_wake_unknown_rejects_no_job")

def test_delegate_unknown_rejects():
    td = Path(tempfile.mkdtemp(prefix="agwv-"))
    assert run(td, "agents", "add", "alpha", "--backend", "echo", "--role", "sub").returncode == 0
    p = run(td, "delegate", "alpah", "do thing")
    assert p.returncode != 0, (p.stdout, p.stderr)
    out = (p.stderr or "") + (p.stdout or "")
    assert "no such agent: alpah" in out and "alpha" in out and "ag agents list" in out, out
    _, o = run_json(td, "delegate", "alpah", "do thing")
    assert o.get("ok") is False and "no such agent" in o.get("error", ""), o
    assert "ag agents list" in (o.get("hint") or ""), o
    print("ok test_delegate_unknown_rejects")

def test_delegate_parent_unknown_rejects_no_job():
    td = Path(tempfile.mkdtemp(prefix="agwv-"))
    assert run(td, "agents", "add", "kid", "--backend", "echo", "--role", "sub").returncode == 0
    p = run(td, "delegate", "kid", "task", "--parent", "nosuchpar")
    assert p.returncode != 0, (p.stdout, p.stderr)
    assert "no such agent" in ((p.stderr or "") + (p.stdout or ""))
    _, o = run_json(td, "delegate", "nosuchkid", "task", "--parent", "kid")
    assert o.get("ok") is False and "no such agent" in o.get("error", ""), o
    assert jobs(td) == [], jobs(td)
    print("ok test_delegate_parent_unknown_rejects_no_job")

def test_max_runtime_chat_hint():
    td = Path(tempfile.mkdtemp(prefix="agwv-"))
    assert run(td, "agents", "add", "m1", "--backend", "echo", "--role", "sub").returncode == 0
    bindir = Path(tempfile.mkdtemp(prefix="agwv-bin-"))
    (bindir / "printf").write_text("#!/usr/bin/env python3\nimport time; time.sleep(60)\nprint('done')\n")
    (bindir / "printf").chmod(0o755)
    env = {"PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    p, o = run_json(td, "chat", "send", "m1", "--timeout", "1", "slow-fg", env=env)
    assert p.returncode == 124, (p.stdout, p.stderr)
    log = chat_log(td, "m1")
    errs = [r.get("text", "") for r in log if r.get("role") == "error"]
    assert errs, log
    assert errs[-1].endswith("(raise --max-runtime or split the task)"), errs[-1]
    print("ok test_max_runtime_chat_hint")

if __name__ == "__main__":
    test_wake_unknown_rejects_no_job()
    test_delegate_unknown_rejects()
    test_delegate_parent_unknown_rejects_no_job()
    test_max_runtime_chat_hint()
    print("all wake-validate tests ok")
