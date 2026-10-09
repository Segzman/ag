#!/usr/bin/env python3
"""`ag setup` per-backend
config (routing.json "backends"): --set backend.*, agents add defaults, ag route warnings, curses Backends rows.
Same isolation as test_ag_setup (temp AG_HOME etc.). No real modal window is ever shown.
Run: python3 tests/test_ag_setup_form.py
"""
import json, os, shutil, subprocess, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ag_setup as base  # noqa: E402
import test_ag_setup_ui as ui  # noqa: E402

AG = base.AG
UP, DOWN, LEFT, RIGHT, ENTER = "\x1bOA", "\x1bOB", "\x1bOD", "\x1bOC", "\r"


class SetupForm(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj
    pty_run = ui.SetupUIs.pty_run

    def routing(self): return json.loads((self.cfg/"routing.json").read_text())

    # ---- --set backend.* ----
    def test_set_backend_flags(self):
        o = self.agj("setup", "--yes", "--harness", "none", "--set", "backend.opencode.model=anthropic/c",
            "--set", "backend.codex.mode=edits", "--set", "backend.gemini.enabled=false", "--set", "backend.claude.plan=true")
        self.assertEqual(o["data"]["backends"]["codex"]["mode"], "edits")
        self.assertEqual(self.routing()["backends"], {"claude": {"plan": True}, "opencode": {"model": "anthropic/c"},
            "codex": {"mode": "edits"}, "gemini": {"enabled": False}})
        self.assertIn("bad --set", self.agj("setup", "--yes", "--set", "backend.gemini.mode=auto", ok=False)["error"])
        self.assertIn("is disabled", self.agj("setup", "--yes", "--harness", "none", "--set", "backend.opencode.enabled=0", ok=False)["error"])
        self.agj("setup", "--yes", "--harness", "none", "--scope", "project", "--set", "backend.gemini.enabled=true")
        self.assertEqual(json.loads((self.proj/".agent"/"routing.json").read_text())["backends"], {"gemini": {"enabled": True}})

    # ---- effects ----
    def write_routing(self, doc):
        self.cfg.mkdir(parents=True, exist_ok=True); (self.cfg/"routing.json").write_text(json.dumps(doc))

    def agents(self): return {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}

    def test_agents_add_uses_backend_defaults(self):
        self.write_routing({"backends": {"codex": {"model": "gpt-x", "mode": "edits", "plan": True}, "gemini": {"enabled": False}},
            "jobs": {"debug": {"ag": {"backend": "codex"}}}})
        self.agj("agents", "add", "a", "--backend", "codex")
        self.agj("agents", "add", "b", "--backend", "codex", "--model", "gpt-y", "--mode", "ro")
        self.agj("agents", "add", "c", "--job", "debug")
        w = self.agj("agents", "add", "d", "--backend", "gemini")["data"]
        self.agj("agents", "add", "e", "--backend", "claude")
        r = self.agents()
        self.assertEqual((r["a"]["model"], r["a"]["mode"], r["a"].get("plan")), ("gpt-x", "edits", True))
        self.assertEqual((r["b"]["model"], r["b"]["mode"]), ("gpt-y", "ro"))
        self.assertEqual((r["c"]["backend"], r["c"]["model"], r["c"]["mode"]), ("codex", "gpt-x", "edits"))
        self.assertIn("disabled", w["warning"])
        self.assertEqual((r["e"]["model"], r["e"].get("mode")), ("default", None))

    def test_route_flags_disabled_backend(self):
        self.write_routing({"backends": {"gemini": {"enabled": False}}, "jobs": {"plan": {"ag": {"backend": "gemini"}}}})
        d = self.agj("route")["data"]
        self.assertEqual(d["backends"]["gemini"]["enabled"], False); self.assertEqual(d["backends"]["gemini"]["source"], "global")
        self.assertIn("plan: routed to disabled backend gemini", d["warnings"][0])
        t = self.ag("route").stdout
        self.assertIn("! backend disabled", t); self.assertIn("backend gemini    disabled (global)", t)

    # ---- curses Backends section ----
    def test_cli_backends_section(self):
        keys = [UP, UP, LEFT, ENTER,                          # gemini: On -> off
            UP, RIGHT, RIGHT, ENTER, "edits", ENTER,          # codex: mode edits
            UP, LEFT, ENTER, "anth", ENTER,                   # opencode: default model
            "s", "y"]
        code, out = self.pty_run(["--ui", "cli", "--harness", "none"], keys)
        self.assertEqual(code, 0, out[-1500:]); self.assertIn("Default model", out)
        self.assertEqual(self.routing()["backends"], {"opencode": {"model": "anthropic/c"}, "codex": {"mode": "edits"},
            "gemini": {"enabled": False}})

    def test_cli_disabled_backend_blocks_save(self):
        code, out = self.pty_run(["--ui", "cli", "--harness", "none"], [UP, UP, UP, UP, LEFT, ENTER, "s", "x", "q", "y"])
        self.assertEqual(code, 0, out[-1500:]); self.assertIn("Can't save yet", out); self.assertIn("cancelled", out)
        self.assertFalse((self.cfg/"routing.json").exists())


if __name__ == "__main__":
    unittest.main()
