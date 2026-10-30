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
