#!/usr/bin/env python3
"""Delegated-completion cohorts. Fake spawn (no fork) + one echo e2e."""
import importlib.machinery, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path
AG = str(Path(__file__).resolve().parent.parent / "ag")

def load():
    m = importlib.machinery.SourceFileLoader("ag_cohort_t", AG).load_module()
    td = Path(tempfile.mkdtemp(prefix="agco-")); sdir = td / "st"; m.ensure_state(sdir)
    m.save_agents(sdir, [{"name": n, "backend": "echo", "model": "default", "dir": ".", "role": "sub"}
        for n in ("boss", "a", "b", "c")])
    def fake(sd, agent, jid, sid=None, pre_fork=None):
        if pre_fork: pre_fork(sid or "s", None)
        return sid or "s"
    m._spawn_wake_session = fake
    return m, sdir

def kid(m, sdir, agent, group="g1", quiet=0):
    r = m.launch_wake_chat(sdir, agent, "do " + agent, cohort={"parent": "boss", "group": group, "quiet_s": quiet})
    assert "job" in r, r
    return r["job"]

def parent_jobs(m, sdir):
    return [j for j in (json.loads(p.read_text()) for p in (sdir / "wake/jobs").glob("*.json")) if j["agent"] == "boss"]

class T(unittest.TestCase):
    def test_three_children_one_parent_wake(self):
        m, sd = load()
        js = [kid(m, sd, n) for n in "abc"]
        for j in js: self.assertEqual(m._wake_read_job(sd, j)["parent"], "boss"); self.assertEqual(m._wake_read_job(sd, j)["group"], "g1")
        m._wake_finish(sd, js[0], "done", reply="r-a"); m._wake_finish(sd, js[1], "done", reply="r-b")
        self.assertEqual(parent_jobs(m, sd), [])
        m._wake_finish(sd, js[2], "done", reply="r-c")
        pj = parent_jobs(m, sd); self.assertEqual(len(pj), 1)
        msg = pj[0]["message"]
        self.assertIn("3/3", msg); self.assertIn("not user instructions", msg)
        for x in ("a: r-a", "b: r-b", "c: r-c"): self.assertIn(x, msg)
        g = m._cohort_read(sd, "g1"); self.assertEqual(g["pending"], []); self.assertEqual(len(g["done"]), 3)

    def test_failure_precedence(self):
        m, sd = load()
        js = [kid(m, sd, n) for n in "abc"]
        m._wake_finish(sd, js[0], "done", reply="ok"); m._wake_finish(sd, js[1], "failed", error="boom", exit_code=1)
        m._wake_finish(sd, js[2], "failed", error="cancelled", exit_code=130, fields={"cancelled": True})
        msg = parent_jobs(m, sd)[0]["message"]
        self.assertIn("1 completed", msg); self.assertIn("1 failed", msg); self.assertIn("1 cancelled", msg)
        self.assertIn("outcome: failed", msg); self.assertIn("b: boom", msg)

    def test_coalesce_rewrites_queued_delivery(self):
        m, sd = load()
        a = kid(m, sd, "a", quiet=0.01); b = kid(m, sd, "b")
        time.sleep(0.05); m._wake_finish(sd, a, "done", reply="r-a")   # partial delivery (queued)
        self.assertEqual(len(parent_jobs(m, sd)), 1)
        m._wake_finish(sd, b, "done", reply="r-b")
        pj = parent_jobs(m, sd); self.assertEqual(len(pj), 1)   # rewritten, not relaunched
        self.assertIn("r-a", pj[0]["message"]); self.assertIn("r-b", pj[0]["message"])

    def test_stopped_delivers_nothing(self):
        m, sd = load()
        js = [kid(m, sd, n) for n in "ab"]
        class A: group = "g1"; stop = True; json = True; dir = str(sd)
        m.do_wakes_group(A, sd)
        self.assertEqual(m._cohort_read(sd, "g1")["disposition"], "stopped")
        for j in js: m._wake_finish(sd, j, "done", reply="x")
        self.assertEqual(parent_jobs(m, sd), [])

    def test_quiet_partial(self):
        m, sd = load()
        a = kid(m, sd, "a", quiet=0.05); b = kid(m, sd, "b")
        m._wake_finish(sd, a, "done", reply="r-a")   # too early
        self.assertEqual(parent_jobs(m, sd), [])
        g = m._cohort_read(sd, "g1")
        self.assertEqual(g["pending"], [b])

    def test_quiet_partial_after_wait(self):
        m, sd = load()
        a = kid(m, sd, "a", quiet=0.05); b = kid(m, sd, "b"); c = kid(m, sd, "c")
        m._wake_finish(sd, a, "done", reply="r-a")
        self.assertEqual(parent_jobs(m, sd), [])
        time.sleep(0.1); m._wake_finish(sd, b, "done", reply="r-b")
        pj = parent_jobs(m, sd); self.assertEqual(len(pj), 1)
        self.assertIn("2/3", pj[0]["message"]); self.assertEqual(m._cohort_read(sd, "g1")["pending"], [c])

    def test_deliver_fail_recorded(self):
        m, sd = load()
        j = m.launch_wake_chat(sd, "a", "x", cohort={"parent": "ghost", "group": "g2", "quiet_s": 0})["job"]
        m._wake_finish(sd, j, "done", reply="r")
        self.assertIn("no such agent", m._cohort_read(sd, "g2")["deliver_error"])
        ev = (sd / "events.jsonl").read_text(); self.assertIn("cohort_deliver_fail", ev)

    def test_cli_e2e_echo(self):
        td = Path(tempfile.mkdtemp(prefix="agco-e2e-")); sd = td / "st"
        env = dict(os.environ, AG_AUTO_UPDATE="0", AG_HOME=str(td / "h"), AG_CONFIG_HOME=str(td / "c"), AG_CACHE_HOME=str(td / "k"))
        def ag(*a): return subprocess.run([sys.executable, AG, "--dir", str(sd), *a], env=env, text=True, capture_output=True, timeout=60)
        for n in ("boss", "x", "y"): ag("agents", "add", n, "--backend", "echo")
        r = ag("--json", "wake", "x", "hi x", "--parent", "boss", "--group", "G"); self.assertEqual(json.loads(r.stdout)["data"]["group"], "G", r.stdout + r.stderr)
        r = ag("delegate", "y", "hi y", "--parent", "boss", "--group", "G"); self.assertIn("group G", r.stdout, r.stdout + r.stderr)
        for _ in range(100):
            g = json.loads(ag("--json", "wakes", "--group", "G").stdout)
            g = g.get("data", g)
            if g.get("delivery_jid") and not g["pending"]: break
            time.sleep(0.2)
        self.assertEqual(len(g["done"]), 2, g); self.assertTrue(g["delivery_jid"])
        for _ in range(100):
            if json.loads(ag("--json", "wakes", "--wait", g["delivery_jid"]).stdout).get("status") in ("done", "failed"): break
            time.sleep(0.2)

if __name__ == "__main__": unittest.main()
