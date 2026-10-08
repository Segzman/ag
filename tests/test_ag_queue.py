#!/usr/bin/env python3
"""Visible turn queue, ag stop, ag chat steer (live ag, real processes).

Fake `claude` on PATH logs each launch (START <last prompt line> [RESUME]),
emits stream-json, and sleeps 30s for prompts containing SLOW. No real models.
Run: python3 tests/test_ag_queue.py
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

FAKE = r"""#!/bin/sh
resume=""
for a; do [ "$a" = "--resume" ] && resume=" RESUME"; last="$a"; done
printf 'START %s%s\n' "$(printf '%s' "$last" | tail -n 1)" "$resume" >> "$FAKE_LOG"
case "$last" in
  *SLOWQUIET*) echo "API Error: 429 rate_limit_error" >&2; sleep 30; exit 1;;
esac
printf '{"type":"system","subtype":"init","session_id":"sid-1"}\n'
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"partial"}]}}\n'
case "$last" in *SLOW*) sleep 30;; *) sleep 0.3;; esac
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"done"}]}}\n'
"""


def flat(o):
    """Envelope {ok,cmd,data|error} -> data plus ok/error."""
    return {**(o.get("data") or {}), "ok": o.get("ok"), **({"error": o["error"]} if "error" in o else {})}


@unittest.skipUnless(os.name == "posix", "flock queue is POSIX-only")
class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ag-queue-test-"))
        self.st = self.tmp / ".agent"
        b = self.tmp / "bin"; b.mkdir()
        (b / "claude").write_text(FAKE); (b / "claude").chmod(0o755)
        self.log = self.tmp / "launches.log"
        self.env = dict(os.environ, PATH=f"{b}{os.pathsep}{os.environ.get('PATH', '')}",
            FAKE_LOG=str(self.log), AG_AUTO_UPDATE="0", AG_HOME=str(self.tmp / "home"),
            AG_CONFIG_HOME=str(self.tmp / "cfg"), AG_CACHE_HOME=str(self.tmp / "cache"), AG_RTK="0")
        self.procs = []
        self.ag("agents", "add", "q", "--backend", "claude", "--dir", str(self.tmp))

    def tearDown(self):
        for p in self.procs:
            if p.poll() is None: p.kill()
        subprocess.run(["pkill", "-f", str(self.tmp / "bin")], capture_output=True)

    def ag(self, *args, timeout=60):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.st), "--json", *args],
            capture_output=True, text=True, timeout=timeout, env=self.env)
        try: return flat(json.loads(p.stdout))
        except Exception: raise AssertionError(f"{args}: rc={p.returncode} {p.stdout!r} {p.stderr!r}")

    def bg(self, *args):
        p = subprocess.Popen([sys.executable, AG, "--dir", str(self.st), "--json", *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env)
        self.procs.append(p)
        return p

    def wait(self, fn, t=20):
        end = time.time() + t
        while time.time() < end:
            if fn(): return True
            time.sleep(0.1)
        self.fail("timed out waiting")

    def tickets(self): return self.ag("queue", "q")["tickets"]
    def launches(self): return self.log.read_text().splitlines() if self.log.exists() else []
    def finish(self, p):
        out, err = p.communicate(timeout=60)
        return p.returncode, flat(json.loads(out)) if out.strip() else {}, err
    def running(self): return (self.st / "queue" / "q" / "running.json").exists()

    def test_fifo_three_senders(self):
        self.ag("queue", "q", "--hold")
        ps = []
        for i, tag in enumerate("ABC"):
            ps.append(self.bg("chat", "send", "q", f"MSG-{tag}"))
            self.wait(lambda: len(self.tickets()) == i + 1)
        self.assertEqual([t["text"] for t in self.tickets()], ["MSG-A", "MSG-B", "MSG-C"])
        self.assertEqual([t["position"] for t in self.tickets()], [1, 2, 3])
        self.assertEqual(next(x for x in self.ag("agents", "list")["agents"] if x["name"] == "q")["queue"], 3)
        self.ag("queue", "q", "--release")
        for p in ps: self.assertEqual(self.finish(p)[0], 0)
        self.assertEqual([l.split()[1] for l in self.launches()], ["MSG-A", "MSG-B", "MSG-C"])

    def test_priority_jumps_queue(self):
        self.ag("queue", "q", "--hold")
        a = self.bg("chat", "send", "q", "MSG-A"); self.wait(lambda: len(self.tickets()) == 1)
        b = self.bg("chat", "send", "q", "MSG-B"); self.wait(lambda: len(self.tickets()) == 2)
        s = self.bg("chat", "steer", "q", "MSG-S"); self.wait(lambda: len(self.tickets()) == 3)
        self.assertEqual(self.tickets()[0]["text"], "MSG-S")
        self.ag("queue", "q", "--release")
        for p in (a, b, s): self.assertEqual(self.finish(p)[0], 0)
        self.assertEqual([l.split()[1] for l in self.launches()], ["MSG-S", "MSG-A", "MSG-B"])

    def test_cancel_waiter(self):
        self.ag("queue", "q", "--hold")
        w = self.bg("chat", "send", "q", "MSG-W"); self.wait(lambda: len(self.tickets()) == 1)
        r = self.ag("queue", "q", "--cancel", self.tickets()[0]["id"])
        self.assertTrue(r["cancelled"])
        rc, out, err = self.finish(w)
        self.assertNotEqual(rc, 0)
        self.assertIn("cancelled from queue", out.get("error", ""), err)
        self.assertEqual(self.tickets(), [])
        self.assertEqual(self.launches(), [])

    def test_hold_then_release(self):
        self.ag("queue", "q", "--hold")
        p = self.bg("chat", "send", "q", "MSG-H")
        time.sleep(1.5)
        self.assertIsNone(p.poll()); self.assertEqual(self.launches(), [])
        self.assertTrue(self.ag("queue", "q")["held"])
        self.ag("queue", "q", "--release")
        rc, out, err = self.finish(p)
        self.assertEqual(rc, 0); self.assertIn("held", err)
        self.assertIn("done", out["reply"])

    def test_dead_pid_ticket_reaped(self):
        qd = self.st / "queue" / "q"; qd.mkdir(parents=True, exist_ok=True)
        dead = subprocess.Popen(["true"]); dead.wait()
        (qd / "0-0000000000-dead1.json").write_text(json.dumps({"id": "dead1", "pid": dead.pid}))
        p = self.bg("chat", "send", "q", "MSG-D")
        rc, out, _ = self.finish(p)
        self.assertEqual(rc, 0)
        self.assertFalse((qd / "0-0000000000-dead1.json").exists())

    def test_stop_interrupts_no_retry(self):
        p = self.bg("chat", "send", "q", "SLOWQUIET")
        self.wait(self.running)
        w = self.ag("wake", "q", "MSG-WAKE")
        jid = w["job"]
        self.wait(lambda: len(self.tickets()) == 1)  # wake worker queued behind the slow turn
        r = self.ag("stop", "q")
        self.assertTrue(r["interrupted"]); self.assertTrue(r["held"])
        rc, out, _ = self.finish(p)
        self.assertTrue(out.get("interrupted"), out); self.assertEqual(out["exit"], 130)
        time.sleep(3)  # a retry would relaunch after 2s backoff
        self.assertEqual(len(self.launches()), 1, self.launches())
        self.assertTrue((self.st / "queue" / "q" / "HOLD").exists())
        self.assertFalse(self.running())
        job = next(j for j in self.ag("wakes", "--agent", "q")["wakes"] if not jid or j["id"] == jid)
        self.assertEqual(job["status"], "failed"); self.assertIn(job.get("error", ""), ("cancelled from queue", "stopped"))
        self.assertEqual(self.tickets(), [])
        evs = (self.st / "events.jsonl").read_text()
        self.assertIn('"stop"', evs)

    def test_steer_interrupts_and_resumes(self):
        p = self.bg("chat", "send", "q", "SLOW-1")
        self.wait(lambda: self.running() and "partial" in "".join(self.launches()) or len(self.launches()) == 1)
        time.sleep(0.5)  # let sid-1 persist
        r = self.ag("chat", "steer", "q", "go left")
        self.assertTrue(r.get("steered")); self.assertIn("done", r["reply"])
        rc, out, _ = self.finish(p)
        self.assertTrue(out.get("interrupted")); self.assertIn("partial", out["reply"])
        ls = self.launches()
        self.assertEqual(len(ls), 2, ls)
        self.assertIn("[steer] user interrupted the previous turn; new instruction: go left", ls[1])
        self.assertTrue(ls[1].endswith("RESUME"), ls[1])
        self.assertFalse((self.st / "queue" / "q" / "HOLD").exists())

    def test_stop_cascade_parent(self):
        self.ag("agents", "add", "kid", "--backend", "echo", "--dir", str(self.tmp))
        jobs = self.st / "wake" / "jobs"; jobs.mkdir(parents=True, exist_ok=True)
        (jobs / "jk1.json").write_text(json.dumps({"id": "jk1", "agent": "kid", "parent": "q",
            "status": "queued", "ts": "2026-01-01T00:00:00", "message": "x"}))
        r = self.ag("stop", "q", "--cascade")
        self.assertEqual([c["agent"] for c in r["cascade"]], ["kid"])
        self.assertEqual(r["cascade"][0]["cancelled_wakes"], ["jk1"])
        self.assertTrue((self.st / "queue" / "kid" / "HOLD").exists())
        self.assertEqual(json.loads((jobs / "jk1.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
