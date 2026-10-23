#!/usr/bin/env python3
"""wakes --wait JOB [JOB…] exit codes. Stdlib only, echo backend, temp state.

Run: python3 -m unittest tests.test_ag_wake_wait
"""
import importlib.machinery
import json, os, subprocess, sys, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")


def run(state, *args, timeout=60):
    p = subprocess.run([sys.executable, AG, "--dir", str(state), *args],
        capture_output=True, text=True, timeout=timeout, env=dict(os.environ))
    return p


def run_json(state, *args, timeout=60):
    p = subprocess.run([sys.executable, AG, "--json", "--dir", str(state), *args],
        capture_output=True, text=True, timeout=timeout, env=dict(os.environ))
    try:
        obj = json.loads(p.stdout or "{}")
    except Exception:
        obj = {}
    return p, obj


def wakes(state):
    _, o = run_json(state, "wakes")
    return o.get("data", {}).get("wakes", [])


def job(state, jid):
    return next((j for j in wakes(state) if j.get("id") == jid), {})


def wait_for(pred, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.2)
    return False


class TestWaitExitCodes(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.td = Path(tempfile.mkdtemp(prefix="agww-"))
        for n in ("m1", "m2"):
            self.assertEqual(run(self.td, "agents", "add", n, "--backend", "echo", "--role", "sub").returncode, 0)

    def test_multi_wait_all_ok(self):
        _, o1 = run_json(self.td, "wake", "m1", "job-a")
        _, o2 = run_json(self.td, "wake", "m2", "job-b")
        j1, j2 = o1["data"].get("job"), o2["data"].get("job")
        p, o = run_json(self.td, "wakes", "--wait", j1, j2, "--timeout", "20")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        jobs = o["data"].get("jobs", [])
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(j.get("status") == "done" for j in jobs), o)
        self.assertFalse(o["data"].get("timed_out"), o)
        p2 = run(self.td, "wakes", "--wait", j1, j2, "--timeout", "20")  # plain: one summary line per job
        self.assertEqual(p2.returncode, 0, (p2.stdout, p2.stderr))
        self.assertEqual(len([l for l in p2.stdout.splitlines() if l.strip()]), 2, p2.stdout)

    def test_multi_wait_one_failed(self):
        _, o1 = run_json(self.td, "wake", "m1", "job-a")
        j1 = o1["data"].get("job")
        self.assertEqual(run(self.td, "agents", "add", "m3bad", "--backend", "echo",
            "--role", "sub", "--dir", "/nonexistent-ag-wake-xyz").returncode, 0)
        _, o3 = run_json(self.td, "wake", "m3bad", "job-c")
        j3 = o3["data"].get("job")
        self.assertTrue(wait_for(lambda: job(self.td, j3).get("status") == "failed"), wakes(self.td))
        p, o = run_json(self.td, "wakes", "--wait", j1, j3, "--timeout", "20")
        self.assertEqual(p.returncode, 1, (p.stdout, p.stderr))
        jobs = o["data"].get("jobs", [])
        self.assertTrue(any(j.get("status") == "failed" for j in jobs), o)
        # single --wait on a failed job exits 1 too
        p2, _ = run_json(self.td, "wakes", "--wait", j3, "--timeout", "20")
        self.assertEqual(p2.returncode, 1, (p2.stdout, p2.stderr))
        # done single keeps exit 0
        p3, o = run_json(self.td, "wakes", "--wait", j1, "--timeout", "20")
        self.assertEqual(p3.returncode, 0, (p3.stdout, p3.stderr))
        self.assertEqual(o["data"].get("status"), "done", o)

    def test_wait_timeout_exit_124(self):
        """Unfinished job past --timeout: exit 124, stale ids listed as pending."""
        import fcntl
        lk_dir = self.td / "wake" / "locks"; lk_dir.mkdir(parents=True, exist_ok=True)
        lk = open(lk_dir / "m1.lock", "a+b"); fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            _, o1 = run_json(self.td, "wake", "m1", "hold-me")
            _, o2 = run_json(self.td, "wake", "m2", "quick-b")
            j1, j2 = o1["data"].get("job"), o2["data"].get("job")
            _, o3 = run_json(self.td, "wake", "m2", "quick-c")
            j3 = o3["data"].get("job")
            self.assertTrue(wait_for(lambda: job(self.td, j1).get("status") == "queued"), wakes(self.td))
            p, o = run_json(self.td, "wakes", "--wait", j1, j2, j3, "--timeout", "1")
            self.assertEqual(p.returncode, 124, (p.stdout, p.stderr))
            self.assertTrue(o["data"].get("timed_out"), o)
            self.assertEqual(o["data"].get("pending"), [j1], o)
            jp, oo = run_json(self.td, "wakes", "--wait", j1, "--timeout", "1")
            self.assertEqual(jp.returncode, 124, (jp.stdout, jp.stderr))
            self.assertTrue(oo["data"].get("timed_out"), oo)
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN); lk.close()
        self.assertTrue(wait_for(lambda: job(self.td, j1).get("status") == "done"), wakes(self.td))


if __name__ == "__main__":
    unittest.main()
