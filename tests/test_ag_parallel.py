#!/usr/bin/env python3
"""Parallel-turn regression: >2 distinct agents overlap, no kill-on-launch.

Proves on this Mac (POSIX flock):
- 4 DISTINCT agents run concurrently: barrier fake backend records active
  count; test requires max_active >= 4 before releasing the barrier, then
  all four finish exit 0. Launching 3rd/4th must not kill 1st/2nd
  (all Popen still alive at full overlap).
- Same-name turns serialize (per-agent flock): 2 concurrent sends on one
  agent both succeed with max_active == 1.
- One timeout/cancel leaves the other three healthy + guard released.

Fake backend: shadowing `printf` (echo backend argv[0]) via PATH. Temp
state only; scoped process cleanup; no real models/providers.

Run: python3 tests/test_ag_parallel.py
Env: AG_PARALLEL_BIN overrides binary (default: live repo ag).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = Path(os.environ.get("AG_PARALLEL_BIN", _REPO_AG))
assert AG.exists(), f"missing ag binary: {AG}"

FAKE_PRINTF = """#!/usr/bin/env python3
import fcntl
import json as _j
import os
import sys
import time

D = os.environ.get("AG_PARALLEL_DIR", "")
MODE = os.environ.get("AG_PARALLEL_MODE", "barrier")
try:
    SLOW_S = float(os.environ.get("AG_PARALLEL_SLOW_S", "3") or 3)
except Exception:
    SLOW_S = 3.0
def _bump(delta):
    try:
        p = os.path.join(D, "counts.json")
        with open(p, "a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.seek(0)
            try:
                st = _j.load(f)
            except Exception:
                st = {"active": 0, "max": 0}
            st["active"] = max(0, int(st.get("active", 0)) + delta)
            st["max"] = max(int(st.get("max", 0)), st["active"])
            a, m = st["active"], st["max"]
            f.seek(0)
            f.truncate()
            _j.dump(st, f)
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            return a, m
    except Exception:
        return 0, 0
    return 0, 0

if MODE == "fast":
    print("FAST_OK")
    sys.exit(0)
if MODE == "slow":
    _bump(+1)
    try:
        time.sleep(SLOW_S)
    finally:
        _bump(-1)
    print("SLOW_OK")
    sys.exit(0)
# barrier mode: hold active until release file appears
_bump(+1)
try:
    t0 = time.time()
    while time.time() - t0 < 25:
        if D and os.path.exists(os.path.join(D, "release")):
            break
        time.sleep(0.05)
finally:
    _bump(-1)
print("PARALLEL_OK")
sys.exit(0)
"""


def _run(state, *args, timeout=20, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    p = subprocess.run(
        [sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=full_env)
    return p


def _setup_state(names, workdir=None):
    td = Path(tempfile.mkdtemp(prefix="agpar-st-"))
    wd = Path(workdir) if workdir else td
    for n in names:
        p = _run(td, "agents", "add", n, "--backend", "echo",
                 "--role", "sub", "--dir", str(wd))
        assert p.returncode == 0, (n, p.stdout, p.stderr)
    return td


def _setup_fake(mode="barrier", slow_s="3"):
    pardir = Path(tempfile.mkdtemp(prefix="agpar-cnts-"))
    bindir = Path(tempfile.mkdtemp(prefix="agpar-bin-"))
    (bindir / "printf").write_text(FAKE_PRINTF)
    (bindir / "printf").chmod(0o755)
    env = {
        "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", ""),
        "AG_PARALLEL_DIR": str(pardir),
        "AG_PARALLEL_MODE": mode,
        "AG_PARALLEL_SLOW_S": str(slow_s),
    }
    return pardir, bindir, env


def _read_counts(pardir):
    try:
        st = json.loads((pardir / "counts.json").read_text())
        return int(st.get("active", 0)), int(st.get("max", 0))
    except Exception:
        return 0, 0


def _wait_max(pardir, want, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, mx = _read_counts(pardir)
        if mx >= want:
            return mx
        time.sleep(0.1)
    return _read_counts(pardir)[1]


def _cleanup(procs, dirs):
    for p in procs:
        try:
            if p.poll() is None:
                p.kill()
        except Exception:
            pass
    for p in procs:
        try:
            p.wait(timeout=5)
        except Exception:
            pass
    for d in dirs:
        try:
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


def test_four_distinct_agents_overlap():
    names = ["p1", "p2", "p3", "p4"]
    td = _setup_state(names)
    pardir, bindir, env = _setup_fake(mode="barrier")
    procs = []
    try:
        for n in names:
            full_env = dict(os.environ, **env)
            procs.append(subprocess.Popen(
                [sys.executable, str(AG), "--dir", str(td),
                 "--json", "chat", "send", n, f"hello-{n}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=full_env))
        mx = _wait_max(pardir, 4, timeout=20)
        assert mx >= 4, f"no 4-way overlap: max_active={mx}"
        # launching 3rd/4th must not kill 1st/2nd: all alive at full overlap
        alive = [p.poll() is None for p in procs]
        assert all(alive), f"earlier job died on later launch: {alive}"
        (pardir / "release").write_text("go")
        outs = [p.communicate(timeout=40) for p in procs]
        for n, p, (out, err) in zip(names, procs, outs):
            assert p.returncode == 0, (n, p.returncode, out, err)
            try:
                obj = json.loads(out or "{}")
                reply = obj.get("data", {}).get("reply", "")
            except Exception:
                reply = out
            assert "PARALLEL_OK" in (reply or out), (n, out, err)
        _, mx2 = _read_counts(pardir)
        print(f"ok test_four_distinct_agents_overlap max_active={mx2}")
        return mx2
    finally:
        _cleanup(procs, [td, pardir, bindir])


def test_same_name_serializes():
    td = _setup_state(["s1"])
    pardir, bindir, env = _setup_fake(mode="slow", slow_s="3")
    procs = []
    try:
        t0 = time.time()
        for i in range(2):
            full_env = dict(os.environ, **env)
            procs.append(subprocess.Popen(
                [sys.executable, str(AG), "--dir", str(td),
                 "--json", "chat", "send", "s1", f"msg-{i}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=full_env))
        outs = [p.communicate(timeout=40) for p in procs]
        el = time.time() - t0
        for i, (p, (out, err)) in enumerate(zip(procs, outs)):
            assert p.returncode == 0, (i, p.returncode, out, err)
        _, mx = _read_counts(pardir)
        assert mx == 1, f"same-agent turns overlapped: max_active={mx}"
        assert el >= 5.0, f"same-agent turns did not serialize: elapsed={el:.1f}s"
        print(f"ok test_same_name_serializes max_active={mx} elapsed={el:.1f}s")
    finally:
        _cleanup(procs, [td, pardir, bindir])


def test_one_timeout_leaves_three_healthy():
    """All four share the barrier; k1 has --timeout 2 so its backend is
    killed while k2-k4 are still blocked in the barrier. Survivors must
    still be alive (killing one job kills nothing else), then release and
    all three finish. Per-call env selects backend behavior explicitly;
    the fake never scans the (stateless, history-replaying) transcript."""
    names = ["k1", "k2", "k3", "k4"]
    td = _setup_state(names)
    pardir, bindir, env = _setup_fake(mode="barrier")
    procs = []
    try:
        specs = [
            ("k1", ["--timeout", "2", "slow-task"]),
            ("k2", ["hello-k2"]),
            ("k3", ["hello-k3"]),
            ("k4", ["hello-k4"]),
        ]
        for name, extra in specs:
            full_env = dict(os.environ, **env)
            procs.append(subprocess.Popen(
                [sys.executable, str(AG), "--dir", str(td),
                 "--json", "chat", "send", name] + extra,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=full_env))
        mx = _wait_max(pardir, 4, timeout=20)
        assert mx >= 4, f"no 4-way overlap before timeout: max_active={mx}"
        # k1 must time out (CLI 124) while the other three are still blocked
        out0, err0 = procs[0].communicate(timeout=30)
        assert procs[0].returncode == 124, \
            ("k1 should time out", procs[0].returncode, out0, err0)
        alive = [p.poll() is None for p in procs[1:]]
        assert all(alive), \
            f"k1 timeout killed survivors: alive={alive}"
        (pardir / "release").write_text("go")
        for p, name in zip(procs[1:], names[1:]):
            out, err = p.communicate(timeout=40)
            assert p.returncode == 0, (name, p.returncode, out, err)
            assert "PARALLEL_OK" in out, (name, out, err)
        # guard released on timed-out agent: follow-up succeeds (fast path
        # selected per-call via env, never via transcript content)
        env_fast = dict(env, AG_PARALLEL_MODE="fast")
        p = _run(td, "--json", "chat", "send", "k1", "after-timeout",
                 timeout=30, env=env_fast)
        assert p.returncode == 0, (p.stdout, p.stderr)
        assert "FAST_OK" in p.stdout, (p.stdout, p.stderr)
        print(f"ok test_one_timeout_leaves_three_healthy overlap_max={mx}")
    finally:
        _cleanup(procs, [td, pardir, bindir])


if __name__ == "__main__":
    mx = test_four_distinct_agents_overlap()
    test_same_name_serializes()
    test_one_timeout_leaves_three_healthy()
    print(f"all parallel tests passed (four_way_max_active={mx})")
