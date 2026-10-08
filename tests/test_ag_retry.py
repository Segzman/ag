#!/usr/bin/env python3
"""Stale-session recovery + transient retry, end to end with a fake `claude` on PATH.

Fake claude behaviour is driven by env FAKE_MODE and a counter file:
- stale:     --resume given -> stderr 'No conversation found...', exit 1; fresh -> ok
- transient: first call 429 + exit 1; later calls ok
- afteroutput: prints assistant text then 429 + exit 1 (must NOT retry)
Every invocation appends its argv to calls.jsonl.
Run: python3 tests/test_ag_retry.py
"""
import json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]; mode = os.environ["FAKE_MODE"]
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(sys.argv[1:]) + "\n")
n = len(open(os.path.join(d, "calls.jsonl")).read().splitlines())
def ok():
    print(json.dumps({"type": "system", "subtype": "init", "session_id": "new-sid-1"}))
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "FAKE_OK"}]}}))
    print(json.dumps({"type": "result", "subtype": "success", "total_cost_usd": 0}))
    sys.exit(0)
if mode == "stale":
    if "--resume" in sys.argv:
        sys.stderr.write("No conversation found with session ID: " + sys.argv[sys.argv.index("--resume") + 1] + "\n"); sys.exit(1)
    ok()
if mode == "transient":
    if n == 1:
        sys.stderr.write("API Error: 429 rate_limit_error\n"); sys.exit(1)
    ok()
if mode == "afteroutput":
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "PARTIAL"}]}}))
    sys.stderr.write("API Error: 429 rate_limit_error\n"); sys.exit(1)
'''


class RetryE2E(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agretry-"))
        self.state = self.td / ".agent"
        (self.td / "bin").mkdir()
        (self.td / "bin" / "claude").write_text(FAKE)
        (self.td / "bin" / "claude").chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.td / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_DIR=str(self.td))
        self.env.pop("AG_RETRY", None)
        self.ag("agents", "add", "r1", "--backend", "claude", "--role", "sub", "--dir", str(self.td))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a, mode="stale"):
        env = dict(self.env, FAKE_MODE=mode)
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=60, env=env)

    def calls(self):
        p = self.td / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def log(self):
        return [json.loads(l) for l in (self.state / "chats" / "r1.jsonl").read_text().splitlines()]

    def test_bogus_sid_fresh_relaunch_with_history(self):
        chat = self.state / "chats"; chat.mkdir(parents=True, exist_ok=True)
        (chat / "r1.jsonl").write_text(
            json.dumps({"role": "user", "text": "earlier question"}) + "\n" +
            json.dumps({"role": "agent", "text": "earlier answer"}) + "\n")
        (chat / "r1.meta.json").write_text(json.dumps(
            {"backend": "claude", "sid": "stale-sid", "sids": {"claude": "stale-sid"}}))
        p = self.ag("--json", "chat", "send", "r1", "new question", mode="stale")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        c = self.calls()
        self.assertEqual(len(c), 2, c)
        self.assertIn("--resume", c[0]); self.assertNotIn("--resume", c[1])
        self.assertIn("earlier answer", c[1][-1]); self.assertTrue(c[1][-1].endswith("new question"))
        self.assertEqual(c[1][-1].count("new question"), 1)
        rows = self.log()
        self.assertEqual(sum(1 for r in rows if r["role"] == "user" and r["text"] == "new question"), 1)
        self.assertTrue(any(r["role"] == "tool" and r["text"] ==
            "[resume] session stale-sid not found; started fresh with history replay" for r in rows))
        meta = json.loads((chat / "r1.meta.json").read_text())
        self.assertEqual(meta["sids"]["claude"], "new-sid-1")

    def test_transient_once_retried(self):
        p = self.ag("--json", "chat", "send", "r1", "hello", mode="transient")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(len(self.calls()), 2)
        self.assertIn("FAKE_OK", p.stdout)
        self.assertEqual(sum(1 for r in self.log() if r["role"] == "user"), 1)

    def test_failure_after_output_not_retried(self):
        p = self.ag("--json", "chat", "send", "r1", "hello", mode="afteroutput")
        self.assertEqual(len(self.calls()), 1)

    def test_opt_out(self):
        self.env["AG_RETRY"] = "0"
        self.ag("--json", "chat", "send", "r1", "hello", mode="transient")
        self.assertEqual(len(self.calls()), 1)


if __name__ == "__main__":
    unittest.main()
