#!/usr/bin/env python3
"""Capability table + per-agent permission modes. Run: python3 tests/test_ag_modes.py"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

def argv(b, mode="", plan=False, **kw): return ag.backend_argv(b, model="", workdir=".", sid=kw.get("sid", ""), prompt="hi", mode=mode, plan=plan)

class Modes(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); self.env = dict(os.environ, AG_AUTO_UPDATE="0", AG_HOME=self.td+"/h", AG_CONFIG_HOME=self.td+"/c", AG_CACHE_HOME=self.td+"/k")
    def ag(self, *a):
        p = subprocess.run([sys.executable, AG, "--dir", self.td+"/.agent", *a], capture_output=True, text=True, env=self.env)
        return p.stdout + p.stderr

    def test_unset_unchanged(self):
        self.assertEqual(argv("claude"), ["claude","-p","--verbose","--output-format","stream-json","--include-partial-messages","hi"])
        self.assertEqual(argv("codex"), ["codex","exec","--json","hi"])
        self.assertIn("default", argv("gemini")); self.assertNotIn("--force", argv("cursor"))

    def test_argv_per_backend(self):
        pm = lambda m, p=False: argv("claude", m, p)[argv("claude", m, p).index("--permission-mode")+1]
        self.assertEqual([pm(m) for m in ag.MODES], ["dontAsk","acceptEdits","auto","bypassPermissions"]); self.assertEqual(pm("full", True), "plan")
        sb = lambda m, p=False: argv("codex", m, p)[argv("codex", m, p).index("-s")+1]
        self.assertEqual([sb(m) for m in ag.MODES], ["read-only","workspace-write","workspace-write","danger-full-access"]); self.assertEqual(sb("full", True), "read-only")
        self.assertEqual(argv("codex","ro",sid="t")[:5], ["codex","exec","--json","-s","read-only"]); self.assertEqual(argv("codex","ro",sid="t")[-3:], ["resume","t","hi"])
        am = lambda m, p=False: argv("gemini", m, p)[argv("gemini", m, p).index("--approval-mode")+1]
        self.assertEqual([am(m) for m in ("ro","edits","full")], ["plan","auto_edit","yolo"]); self.assertEqual(am("auto"), "default"); self.assertEqual(am("", True), "plan")
        self.assertEqual(argv("cursor","ro")[-3:], ["--mode","ask","hi"]); self.assertEqual(argv("cursor","full")[-4:], ["--force","--sandbox","disabled","hi"])
        self.assertIn("--force", argv("cursor","auto")); self.assertEqual(argv("cursor","", True)[-3:], ["--mode","plan","hi"])
        self.assertEqual(argv("echo","full"), ["printf","%s\n","hi"])

    def test_opencode_config_merge(self):
        sd = Path(self.td)/"sd"; sd.mkdir()
        a = {"name":"oc","backend":"opencode","mode":"edits"}
        env = ag.mode_env(sd, a, {})
        cfg = json.loads(Path(env["OPENCODE_CONFIG"]).read_text())
        self.assertEqual(cfg["permission"], {"edit":"allow","bash":"deny"})
        base = Path(self.td)/"prof.json"; base.write_text(json.dumps({"mcp":{"x":{}},"permission":{"webfetch":"deny","edit":"ask"}}))
        env2 = ag.mode_env(sd, {**a,"mode":"full"}, {"OPENCODE_CONFIG":str(base),"OTHER":"1"})
        cfg2 = json.loads(Path(env2["OPENCODE_CONFIG"]).read_text())
        self.assertEqual(cfg2["permission"], {"webfetch":"deny","edit":"ask","*":"allow"}); self.assertIn("mcp", cfg2); self.assertEqual(env2["OTHER"], "1")
        self.assertEqual(json.loads(base.read_text())["permission"]["edit"], "ask")  # profile file untouched
        self.assertEqual(json.loads(Path(ag.mode_env(sd, {**a,"mode":"","plan":True}, {})["OPENCODE_CONFIG"]).read_text())["permission"], {"edit":"deny","bash":"deny"})
        self.assertEqual(ag.mode_env(sd, {"name":"oc","backend":"opencode"}, {"K":"v"}), {"K":"v"})
        self.assertEqual(ag.mode_env(sd, {"name":"c","backend":"claude","mode":"ro"}, {}), {})

    def test_warning(self):
        self.assertEqual(ag.mode_warning("claude","ro"), ""); self.assertEqual(ag.mode_warning("claude",""), "")
        self.assertIn("advisory", ag.mode_warning("echo","ro")); self.assertIn("auto", ag.mode_warning("gemini","auto"))
        self.assertIn("advisory", ag.mode_warning("echo","",True))

    def test_cli_roundtrip_and_caps(self):
        self.assertIn("added", self.ag("agents","add","m1","--backend","claude","--mode","edits","--plan"))
        r = {x["name"]:x for x in json.loads(self.ag("--json","agents","list"))["data"]["agents"]}["m1"]
        self.assertEqual((r["mode"], r["plan"]), ("edits", True))
        self.ag("agents","set","m1","--no-plan","--mode","ro")
        r = {x["name"]:x for x in json.loads(self.ag("--json","agents","list"))["data"]["agents"]}["m1"]
        self.assertEqual((r["mode"], r["plan"]), ("ro", False)); self.assertIn("ro", self.ag("agents","list"))
        self.assertIn("bad mode", self.ag("agents","set","m1","--mode","nope"))
        self.ag("agents","set","m1","--mode","");
        self.assertFalse({x["name"]:x for x in json.loads(self.ag("--json","agents","list"))["data"]["agents"]}["m1"]["mode"])
        out = self.ag("agents","add","e1","--backend","echo","--mode","ro"); self.assertIn("[mode] echo", out)
        d = json.loads(self.ag("--json","agents","caps"))["data"]["caps"]
        self.assertEqual([x["backend"] for x in d], ["claude","opencode","codex","gemini","cursor","echo","acp"])
        for k in ("resume","system_flag","mcp_flag","skills","interrupt","usage","modes","native_enforce"): self.assertIn(k, d[0])
        self.assertFalse(d[-1]["native_enforce"])
        self.assertEqual(json.loads(self.ag("--json","agents","caps","codex"))["data"]["caps"][0]["backend"], "codex")

    def test_turn_warning(self):
        self.ag("agents","add","e2","--backend","echo","--mode","full")
        self.assertIn("[mode] echo", self.ag("chat","send","e2","hello"))

if __name__ == "__main__": unittest.main()
