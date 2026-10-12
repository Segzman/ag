#!/usr/bin/env python3
"""Dispatch mode ceiling (AG_PARENT_AGENT/AG_PARENT_MODE set by _turn_env). Run: python3 tests/test_ag_mode_ceiling.py"""
import importlib.machinery, importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

FAKE = '''#!/usr/bin/env python3
import json, os, sys
open(os.environ["SEAMS_LOG"], "a").write(json.dumps({"argv": sys.argv[1:],
    "env": {k: os.environ.get(k, "") for k in ("AG_PARENT_AGENT", "AG_PARENT_MODE")}}) + "\\n")
print(json.dumps({"text": "ok"}))
'''

class Ceiling(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); self.addCleanup(shutil.rmtree, self.td, True)
        td = self.td
        self.bin = Path(td, "bin"); self.bin.mkdir()
        f = self.bin / "claude"; f.write_text(FAKE); f.chmod(0o755)
        self.log = Path(td, "log"); self.sd = Path(td, ".agent"); self.sd.mkdir()
        self.old = dict(os.environ)
        os.environ.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", SEAMS_LOG=str(self.log),
            AG_AUTO_UPDATE="0", AG_HOME=td + "/h", AG_CONFIG_HOME=td + "/c", AG_CACHE_HOME=td + "/k",
            AG_RTK="0", AG_PROBE="0", AG_SECRET_POPUP="0")
        self.env = dict(os.environ)
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)
    def cli(self, *a, env=None):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a],
            capture_output=True, text=True, env=env or self.env)
        return p.returncode, p.stdout + p.stderr
    def ceil_env(self, mode="edits", off=False):
        e = dict(self.env, AG_PARENT_AGENT="p", AG_PARENT_MODE=mode)
        if off: e["AG_MODE_CEILING"] = "off"
        return e

    def test_rank_order(self):
        self.assertEqual([ag._mode_rank(m) for m in ag.MODES], [0, 1, 2, 3])
        self.assertEqual(ag._mode_rank("", True), 0)  # plan shares the floor with ro
        self.assertEqual(ag._mode_rank("plan"), 0)
        self.assertEqual(ag._mode_rank(""), 1)  # unset = backend default = edits

    def test_ceiling_env(self):
        self.assertIsNone(ag._mode_ceiling())
        self.assertEqual(ag._mode_ceiling_err("full"), "")
        os.environ.update(AG_PARENT_AGENT="p", AG_PARENT_MODE="ro")
        try:
            self.assertEqual(ag._mode_ceiling(), ("p", "ro"))
            self.assertEqual(ag._mode_ceiling_err("full"), "mode full exceeds parent ceiling ro")
            self.assertEqual(ag._mode_ceiling_err("ro"), "")
            os.environ.pop("AG_PARENT_MODE", None)  # unset parent mode = edits
            self.assertEqual(ag._mode_ceiling(), ("p", "edits"))
            self.assertEqual(ag._mode_ceiling_err("auto"), "mode auto exceeds parent ceiling edits")
            self.assertEqual(ag._mode_ceiling_err("edits"), "")
            os.environ["AG_PARENT_MODE"] = "plan"
            self.assertEqual(ag._mode_ceiling_err("ro"), "")  # plan/ro share the floor
            self.assertEqual(ag._mode_ceiling_err("edits"), "mode edits exceeds parent ceiling plan")
            os.environ["AG_MODE_CEILING"] = "off"
            self.assertIsNone(ag._mode_ceiling())
            self.assertEqual(ag._mode_ceiling_err("full"), "")
        finally:
            for k in ("AG_PARENT_AGENT", "AG_PARENT_MODE", "AG_MODE_CEILING"):
                if k in self.old: os.environ[k] = self.old[k]
                else: os.environ.pop(k, None)

    def test_add_reject_and_allow(self):
        e = self.ceil_env("ro")
        rc, out = self.cli("agents", "add", "c1", "--backend", "echo", "--mode", "full", env=e)
        self.assertNotEqual(rc, 0); self.assertIn("mode full exceeds parent ceiling ro", out)
        self.assertNotIn("c1", self.cli("agents", "list", env=e)[1])  # refused, not created
        self.assertEqual(self.cli("agents", "add", "c1", "--backend", "echo", "--mode", "ro", env=e)[0], 0)
        self.assertEqual(self.cli("agents", "add", "c2", "--backend", "echo", "--mode", "full", "--plan", env=e)[0], 0)  # plan never exceeds
        e2 = dict(self.env, AG_PARENT_AGENT="p")  # unset parent mode = edits
        rc, out = self.cli("agents", "add", "c3", "--backend", "echo", "--mode", "auto", env=e2)
        self.assertNotEqual(rc, 0); self.assertIn("mode auto exceeds parent ceiling edits", out)
        self.assertEqual(self.cli("agents", "add", "c3", "--backend", "echo", "--mode", "edits", env=e2)[0], 0)
        self.assertEqual(self.cli("agents", "add", "c4", "--backend", "echo", "--mode", "full",
            env=self.ceil_env("ro", off=True))[0], 0)  # off disables

    def test_set_reject(self):
        self.assertEqual(self.cli("agents", "add", "s1", "--backend", "echo", "--mode", "edits")[0], 0)
        e = self.ceil_env("ro")
        rc, out = self.cli("agents", "set", "s1", "--mode", "full", env=e)
        self.assertNotEqual(rc, 0); self.assertIn("mode full exceeds parent ceiling ro", out)
        cur = {x["name"]: x for x in json.loads(self.cli("--json", "agents", "list", env=e)[1])["data"]["agents"]}["s1"]
        self.assertEqual(cur.get("mode"), "edits")  # refused, unchanged
        self.assertEqual(self.cli("agents", "set", "s1", "--mode", "ro", env=e)[0], 0)
        self.assertEqual(self.cli("agents", "add", "s2", "--backend", "echo", "--mode", "full", "--plan")[0], 0)
        rc, out = self.cli("agents", "set", "s2", "--no-plan", env=e)  # dropping plan escalates
        self.assertNotEqual(rc, 0); self.assertIn("exceeds parent ceiling", out)

    def test_turn_env_marker(self):
        e = ag._turn_env(self.sd, {"name": "par", "backend": "echo", "mode": "full"}, "", "")
        self.assertEqual((e["AG_PARENT_AGENT"], e["AG_PARENT_MODE"]), ("par", "full"))
        e = ag._turn_env(self.sd, {"name": "par", "backend": "echo", "mode": "full", "plan": True}, "", "")
        self.assertEqual(e["AG_PARENT_MODE"], "plan")
        e = ag._turn_env(self.sd, {"name": "par", "backend": "echo"}, "", "")
        self.assertEqual(e["AG_PARENT_MODE"], "")

    def _add_claude(self, name, *a):
        rc, out = self.cli("agents", "add", name, "--backend", "claude", "--dir", self.td, *a)
        self.assertEqual(rc, 0, out)

    def test_turn_clamp(self):
        self._add_claude("kid", "--mode", "full")
        os.environ.update(AG_PARENT_AGENT="p", AG_PARENT_MODE="edits")
        try:
            self.log.write_text("")
            r = ag.run_turn(self.sd, "kid", "hello")
            self.assertEqual(r.get("exit"), 0, r)
            last = json.loads(self.log.read_text().splitlines()[-1])
            self.assertIn("acceptEdits", last["argv"])  # clamped, not bypassPermissions
            self.assertNotIn("bypassPermissions", last["argv"])
            self.assertEqual((last["env"]["AG_PARENT_AGENT"], last["env"]["AG_PARENT_MODE"]), ("kid", "edits"))  # nesting sees the clamped mode
            rows = ag.load_chat_log(self.sd, "kid")
            self.assertTrue(any(x.get("role") == "tool" and "[mode] clamped to edits by parent p" in (x.get("text") or "") for x in rows), rows[-4:])
            cur = next(x for x in ag.load_agents(self.sd) if x["name"] == "kid")
            self.assertEqual(cur.get("mode"), "full")  # clamp is per-turn, roster untouched
        finally:
            for k in ("AG_PARENT_AGENT", "AG_PARENT_MODE"):
                os.environ.pop(k, None)

    def test_turn_no_clamp_when_off(self):
        self._add_claude("kid2", "--mode", "full")
        os.environ.update(AG_PARENT_AGENT="p", AG_PARENT_MODE="edits", AG_MODE_CEILING="off")
        try:
            self.log.write_text("")
            r = ag.run_turn(self.sd, "kid2", "hello")
            self.assertEqual(r.get("exit"), 0, r)
            last = json.loads(self.log.read_text().splitlines()[-1])
            self.assertIn("bypassPermissions", last["argv"])
            rows = ag.load_chat_log(self.sd, "kid2")
            self.assertFalse(any("[mode] clamped" in (x.get("text") or "") for x in rows))
        finally:
            for k in ("AG_PARENT_AGENT", "AG_PARENT_MODE", "AG_MODE_CEILING"):
                os.environ.pop(k, None)

    def test_delegate_clamp(self):
        self._add_claude("d1", "--mode", "full")
        e = dict(self.ceil_env("edits"), PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", SEAMS_LOG=str(self.log))
        rc, out = self.cli("delegate", "d1", "do it", env=e)
        self.assertEqual(rc, 0, out)
        rows = json.loads(self.cli("--json", "chat", "log", "d1", env=e)[1])["data"]["log"]
        self.assertTrue(any(x.get("role") == "tool" and "[mode] clamped to edits by parent p" in (x.get("text") or "") for x in rows), rows[-4:])

if __name__ == "__main__": unittest.main()
