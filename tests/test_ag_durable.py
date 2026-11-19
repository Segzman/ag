#!/usr/bin/env python3
"""yc-durable: wake turn log, wakes --retry, resume-after-crash, unknown-reset parking.
In-process (stubbed launch/run_turn, temp dirs) + end-to-end with a fake claude.
No network, no real backends. Run: python3 -m pytest -q tests/test_ag_durable.py"""
import importlib.machinery, json, os, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timezone
from pathlib import Path

def _load_src(loader):
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = _load_src(importlib.machinery.SourceFileLoader("ag_durable_t", _AG))
DEAD_PID = 2 ** 22 + 7


def _pid_alive(pid):
    try: os.kill(pid, 0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


def fresh():
    sdir = Path(tempfile.mkdtemp(prefix="agdurable-")) / "st"
    AG.ensure_state(sdir)
    AG.save_agents(sdir, [{"name": "w", "backend": "echo", "model": "default", "dir": ".", "role": "sub"}])
    return sdir


def job(sdir, jid, status="failed", **kw):
    j = {"id": jid, "agent": "w", "message": "do it", "trigger": "t", "status": status,
         "ts": AG.now_iso(), "notify": "", "on_done": "", "timeout_s": 0, "max_runtime_s": 0, **kw}
    AG._wake_write_job(sdir, j)
    return j


class StubLaunch:
    def __init__(self): self.calls = []
    def __call__(self, sdir, agent, message, **kw):
        self.calls.append((agent, message, kw))
        return {"job": f"new{len(self.calls)}", "session": "s"}


class PureTest(unittest.TestCase):
    def test_usage_park_epoch_unknown_reset_parks_until_cooling_ends(self):
        now = 1_700_000_000.0
        r = {"usage_limit": True, "usage_limit_when": "", "reset_at": ""}
        ep = AG._usage_park_epoch(r, now=now)
        self.assertAlmostEqual(ep, now + 3600, delta=2)
        r2 = {"usage_limit": True, "usage_limit_when": "",
              "reset_at": datetime.fromtimestamp(now + 600, timezone.utc).isoformat()}
        self.assertAlmostEqual(AG._usage_park_epoch(r2, now=now), now + 600, delta=2)

    def test_usage_park_epoch_known_reset_and_non_limit(self):
        now = 1_700_000_000.0
        past = datetime.fromtimestamp(now - 60, timezone.utc).isoformat()
        self.assertIsNone(AG._usage_park_epoch(
            {"usage_limit": True, "usage_limit_when": "19:40", "reset_at": past}, now=now))
        fut = datetime.fromtimestamp(now + 60, timezone.utc).isoformat()
        self.assertAlmostEqual(AG._usage_park_epoch(
            {"usage_limit": True, "usage_limit_when": "19:40", "reset_at": fut}, now=now), now + 60, delta=2)
        self.assertIsNone(AG._usage_park_epoch({"usage_limit": False}, now=now))
        self.assertIsNone(AG._usage_park_epoch(
            {"usage_limit": True, "usage_limit_when": "", "timed_out": True}, now=now))


class TurnLogTest(unittest.TestCase):
    def test_turnlog_appends_beside_job(self):
        sdir = fresh(); job(sdir, "j1", status="queued")
        AG._wake_turnlog(sdir, "j1", "started", agent="w")
        AG._wake_turnlog(sdir, "j1", "session", backend_sid="bs1")
        p = sdir / "wake" / "jobs" / "j1.turns.jsonl"
        self.assertTrue(p.exists())
        rows = AG._wake_turnlog_read(sdir, "j1")
        self.assertEqual([r["event"] for r in rows], ["started", "session"])
        self.assertEqual(rows[1]["backend_sid"], "bs1")
        self.assertEqual(AG._wake_backend_sid(sdir, {"id": "j1", "agent": "w"}), "bs1")

    def test_finish_logs_finished_and_failed(self):
        sdir = fresh(); job(sdir, "d1", status="queued"); job(sdir, "f1", status="running")
        AG._wake_finish(sdir, "d1", "done", reply="ok")
        AG._wake_finish(sdir, "f1", "failed", error="boom", exit_code=3)
        self.assertEqual([r["event"] for r in AG._wake_turnlog_read(sdir, "d1")], ["finished"])
        rows = AG._wake_turnlog_read(sdir, "f1")
        self.assertEqual(rows[-1]["event"], "failed")
        self.assertEqual(rows[-1]["reason"], "boom")


class RetryTest(unittest.TestCase):
    def setUp(self):
        self.real = AG.launch_wake_chat; self.stub = StubLaunch(); AG.launch_wake_chat = self.stub
    def tearDown(self): AG.launch_wake_chat = self.real

    def test_retry_failed_same_options_and_model_override(self):
        sdir = fresh()
        job(sdir, "old1", verify="make test", escalate=["m2"], verify_timeout_s=42)
        row = AG._wake_retry_one(sdir, AG._wake_read_job(sdir, "old1"), model="big-model")
        self.assertEqual(row["action"], "retried")
        agent, message, kw = self.stub.calls[0]
        self.assertEqual((agent, message), ("w", "do it"))
        self.assertEqual(kw["verify"], "make test"); self.assertEqual(kw["escalate"], ["m2"])
        self.assertEqual(kw["verify_timeout_s"], 42); self.assertEqual(kw["model"], "big-model")
        self.assertEqual(kw["extra"]["retry_of"], "old1")
        self.assertEqual(AG._wake_read_job(sdir, "old1")["retried_as"], "new1")
        self.assertEqual(AG._wake_turnlog_read(sdir, "old1")[-1]["event"], "retry")

    def test_retry_parked_claims_and_marks_retried(self):
        sdir = fresh(); job(sdir, "p1", status="parked", resume_at=time.time() + 60)
        row = AG._wake_retry_one(sdir, AG._wake_read_job(sdir, "p1"))
        self.assertEqual(row["action"], "retried")
        cur = AG._wake_read_job(sdir, "p1")
        self.assertEqual(cur["status"], "retried"); self.assertEqual(cur["retried_as"], "new1")
        # a second retry of the now-retried job is skipped, not relaunched
        row2 = AG._wake_retry_one(sdir, cur)
        self.assertTrue(row2["action"].startswith("skipped"))
        self.assertEqual(len(self.stub.calls), 1)

    def test_retry_skips_live_and_done(self):
        sdir = fresh(); job(sdir, "r1", status="running"); job(sdir, "d1", status="done")
        self.assertTrue(AG._wake_retry_one(sdir, AG._wake_read_job(sdir, "r1"))["action"].startswith("skipped"))
        self.assertTrue(AG._wake_retry_one(sdir, AG._wake_read_job(sdir, "d1"))["action"].startswith("skipped"))
        self.assertEqual(self.stub.calls, [])


class InterruptedTest(unittest.TestCase):
    def setUp(self):
        self.real = AG.launch_wake_chat; self.stub = StubLaunch(); AG.launch_wake_chat = self.stub
    def tearDown(self): AG.launch_wake_chat = self.real

    def running_dead(self, sdir, jid, **kw):
        return job(sdir, jid, status="running", pid=DEAD_PID, session="nosuch",
                   ts="2020-01-01T00:00:00+00:00", running_at="2020-01-01T00:00:00+00:00", **kw)

    def test_dead_worker_no_session_fails_with_clear_reason(self):
        sdir = fresh(); self.running_dead(sdir, "x1")
        rows = AG._wake_handle_interrupted(sdir)
        self.assertEqual(len(rows), 1); self.assertEqual(rows[0]["action"], "failed")
        cur = AG._wake_read_job(sdir, "x1")
        self.assertEqual(cur["status"], "failed"); self.assertTrue(cur["interrupted"])
        self.assertIn("interrupted:", cur["error"]); self.assertIn("no backend session id", cur["error"])
        self.assertEqual([r["event"] for r in AG._wake_turnlog_read(sdir, "x1")], ["interrupted", "failed"])
        self.assertEqual(AG._wake_handle_interrupted(sdir), [])  # never double-run
        self.assertEqual(self.stub.calls, [])

    def test_dead_worker_session_known_but_not_opted_in_fails(self):
        sdir = fresh(); self.running_dead(sdir, "x2", backend_sid="bs9")
        rows = AG._wake_handle_interrupted(sdir)
        self.assertEqual(rows[0]["action"], "failed")
        self.assertIn("wake.resume is not enabled", AG._wake_read_job(sdir, "x2")["error"])
        self.assertEqual(self.stub.calls, [])

    def test_dead_worker_opted_in_resumes_session(self):
        sdir = fresh(); self.running_dead(sdir, "x3", backend_sid="bs9", resume=True)
        rows = AG._wake_handle_interrupted(sdir)
        self.assertEqual(rows[0]["action"], "resumed")
        agent, message, kw = self.stub.calls[0]
        self.assertEqual(message, AG.WAKE_RESUME_MSG)
        self.assertEqual(kw["extra"], {"resume_of": "x3", "backend_sid": "bs9"})
        cur = AG._wake_read_job(sdir, "x3")
        self.assertEqual(cur["status"], "interrupted"); self.assertEqual(cur["resumed_as"], "new1")
        self.assertEqual([r["event"] for r in AG._wake_turnlog_read(sdir, "x3")], ["interrupted", "resumed"])
        self.assertEqual(AG._wake_handle_interrupted(sdir), [])

    def test_config_wake_resume_opts_in(self):
        sdir = fresh()
        cfg = Path(tempfile.mkdtemp(prefix="agcfg-")); (cfg / "config.json").write_text(json.dumps({"wake": {"resume": True}}))
        old = os.environ.get("AG_CONFIG_HOME"); os.environ["AG_CONFIG_HOME"] = str(cfg)
        try:
            self.assertTrue(AG._wake_resume_opted_in(sdir, {"agent": "w"}))
            (cfg / "config.json").write_text(json.dumps({"wake": {"resume": False}}))
            self.assertFalse(AG._wake_resume_opted_in(sdir, {"agent": "w"}))
        finally:
            if old is None: os.environ.pop("AG_CONFIG_HOME", None)
            else: os.environ["AG_CONFIG_HOME"] = old

    def test_live_worker_left_alone(self):
        sdir = fresh(); job(sdir, "lv", status="running", pid=os.getpid(), session="nosuch")
        self.assertEqual(AG._wake_handle_interrupted(sdir), [])
        self.assertEqual(AG._wake_read_job(sdir, "lv")["status"], "running")


class ParkCapTest(unittest.TestCase):
    """do__wake_run with a stubbed run_turn returning an unknown-reset usage limit."""
    def setUp(self):
        self.real = AG.run_turn
        def fake(sdir, name, text, **kw):
            if kw.get("on_start"): kw["on_start"]()
            return {"sid": "bs1", "exit": 1, "reply": "limit", "error": "claude usage limit (reset time unknown; cooling 1h)",
                    "usage_limit": True, "usage_limit_when": "",
                    "reset_at": datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()}
        AG.run_turn = fake
    def tearDown(self): AG.run_turn = self.real

    def run_worker(self, sdir, jid):
        a = type("A", (), {"dir": str(sdir), "jid": jid})()
        return AG.do__wake_run(a)

    def test_unknown_reset_parks_and_counts(self):
        sdir = fresh(); job(sdir, "u1", status="queued")
        self.assertEqual(self.run_worker(sdir, "u1"), 0)
        cur = AG._wake_read_job(sdir, "u1")
        self.assertEqual(cur["status"], "parked")
        self.assertTrue(cur["usage_reset_unknown"]); self.assertEqual(cur["park_retries"], 1)
        self.assertGreater(cur["resume_at"], time.time())
        evs = [r["event"] for r in AG._wake_turnlog_read(sdir, "u1")]
        self.assertIn("session", evs); self.assertIn("parked", evs)
        self.assertEqual(cur["backend_sid"], "bs1")

    def test_unknown_reset_cap_3_fails(self):
        sdir = fresh(); job(sdir, "u2", status="queued", park_retries=3)
        self.assertEqual(self.run_worker(sdir, "u2"), 1)
        cur = AG._wake_read_job(sdir, "u2")
        self.assertEqual(cur["status"], "failed")
        self.assertIn("usage limit", cur["error"])


FAKE_CLAUDE = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/claude.log"
if [ -f "$FAKE_DIR/claude.fail" ]; then
  echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
  python3 -c 'import json,sys; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":sys.argv[1],"total_cost_usd":0}))' "$(cat "$FAKE_DIR/claude.fail")"
  exit 1
fi
if [ -f "$FAKE_DIR/claude.limit" ]; then
  echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
  python3 -c 'import json,sys; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":sys.argv[1],"total_cost_usd":0}))' "$(cat "$FAKE_DIR/claude.limit")"
  exit 1
fi
echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"claude ok"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"claude ok","total_cost_usd":0}'
"""
LIMIT_KNOWN = "You've hit your individual spend limit · your 5-hour limit resets 7:40pm (America/Toronto)"
LIMIT_UNKNOWN = "You've hit your usage limit · try again later"


@unittest.skipUnless(os.name == "posix", "wake workers are POSIX")
class EndToEnd(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cfg", "cache", "state"):
            (r / d).mkdir(parents=True)
        (r / "home/.codex/models_cache.json").write_text(json.dumps({"models": []}))
        (r / "fake/claude").write_text(FAKE_CLAUDE); (r / "fake/claude").chmod(0o755)
        subprocess.run(["git", "init", "-q", str(r / "proj")], check=True)
        self.fake, self.state, self.cfg = r / "fake", r / "state", r / "cfg"
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"),
            AG_CACHE_HOME=str(r / "cache"), AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0",
            AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_ZEN_URL="off", HOME=str(r / "home"),
            PATH=f"{r / 'fake'}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
            AG_MODELSDEV_URL="http://127.0.0.1:1/api.json", AG_OPENROUTER_RANKINGS_URL="off",
            FAKE_DIR=str(r / "fake"), AG_PRIVACY="off")
        p = self.run_ag("agents", "add", "w", "--backend", "claude", "--role", "sub", "--dir", str(r / "proj"))
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))

    def run_ag(self, *args):
        return subprocess.run([sys.executable, _AG, "--dir", str(self.state), *args],
            capture_output=True, text=True, timeout=120, cwd=self.root / "proj",
            env=self.env, stdin=subprocess.DEVNULL)

    def run_json(self, *args):
        p = self.run_ag("--json", *args)
        return p, (json.loads(p.stdout) if p.stdout.strip() else {})

    def job(self, jid):
        try: return json.loads((self.state / "wake/jobs" / f"{jid}.json").read_text())
        except Exception: return {}

    def turnlog(self, jid):
        p = self.state / "wake/jobs" / f"{jid}.turns.jsonl"
        if not p.exists(): return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def wait_for(self, pred, timeout=60):
        t0 = time.time()
        while time.time() - t0 < timeout:
            if pred(): return True
            time.sleep(0.2)
        return False

    def wake(self, text, *flags):
        p, o = self.run_json("wake", "w", *flags, text)
        self.assertTrue(o.get("ok"), (p.stdout, p.stderr, o))
        return o["data"]["job"]

    def test_turnlog_for_successful_wake(self):
        jid = self.wake("hello durable")
        self.assertTrue(self.wait_for(lambda: self.job(jid).get("status") == "done"
            and self.turnlog(jid) and self.turnlog(jid)[-1]["event"] == "finished"), self.job(jid))
        evs = [r["event"] for r in self.turnlog(jid)]
        self.assertEqual(evs[0], "started"); self.assertIn("session", evs); self.assertEqual(evs[-1], "finished")
        srow = next(r for r in self.turnlog(jid) if r["event"] == "session")
        self.assertEqual(srow["backend_sid"], "cl_1")
        self.assertEqual(self.job(jid)["backend_sid"], "cl_1")

    def test_retry_failed_wake_e2e(self):
        (self.fake / "claude.fail").write_text("some backend error")
        jid = self.wake("retry me")
        self.assertTrue(self.wait_for(lambda: self.job(jid).get("status") == "failed"), self.job(jid))
        (self.fake / "claude.fail").unlink()
        p, o = self.run_json("wakes", "--retry", jid)
        self.assertTrue(o.get("ok"), (p.stdout, p.stderr, o))
        rows = o["data"]["retried"]
        self.assertEqual(rows[0]["action"], "retried"); new = rows[0]["new"]
        self.assertTrue(self.wait_for(lambda: self.job(new).get("status") == "done"), self.job(new))
        nj = self.job(new)
        self.assertEqual(nj["retry_of"], jid); self.assertEqual(nj["message"], "retry me")
        self.assertEqual(self.job(jid)["retried_as"], new)
        p, o = self.run_json("wakes", "--retry", "nosuch99")
        self.assertFalse(o.get("ok"), o)

    def test_unknown_reset_parks_instead_of_failing_e2e(self):
        (self.fake / "claude.limit").write_text(LIMIT_UNKNOWN)
        jid = self.wake("unknown reset work")
        self.assertTrue(self.wait_for(lambda: self.job(jid).get("status") == "parked"), self.job(jid))
        j = self.job(jid)
        self.assertTrue(j["usage_reset_unknown"]); self.assertEqual(j["park_retries"], 1)
        self.assertGreater(j["resume_at"], time.time())

    def craft_running_dead(self, jid, **kw):
        j = {"id": jid, "agent": "w", "message": "craft", "trigger": "t", "status": "running",
             "ts": "2020-01-01T00:00:00+00:00", "running_at": "2020-01-01T00:00:00+00:00",
             "pid": DEAD_PID, "session": "nosuch", "notify": "", "on_done": "",
             "timeout_s": 0, "max_runtime_s": 0, **kw}
        d = self.state / "wake/jobs"; d.mkdir(parents=True, exist_ok=True)
        (d / f"{jid}.json").write_text(json.dumps(j))

    def tick(self):
        iso = datetime.fromtimestamp(time.time() + 1).isoformat()
        p, o = self.run_json("schedule", "tick", "--now", iso)
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        return o["data"]["dirs"][0]["actions"]

    def test_tick_marks_dead_worker_interrupted_then_failed(self):
        self.craft_running_dead("dead1")
        acts = [a for a in self.tick() if a.get("id") == "dead1"]
        self.assertEqual(acts[0]["action"], "failed")
        j = self.job("dead1")
        self.assertEqual(j["status"], "failed"); self.assertTrue(j["interrupted"])
        self.assertIn("no backend session id", j["error"])
        self.assertEqual([r["event"] for r in self.turnlog("dead1")], ["interrupted", "failed"])

    def test_tick_resumes_dead_worker_when_opted_in(self):
        (self.cfg / "config.json").write_text(json.dumps({"wake": {"resume": True}}))
        self.craft_running_dead("dead2", backend_sid="cl_1")
        acts = [a for a in self.tick() if a.get("id") == "dead2"]
        self.assertEqual(acts[0]["action"], "resumed")
        old = self.job("dead2")
        self.assertEqual(old["status"], "interrupted"); new = old["resumed_as"]
        self.assertTrue(self.wait_for(lambda: self.job(new).get("status") == "done"), self.job(new))
        # status "done" is written before the detached worker exits; wait for the
        # process so teardown does not race its last writes (ENOTEMPTY on rmtree).
        pid = self.job(new).get("pid"); self.assertTrue(pid, self.job(new))
        self.assertTrue(self.wait_for(lambda: not _pid_alive(pid), timeout=60), ("worker still running", pid))
        self.assertEqual(self.job(new)["message"], "continue; you were interrupted")
        self.assertEqual(self.job(new)["resume_of"], "dead2")


if __name__ == "__main__":
    unittest.main()
