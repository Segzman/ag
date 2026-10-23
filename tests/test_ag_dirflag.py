#!/usr/bin/env python3
"""Item A: global --dir accepted after the subcommand.

Offline: exec of the single-file `ag`, handlers monkeypatched, no subprocess.
Covers: lift helper (drop/keep/rules), end-to-end dispatch through main(),
agents add/set own --dir, tokens after a bare `--`, repeated --dir, bespoke
--dir=V form, dangling --dir still rejected by argparse.

Run: python3 -m unittest tests.test_ag_dirflag
Env: AG_BIN overrides live ag path (default: repo ag).
"""
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("AG_AUTO_UPDATE", "0")

AG = Path(os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag")))
assert AG.exists(), f"missing ag: {AG}"

spec = importlib.util.spec_from_loader("agmod_dirflag", loader=None)
ag = importlib.util.module_from_spec(spec)
ag.__file__ = str(AG)
exec(AG.read_text(), ag.__dict__)


def sub_names():
    chaps = next((s.choices for s in ag.build()._actions if isinstance(s, argparse._SubParsersAction)), {})
    return set(chaps)


class Catch:
    def __init__(self):
        self.a = None

    def __call__(self, argp):
        self.a = argp
        return 7


class Lift(unittest.TestCase):
    def setUp(self):
        self.subs = sub_names()

    def test_bare_after_subcommand(self):
        argv, d = ag._lift_trailing_dir(["agents", "rm", "x", "--dir", "D1"], self.subs)
        self.assertEqual(argv, ["agents", "rm", "x"])
        self.assertEqual(d, "D1")

    def test_eq_form(self):
        argv, d = ag._lift_trailing_dir(["wakes", "--dir=D2"], self.subs)
        self.assertEqual(argv, ["wakes"])
        self.assertEqual(d, "D2")

    def test_before_subcommand_untouched(self):
        argv, d = ag._lift_trailing_dir(["--dir", "D0", "wakes"], self.subs)
        self.assertEqual(argv, ["--dir", "D0", "wakes"])
        self.assertIsNone(d)

    def test_own_dir_skipped(self):
        for subcmd, val in ((["agents", "add", "x", "--dir", "W"], "W"),
                            (["agents", "set", "x", "--dir", "W"], "W")):
            argv, d = ag._lift_trailing_dir(subcmd, self.subs)
            self.assertEqual(argv, subcmd)
            self.assertIsNone(d, val)

    def test_child_args_after_ddash_untouched(self):
        argv, d = ag._lift_trailing_dir(["run", "--", "ls", "--dir", "K"], self.subs)
        self.assertEqual(argv, ["run", "--", "ls", "--dir", "K"])
        self.assertIsNone(d)

    def test_repeated_last_wins(self):
        argv, d = ag._lift_trailing_dir(["wakes", "--dir", "X", "--dir", "Y"], self.subs)
        self.assertEqual(argv, ["wakes"])
        self.assertEqual(d, "Y")

    def test_dangling_dir_left_for_argparse(self):
        argv, d = ag._lift_trailing_dir(["agents", "rm", "x", "--dir"], self.subs)
        self.assertEqual(argv, ["agents", "rm", "x", "--dir"])
        self.assertIsNone(d)

    def test_no_subcommand_untouched(self):
        argv, d = ag._lift_trailing_dir(["--dir", "D"], self.subs)
        self.assertEqual(argv, ["--dir", "D"])
        self.assertIsNone(d)


class Dispatch(unittest.TestCase):
    def _run(self, argv, handler):
        c = Catch()
        orig = getattr(ag, handler, None)
        setattr(ag, handler, c)
        try:
            rc = ag.main(list(argv))
        finally:
            if orig is not None:
                setattr(ag, handler, orig)
        self.assertEqual(rc, 7)
        return c.a

    def test_wakes_dir_after(self):
        a = self._run(["wakes", "--dir", "DA"], "do_wakes")
        self.assertEqual(a.dir, "DA")

    def test_agents_rm_dir_after(self):
        a = self._run(["agents", "rm", "ghost", "--dir", "DB"], "do_agents")
        self.assertEqual(a.dir, "DB")
        self.assertEqual(a.sub, "rm")

    def test_dir_eq_after(self):
        a = self._run(["wakes", "--dir=DC"], "do_wakes")
        self.assertEqual(a.dir, "DC")

    def test_agents_add_keeps_workdir(self):
        a = self._run(["agents", "add", "w2", "--dir", "W"], "do_agents")
        self.assertEqual(a.adir, "W")
        self.assertIsNone(a.dir)

    def test_both_positions(self):
        a = self._run(["--dir", "P", "wakes"], "do_wakes")
        self.assertEqual(a.dir, "P")
        a = self._run(["--dir", "P", "wakes", "--dir", "Q"], "do_wakes")
        self.assertEqual(a.dir, "Q")

    def test_run_child_args_kept(self):
        a = self._run(["run", "--", "ls", "--dir", "K"], "do_run")
        self.assertEqual(a.cmd, ["ls", "--dir", "K"])

    def test_dangling_dir_still_rejected(self):
        c = Catch()
        orig = ag.do_wakes
        ag.do_wakes = c
        try:
            with self.assertRaises(SystemExit):
                ag.main(["agents", "rm", "x", "--dir"])
        finally:
            ag.do_wakes = orig
        self.assertIsNone(c.a)

    def test_json_after_subcommand(self):
        a = self._run(["route", "implement", "--json"], "do_route")
        self.assertTrue(a.json)


class Live(unittest.TestCase):
    """Real CLI subprocess (python3 AG only, no paid backends)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agdf-live-")
        self.state = os.path.join(self.tmp, "state")
        self.env = dict(os.environ, AG_AUTO_UPDATE="0")

    def _run(self, argv):
        return subprocess.run([sys.executable, str(AG), *argv],
                              capture_output=True, text=True, timeout=120,
                              env=self.env, cwd=self.tmp)

    def test_wakes_dir_after(self):
        r = self._run(["wakes", "--dir", str(self.state)])
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_agents_rm_dir_after(self):
        r = self._run(["agents", "rm", "ghost", "--dir", str(self.state)])
        self.assertTrue(r.returncode != 2, r.stderr)
        self.assertNotIn("unrecognized", r.stderr, r.stderr)

    def test_agents_add_dir_is_workdir(self):
        r = self._run(["--dir", str(self.state), "agents", "add", "lw",
                       "--backend", "echo", "--role", "sub", "--dir", str(self.tmp)])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        r2 = self._run(["agents", "list", "--dir", str(self.state)])
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("lw", r2.stdout)


if __name__ == "__main__":
    unittest.main()
