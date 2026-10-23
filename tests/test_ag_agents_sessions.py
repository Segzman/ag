"""Agent removal, re-add and reset must not resume an old backend session."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")


class AgentSessions(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-agent-sessions-"))
        self.state = self.td / "state"
        home = self.td / "home"
        for p in (self.state, self.state / "chats", home, home / "cfg", home / "cache"):
            p.mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ, AG_HOME=str(home), AG_CONFIG_HOME=str(home / "cfg"),
            AG_CACHE_HOME=str(home / "cache"), AG_AUTO_UPDATE="0")
        self.addCleanup(__import__("shutil").rmtree, self.td, True)

    def run_ag(self, *args, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), "--json", *args],
            capture_output=True, text=True, env=self.env, timeout=30)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def add_agent(self, name="w"):
        self.run_ag("agents", "add", name, "--backend", "echo")

    def meta_path(self, name="w"):
        return self.state / "chats" / f"{name}.meta.json"

    def seed_session(self, name="w"):
        p = self.meta_path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        d = json.loads(p.read_text()) if p.exists() else {"backend": "opencode", "model": "old", "dir": ".", "role": "sub"}
        d.update(backend="opencode", sid="stale-single", sids={"opencode": "stale-map", "claude": "other"})
        p.write_text(json.dumps(d))

    def session_ids(self, name="w"):
        d = json.loads(self.meta_path(name).read_text())
        return d["sid"], d["sids"]

    def test_rm_reset_and_add_clear_sessions_but_keep_chat_log(self):
        self.add_agent()
        log = self.state / "chats" / "w.jsonl"
        log.write_text('{"role":"agent","text":"keep me"}\n')

        self.seed_session()
        p = self.run_ag("agents", "reset", "w")
        self.assertTrue(json.loads(p.stdout)["ok"])
        self.assertEqual(self.session_ids(), ("", {}))
        self.assertIn("keep me", log.read_text())
        self.assertIn("w", [r["name"] for r in json.loads((self.state / "agents.json").read_text())])

        self.seed_session()
        self.run_ag("agents", "rm", "w")
        self.assertEqual(self.session_ids(), ("", {}))
        self.assertIn("keep me", log.read_text())

        # A stale metadata file can survive outside the normal rm flow too.
        self.seed_session()
        self.add_agent()
        self.assertEqual(self.session_ids(), ("", {}))
        self.assertIn("keep me", log.read_text())

    def test_rm_purge_removes_chat_log_and_metadata(self):
        self.add_agent()
        log = self.state / "chats" / "w.jsonl"
        log.write_text('{"role":"agent","text":"remove me"}\n')
        self.seed_session()
        self.run_ag("agents", "rm", "w", "--purge")
        self.assertFalse(log.exists())
        self.assertFalse(self.meta_path().exists())


if __name__ == "__main__":
    unittest.main()
