#!/usr/bin/env python3
"""`ag setup` front-ends: pure state model, mac hub (fake osascript via AG_OSASCRIPT), curses
editor driven through a real pty. Same isolation as test_ag_setup (temp AG_HOME etc.).
Run: python3 tests/test_ag_setup_ui.py
"""
import fcntl, importlib.machinery, importlib.util, json, os, pty, select, signal, struct, sys, termios, time, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ag_setup as base  # noqa: E402

AG = base.AG
FAKE_OSA = """#!{py}
import json, os, sys
d = os.environ["FAKE_DIR"]
q = json.load(open(d + "/osa.json"))
ans = q.pop(0) if q else "__ag_cancel__"
json.dump(q, open(d + "/osa.json", "w"))
with open(d + "/osa.log", "a") as f: f.write(json.dumps(sys.argv[1:]) + "\\n")
print(ans)
"""
DOWN, LEFT, RIGHT, ENTER = "\x1bOB", "\x1bOD", "\x1bOC", "\r"


def load_ag():
    ld = importlib.machinery.SourceFileLoader("ag_setup_ui_mod", AG)
    spec = importlib.util.spec_from_loader("ag_setup_ui_mod", ld)
    m = importlib.util.module_from_spec(spec); ld.exec_module(m)
    return m


class SetupState(unittest.TestCase):
    """Pure state helpers shared by every UI."""
    @classmethod
    def setUpClass(cls): cls.m = load_ag()

    def st(self, **kw):
        ex = kw.pop("existing", {"global": {}, "project": {}})
        return self.m.setup_state_from(kw.pop("scope", "global"), kw.pop("preset", None), kw.pop("sets", None),
            kw.pop("harn", ["claude"]), kw.pop("claude_md", None), proj="/p", existing=ex)

    def test_preset_apply_and_custom_edits(self):
        m, s = self.m, self.st()
        self.assertEqual(m.setup_custom_jobs(s), [])
        m.setup_edit_job(s, "mechanical", claude="sonnet")
        m.setup_edit_job(s, "review", ag={"backend": "opencode", "model": "x/y"})
        self.assertEqual(m.setup_custom_jobs(s), ["mechanical", "review"])
        m.setup_set_scope(s, "project")                   # scope switch keeps UI edits
        self.assertEqual((s["jobs"]["mechanical"]["claude"], s["jobs"]["review"]["ag"]["model"]), ("sonnet", "x/y"))
        m.setup_apply_preset(s, "quality-first")           # preset wins outright
        self.assertEqual(m.setup_custom_jobs(s), [])
        self.assertEqual(s["jobs"]["mechanical"], {"claude": "sonnet", "ag": {"backend": "claude", "model": "sonnet"}})

    def test_on_disk_picks_kept_unless_preset_forced(self):
        ex = {"global": {"preset": "cost-first", "jobs": {"plan": {"claude": "haiku"}}}, "project": {}}
        self.assertEqual(self.st(existing=ex)["jobs"]["plan"]["claude"], "haiku")
        self.assertEqual(self.st(existing=ex, preset="cost-first")["jobs"]["plan"]["claude"], "opus")
        s = self.st(existing=ex, sets={"plan": {"claude": "fable"}})
        self.assertEqual(s["jobs"]["plan"]["claude"], "fable")

    def test_rows(self):
        m, s = self.m, self.st(harn=["claude", "opencode"])
        rows = m.setup_rows(s)
        self.assertEqual([k for k, _ in rows], ["scope", "harnesses", "preset", "claude_md"] + [f"job:{j}" for j in m.JOB_KEYS])
        self.assertEqual(rows[1][1], "Harnesses: claude, opencode")
        self.assertEqual(rows[3][1], "Write CLAUDE.md: yes (auto)")
        self.assertEqual(rows[4][1], f"mechanical — Claude: haiku · ag: opencode/{m.OPENCODE_MODEL_DEFAULT}")
        s["claude_md"] = False; s["harnesses"] = []
        self.assertEqual(m.setup_rows(s)[1:4:2], [("harnesses", "Harnesses: none"), ("claude_md", "Write CLAUDE.md: no")])

    def test_targets(self):
        import tempfile
        m = self.m
        with tempfile.TemporaryDirectory() as td:
            old = {k: os.environ.get(k) for k in ("AG_HOME", "AG_CONFIG_HOME", "AG_SKILLS_DIR")}
            os.environ.update(AG_HOME=td, AG_CONFIG_HOME=td + "/cfg", AG_SKILLS_DIR=td + "/none")
            try:
                s = self.st(harn=[])
                t = m.setup_targets(s)
                self.assertEqual([(str(p), st) for p, _, _, st in t["plan"]], [(td + "/cfg/routing.json", "new")])
                s["claude_md"] = True
                self.assertEqual([p.name for p, *_ in m.setup_targets(s)["plan"]], ["routing.json", "CLAUDE.md"])
                s["harnesses"] = ["claude"]
                self.assertIn("skill template missing", m.setup_targets(s)["skill_err"])
                m.setup_set_scope(s, "project")
                t = m.setup_targets(s)
                self.assertEqual(str(t["plan"][0][0]), "/p/.agent/routing.json")
                self.assertEqual(json.loads(t["plan"][0][1])["jobs"], s["jobs"])
            finally:
                for k, v in old.items():
                    if v is None: os.environ.pop(k, None)
                    else: os.environ[k] = v


class SetupUIs(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj

    def mac(self, answers, *args, oc_ids=None):
        if oc_ids: (self.fake/"oc.ids").write_text("\n".join(oc_ids) + "\n")
        self.agj("models")                                  # warm cache -> no background refresh race
        (self.fake/"osascript").write_text(FAKE_OSA.format(py=sys.executable)); (self.fake/"osascript").chmod(0o755)
        (self.fake/"osa.json").write_text(json.dumps(answers))
        p = self.ag("setup", "--ui", "mac", *args, env={"AG_OSASCRIPT": str(self.fake/"osascript")})
        log = [json.loads(l) for l in (self.fake/"osa.log").read_text().splitlines()]
        self.assertEqual(json.loads((self.fake/"osa.json").read_text()), [], (p.stdout, p.stderr))  # all consumed
        for call in log:                                  # dynamic strings only via argv
            self.assertEqual(call[0], "-e"); self.assertIn("on run argv", call[1])
            for s in ("review", "anthropic", "opencode/", "Harnesses"): self.assertNotIn(s, call[1])
        return p, log

    def test_mac_hub_session_filter_custom_save(self):
        ids = [f"opencode/m{i:02}" for i in range(29)] + ["anthropic/c"]
        p, log = self.mac(["Harnesses: opencode, codex", "claude\nopencode",
            "review — Claude: opus · ag: claude/opus", "sonnet", "opencode", "anthropic", "anthropic/c",
            "debug — Claude: opus · ag: claude/opus", "haiku", "codex", "Type a custom id…", "my/custom",
            "✓ Save", "Save", ""], oc_ids=ids)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("wrote", p.stdout)
        rt = json.loads((self.cfg/"routing.json").read_text())["jobs"]
        self.assertEqual(rt["review"], {"claude": "sonnet", "ag": {"backend": "opencode", "model": "anthropic/c"}})
        self.assertEqual(rt["debug"], {"claude": "haiku", "ag": {"backend": "codex", "model": "my/custom"}})
        self.assertTrue((self.home/".claude"/"skills"/"ag-agents"/"SKILL.md").exists(), p.stdout)
        self.assertFalse((self.home/".codex"/"skills").exists())  # deselected
        self.assertIn("| review — code review, verification | sonnet | opencode / anthropic/c |",
            (self.home/".claude"/"CLAUDE.md").read_text())
        self.assertEqual(log[0][2:6], ["ag setup", log[0][3], "Edit", "Quit"])
        self.assertTrue(log[0][3].startswith("Pick a row"))
        self.assertIn("Filter models", log[5][3])           # >25 ids -> filter dialog first
        self.assertEqual(log[6][8:], ["anthropic/c", "Type a custom id…"])  # filtered list + custom row
        self.assertIn(str(self.cfg/"routing.json").replace(str(self.home), "~"), log[13][3])  # save alert lists targets
        self.assertIn("display notification", log[14][1])

    def test_mac_preset_confirm_and_project_scope(self):
        p, log = self.mac(["review — Claude: opus · ag: claude/opus", "haiku", "claude", "haiku",
            "Preset: cost-first (custom picks)", "balanced", "Apply", "✓ Save", "Save", ""], "--scope", "project", "--harness", "none")
        self.assertEqual(p.returncode, 0, p.stderr)
        doc = json.loads((self.proj/".agent"/"routing.json").read_text())
        self.assertEqual(doc["preset"], "balanced")
        self.assertEqual(doc["jobs"]["review"], {"claude": "sonnet", "ag": {"backend": "claude", "model": "sonnet"}})
        self.assertIn("review", log[6][3])                  # confirm names the overwritten job
        self.assertFalse((self.cfg/"routing.json").exists())

    def test_mac_quit_writes_nothing(self):
        p, _ = self.mac(["__ag_cancel__"])
        self.assertIn("cancelled", p.stdout)
        self.assertFalse((self.cfg/"routing.json").exists() or (self.home/".claude").exists())

    # ---- curses in a real pty ----
    def pty_run(self, args, keys, size=(24, 80), after_start=None):
        self.agj("models")
        env = {**self.env, "TERM": "xterm-256color", "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "AG_AUTO_UPDATE": "0"}
        env.pop("NO_COLOR", None); env.pop("AG_SETUP_UI", None)
        pid, fd = pty.fork()
        if pid == 0:
            try:
                fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", size[0], size[1], 0, 0))
                os.chdir(self.proj); os.execve(sys.executable, [sys.executable, AG, "setup", *args], env)
            finally: os._exit(127)
        out = bytearray()
        def drain(t):
            end = time.time() + t
            while time.time() < end:
                r, _, _ = select.select([fd], [], [], 0.05)
                if r:
                    try: out.extend(os.read(fd, 65536))
                    except OSError: return
        drain(2.0)
        if after_start: after_start(pid, fd, drain)
        for k in keys: os.write(fd, k.encode()); drain(0.35)
        status, end = None, time.time() + 20
        while time.time() < end:
            drain(0.2)
            w, status = os.waitpid(pid, os.WNOHANG)
            if w: break
        else:
            os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0); self.fail("ag setup --ui cli hung:\n" + out.decode(errors="replace")[-2000:])
        drain(0.3); os.close(fd)
        return os.waitstatus_to_exitcode(status), out.decode(errors="replace")

    def test_cli_editor_session(self):
        keys = [DOWN, ENTER, " ", ENTER,                    # harnesses: + claude (opencode, codex detected)
            DOWN, "\x1b[B", DOWN, "\x1b[B", DOWN, RIGHT, ENTER, "openc", ENTER, "anth", ENTER,   # review ag (CSI + SS3 arrows)
            DOWN, ENTER, ENTER, "\t", "\x15", "my/custom-1", ENTER,                      # debug ag model: custom id
            LEFT, ENTER, "hai", ENTER,                      # debug claude -> haiku
            "?", "x", "s", "y"]
        code, out = self.pty_run(["--ui", "cli"], keys)
        self.assertEqual(code, 0, out[-1500:])
        self.assertIn("ag setup", out); self.assertIn("Writes (global)", out)
        self.assertIn("wrote", out)
        rt = json.loads((self.cfg/"routing.json").read_text())["jobs"]
        self.assertEqual(rt["review"]["ag"], {"backend": "opencode", "model": "anthropic/c"})
        self.assertEqual(rt["debug"], {"claude": "haiku", "ag": {"backend": "claude", "model": "my/custom-1"}})
        for d in (self.home/".claude", self.home/".config"/"opencode"):
            self.assertTrue((d/"skills"/"ag-cli"/"SKILL.md").exists(), d)
        self.assertIn("my/custom-1", (self.home/".claude"/"CLAUDE.md").read_text())

    def test_cli_resize_then_quit_writes_nothing(self):
        def resize(pid, fd, drain):
            for rows, cols in ((20, 70), (10, 40), (24, 80)):
                fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0)); os.kill(pid, signal.SIGWINCH); drain(0.4)
        code, out = self.pty_run(["--ui", "cli"], ["q"], after_start=resize)
        self.assertEqual(code, 0, out[-1500:])
        self.assertIn("terminal too small", out); self.assertIn("cancelled", out)
        self.assertFalse((self.cfg/"routing.json").exists())

    def test_cli_too_small_falls_back_to_plain(self):
        code, out = self.pty_run(["--ui", "cli", "--scope", "global", "--harness", "none", "--preset", "cost-first"],
            ["n\r"] * 6 + ["n\r"], size=(10, 40))
        self.assertIn("change? (y/N)", out)
        self.assertFalse((self.cfg/"routing.json").exists())

    def test_bad_ui(self):
        self.assertIn("bad --ui", self.agj("setup", ok=False, env={"AG_SETUP_UI": "gtk"})["error"])


if __name__ == "__main__":
    unittest.main()
