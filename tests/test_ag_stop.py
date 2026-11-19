#!/usr/bin/env python3
"""fx-stop: `ag chat stop` alias + `stop --all-wakes`, and steer/send that cannot hang.

Echo backend + hand-written queue/wake state, no real backends, no network.
Covers: chat stop == stop; --all-wakes also cancels parked wakes (plain stop
leaves them); steer on an idle agent says so and exits without starting a
turn; steer's queue/FIFO/HOLD wait is bounded by --timeout (default 30);
stop kills the running turn's backend process group and prints what stopped.
Run: python3 tests/test_ag_stop.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))


@unittest.skipUnless(os.name == "posix", "queue/guard is POSIX-only")
class StopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ag-stop-test-"))
        self.st = self.tmp / ".agent"
        self.env = dict(os.environ, AG_AUTO_UPDATE="0", AG_PROBE="0", AG_RTK="0",
            AG_HOME=str(self.tmp / "home"), AG_CONFIG_HOME=str(self.tmp / "cfg"),
            AG_CACHE_HOME=str(self.tmp / "cache"))
        self.procs = []
        self.addCleanup(self._reap)

    def _reap(self):
        for p in self.procs:
            if p.poll() is None: p.kill()
            try: p.wait(timeout=5)
            except Exception: pass

    def json_ag(self, *args, timeout=60):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.st), "--json", *args],
            capture_output=True, text=True, timeout=timeout, env=self.env)
        try: obj = json.loads(p.stdout) if p.stdout.strip() else {}
        except Exception: self.fail(f"{args}: rc={p.returncode} {p.stdout!r} {p.stderr!r}")
        return p.returncode, obj

    def text_ag(self, *args, timeout=60):
        return subprocess.run([sys.executable, AG, "--dir", str(self.st), *args],
            capture_output=True, text=True, timeout=timeout, env=self.env)

    def ok(self, *args):
        rc, obj = self.json_ag(*args)
        self.assertEqual(rc, 0, obj)
        return obj.get("data") or {}

    def add(self, name):
        self.ok("agents", "add", name, "--backend", "echo", "--dir", str(self.tmp))

    def sleeper(self, leader=False):
        p = subprocess.Popen(["sleep", "120"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=leader)
        self.procs.append(p)
        return p

    def put_job(self, jid, name, status, **extra):
        d = self.st / "wake" / "jobs"; d.mkdir(parents=True, exist_ok=True)
        j = {"id": jid, "agent": name, "status": status, "ts": "2026-01-01T00:00:00",
            "message": "task", "session": None, **extra}
        if status == "parked": j.setdefault("resume_at", int(time.time()) + 3600)
        (d / f"{jid}.json").write_text(json.dumps(j))

    def job(self, jid):
        return json.loads((self.st / "wake" / "jobs" / f"{jid}.json").read_text())

    def fake_running(self, name, proc):
        qd = self.st / "queue" / name; qd.mkdir(parents=True, exist_ok=True)
        (qd / "running.json").write_text(json.dumps(
            {"pid": proc.pid, "pgid": proc.pid, "owner": proc.pid, "ts": "2026-01-01T00:00:00"}))
        return qd

    # --- ag chat stop alias ---

    def test_chat_stop_alias_text_and_state(self):
        self.add("w"); self.put_job("j1", "w", "queued")
        p = self.text_ag("chat", "stop", "w")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("w: held,", p.stdout)
        self.assertIn("no running turn", p.stdout)
        self.assertIn("1 wake(s) cancelled", p.stdout)
        self.assertIn("queue stays held: ag queue w --release", p.stdout)
        self.assertEqual(self.job("j1")["status"], "failed")
        self.assertTrue((self.st / "queue" / "w" / "HOLD").exists())

    def test_chat_stop_alias_json_matches_stop(self):
        self.add("w2"); self.put_job("j2", "w2", "queued")
        d1 = self.ok("chat", "stop", "w2")
        self.assertEqual(d1["cancelled_wakes"], ["j2"]); self.assertTrue(d1["held"])
        self.add("w3"); self.put_job("j3", "w3", "running")
        d2 = self.ok("stop", "w3")
        self.assertEqual(d2["cancelled_wakes"], ["j3"])
        for k in ("agent", "held", "cancelled_tickets", "cancelled_wakes", "interrupted"):
            self.assertEqual(set(d1) >= {k}, set(d2) >= {k})

    def test_chat_stop_unknown_agent(self):
        self.add("w")
        rc, obj = self.json_ag("chat", "stop", "ghost")
        self.assertEqual(rc, 1)
        self.assertIn("no such agent: ghost", obj["error"])

    # --- --all-wakes: plain stop keeps parked, --all-wakes cancels them ---

    def test_stop_leaves_parked_all_wakes_cancels(self):
        self.add("a")
        self.put_job("q1", "a", "queued"); self.put_job("p1", "a", "parked")
        d = self.ok("stop", "a")
        self.assertEqual(d["cancelled_wakes"], ["q1"])
        self.assertEqual(self.job("p1")["status"], "parked")  # auto-resume survives a plain stop
        d = self.ok("stop", "a", "--all-wakes")
        self.assertEqual(d["cancelled_wakes"], ["p1"])
        self.assertEqual(self.job("p1")["status"], "failed")

    def test_chat_stop_all_wakes(self):
        self.add("b"); self.put_job("p2", "b", "parked")
        d = self.ok("chat", "stop", "b", "--all-wakes")
        self.assertEqual(d["cancelled_wakes"], ["p2"])

    # --- steer on an idle agent: say so and exit, no turn started ---

    def test_steer_idle_says_so_and_exits(self):
        self.add("s0")
        rc, obj = self.json_ag("chat", "steer", "s0", "change course")
        self.assertEqual(rc, 1)
        self.assertIn("no running turn", obj["error"])
        self.assertIn("chat send", obj["error"])
        self.assertFalse((self.st / "chats" / "s0.jsonl").exists())  # no turn was started

    # --- steer waits are bounded (the reported hang) ---

    def test_steer_held_queue_times_out(self):
        self.add("h"); x = self.sleeper()
        self.fake_running("h", x)
        self.ok("queue", "h", "--hold")
        t0 = time.time()
        rc, obj = self.json_ag("chat", "steer", "h", "--timeout", "1.5", "go")
        self.assertLess(time.time() - t0, 15, "steer blocked like the old minutes-long hang")
        self.assertEqual(rc, 1)
        self.assertIn("timed out", obj["error"])
        self.assertIn("queue held", obj["error"])

    def test_steer_fifo_wait_times_out_never_launches(self):
        self.add("f"); x = self.sleeper(); y = self.sleeper()
        qd = self.fake_running("f", x)
        (qd / "seq").write_text("1")
        (qd / "0-0000000001-blok1.json").write_text(json.dumps(  # a live ticket ahead of steer
            {"id": "blok1", "pid": y.pid, "prio": 0, "text": "blocker", "source": "chat",
             "ts": "2026-01-01T00:00:00", "sig": False}))
        t0 = time.time()
        rc, obj = self.json_ag("chat", "steer", "f", "--timeout", "1.5", "go")
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(rc, 1)
        self.assertIn("timed out", obj["error"])
        self.assertIn("1 live ticket(s) ahead", obj["error"])
        self.assertIn("ag stop f", obj["error"])
        live = [t for t in self.ok("queue", "f")["tickets"]]
        self.assertEqual([t["id"] for t in live], ["blok1"])  # steer's ticket was cleaned up
        self.assertFalse((self.st / "chats" / "f.jsonl").exists())  # never launched a turn

    def test_steer_default_timeout_is_30(self):
        self.add("d")
        h = self.text_ag("chat", "steer", "--help")
        self.assertIn("default 30", h.stdout)

    # --- stop kills the running turn's process group and prints it ---

    def test_stop_kills_backend_group_and_reports(self):
        self.add("k"); x = self.sleeper(leader=True)
        self.fake_running("k", x)
        p = self.text_ag("stop", "k")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("turn interrupted", p.stdout)
        end = time.time() + 5
        while x.poll() is None and time.time() < end: time.sleep(0.1)
        self.assertIsNotNone(x.poll(), "backend process group survived ag stop")


if __name__ == "__main__":
    unittest.main()
