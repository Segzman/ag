#!/usr/bin/env python3
"""Wake usage-limit parking + scheduler auto-resume. Fake claude, temp state."""
import json, os, subprocess, sys, tempfile, time, unittest
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent / "ag"))
MSG = "You've hit your individual spend limit · run /usage-credits to ask your admin for a higher limit · your 5-hour limit resets 7:40pm (America/Toronto)"
FAKE_CLAUDE = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/claude.log"
if [ -f "$FAKE_DIR/claude.limit" ]; then
  echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
  python3 -c 'import json,sys; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":sys.argv[1],"total_cost_usd":0}))' "$(cat "$FAKE_DIR/claude.limit")"
  exit 1
fi
echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"claude ok"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"claude ok","total_cost_usd":0}'
"""


class WakePark(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cfg", "cache", "state"):
            (r / d).mkdir(parents=True)
        (r / "home/.codex/models_cache.json").write_text(json.dumps({"models": []}))
        (r / "fake/claude").write_text(FAKE_CLAUDE)
        (r / "fake/claude").chmod(0o755)
        subprocess.run(["git", "init", "-q", str(r / "proj")], check=True)
        self.fake = r / "fake"; self.state = r / "state"
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"),
            AG_CACHE_HOME=str(r / "cache"), AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0",
            AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_ZEN_URL="off", HOME=str(r / "home"),
            PATH=f"{r / 'fake'}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
            AG_MODELSDEV_URL="http://127.0.0.1:1/api.json", AG_OPENROUTER_RANKINGS_URL="off",
            FAKE_DIR=str(r / "fake"), AG_PRIVACY="off")
        p = self.run_ag("agents", "add", "w", "--backend", "claude", "--role", "sub",
            "--dir", str(r / "proj"))
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))

    def run_ag(self, *args):
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *args],
            capture_output=True, text=True, timeout=90, cwd=self.root / "proj",
            env=self.env, stdin=subprocess.DEVNULL)

    def run_json(self, *args):
        p = self.run_ag("--json", *args)
        o = json.loads(p.stdout) if p.stdout.strip() else {}
        return p, o

    def limit(self, text=MSG):
        (self.fake / "claude.limit").write_text(text)

    def wake(self, text, *flags):
        p, o = self.run_json("wake", "w", *flags, text)
        self.assertTrue(o.get("ok"), (p.stdout, p.stderr, o))
        jid = o.get("data", {}).get("job")
        self.assertTrue(jid, o)
        return jid

    def job(self, jid):
        try:
            return json.loads((self.state / "wake/jobs" / f"{jid}.json").read_text())
        except Exception:
            return {}

    def wakes(self):
        _, o = self.run_json("wakes")
        return o.get("data", {}).get("wakes", [])

    def events(self):
        _, o = self.run_json("events", "--limit", "100")
        return o.get("data", {}).get("events", [])

    def wait_for(self, pred, timeout=30):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if pred():
                return True
            time.sleep(0.1)
        return False

    def parked(self, text="parked work", *flags):
        self.limit()
        jid = self.wake(text, *flags)
        self.assertTrue(self.wait_for(lambda: self.job(jid).get("status") == "parked"),
            self.job(jid))
        return jid

    def test_usage_limit_parks_with_resume_and_wakes_text(self):
        jid = self.parked("plan-capped work")
        j = self.job(jid)
        self.assertTrue(j.get("usage_limit"), j)
        self.assertGreater(j.get("resume_at", 0), time.time())
        self.assertEqual(j.get("message"), "plan-capped work")
        self.assertEqual(j.get("agent"), "w")
        row = next(r for r in self.wakes() if r.get("id") == jid)
        self.assertEqual(row.get("status"), "parked")
        txt = self.run_ag("wakes").stdout
        self.assertIn("parked", txt)
        self.assertIn("resume=", txt)
        evs = self.events()
        self.assertTrue(any(e.get("type") == "wake_park" and e.get("job") == jid for e in evs))
        self.assertFalse(any(e.get("type") == "wake_fail" and e.get("job") == jid for e in evs))

    def test_cancel_parked_job(self):
        jid = self.parked("cancel me")
        p, o = self.run_json("wakes", "--cancel", jid)
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        j = self.job(jid)
        self.assertEqual(j.get("status"), "failed", j)
        self.assertTrue(j.get("cancelled"), j)
        self.assertIn("cancel", (j.get("error") or "").lower())

    def test_tick_resumes_parked_once(self):
        jid = self.parked("resume me")
        due = self.job(jid)["resume_at"]
        (self.fake / "claude.limit").unlink()
        p = self.run_ag("ratelimit", "clear")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        iso = datetime.fromtimestamp(due + 1).isoformat()
        p, o = self.run_json("schedule", "tick", "--now", iso, "--reap")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        acts = o["data"]["dirs"][0]["actions"]
        act = next((x for x in acts if x.get("id") == jid), {})
        self.assertEqual(act.get("action"), "resumed", acts)
        new = act["new"]
        self.assertTrue(self.wait_for(lambda: self.job(new).get("status") == "done"),
            self.job(new))
        old = self.job(jid); nj = self.job(new)
        self.assertEqual(old.get("status"), "resumed", old)
        self.assertEqual(old.get("resumed_as"), new, old)
        self.assertEqual(nj.get("parked_from"), jid, nj)
        self.assertEqual(nj.get("message"), "resume me")
        self.assertEqual(nj.get("agent"), "w")
        evs = self.events()
        self.assertTrue(any(e.get("type") == "wake_resume" and e.get("job") == new
            and e.get("old") == jid for e in evs), evs)
        p, o = self.run_json("schedule", "tick", "--now", iso, "--reap")
        self.assertEqual(o["data"]["dirs"][0]["actions"], [])
        self.assertEqual(len(list((self.state / "wake/jobs").glob("*.json"))), 2)

    def test_no_park_keeps_failure(self):
        self.limit()
        jid = self.wake("no park", "--no-park")
        self.assertTrue(self.wait_for(lambda: self.job(jid).get("status") == "failed"),
            self.job(jid))
        j = self.job(jid)
        self.assertEqual(j.get("status"), "failed", j)
        self.assertNotIn("resume_at", j)
        self.assertNotIn("usage_limit", j)
        self.assertIn("usage limit", (j.get("error") or "").lower())
        self.assertFalse(any(e.get("type") == "wake_park" and e.get("job") == jid
            for e in self.events()))


if __name__ == "__main__":
    unittest.main()
