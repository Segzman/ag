#!/usr/bin/env python3
"""Per-agent/per-turn stall watchdog (item B): flags, precedence, actionable error.

- _turn_stall_s precedence: per-turn > agent stall_s > AG_STALL_S env > backend default
- agents add|set --stall-s N (0 = off, "" clears) persisted on the row, shown in agents list --json
- wake|chat send --stall-s N threads into run_turn (worker: job stall_s)
- stall error text names `agents set NAME --stall-s 900`
Run: python3 tests/test_ag_stall.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
d = os.environ["FAKE_DIR"]; mode = os.environ.get("FAKE_MODE", "ok")
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(sys.argv[1:]) + "\n")
n = len(open(os.path.join(d, "calls.jsonl")).read().splitlines())
def ok(tag="FAKE_OK"):
    print(json.dumps({"type": "text", "timestamp": 2, "sessionID": "ses-1", "part": {"type": "text", "text": tag}}))
    sys.exit(0)
if mode == "slow":         # n==1: silent 3s then success (watchdog kills); retry ok
    if n == 1: time.sleep(3); ok()
    ok()
if mode == "stallsilent":  # every attempt: silent 3s then exit 1 (no output at all)
    time.sleep(3); sys.exit(1)
ok()
'''


def load_ag():
    import importlib.util
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("ag_stall_mod", AG)  # no .py extension: explicit loader
    spec = importlib.util.spec_from_loader("ag_stall_mod", loader)
    m = importlib.util.module_from_spec(spec)
    loader.exec_module(m)
    return m


class StallUnit(unittest.TestCase):
    """Precedence resolution: per-turn > agent stall_s (0 = off) > env > backend default."""

    def setUp(self):
        self.m = load_ag()
        self.saved = os.environ.pop("AG_STALL_S", None)
        self.addCleanup(self.restore_env)

    def restore_env(self):
        if self.saved is not None: os.environ["AG_STALL_S"] = self.saved

    def test_backend_default_when_unset(self):
        self.assertEqual(self.m._turn_stall_s({}, "echo"), 0)          # echo backend: watchdog off
        self.assertEqual(self.m._turn_stall_s({}, "opencode"), 240.0)  # opencode default 240
        self.assertEqual(self.m._turn_stall_s({}, "claude"), 0)

    def test_agent_beats_env(self):
        os.environ["AG_STALL_S"] = "5"
        self.assertEqual(self.m._turn_stall_s({"stall_s": 900}, "echo"), 900)
        self.assertEqual(self.m._turn_stall_s({}, "echo"), 5)          # env still applies without agent value

    def test_per_turn_beats_agent_and_env(self):
        os.environ["AG_STALL_S"] = "5"
        a = {"stall_s": 900}
        self.assertEqual(self.m._turn_stall_s(a, "echo", 3), 3)
        self.assertEqual(self.m._turn_stall_s(a, "echo", 0), 0)        # per-turn 0 = off wins over agent 900

    def test_zero_agent_disables_env(self):
        os.environ["AG_STALL_S"] = "5"
        self.assertEqual(self.m._turn_stall_s({"stall_s": 0}, "opencode"), 0)

    def test_bad_values_fall_back(self):
        os.environ["AG_STALL_S"] = "5"
        self.assertEqual(self.m._turn_stall_s({"stall_s": None}, "echo"), 5)
        self.assertEqual(self.m._turn_stall_s({"stall_s": "junk"}, "echo"), 5)


class StallFlags(unittest.TestCase):
    """CLI: agents add|set --stall-s persistence + agents list --json exposure."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agstall-"))
        self.state = self.td / ".agent"
        self.env = dict(os.environ, PATH=os.environ["PATH"], FAKE_DIR=str(self.td),
                        AG_PROBE="0", AG_AUTO_UPDATE="0", AG_RTK="0",
                        AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
                        AG_CACHE_HOME=str(self.td / "cache"))
        self.env.pop("AG_STALL_S", None)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a):
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=120, env=self.env)

    def job(self):
        for p in sorted((self.state / "wake" / "jobs").glob("*.json")):
            try: return json.loads(p.read_text())
            except Exception: continue
        return {}

    def row(self, name):
        out = json.loads(self.ag("--json", "agents", "list").stdout)["data"]["agents"]
        return next(r for r in out if r["name"] == name)

    def test_add_set_clear_show(self):
        p = self.ag("agents", "add", "e", "--backend", "echo", "--stall-s", "5")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(self.row("e").get("stall_s"), 5)
        self.assertEqual(self.ag("agents", "set", "e", "--stall-s", "30").returncode, 0)
        self.assertEqual(self.row("e").get("stall_s"), 30)
        self.assertEqual(self.ag("agents", "set", "e", "--stall-s", "0").returncode, 0)
        self.assertEqual(self.row("e").get("stall_s"), 0)   # stored 0 = off
        p = self.ag("agents", "set", "e", "--stall-s", "")        # "" clears -> key gone
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertNotIn("stall_s", self.row("e"))
        p = self.ag("agents", "set", "e", "--stall-s", "abc")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("--stall-s", p.stderr + p.stdout)

    def test_wake_job_records_per_turn_stall(self):
        p = self.ag("agents", "add", "e2", "--backend", "echo", "--dir", str(self.td))
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        p = self.ag("wake", "e2", "--stall-s", "7", "quick")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        deadline = time.time() + 20
        j = {}
        while time.time() < deadline:
            j = self.job()
            if j.get("agent") == "e2" and j.get("status") in ("done", "failed"): break
            time.sleep(0.1)
        self.assertEqual(j.get("status"), "done", j)
        self.assertEqual(j.get("stall_s"), 7.0, j)
        p = self.ag("wake", "e2", "no flag")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        deadline = time.time() + 20
        j2 = {}
        while time.time() < deadline:
            for pth in sorted((self.state / "wake" / "jobs").glob("*.json")):
                try: cand = json.loads(pth.read_text())
                except Exception: continue
                if cand.get("agent") == "e2" and cand.get("id") != j.get("id") \
                        and cand.get("status") in ("done", "failed"): j2 = cand
            if j2: break
            time.sleep(0.1)
        self.assertNotIn("stall_s", j2)


class StallE2E(unittest.TestCase):
    """Fake opencode binary: watchdog kill + actionable text + per-turn disable."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agstalle-"))
        self.state = self.td / ".agent"
        (self.td / "bin").mkdir()
        (self.td / "bin" / "opencode").write_text(FAKE)
        (self.td / "bin" / "opencode").chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.td / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_DIR=str(self.td), FAKE_MODE="ok",
                        AG_PROBE="0", AG_AUTO_UPDATE="0", AG_RTK="0",
                        AG_RATELIMIT_BASE_S="0.2",
                        AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
                        AG_CACHE_HOME=str(self.td / "cache"))
        self.env.pop("AG_STALL_S", None)
        self.ag("agents", "add", "s1", "--backend", "opencode", "--model", "opencode/paywalled-x",
                "--stall-s", "1")  # non-free model: no fallback chain, failures are terminal

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a, extra=None):
        env = dict(self.env, **(extra or {}))
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=180, env=env)

    def job(self):
        for p in sorted((self.state / "wake" / "jobs").glob("*.json")):
            try: return json.loads(p.read_text())
            except Exception: continue
        return {}

    def test_kill_names_the_fix(self):
        # AG_RETRY=0: first killed attempt is terminal (else the ratelimit retry cycle
        # with its backoff escalation spins for minutes); error row carries the hint
        p = self.ag("--json", "chat", "send", "s1", "hi",
                    extra={"FAKE_MODE": "stallsilent", "AG_RETRY": "0"})
        data = json.loads(p.stdout)["data"]  # CLI exits 0; turn exit + reply carry the failure
        self.assertNotEqual(data["exit"], 0, data)
        self.assertIn("stalled: no output for 1s", data["reply"], data)
        self.assertIn("raise it: ag agents set s1 --stall-s 900", data["reply"], data)

    def test_kill_retries_then_ok(self):
        # slow: attempt 1 killed at 1s, retry succeeds -> turn ok (watchdog fires, work recovers)
        p = self.ag("--json", "chat", "send", "s1", "hi", extra={"FAKE_MODE": "slow"})
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("FAKE_OK", p.stdout)

    def test_per_turn_disable_wins_over_agent(self):
        # agent stall_s=1 would kill attempt 1; --stall-s 0 disables -> whole turn succeeds
        p2 = self.ag("--json", "chat", "send", "s1", "--stall-s", "0", "hi",
                     extra={"FAKE_MODE": "slow"})
        self.assertEqual(p2.returncode, 0, (p2.stdout, p2.stderr))
        self.assertIn("FAKE_OK", p2.stdout)
        self.assertNotIn("stalled", p2.stdout, p2.stdout)

    def test_wake_worker_uses_agent_and_per_turn_stall(self):
        # AG_RETRY=0: single killed attempt ends the job (no ratelimit spin)
        # agent stall_s=1, no per-turn flag: silent attempt killed -> stalled error in job
        self.ag("agents", "add", "w1", "--backend", "opencode", "--model", "opencode/paywalled-x",
                "--stall-s", "1")
        p = self.ag("wake", "w1", "die quietly",
                    extra={"FAKE_MODE": "stallsilent", "AG_RETRY": "0"})
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        j, deadline = {}, time.time() + 90
        while time.time() < deadline:
            j = self.job()
            if j.get("agent") == "w1" and j.get("status") in ("done", "failed"): break
            time.sleep(0.2)
        self.assertEqual(j.get("status"), "failed", j)
        self.assertIn("stalled: no output for 1s", j.get("error", ""), j)
        self.assertIn("raise it: ag agents set w1 --stall-s 900", j.get("error", ""), j)
        # per-turn --stall-s 0: watchdog off -> raw backend failure, no stalled text
        p2 = self.ag("wake", "w1", "--stall-s", "0", "run again",
                     extra={"FAKE_MODE": "stallsilent", "AG_RETRY": "0"})
        self.assertEqual(p2.returncode, 0, (p2.stdout, p2.stderr))
        j2, hit = None, []
        deadline = time.time() + 90
        while time.time() < deadline:
            jobs = []
            for pth in sorted((self.state / "wake" / "jobs").glob("*.json")):
                try: jobs.append(json.loads(pth.read_text()))
                except Exception: continue
            hit = [j2 for j2 in jobs if j2.get("agent") == "w1" and j2.get("status") in ("done", "failed")]
            if len([j2 for j2 in hit if j2.get("id") != j.get("id")]) >= 1: break
            time.sleep(0.2)
        j2 = next((j3 for j3 in hit if j3.get("id") != j.get("id")), None)
        self.assertTrue(j2, f"no completed second job: {hit}")
        self.assertEqual(j2.get("status"), "failed", j2)
        self.assertNotIn("stalled", j2.get("error", ""), j2)


if __name__ == "__main__":
    unittest.main()
