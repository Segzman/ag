#!/usr/bin/env python3
"""SSH-like wake regressions: workdir parity foreground vs wake. Stdlib only.

Run: python3 tests/test_ag_ssh_wake.py
Env: AG_SSH_BIN overrides binary (baseline-red uses a /tmp copy of ag).

Validation uses an SSH-like subprocess environment (minimal PATH,
separate launch cwd/state dir, stdin DEVNULL, exited caller); no real
network involved.

Fake opencode backend prints `CWD=<pwd> DIR=<--dir value>` as the JSON
text part; tests read it back from the agent reply (bounded, last line).
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = Path(os.environ.get("AG_SSH_BIN", os.environ.get("AG_WAKE_BIN", _REPO_AG)))
assert AG.exists(), f"missing ag binary: {AG}"

MIN_PATH = "/usr/bin:/bin"


def run(state, *args, timeout=25, env=None, cwd=None, stdin_devnull=False):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    return subprocess.run(
        [sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=full_env,
        cwd=cwd or "/tmp",
        stdin=subprocess.DEVNULL if stdin_devnull else None)


def run_json(state, *args, **kw):
    p = run(state, "--json", *args, **kw)
    try:
        obj = json.loads(p.stdout or "{}")
    except Exception:
        obj = {}
    return p, obj


def wait_job(state, jid, timeout=20):
    t0 = time.time()
    sp = Path(str(state))
    while time.time() - t0 < timeout:
        for f in (sp / "wake" / "jobs").glob("*.json"):
            try:
                j = json.loads(f.read_text())
            except Exception:
                continue
            if j.get("id") == jid and j.get("status") in ("done", "failed"):
                return j
        time.sleep(0.2)
    return {}


def last_agent_text(state, name):
    _, o = run_json(state, "chat", "log", name, "--limit", "30")
    rows = (o.get("data", {}) or {}).get("log", [])
    for r in reversed(rows):
        if r.get("role") == "agent":
            return (r.get("text", "") or "")[-300:]
    return ""


def make_opencode(path, delay=0):
    """Fake opencode: reports pwd + --dir value as JSON text event."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "#!/bin/sh\n"
        "d=''; prev=''\n"
        "for a in \"$@\"; do if [ \"$prev\" = \"--dir\" ]; then d=\"$a\"; fi; prev=\"$a\"; done\n"
        "msg=\"CWD=$(pwd) DIR=$d\"\n"
        + (f"sleep {delay}\n" if delay else "") +
        "esc=$(printf '%s' \"$msg\" | sed 's/\\\\/\\\\\\\\/g; s/\"/\\\\\"/g')\n"
        "printf '{\"type\":\"text\",\"part\":{\"text\":\"%s\"}}\\n' \"$esc\"\n"
        "printf '{\"type\":\"step_finish\",\"part\":{\"reason\":\"stop\"}}\\n'\n"
    )
    path.chmod(0o755)


def setup_proj(home_extra_bin=None):
    proj = Path(tempfile.mkdtemp(prefix="agssh-proj-")).resolve()
    (proj / "sub").mkdir()
    state = Path(tempfile.mkdtemp(prefix="agssh-state-")).resolve()
    home = Path(tempfile.mkdtemp(prefix="agssh-home-")).resolve()
    make_opencode(home / ".opencode" / "bin" / "opencode")
    env = {"PATH": MIN_PATH, "HOME": str(home)}
    if home_extra_bin:
        env["PATH"] = home_extra_bin + ":" + MIN_PATH
    return proj, state, env


def add_oc(state, name, adir, env, cwd):
    p = run(state, "agents", "add", name, "--backend", "opencode",
            "--role", "sub", "--dir", adir, env=env, cwd=cwd)
    assert p.returncode == 0, (p.returncode, p.stderr[-300:])


def foreground_probe(state, name, env, cwd):
    p, o = run_json(state, "chat", "send", name, "probe", env=env, cwd=cwd)
    assert p.returncode == 0 and o.get("ok"), (p.returncode, p.stdout[-300:], p.stderr[-300:])
    return last_agent_text(state, name)


def wake_probe(state, name, env, cwd):
    p, o = run_json(state, "wake", name, "probe", env=env, cwd=cwd)
    assert p.returncode == 0 and o.get("ok"), (p.returncode, p.stdout[-300:], p.stderr[-300:])
    jid = (o.get("data", {}) or {}).get("job", "")
    assert jid, o
    job = wait_job(state, jid)
    assert job.get("status") == "done", job
    return last_agent_text(state, name)


def test_foreground_wake_workdir_parity():
    """Dot + relative subproject agent dirs: wake must match foreground."""
    for adir, expect in ((".", "PROJ"), ("sub", "SUB")):
        proj, state, env = setup_proj()
        name = "oc-" + ("dot" if adir == "." else "sub")
        add_oc(state, name, adir, env, str(proj))
        fg = foreground_probe(state, name, env, str(proj))
        wk = wake_probe(state, name, env, str(proj))
        want = str(proj if expect == "PROJ" else proj / "sub")
        for label, got in (("foreground", fg), ("wake", wk)):
            assert f"CWD={want}" in got, (adir, label, got)
            assert f"DIR={want}" in got, (adir, label, got)
        assert fg.strip().splitlines()[-1] == wk.strip().splitlines()[-1], (fg, wk)
    print("ok test_foreground_wake_workdir_parity")


def test_absolute_agent_dir_unchanged():
    proj, state, env = setup_proj()
    target = proj / "sub"
    add_oc(state, "ocabs", str(target), env, str(proj))
    fg = foreground_probe(state, "ocabs", env, str(proj))
    wk = wake_probe(state, "ocabs", env, str(proj))
    assert f"CWD={target}" in fg and f"DIR={target}" in fg, fg
    assert f"CWD={target}" in wk and f"DIR={target}" in wk, wk
    print("ok test_absolute_agent_dir_unchanged")


def test_relative_state_dir_and_minimal_path():
    """Relative --dir from project + minimal-PATH fallback binary."""
    proj = Path(tempfile.mkdtemp(prefix="agssh-relproj-")).resolve()
    (proj / "sub").mkdir()
    home = Path(tempfile.mkdtemp(prefix="agssh-relhome-")).resolve()
    make_opencode(home / ".opencode" / "bin" / "opencode")
    env = {"PATH": MIN_PATH, "HOME": str(home)}
    p = run(".agent-rel", "agents", "add", "ocrel", "--backend", "opencode",
            "--role", "sub", "--dir", "sub", env=env, cwd=str(proj))
    assert p.returncode == 0, (p.returncode, p.stderr[-300:])
    p, o = run_json(".agent-rel", "wake", "ocrel", "probe", env=env, cwd=str(proj))
    assert p.returncode == 0 and o.get("ok"), (p.returncode, p.stdout[-300:])
    jid = (o.get("data", {}) or {}).get("job", "")
    job = wait_job(proj / ".agent-rel", jid)
    assert job.get("status") == "done", job
    sess = job.get("session", "")
    st = json.loads((proj / ".agent-rel" / "sessions" / sess / "status.json").read_text())
    assert "--dir /" in st.get("cmd", ""), st.get("cmd", "")[-200:]
    _, o = run_json(proj / ".agent-rel", "chat", "log", "ocrel", "--limit", "10")
    rows = (o.get("data", {}) or {}).get("log", [])
    txt = next((r.get("text", "") for r in reversed(rows) if r.get("role") == "agent"), "")
    assert f"CWD={proj / 'sub'}" in txt and f"DIR={proj / 'sub'}" in txt, txt[-200:]
    print("ok test_relative_state_dir_and_minimal_path")


def test_relative_path_bin_with_subproject():
    """Relative PATH entry binary must launch with subproject workdir."""
    proj = Path(tempfile.mkdtemp(prefix="agssh-relbin-")).resolve()
    (proj / "sub").mkdir()
    (proj / "relbin").mkdir()
    make_opencode(proj / "relbin" / "opencode")
    home = Path(tempfile.mkdtemp(prefix="agssh-relbinhome-")).resolve()
    env = {"PATH": "relbin:" + MIN_PATH, "HOME": str(home)}
    state = Path(tempfile.mkdtemp(prefix="agssh-relbinstate-")).resolve()
    add_oc(state, "ocrb", "sub", env, str(proj))
    got = wake_probe(state, "ocrb", env, str(proj))
    assert f"CWD={proj / 'sub'}" in got, got
    assert f"DIR={proj / 'sub'}" in got, got
    print("ok test_relative_path_bin_with_subproject")


def test_detached_slow_backend_survives_caller_exit():
    """Delayed backend + DEVNULL stdin: wake returns fast, job completes."""
    proj = Path(tempfile.mkdtemp(prefix="agssh-detproj-")).resolve()
    state = Path(tempfile.mkdtemp(prefix="agssh-detstate-")).resolve()
    home = Path(tempfile.mkdtemp(prefix="agssh-dethome-")).resolve()
    make_opencode(home / ".opencode" / "bin" / "opencode", delay=5)
    env = {"PATH": MIN_PATH, "HOME": str(home)}
    add_oc(state, "ocdet", ".", env, str(proj))
    t0 = time.time()
    p, o = run_json(state, "wake", "ocdet", "slow task", env=env, cwd=str(proj),
                    stdin_devnull=True)
    cli_dt = time.time() - t0
    assert p.returncode == 0 and o.get("ok"), (p.returncode, p.stdout[-300:])
    assert cli_dt < 4, cli_dt  # caller not waiting on backend
    jid = (o.get("data", {}) or {}).get("job", "")
    job = wait_job(state, jid, timeout=25)
    assert job.get("status") == "done", job
    print("ok test_detached_slow_backend_survives_caller_exit")


if __name__ == "__main__":
    test_foreground_wake_workdir_parity()
    test_absolute_agent_dir_unchanged()
    test_relative_state_dir_and_minimal_path()
    test_relative_path_bin_with_subproject()
    test_detached_slow_backend_survives_caller_exit()
    print("all ssh wake tests passed")
