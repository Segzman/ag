#!/usr/bin/env python3
"""Acceptance: --escalate ladder on chat send + --allow-cmd for claude (spec 2026-10-30-escalate.md). Offline, echo backend."""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
AG = str(Path(__file__).resolve().parent.parent / "ag")
ENV = dict(os.environ, AG_AUTO_UPDATE="0", AG_PROBE="0")

def load():
    l = importlib.machinery.SourceFileLoader("ag_escalate_t", AG)
    s = importlib.util.spec_from_loader("ag_escalate_t", l)
    m = importlib.util.module_from_spec(s); l.exec_module(m)
    return m


class Setup(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-esc-t-"))
        self.state, self.work = self.td / "st", self.td / "work"; self.work.mkdir()
        self.counter = self.td / "n"
        self.ag("agents", "add", "e", "--backend", "echo", "--model", "m1", "--dir", str(self.work))

    def ag(self, *a, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), *a], capture_output=True, text=True, env=ENV, timeout=120)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def send(self, *a, ok=True):
        p = self.ag("--json", "chat", "send", "e", "fix the thing", *a, ok=ok)
        return p, (json.loads(p.stdout) if p.stdout.strip().startswith("{") else {})

    def pass_on(self, n):
        # verify passes on the n-th run; prints FAILMARK<k> on failing runs
        c = self.counter
        return f'k=$(( $(cat {c} 2>/dev/null || echo 0) + 1 )); echo $k > {c}; [ $k -ge {n} ] && exit 0; echo FAILMARK$k; exit 1'


class Escalate(Setup):
    def test_escalates_until_verify_passes(self):
        p, d = self.send("--verify", self.pass_on(3), "--escalate", "m2,m3")
        data = d["data"]
        self.assertEqual([x["model"] for x in data["escalation"]], ["m1", "m2", "m3"])
        self.assertEqual([x["verify_exit"] for x in data["escalation"]], [1, 1, 0])
        self.assertEqual(data["verify"]["exit"], 0)
        self.assertIn("FAILMARK2", data["reply"])           # last attempt saw the previous failure output
        self.assertIn("fix the thing", data["reply"])       # and the original task

    def test_stops_early_when_first_passes(self):
        p, d = self.send("--verify", "true", "--escalate", "m2,m3")
        self.assertEqual([x["model"] for x in d["data"]["escalation"]], ["m1"])

    def test_all_fail_reports_every_attempt(self):
        p, d = self.send("--verify", "echo nope; exit 2", "--escalate", "m2")
        data = d["data"]
        self.assertEqual([(x["model"], x["verify_exit"]) for x in data["escalation"]], [("m1", 2), ("m2", 2)])
        self.assertEqual(data["verify"]["exit"], 2)

    def test_roster_model_unchanged(self):
        self.send("--verify", self.pass_on(2), "--escalate", "m2")
        rows = json.loads(self.ag("--json", "agents", "list").stdout)["data"]
        rows = rows.get("agents", rows) if isinstance(rows, dict) else rows
        self.assertEqual(next(r for r in rows if r["name"] == "e")["model"], "m1")

    def test_requires_verify(self):
        p, d = self.send("--escalate", "m2", ok=False)
        self.assertIn("--escalate needs --verify", p.stdout + p.stderr)

    def test_no_escalate_no_key(self):
        p, d = self.send("--verify", "true")
        self.assertNotIn("escalation", d["data"])

    def test_log_rows(self):
        self.send("--verify", self.pass_on(2), "--escalate", "m2")
        log = self.ag("chat", "log", "e").stdout
        self.assertIn("[escalate] -> m2", log)


class WakeEscalate(Setup):
    """Same ladder in the background wake worker; fake spawn, worker body run in-process."""
    def setUp(self):
        super().setUp()
        self.m = m = load()
        m._spawn_wake_session = lambda sd, agent, jid, sid=None, pre_fork=None: (pre_fork and pre_fork(sid or "s", None)) or sid or "s"

    def wake(self, verify, esc):
        r = self.m.launch_wake_chat(self.state, "e", "fix the thing", verify=verify, escalate=esc)
        self.assertEqual(self.m._wake_read_job(self.state, r["job"]).get("escalate"), esc)
        self.m.do__wake_run(self.m.build().parse_args(["--dir", str(self.state), "_wake_run", r["job"]]))
        return self.m._wake_read_job(self.state, r["job"])

    def test_escalates_until_verify_passes(self):
        j = self.wake(self.pass_on(3), ["m2", "m3"])
        self.assertEqual([(x["model"], x["verify_exit"]) for x in j["escalation"]], [("m1", 1), ("m2", 1), ("m3", 0)])
        self.assertEqual(j["verify"]["exit"], 0); self.assertEqual(j["status"], "done")
        rows = self.m.load_chat_log(self.state, "e"); log = [x["text"] for x in rows]
        last = [x["text"] for x in rows if x.get("role") == "user"][-1]  # job reply is truncated; the prompt is in the log
        self.assertIn("FAILMARK2", last); self.assertIn("fix the thing", last)
        self.assertEqual([t for t in log if t.startswith("[escalate]")], ["[escalate] -> m2 (verify exit 1)", "[escalate] -> m3 (verify exit 1)"])
        rows = json.loads(self.ag("--json", "agents", "list").stdout)["data"]
        rows = rows.get("agents", rows) if isinstance(rows, dict) else rows
        self.assertEqual(next(r for r in rows if r["name"] == "e")["model"], "m1")

    def test_stops_early_and_all_fail(self):
        self.assertEqual([x["model"] for x in self.wake("true", ["m2"])["escalation"]], ["m1"])
        j = self.wake("echo nope; exit 2", ["m2"])
        self.assertEqual([(x["model"], x["verify_exit"]) for x in j["escalation"]], [("m1", 2), ("m2", 2)])
        self.assertEqual(j["verify"]["exit"], 2)

    def test_no_escalate_unchanged(self):
        r = self.m.launch_wake_chat(self.state, "e", "x", verify="true")
        self.assertNotIn("escalate", self.m._wake_read_job(self.state, r["job"]))

    def test_cli_requires_verify(self):
        p = self.ag("wake", "e", "go", "--escalate", "m2", ok=False)
        self.assertIn("--escalate needs --verify", p.stdout + p.stderr)


class AllowCmdClaude(unittest.TestCase):
    def test_edits_mode_adds_allowed_tools(self):
        m = load()
        argv = m.backend_argv("claude", prompt="hi", mode="edits", allow_cmd=["python3 -m unittest", "git diff"])
        i = argv.index("--allowedTools")
        self.assertEqual(argv[i + 1:i + 3], ["Bash(python3 -m unittest*)", "Bash(git diff*)"])
        self.assertEqual(argv[-1], "hi")

    def test_no_patterns_or_other_modes(self):
        m = load()
        self.assertNotIn("--allowedTools", m.backend_argv("claude", prompt="hi", mode="edits"))
        self.assertNotIn("--allowedTools", m.backend_argv("claude", prompt="hi", mode="ro", allow_cmd=["x"]))

    def test_existing_argv_unchanged(self):
        m = load()
        self.assertEqual(m.backend_argv("claude", model="sonnet", workdir=".", sid="s1", prompt="hi"),
            ["claude", "-p", "--verbose", "--output-format", "stream-json", "--include-partial-messages", "--model", "sonnet", "--resume", "s1", "hi"])


if __name__ == "__main__":
    unittest.main()
