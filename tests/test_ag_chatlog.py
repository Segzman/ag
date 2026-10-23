#!/usr/bin/env python3
"""Tests for `ag chat log` conveniences (item F). Stdlib only, offline.
Run: AG_AUTO_UPDATE=0 python3 tests/test_ag_chatlog.py

Fixture: rows are written straight to <state>/chats/<name>.jsonl (the same
rows append_chat produces); the `chat log` branch only reads the log, so no
roster entry or backend is needed.
"""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

_REPO_AG = str(Path(__file__).resolve().parent.parent / "ag")
AG = Path(os.environ.get("AG_CHATLOG_BIN", _REPO_AG))
assert AG.exists(), f"missing ag binary: {AG}"

def run(state, *args, timeout=20):
    env = dict(os.environ, AG_AUTO_UPDATE="0")
    return subprocess.run([sys.executable, str(AG), "--dir", str(state)] + list(args),
        capture_output=True, text=True, timeout=timeout, env=env)

def run_json(state, *args, timeout=20):
    p = run(state, "--json", *args, timeout=timeout)
    try: obj = json.loads(p.stdout or "{}")
    except Exception: obj = {}
    return p, obj

def seed(state, name, rows):
    d = state / "chats"; d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{name}.jsonl", "w") as f:
        for r in rows: f.write(json.dumps(r) + "\n")

def row(role, text, ts=None):
    return {"ts": ts or "2026-10-22T00:00:00", "role": role, "text": text}

FIXTURE = [
    row("user", "q1"),
    row("tool", "$ echo hi"), row("toolout", "hi"),
    row("agent", "A1 says hi"),
    row("user", "q2"),
    row("tool", "[mode] busy"), row("toolout", "to2"), row("diff", "d"), row("reasoning", "r"),
    row("agent", "A2 final answer"),
    row("tool", "$ tail-after-last-turn"),
]

class ChatLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._t = tempfile.TemporaryDirectory(prefix="ag-chatlog-")
        cls.st = Path(cls._t.name)
        seed(cls.st, "e", FIXTURE)

    @classmethod
    def tearDownClass(cls):
        cls._t.cleanup()

    def test_default_json_rows(self):
        _, o = run_json(self.st, "chat", "log", "e")
        log = o["data"]["log"]
        self.assertEqual(log[-1]["text"], "$ tail-after-last-turn")
        self.assertEqual(len(log), 11)  # fixture rows; well under the default 20 window

    def test_tail(self):
        _, o = run_json(self.st, "chat", "log", "e", "--tail", "2")
        log = o["data"]["log"]
        self.assertEqual([r["text"] for r in log], ["A2 final answer", "$ tail-after-last-turn"])
    def test_tail_beats_limit(self):
        _, o = run_json(self.st, "chat", "log", "e", "--tail", "2", "--limit", "1")
        self.assertEqual(len(o["data"]["log"]), 2)
    def test_tail_zero(self):
        _, o = run_json(self.st, "chat", "log", "e", "--tail", "0")
        self.assertEqual(o["data"]["log"], [])
        self.assertEqual(o["ok"], True)
    def test_tail_negative_error(self):
        p, o = run_json(self.st, "chat", "log", "e", "--tail", "-1")
        self.assertEqual(p.returncode, 1)
        self.assertEqual(o["ok"], False)
        self.assertIn("--tail", o.get("error", ""))

    def test_no_tools(self):
        _, o = run_json(self.st, "chat", "log", "e", "--no-tools", "--json")
        roles = [r["role"] for r in o["data"]["log"]]
        self.assertNotIn("tool", roles); self.assertNotIn("toolout", roles)
        self.assertNotIn("diff", roles); self.assertNotIn("reasoning", roles)
        self.assertIn("agent", roles); self.assertIn("user", roles)

    def test_last_raw_text(self):
        p = run(self.st, "chat", "log", "e", "--last")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "A2 final answer")  # raw, no "agent:" prefix
    def test_last_json_row(self):
        _, o = run_json(self.st, "chat", "log", "e", "--last", "--json")
        log = o["data"]["log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["role"], "agent")
        self.assertEqual(log[0]["text"], "A2 final answer")
    def test_last_hides_trailing_tool_rows(self):
        # tool rows appended after the last agent reply are not the agent reply
        _, o = run_json(self.st, "chat", "log", "e", "--last", "--json")
        self.assertEqual(o["data"]["log"][-1]["role"], "agent")
    def test_last_prefers_final_assistant_message(self):
        # streaming backends (opencode): assistant rows per message, then an 'agent' whole-turn summary
        seed(self.st, "oc", [row("user", "old task"), row("assistant", "old final"), row("agent", "old summary"),
                             row("user", "new task"), row("assistant", "Reading spec."), row("tool", "read x"),
                             row("assistant", "Done: final report"), row("agent", "Reading spec.\nDone: final report")])
        p = run(self.st, "chat", "log", "oc", "--last")
        self.assertEqual(p.stdout.strip(), "Done: final report")
    def test_last_no_agent(self):
        seed(self.st, "empty", [row("user", "only a question")])
        p = run(self.st, "chat", "log", "empty", "--last")
        self.assertEqual(p.returncode, 0)
        self.assertEqual(p.stdout.strip(), "(none)")
    def test_last_overrides_tail(self):
        _, o = run_json(self.st, "chat", "log", "e", "--last", "--tail", "1", "--json")
        self.assertEqual(o["data"]["log"][0]["text"], "A2 final answer")

    def test_text_format_unchanged(self):
        p = run(self.st, "chat", "log", "e", "--tail", "1")
        self.assertEqual(p.stdout.strip(), "tool: $ tail-after-last-turn")

if __name__ == "__main__":
    unittest.main(verbosity=1)
