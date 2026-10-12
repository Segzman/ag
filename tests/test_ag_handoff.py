#!/usr/bin/env python3
"""Cross-backend handoff with a token budget, end to end with fake backends.

harness use A B --budget N (or agents set A --backend B) stores
pending_handoff {from, budget} on the agent; the next turn replays
newest-first user/agent rows until budget (tokens ~ chars/4),
oldest-dropped, prefixed [handoff from <old>]; consumed after the first
successful turn, kept on failure. AG_HANDOFF_BUDGET overrides the default.
Run: python3 tests/test_ag_handoff.py
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(["claude"] + sys.argv[1:]) + "\n")
if os.environ.get("FAKE_MODE") == "fail":
    sys.stderr.write("boom\n"); sys.exit(1)
print(json.dumps({"type": "system", "subtype": "init", "session_id": "claude-sid-1"}))
print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "CLAUDE_OK"}]}}))
print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0}))
'''

FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(["codex"] + sys.argv[1:]) + "\n")
if os.environ.get("FAKE_MODE") == "fail":
    sys.stderr.write("boom\n"); sys.exit(1)
print(json.dumps({"type": "thread.started", "thread_id": "codex-t-1"}))
print(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "CODEX_OK"}}))
print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}))
'''


class HandoffE2E(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aghandoff-"))
        self.state = self.td / ".agent"
        (self.td / "bin").mkdir()
        (self.td / "bin" / "claude").write_text(FAKE_CLAUDE)
        (self.td / "bin" / "claude").chmod(0o755)
        (self.td / "bin" / "codex").write_text(FAKE_CODEX)
        (self.td / "bin" / "codex").chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.td / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_DIR=str(self.td), FAKE_MODE="ok", AG_PROBE="0", AG_AUTO_UPDATE="0",
                        AG_RTK="0", AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
                        AG_CACHE_HOME=str(self.td / "k"))
        self.env.pop("AG_HANDOFF_BUDGET", None)
        self.ag("agents", "add", "w", "--backend", "claude", "--role", "sub", "--dir", str(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a, mode="ok", extra=None):
        env = dict(self.env, FAKE_MODE=mode)
        if extra: env.update(extra)
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=120, env=env)

    def agents(self):
        p = self.ag("--json", "agents", "list")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        return {r["name"]: r for r in json.loads(p.stdout)["data"]["agents"]}

    def calls(self):
        p = self.td / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def seed_log(self, rows):
        chat = self.state / "chats"; chat.mkdir(parents=True, exist_ok=True)
        (chat / "w.jsonl").write_text("".join(json.dumps({"role": r, "text": t}) + "\n" for r, t in rows))

    def test_use_budget_stores_pending(self):
        p = self.ag("harness", "use", "w", "codex", "--budget", "100")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(self.agents()["w"]["pending_handoff"], {"from": "claude", "budget": 100})

    def test_default_and_env_budget(self):
        self.ag("harness", "use", "w", "codex")
        self.assertEqual(self.agents()["w"]["pending_handoff"], {"from": "claude", "budget": 4000})
        self.ag("harness", "use", "w", "claude")
        p = self.ag("harness", "use", "w", "codex", extra={"AG_HANDOFF_BUDGET": "250"})
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(self.agents()["w"]["pending_handoff"], {"from": "claude", "budget": 250})

    def test_bad_budget_rejected(self):
        for bad in ("0", "-5", "junk"):
            p = self.ag("harness", "use", "w", "codex", "--budget", bad)
            self.assertNotEqual(p.returncode, 0, bad)
            self.assertIn("bad --budget", (p.stdout + p.stderr), bad)
        self.assertEqual(self.agents()["w"]["backend"], "claude")
        self.assertNotIn("pending_handoff", self.agents()["w"])

    def test_same_backend_no_pending(self):
        p = self.ag("harness", "use", "w", "claude")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertNotIn("pending_handoff", self.agents()["w"])

    def test_agents_set_backend_sets_pending(self):
        p = self.ag("agents", "set", "w", "--backend", "codex")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(self.agents()["w"]["pending_handoff"], {"from": "claude", "budget": 4000})

    def test_replay_bounded_prefixed_consumed(self):
        self.seed_log([("user", "OLDQ " + "x" * 200), ("agent", "OLDA " + "y" * 200),
                       ("user", "new question"), ("agent", "new answer")])
        p = self.ag("harness", "use", "w", "codex", "--budget", "60")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        p = self.ag("chat", "send", "w", "next task")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("CODEX_OK", p.stdout)
        prompt = " ".join(" ".join(c) for c in self.calls())
        self.assertIn("[handoff from claude]", prompt)
        self.assertIn("new question", prompt)
        self.assertIn("next task", prompt)
        self.assertNotIn("OLDQ", prompt)  # oldest-dropped under budget
        self.assertNotIn("pending_handoff", self.agents()["w"])  # consumed

    def test_failure_keeps_pending(self):
        self.seed_log([("user", "q"), ("agent", "a")])
        self.ag("harness", "use", "w", "codex", "--budget", "60")
        p = self.ag("chat", "send", "w", "next task", mode="fail")
        self.assertIn("[exit=1]", p.stdout, (p.stdout, p.stderr))
        self.assertEqual(self.agents()["w"]["pending_handoff"], {"from": "claude", "budget": 60})


if __name__ == "__main__":
    unittest.main()
