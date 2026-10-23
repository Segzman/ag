#!/usr/bin/env python3
"""--verify CMD: ag checks the work instead of trusting the agent's self-report.

run_verify units + chat/delegate/wake plumbing + wake-worker harvest + cohort
summary suffix. Fake spawn (no fork) + one real echo e2e. All offline.
"""
import importlib.machinery, importlib.util, io, json, os, subprocess, sys, tempfile, unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
AG = str(Path(__file__).resolve().parent.parent / "ag")

def load():
    """Fresh ag module + echo roster (agent dir = the temp state dir so
    delegate_task's harness_link copies stay inside the fixtures)."""
    _l = importlib.machinery.SourceFileLoader("ag_verify_t", AG)
    _s = importlib.util.spec_from_loader("ag_verify_t", _l)
    m = importlib.util.module_from_spec(_s); _l.exec_module(m)
    td = Path(tempfile.mkdtemp(prefix="agvf-")); sdir = td / "st"; m.ensure_state(sdir)
    m.save_agents(sdir, [{"name": n, "backend": "echo", "model": "default", "dir": str(sdir), "role": "sub"}
        for n in ("boss", "a", "b")])
    def fake(sd, agent, jid, sid=None, pre_fork=None):
        if pre_fork: pre_fork(sid or "s", None)
        return sid or "s"
    m._spawn_wake_session = fake
    return m, td, sdir

def run(m, fn, *argv):
    """Parse argv, run fn(ns); returns (rc, data|None, stdout, stderr)."""
    ns = m.build().parse_args(list(argv))
    buf, err = io.StringIO(), io.StringIO()
    with redirect_stdout(buf), redirect_stderr(err):
        rc = fn(ns)
    txt = buf.getvalue()
    data = json.loads(txt) if txt.strip().startswith("{") else None
    return rc or 0, data, txt, err.getvalue()

def worker(m, sd, jid):
    """Simulate the wake worker body in-process (status queued -> done/failed)."""
    return m.do__wake_run(m.build().parse_args(["--dir", str(sd), "_wake_run", jid]))


class TVerifyUnits(unittest.TestCase):
    def test_ok_fail_empty(self):
        m, td, sd = load()
        v = m.run_verify(sd, "a", "echo verified-ok")
        self.assertEqual(v["cmd"], "echo verified-ok"); self.assertEqual(v["exit"], 0)
        self.assertIn("verified-ok", v["tail"])
        self.assertEqual(m.run_verify(sd, "a", "exit 3")["exit"], 3)
        v = m.run_verify(sd, "a", "")
        self.assertEqual(v["exit"], 1); self.assertIn("empty command", v["tail"])

    def test_timeout_exit_124(self):
        m, td, sd = load()
        v = m.run_verify(sd, "a", "sleep 3", timeout_s=0.5)
        self.assertEqual(v["exit"], 124); self.assertIn("timed out", v["tail"])

    def test_tail_last_40_lines(self):
        m, td, sd = load()
        v = m.run_verify(sd, "a", "for i in $(seq 1 50); do echo L$i; done")
        ls = v["tail"].splitlines()
        self.assertEqual(len(ls), 40)
        self.assertTrue(ls[-1].startswith("L50")); self.assertTrue(ls[0].startswith("L11"))

    def test_workdir_is_agent_dir(self):
        m, td, sd = load()
        w = td / "work"; w.mkdir(); (w / "marker.txt").write_text("x")
        rows = json.loads((sd / "agents.json").read_text())
        for r in rows:
            if r["name"] == "a": r["dir"] = str(w)
        m.save_agents(sd, rows)
        v = m.run_verify(sd, "a", "test -f marker.txt && echo in-workdir")
        self.assertEqual(v["exit"], 0); self.assertIn("in-workdir", v["tail"])
        v = m.run_verify(sd, "b", "test -f marker.txt && echo in-workdir")  # other agent: cwd, not the workdir
        self.assertEqual(v["exit"], 1); self.assertEqual(v["tail"], "")


class TVerifyLogRow(unittest.TestCase):
    def test_log_row(self):
        m, td, sd = load()
        m.verify_log_row(sd, "a", {"cmd": "echo ok", "exit": 0, "tail": "ok"})
        row = m.load_chat_log(sd, "a")[-1]
        self.assertEqual((row["role"], row["text"]), ("tool", "[verify] exit=0"))


class TChatVerify(unittest.TestCase):
    def test_send_json(self):
        m, td, sd = load()
        rc, d, _, _ = run(m, m.do_chat, "--dir", str(sd), "--json", "chat", "send", "a",
            "build it", "--verify", "echo ok", "--verify-timeout", "700")
        self.assertEqual(rc, 0); self.assertEqual(d["data"]["exit"], 0)
        self.assertEqual(d["data"]["verify"], {"cmd": "echo ok", "exit": 0, "tail": "ok"})
        self.assertEqual(m.load_chat_log(sd, "a")[-1]["text"], "[verify] exit=0")

    def test_send_no_verify(self):
        m, td, sd = load()
        rc, d, _, _ = run(m, m.do_chat, "--dir", str(sd), "--json", "chat", "send", "a", "plain")
        self.assertNotIn("verify", d["data"])
        self.assertEqual(m.load_chat_log(sd, "a")[-1]["role"], "agent")

    def test_send_text_output(self):
        m, td, sd = load()
        _, _, out, _ = run(m, m.do_chat, "--dir", str(sd), "chat", "send", "a", "go", "--verify", "echo ok")
        self.assertIn("[exit=0]", out); self.assertIn("verify=0", out)

    def test_send_invalid_verify_timeout(self):
        m, td, sd = load()
        _, _, _, err = run(m, m.do_chat, "--dir", str(sd), "chat", "send", "a",
            "go", "--verify", "echo ok", "--verify-timeout", "-3")
        self.assertIn("invalid --verify-timeout", err)


class TDelegateVerify(unittest.TestCase):
    def test_sync_json(self):
        m, td, sd = load()
        rc, d, _, _ = run(m, m.do_delegate, "--dir", str(sd), "--json", "delegate", "a",
            "make", "--verify", "echo ok")
        self.assertEqual(rc, 0); self.assertEqual(d["data"]["verify"]["exit"], 0)
        self.assertEqual(m.load_chat_log(sd, "a")[-1]["text"], "[verify] exit=0")

    def test_sync_text(self):
        m, td, sd = load()
        _, _, out, _ = run(m, m.do_delegate, "--dir", str(sd), "delegate", "a", "make", "--verify", "echo ok")
        self.assertIn("(exit=0)", out); self.assertIn("verify=0", out)

    def test_parent_path_job_carries_verify(self):
        m, td, sd = load()
        rc, d, _, _ = run(m, m.do_delegate, "--dir", str(sd), "--json", "delegate", "a", "task",
            "--parent", "boss", "--group", "gd", "--verify", "echo ok")
        self.assertEqual(rc, 0); self.assertEqual(d["data"]["group"], "gd")
        j = next(j for j in (json.loads(p.read_text()) for p in (sd / "wake/jobs").glob("*.json"))
            if j["agent"] == "a" and j.get("verify"))
        self.assertEqual(j["verify"], "echo ok")


class TWakeVerify(unittest.TestCase):
    def test_wake_job_carries_verify(self):
        m, td, sd = load()
        rc, d, _, _ = run(m, m.do_wake, "--dir", str(sd), "--json", "wake", "a", "do it",
            "--verify", "echo ok", "--verify-timeout", "700")
        self.assertEqual(rc, 0); j = m._wake_read_job(sd, d["data"]["job"])
        self.assertEqual(j["verify"], "echo ok"); self.assertEqual(j["verify_timeout_s"], 700)

    def test_wake_invalid_verify_timeout(self):
        m, td, sd = load()
        _, _, _, err = run(m, m.do_wake, "--dir", str(sd), "wake", "a", "go",
            "--verify", "echo ok", "--verify-timeout", "-1")
        self.assertIn("invalid --verify-timeout", err)

    def test_worker_runs_verify_and_records(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "turn it", verify="echo verify-payload", verify_timeout_s=700)
        self.assertEqual(worker(m, sd, r["job"]), 0)
        j = m._wake_read_job(sd, r["job"])
        self.assertEqual(j["status"], "done"); self.assertEqual(j["backend_exit"], 0)
        self.assertEqual(j["verify"], {"cmd": "echo verify-payload", "exit": 0, "tail": "verify-payload"})
        self.assertEqual(j["verify_timeout_s"], 700)
        self.assertEqual(m.load_chat_log(sd, "a")[-1]["text"], "[verify] exit=0")
        buf = io.StringIO()
        with redirect_stdout(buf):
            m.do_wakes(m.build().parse_args(["--dir", str(sd), "--json", "wakes"]))
        row = json.loads(buf.getvalue())["data"]["wakes"][0]
        self.assertEqual(row["verify"]["exit"], 0)  # wakes --json exposes verify exit separately

    def test_worker_verify_fail_keeps_backend_exit(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "turn", verify="exit 3")
        self.assertEqual(worker(m, sd, r["job"]), 0)
        j = m._wake_read_job(sd, r["job"])
        self.assertEqual(j["status"], "done"); self.assertEqual(j["backend_exit"], 0)
        self.assertEqual(j["verify"]["exit"], 3)
        self.assertEqual(m.load_chat_log(sd, "a")[-1]["text"], "[verify] exit=3")

    def test_error_turn_skips_verify(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "turn", verify="echo x")
        rows = json.loads((sd / "agents.json").read_text())
        m.save_agents(sd, [x for x in rows if x["name"] != "a"])  # turn cannot run
        self.assertEqual(worker(m, sd, r["job"]), 1)
        j = m._wake_read_job(sd, r["job"])
        self.assertEqual(j["status"], "failed")
        self.assertIsInstance(j.get("verify"), str)  # planned cmd only; never executed
        self.assertFalse(any("[verify]" in x["text"] for x in m.load_chat_log(sd, "a")))


class TCohortVerify(unittest.TestCase):
    def test_summary_includes_verify(self):
        m, td, sd = load()
        r = m.launch_wake_chat(sd, "a", "x", cohort={"parent": "boss", "group": "g9", "quiet_s": 0},
            verify="echo ok")
        self.assertEqual(worker(m, sd, r["job"]), 0)
        g = m._cohort_read(sd, "g9")
        self.assertEqual(g["pending"], []); self.assertEqual(len(g["done"]), 1)
        self.assertTrue(g["done"][0]["summary"].rstrip().endswith("[verify exit=0]"), g["done"])
        pj = [j for j in (json.loads(p.read_text()) for p in (sd / "wake/jobs").glob("*.json"))
            if j["agent"] == "boss"]
        self.assertEqual(len(pj), 1)
        self.assertIn("[verify exit=0]", pj[0]["message"])


class TE2E(unittest.TestCase):
    def test_chat_send_e2e(self):
        td = Path(tempfile.mkdtemp(prefix="agvf-e2e-")); sd = td / "st"
        env = dict(os.environ, AG_AUTO_UPDATE="0", AG_HOME=str(td / "h"), AG_CONFIG_HOME=str(td / "c"),
            AG_CACHE_HOME=str(td / "k"))
        def ag(*a):
            return subprocess.run([sys.executable, AG, "--dir", str(sd), *a], env=env, text=True,
                capture_output=True, timeout=60)
        ag("agents", "add", "a", "--backend", "echo")
        r = ag("--json", "chat", "send", "a", "hello", "--verify", "echo ok")
        d = json.loads(r.stdout)["data"]
        self.assertEqual(d["verify"], {"cmd": "echo ok", "exit": 0, "tail": "ok"}, r.stdout + r.stderr)
        r = ag("--json", "chat", "log", "a")
        self.assertIn("[verify] exit=0", json.loads(r.stdout)["data"]["log"][-1]["text"])


if __name__ == "__main__": unittest.main()
