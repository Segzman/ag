#!/usr/bin/env python3
"""Per-agent env + keys (agents add/set --env/--env-unset/--env-clear/--claude-config-dir/--subscription-only,
profile env, agents env). Fake claude dumps its env. Run: python3 tests/test_ag_env.py"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

FAKE = '''#!/usr/bin/env python3
import json, os
open(os.environ["ENV_LOG"], "w").write(json.dumps(dict(os.environ)))
print(json.dumps({"type": "result", "result": "ok"}))
'''

class EnvT(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); td = self.td
        b = Path(td, "bin"); b.mkdir(); f = b / "claude"; f.write_text(FAKE); f.chmod(0o755)
        self.log = Path(td, "env.json"); self.sd = Path(td, ".agent"); self.sd.mkdir()
        self.old = dict(os.environ)
        os.environ.update(PATH=f"{b}{os.pathsep}{os.environ['PATH']}", ENV_LOG=str(self.log), AG_AUTO_UPDATE="0",
            AG_HOME=td+"/h", AG_CONFIG_HOME=td+"/c", AG_CACHE_HOME=td+"/k", AG_RTK="0", AG_PROBE="0", AG_SECRET_POPUP="0",
            SRC_TOK="tok-123", ANTHROPIC_API_KEY="sk-ant-xxxxxxxxxxxxxxxx", OPENAI_API_KEY="sk-yyyyyyyyyyyyyyyy", BASE_VAR="base")
        os.environ.pop("NOPE_UNSET", None)
        self.env = dict(os.environ)
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)
    def cli(self, *a, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a], capture_output=True, text=True, env=self.env)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p
    def turn(self, name="w"):
        self.log.unlink(missing_ok=True)
        notes = []
        r = ag.run_turn(self.sd, name, "hi", on_event=lambda n, k, t: notes.append((k, t)))
        self.assertEqual(r.get("exit"), 0, r)
        return json.loads(self.log.read_text()), notes
    def add(self, *a): self.cli("agents", "add", "w", "--backend", "claude", "--dir", self.td, *a)

    def test_precedence_unset_and_refs(self):
        self.cli("harness", "profile", "add", "p", "--env", "A=prof", "--env", "B=prof", "--env-unset", "BASE_VAR")
        self.add("--env", "B=agent", "--env", "MY_TOKEN=${SRC_TOK}", "--env", "PATH+=" + self.td + "/extra", "--env", "C=~x")
        self.cli("harness", "profile", "set", "w", "--profile", "p")
        e, _ = self.turn()
        self.assertEqual((e["A"], e["B"]), ("prof", "agent"))
        self.assertEqual(e["MY_TOKEN"], "tok-123"); self.assertNotIn("BASE_VAR", e)
        self.assertTrue(e["PATH"].startswith(self.td + "/extra" + os.pathsep)); self.assertEqual(e["C"], "~x")
        self.cli("agents", "set", "w", "--env-unset", "A", "--env-unset", "B")
        e, _ = self.turn(); self.assertNotIn("B", e); self.assertNotIn("A", e)  # agent unset beats profile set
        self.cli("agents", "set", "w", "--env-clear")
        e, _ = self.turn(); self.assertNotIn("MY_TOKEN", e); self.assertEqual(e["A"], "prof")

    def test_subscription_only_strip(self):
        self.add("--subscription-only", "on", "--env", "GEMINI_API_KEY=${SRC_TOK}")
        e, _ = self.turn()
        for k in ag.SUBSCRIPTION_STRIP: self.assertNotIn(k, e)
        self.assertEqual(e["BASE_VAR"], "base")
        self.cli("agents", "set", "w", "--subscription-only", "off")
        e, _ = self.turn(); self.assertIn("ANTHROPIC_API_KEY", e); self.assertEqual(e["GEMINI_API_KEY"], "tok-123")

    def test_refusals(self):
        for bad in ("HOME=/x", "USERPROFILE=/x", "PATH=/x", "MY_API_KEY=literal", "X_PASSWORD=pw", "S_SECRET=s", "T_TOKEN=t"):
            p = self.cli("agents", "add", "z", "--backend", "echo", "--env", bad, ok=False)
            self.assertNotEqual(p.returncode, 0, bad)
        self.assertNotIn("z", [r["name"] for r in ag.load_agents(self.sd)])
        self.assertNotEqual(self.cli("agents", "add", "z", "--backend", "echo", "--env-unset", "PATH", ok=False).returncode, 0)
        self.assertNotEqual(self.cli("harness", "profile", "add", "q", "--env", "K_TOKEN=lit", ok=False).returncode, 0)
        self.cli("agents", "add", "z", "--backend", "echo", "--env", "K_TOKEN={env:SRC_TOK}")  # refs ok

    def test_unresolved_ref_dropped_with_note(self):
        self.add("--env", "MY_KEY=${NOPE_UNSET}")
        e, notes = self.turn()
        self.assertNotIn("MY_KEY", e); self.assertFalse(any("${" in v for v in e.values()))
        self.assertEqual(len([t for k, t in notes if k == "tool" and "NOPE_UNSET" in t]), 1)

    def test_claude_config_dir_clears_sid(self):
        self.add("--claude-config-dir", "~/acct1")
        e, _ = self.turn(); self.assertEqual(e["CLAUDE_CONFIG_DIR"], str(Path("~/acct1").expanduser()))
        ag.save_chat_meta(self.sd, "w", {"sid": "s1", "sids": {"claude": "s1"}, "backend": "claude"})
        self.cli("agents", "set", "w", "--claude-config-dir", "~/acct1")  # unchanged: sid kept
        self.assertEqual(ag.load_chat_meta(self.sd, "w").get("sid"), "s1")
        self.cli("agents", "set", "w", "--claude-config-dir", "~/acct2")
        m = ag.load_chat_meta(self.sd, "w"); self.assertEqual((m.get("sid"), (m.get("sids") or {}).get("claude")), ("", None))

    def test_agents_env_redaction(self):
        self.add("--env", "MY_TOKEN=${SRC_TOK}", "--env", "FOO=bar", "--env-unset", "BASE_VAR")
        p = self.cli("agents", "env", "w", "--json"); d = json.loads(p.stdout)["data"]
        self.assertEqual(d["set"]["MY_TOKEN"], "REDACTED"); self.assertEqual(d["set"]["FOO"], "bar")
        self.assertIn("BASE_VAR", d["unset"]); self.assertNotIn("tok-123", p.stdout)

    def test_native_and_compact_match_headless(self):
        self.add("--env", "FOO=bar", "--env", "MY_TOKEN=${SRC_TOK}", "--env-unset", "BASE_VAR", "--subscription-only", "on")
        a = next(r for r in ag.load_agents(self.sd) if r["name"] == "w")
        n = ag.native_launch(self.sd, "w", "claude")
        self.assertEqual((n["env"]["FOO"], n["env"]["MY_TOKEN"]), ("bar", "tok-123"))
        self.assertIn("BASE_VAR", n["env_unset"]); self.assertIn("ANTHROPIC_API_KEY", n["env_unset"])
        head = ag._turn_env(self.sd, a, "claude", "")
        nat = {k: v for k, v in dict(os.environ, **n["env"]).items() if k not in n["env_unset"]}
        for k in ("FOO", "MY_TOKEN", "BASE_VAR", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"): self.assertEqual(head.get(k), nat.get(k), k)

if __name__ == "__main__": unittest.main()
