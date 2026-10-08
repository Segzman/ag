#!/usr/bin/env python3
"""run_turn seams (_turn_prompt/_turn_argv/_turn_env/_turn_source): pin exact argv/env/prompt
per backend via fake binaries on PATH. Run: python3 tests/test_ag_turn_seams.py"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod", AG); _s = importlib.util.spec_from_loader("ag_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

FAKE = '''#!/usr/bin/env python3
import json, os, sys
keys = ("OPENCODE_CONFIG", "CODEX_HOME", "SSH_ASKPASS", "SUDO_ASKPASS", "PATH")
open(os.environ["SEAMS_LOG"], "a").write(json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read(),
    "env": {k: os.environ[k] for k in keys if k in os.environ}, "cwd": os.getcwd()}) + "\\n")
print(json.dumps({"text": "ok"}))
'''

class Seams(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp(); td = self.td
        self.bin = Path(td, "bin"); self.bin.mkdir()
        for b in ("claude", "codex", "gemini", "opencode"):
            f = self.bin / b; f.write_text(FAKE); f.chmod(0o755)
        self.log = Path(td, "log"); self.sd = Path(td, ".agent"); self.sd.mkdir()
        self.old = dict(os.environ)
        os.environ.update(PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}", SEAMS_LOG=str(self.log), AG_AUTO_UPDATE="0",
            AG_HOME=td+"/h", AG_CONFIG_HOME=td+"/c", AG_CACHE_HOME=td+"/k", AG_RTK="0", AG_PROBE="0", AG_SECRET_POPUP="0")
        self.env = dict(os.environ)
    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)
    def cli(self, *a):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a], capture_output=True, text=True, env=self.env)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
    def turn(self, backend, *add, sid="", text="hello", set_=()):
        name = backend[:2] + str(len(list(self.log.parent.glob("n*"))))
        Path(self.td, "n" + name).touch()
        self.cli("agents", "add", name, "--backend", backend, "--dir", self.td, *add)
        for s in set_: self.cli(*s.replace("NAME", name).split())
        if sid: ag.save_chat_meta(self.sd, name, {"sid": sid, "sids": {backend: sid}, "backend": backend})
        self.log.write_text("")
        r = ag.run_turn(self.sd, name, text)
        self.assertEqual(r.get("exit"), 0, r)
        return json.loads(self.log.read_text().splitlines()[-1])

    def test_claude_fresh_and_resumed(self):
        L = self.turn("claude", "--system", "Be terse.")
        self.assertEqual(L["argv"][:5], ["-p", "--verbose", "--output-format", "stream-json", "--include-partial-messages"])
        i = L["argv"].index("--append-system-prompt"); self.assertEqual(L["argv"][i+1], "Be terse.")
        self.assertEqual(L["argv"][-1], "hello"); self.assertNotIn("--resume", L["argv"])
        L = self.turn("claude", sid="s-1")
        self.assertEqual(L["argv"][-3:], ["--resume", "s-1", "hello"]); self.assertNotIn("--append-system-prompt", L["argv"])

    def test_system_role_prepend_codex_gemini(self):
        L = self.turn("codex", "--system", "Be terse.")  # codex: -c developer_instructions, no prepend
        self.assertEqual(L["argv"][:5], ["exec", "--json", "-c", 'developer_instructions="Be terse."', L["argv"][4]])
        self.assertEqual(L["argv"][-1], "hello")
        L = self.turn("codex", "--system", "Be terse.", sid="t-9")
        self.assertEqual(L["argv"][-3:], ["resume", "t-9", "hello"]); self.assertIn('developer_instructions="Be terse."', L["argv"])
        for b, pre in (("gemini", None),):
            L = self.turn(b, "--system", "Be terse.")
            p = [x for x in L["argv"] if "hello" in x][0]
            self.assertTrue(p.startswith("[system role: custom]\nBe terse.\n\n---\n"), p); self.assertTrue(p.endswith("hello"), p)
            if pre: self.assertEqual(L["argv"][:2], pre)
            L = self.turn(b, "--system", "Be terse.", sid="t-9")  # resumed: no prepend
            self.assertIn("hello", L["argv"]); self.assertNotIn("[system role", " ".join(L["argv"]))

    def test_mode_plan(self):
        L = self.turn("claude", "--mode", "ro")
        self.assertEqual(L["argv"][-3:], ["--permission-mode", "dontAsk", "hello"])
        L = self.turn("codex", "--mode", "edits", "--plan")
        self.assertEqual(L["argv"][:5], ["exec", "--json", "-s", "read-only", "hello"][:4] + [L["argv"][4]])
        L = self.turn("gemini", "--mode", "full")
        self.assertEqual(L["argv"][L["argv"].index("--approval-mode") + 1], "yolo")

    def test_opencode_mode_env_overlay(self):
        L = self.turn("opencode", "--mode", "edits")
        p = Path(L["env"]["OPENCODE_CONFIG"]); self.assertEqual(p.parent.parent.name, "modes")
        self.assertEqual(json.loads(p.read_text())["permission"], {"edit": "allow", "bash": "deny"})
        L = self.turn("opencode")
        self.assertNotIn("OPENCODE_CONFIG", L["env"])

    def test_profile_argv_and_env(self):
        instr = Path(self.td, "I.md"); instr.write_text("PROFILE-RULES")
        oc = Path(self.td, "oc.json"); oc.write_text("{}")
        self.cli("harness", "profile", "add", "demo", "--instructions", str(instr), "--opencode-config", str(oc))
        L = self.turn("opencode", set_=("harness profile set NAME --profile demo",))
        self.assertIn("profiles_effective", L["env"]["OPENCODE_CONFIG"])
        self.assertIn("PROFILE-RULES", [x for x in L["argv"] if "hello" in x][0])  # profile context reaches prompt (system-role block)
        L = self.turn("claude", set_=("harness profile set NAME --profile demo",))
        self.assertIn("PROFILE-RULES", L["argv"][L["argv"].index("--append-system-prompt") + 1])

    def test_helpers_direct(self):
        a = {"name": "x", "backend": "codex", "system": "S"}
        pr, sysx, prof = ag._turn_prompt(self.sd, a, "codex", "", "hi", None, None)
        self.assertEqual((prof, sysx), ("", "S")); self.assertEqual(pr, "hi")  # codex: system via -c developer_instructions, no prepend
        self.assertEqual(ag._turn_prompt(self.sd, a, "codex", "sid", "hi", "cmd", None)[0], "hi")
        self.assertEqual(ag._turn_argv(self.sd, a, "codex", "default", ".", "", "hi", "S", None, ""), ag.backend_argv("codex", model="default", workdir=".", prompt="hi")[:3] + ["-c", 'developer_instructions="S"'] + ag.backend_argv("codex", model="default", workdir=".", prompt="hi")[3:])
        e = ag._turn_env(self.sd, a, "codex", "")
        self.assertEqual({k: v for k, v in e.items() if k in os.environ and os.environ[k] != v}, {})
        class P: stdout = iter(["a\n"])
        self.assertEqual(list(ag._turn_source(P())), ["a\n"])

if __name__ == "__main__": unittest.main()
