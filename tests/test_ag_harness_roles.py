#!/usr/bin/env python3
"""Harness selection and editable role presets; isolated state, no remote calls."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

AG = Path(os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag")))
spec = importlib.util.spec_from_loader("agmod", loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)


class HarnessRolesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ag-harness-roles-")
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        ag.save_agents(self.state, [{"name": "worker", "backend": "claude",
            "model": "custom-claude", "dir": ".", "role": "sub",
            "persona": "reviewer", "system": "", "profile": "shared"}])
        ag.save_chat_meta(self.state, "worker", {"backend": "claude",
            "sid": "claude-session", "sids": {"codex": "codex-session"}})

    def cli(self, *args, ok=True):
        p = subprocess.run([sys.executable, str(AG), "--dir", str(self.state),
            "--json", *args], text=True, capture_output=True, timeout=15)
        self.assertTrue(p.stdout.strip().startswith("{"), (p.stdout, p.stderr))
        data = json.loads(p.stdout)
        self.assertEqual(data["ok"], ok, data)
        self.assertEqual(p.returncode == 0, ok, (p.stdout, p.stderr))
        return data.get("data") if ok else data

    def test_cli_switch_and_current_are_structured(self):
        result = self.cli("harness", "use", "worker", "codex", "--model", "custom-codex")
        current = self.cli("harness", "current", "worker")
        for data in (result, current):
            self.assertEqual(data["agent"], "worker")
            self.assertEqual(data["backend"], "codex")
            self.assertEqual(data["model"], "custom-codex")
            self.assertEqual(data["sid"], "codex-session")
            self.assertEqual(data["persona"], "reviewer")
            self.assertEqual(data["profile"], "shared")
        self.cli("harness", "current", "missing", ok=False)
        self.cli("harness", "use", "missing", "codex", ok=False)

    def test_both_switch_interfaces_keep_models_backend_specific(self):
        self.cli("agents", "set", "worker", "--backend", "opencode")
        row = ag.load_agents(self.state)[0]
        self.assertEqual(row["model"], ag.OPENCODE_MODEL_DEFAULT)
        self.cli("agents", "set", "worker", "--model", "provider/custom-oc")
        self.cli("harness", "use", "worker", "codex")
        self.assertEqual(ag.load_agents(self.state)[0]["model"], "default")
        self.cli("harness", "use", "worker", "claude")
        self.assertEqual(ag.load_agents(self.state)[0]["model"], "custom-claude")
        self.assertEqual(ag.load_chat_meta(self.state, "worker")["sid"], "claude-session")
        self.cli("agents", "set", "worker", "--backend", "opencode")
        self.assertEqual(ag.load_agents(self.state)[0]["model"], "provider/custom-oc")
        self.assertEqual(ag.load_chat_meta(self.state, "worker")["sid"], "")

    def test_explicit_model_wins_and_same_backend_updates(self):
        self.cli("harness", "use", "worker", "codex", "--model", "first-codex")
        self.cli("harness", "use", "worker", "codex", "--model", "second-codex")
        self.cli("agents", "set", "worker", "--backend", "claude", "--model", "explicit-claude")
        self.cli("harness", "use", "worker", "codex")
        self.assertEqual(ag.load_agents(self.state)[0]["model"], "second-codex")
        self.cli("harness", "use", "worker", "opencode", "--model", "default")
        self.assertEqual(ag.load_agents(self.state)[0]["model"], ag.OPENCODE_MODEL_DEFAULT)

    def test_foreign_meta_sid_never_becomes_current_backends_sid(self):
        ag.save_chat_meta(self.state, "worker", {"backend": "opencode",
            "sid": "foreign-session", "sids": {"claude": "claude-session"}})
        self.cli("agents", "set", "worker", "--backend", "codex")
        self.cli("agents", "set", "worker", "--backend", "claude")
        meta = ag.load_chat_meta(self.state, "worker")
        self.assertEqual(meta["sid"], "claude-session")
        self.assertEqual(meta["sids"]["opencode"], "foreign-session")

    def test_busy_rejects_both_interfaces_without_partial_changes(self):
        lock = ag._acquire_agent_turn(self.state, "worker")
        try:
            for args in (("harness", "use", "worker", "codex"),
                         ("agents", "set", "worker", "--backend", "codex", "--persona", "planner")):
                err = self.cli(*args, ok=False)
                self.assertIn("busy", err["error"])
                row = ag.load_agents(self.state)[0]
                self.assertEqual(row["backend"], "claude")
                self.assertEqual(row["persona"], "reviewer")
        finally:
            ag._release_agent_turn(lock)
        self.cli("harness", "use", "worker", "codex")

    def test_guard_oserror_is_clean_error_not_traceback(self):
        orig = ag._acquire_agent_turn
        def boom(sdir, name, blocking=True):
            raise PermissionError(13, "Permission denied")
        ag._acquire_agent_turn = boom
        try:
            ok, msg = ag.update_agent_settings(self.state, "worker", {"backend": "opencode"})
        finally:
            ag._acquire_agent_turn = orig
        self.assertFalse(ok)
        self.assertNotIn("Traceback", msg)
        self.assertIn("turn guard", msg)
        self.assertEqual(ag.load_agents(self.state)[0]["backend"], "claude")

    def test_invalid_persona_does_not_partially_switch(self):
        self.cli("agents", "set", "worker", "--backend", "codex",
            "--persona", "missing", ok=False)
        self.assertEqual(ag.load_agents(self.state)[0]["backend"], "claude")
        self.assertEqual(ag.load_chat_meta(self.state, "worker")["sid"], "claude-session")

    def test_builtin_roles_can_be_customized_and_reset_individually(self):
        defaults = {r["name"]: r["system"] for r in ag.DEFAULT_ROLES}
        self.cli("roles", "set", "planner", "--system", "Plan only this project.")
        self.cli("roles", "set", "reviewer", "--system", "Find correctness bugs.")
        role = self.cli("roles", "show", "reviewer")["role"]
        self.assertEqual(role["name"], "reviewer")
        self.assertEqual(role["system"], "Find correctness bugs.")
        self.assertTrue(role["builtin"])
        self.assertTrue(role["customized"])
        self.cli("roles", "reset", "reviewer")
        self.assertEqual(self.cli("roles", "show", "reviewer")["role"]["system"], defaults["reviewer"])
        self.assertEqual(self.cli("roles", "show", "planner")["role"]["system"], "Plan only this project.")
        self.cli("roles", "rm", "implementer")
        self.cli("roles", "reset", "implementer")
        self.assertEqual(self.cli("roles", "show", "implementer")["role"]["system"], defaults["implementer"])
        self.cli("roles", "add", "local", "--system", "Project-specific work.")
        before = (self.state / "roles.json").read_text()
        self.cli("roles", "reset", "local", ok=False)
        self.cli("roles", "reset", "unknown", ok=False)
        self.assertEqual((self.state / "roles.json").read_text(), before)


if __name__ == "__main__":
    unittest.main()
