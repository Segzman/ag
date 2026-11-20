#!/usr/bin/env python3
"""Worktree agents are told where their copy lives (system-prompt line) and get the worktree root first
on PATH when the repo ships a PATH-named executable (./ag). Complements the post-turn escape guard in
test_ag_worktree_escape.py. Run: python3 -m pytest -q tests/test_ag_worktree_guard.py"""
import importlib.machinery, importlib.util, os, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_wtguard_mod", AG); _s = importlib.util.spec_from_loader("ag_wtguard_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


class WtGuard(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()
        self.wt = self.td / "wt"; self.wt.mkdir()
        self.bin = self.td / "bin"; self.bin.mkdir()
        for d in (self.wt, self.bin):
            f = d / "ag"; f.write_text("#!/bin/sh\ntrue\n"); f.chmod(0o755)
        self.row = {"name": "w", "backend": "claude", "dir": str(self.wt), "worktree": {"path": str(self.wt), "repo": "r", "branch": "b"}}

    def test_guard_line_exact(self):
        self.assertEqual(ag.wt_guard_line(self.row),
            f"Your working copy is {self.wt}. Never edit files outside it; run tools from it (e.g. ./ag, not the ag on PATH).")

    def test_path_prepend_only_when_repo_shadows_a_path_command(self):
        self.assertEqual(ag.wt_path_prepend(str(self.wt), str(self.bin)), str(self.wt))
        plain = self.td / "plain"; plain.mkdir(); (plain / "notes.txt").write_text("x")
        self.assertIsNone(ag.wt_path_prepend(str(plain), str(self.bin)))
        self.assertIsNone(ag.wt_path_prepend(str(self.td / "missing"), str(self.bin)))

    def test_turn_prompt_and_env_only_for_worktree_agents(self):
        sd = self.td / ".agent"; sd.mkdir()
        old = dict(os.environ)
        os.environ.update(PATH=str(self.bin) + os.pathsep + old["PATH"], AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
            AG_CACHE_HOME=str(self.td / "k"), AG_AUTO_UPDATE="0", AG_RTK="0", AG_PROBE="0", AG_SECRET_POPUP="0")
        try:
            _p, sys_txt, _n = ag._turn_prompt(sd, self.row, "claude", "", "hi", None, None)
            self.assertIn(ag.wt_guard_line(self.row), sys_txt)
            parts = ag._turn_env(sd, self.row, "claude", "")["PATH"].split(os.pathsep)
            self.assertLess(parts.index(str(self.wt)), parts.index(str(self.bin)))
            plain = {k: v for k, v in self.row.items() if k != "worktree"}
            _p, sys_txt, _n = ag._turn_prompt(sd, plain, "claude", "", "hi", None, None)
            self.assertNotIn("Your working copy", sys_txt or "")
            self.assertNotIn(str(self.wt), ag._turn_env(sd, plain, "claude", "")["PATH"].split(os.pathsep))
        finally:
            os.environ.clear(); os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
