#!/usr/bin/env python3
"""wakes --reap, stale-session wake fire, agents ps. In-process, echo roster, no models.
Run: python3 tests/test_ag_reap.py"""
import importlib.machinery, json, os, subprocess, sys, tempfile, threading, time, unittest
from datetime import datetime, timezone
from pathlib import Path

_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = importlib.machinery.SourceFileLoader("ag_reap_t", _AG).load_module()


def fresh(backend="echo"):
    sdir = Path(tempfile.mkdtemp(prefix="agreap-")) / "st"
    AG.ensure_state(sdir)
    AG.save_agents(sdir, [{"name": "w", "backend": backend, "model": "default", "dir": ".", "role": "sub"}])
    return sdir


def lost_job(sdir, jid, lost="queued", **kw):
    j = {"id": jid, "agent": "w", "message": "do it", "trigger": "t", "status": "failed", "lost_status": lost,
         "ts": AG.now_iso(), "notify": "", "on_done": "", "timeout_s": 0, "max_runtime_s": 0, **kw}
    AG._wake_write_job(sdir, j)


class Stub:
    """Replace launch_wake_chat; records calls."""
    def __init__(self): self.calls = []; self.lock = threading.Lock()
    def __call__(self, sdir, agent, message, **kw):
        with self.lock:
            self.calls.append((message, kw)); n = len(self.calls)
        time.sleep(0.05)
        return {"job": f"new{n}", "session": "s"}


class ReapTest(unittest.TestCase):
    def setUp(self):
        self.real = AG.launch_wake_chat; self.stub = Stub(); AG.launch_wake_chat = self.stub
    def tearDown(self): AG.launch_wake_chat = self.real

    def test_reap_once_even_concurrent(self):
        sdir = fresh(); lost_job(sdir, "old1")
        res = []
        ts = [threading.Thread(target=lambda: res.append(AG.wakes_reap(sdir))) for _ in range(2)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(len(self.stub.calls), 1)
        kw = self.stub.calls[0][1]
        self.assertEqual(kw["extra"], {"retry_of": "old1", "attempts": 2})
        self.assertEqual(AG._wake_read_job(sdir, "old1")["reaped_by"], "new1")
        self.assertEqual(AG.wakes_reap(sdir), [])  # third run: nothing

    def test_running_needs_flag_and_prefix(self):
        sdir = fresh(); lost_job(sdir, "r1", lost="running")
        self.assertEqual(AG.wakes_reap(sdir), [])
        self.assertEqual(len(self.stub.calls), 0)
        r = AG.wakes_reap(sdir, include_running=True)
        self.assertEqual(r[0]["action"], "reaped")
        self.assertTrue(self.stub.calls[0][0].startswith(AG.REAP_NOTE))

    def test_max_attempts_and_dry_run_and_agent(self):
        sdir = fresh(); lost_job(sdir, "m1", attempts=3); lost_job(sdir, "m2")
        r = {x["old"]: x["action"] for x in AG.wakes_reap(sdir, dry_run=True)}
        self.assertEqual(r, {"m1": "skipped-max-attempts", "m2": "would-reap"})
        self.assertEqual(self.stub.calls, [])
        self.assertEqual(AG.wakes_reap(sdir, agent="other"), [])
        self.assertEqual([x["old"] for x in AG.wakes_reap(sdir) if x["action"] == "reaped"], ["m2"])

    def test_cancelled_job_not_lost(self):
        sdir = fresh(); lost_job(sdir, "c1", lost=None)
        j = AG._wake_read_job(sdir, "c1"); j.pop("lost_status"); AG._wake_write_job(sdir, j)
        self.assertEqual(AG.wakes_reap(sdir, include_running=True), [])

    def test_reconcile_records_lost_status(self):
        sdir = fresh()
        j = {"id": "q1", "agent": "w", "message": "x", "status": "queued", "ts": "2020-01-01T00:00:00+00:00", "session": "nosuch"}
        AG._wake_write_job(sdir, j)
        out = AG._wake_reconcile(sdir, j)
        self.assertEqual((out["status"], out["lost_status"]), ("failed", "queued"))

    def test_stale_session_fires_wake_once(self):
        sdir = fresh(); sp = AG._sroot(sdir) / "sess1"; sp.mkdir(parents=True)
        AG._swrite(sp, {"id": "sess1", "name": "n", "cmd": "sleep 1", "running": False, "exit": -1, "stale": True,
                        "wake_agent": "w", "wake_message": "m", "wake_fired": False})
        for _ in range(3):
            st = AG._sstatus(sp); AG._reconcile_stale(sdir, sp, st)
        self.assertEqual(len(self.stub.calls), 1)
        self.assertTrue(AG._sstatus(sp)["wake_fired"])


@unittest.skipUnless(os.name == "posix", "ps is POSIX")
class PsTest(unittest.TestCase):
    def setUp(self):
        self.procs = []
    def tearDown(self):
        for p in self.procs:
            try: os.killpg(p.pid, 9)
            except Exception: pass
            try: p.wait(5)
            except Exception: pass
    def spawn(self):
        p = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60 & wait"], start_new_session=True); self.procs.append(p)
        time.sleep(0.4); return p

    def rec(self, sdir, pid, ts=None):
        qd = AG._qdir(sdir, "w", mk=True)
        (qd / "running.json").write_text(json.dumps({"pid": pid, "pgid": pid, "owner": os.getpid(), "ts": ts or AG.now_iso()}))

    def test_summed_tree_and_json(self):
        sdir = fresh(); p = self.spawn(); self.rec(sdir, p.pid)
        rows = AG.agents_ps(sdir)
        self.assertEqual(len(rows), 1); r = rows[0]
        self.assertEqual((r["agent"], r["state"], r["root"], r["backend"]), ("w", "turn", p.pid, "echo"))
        self.assertGreaterEqual(r["procs"], 3)
        for k in ("pids", "cpu", "rss_mb", "elapsed_s"): self.assertIn(k, r)
        cp = subprocess.run([sys.executable, _AG, "--dir", str(sdir.parent / ".agent"), "--json", "agents", "ps"],
                            capture_output=True, text=True, env={**os.environ, "AG_AUTO_UPDATE": "0"})
        self.assertEqual(cp.returncode, 0, cp.stderr)

    def test_stale_records_ignored(self):
        sdir = fresh(); self.rec(sdir, 2 ** 22 + 12345)  # dead pid
        self.assertEqual(AG.agents_ps(sdir), [])
        p = self.spawn(); self.rec(sdir, p.pid, ts="2020-01-01T00:00:00+00:00")  # record predates process = pid reuse
        self.assertEqual(AG.agents_ps(sdir), [])


if __name__ == "__main__":
    unittest.main()
