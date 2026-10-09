#!/usr/bin/env python3
"""`ag setup` mac form (JSON contract via fake AG_OSASCRIPT; real JXA selftest, skippable) + per-backend
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
# fake osascript: `-l JavaScript -e SRC JSON` = the form (answer from form.json: {"cancel":1} or a patch over the
# initial values); `-e SRC ...` = notification. Every argv is logged.
FAKE_FORM = """#!{py}
import json, os, sys
d = os.environ["FAKE_DIR"]; a = sys.argv[1:]
with open(d + "/osa.log", "a") as f: f.write(json.dumps(a) + "\\n")
if a[:2] != ["-l", "JavaScript"]: sys.exit(0)
p = json.loads(a[-1]); json.dump(p, open(d + "/payload.json", "w"))
ans = json.load(open(d + "/form.json"))
if ans.get("cancel"): sys.exit(1)
out = {{k: p[k] for k in ("scope", "harnesses", "preset", "claude_md", "jobs", "backends")}}
for k, v in ans.items():
    if isinstance(v, dict) and isinstance(out.get(k), dict):
        for kk, vv in v.items(): out[k][kk] = {{**out[k][kk], **vv}} if isinstance(vv, dict) and isinstance(out[k].get(kk), dict) else vv
    else: out[k] = v
print(json.dumps(out))
"""
UP, DOWN, LEFT, RIGHT, ENTER = "\x1bOA", "\x1bOB", "\x1bOD", "\x1bOC", "\r"


def gui_ok():
    if sys.platform != "darwin" or os.environ.get("AG_NO_GUI") or not shutil.which("osascript"): return False
    return subprocess.run(["pgrep", "-x", "WindowServer"], capture_output=True).returncode == 0


class SetupForm(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj
    pty_run = ui.SetupUIs.pty_run

    def form(self, answer, *args, **env):
        self.agj("models")                                    # warm cache: no background refresh race
        (self.fake/"osascript").write_text(FAKE_FORM.format(py=sys.executable)); (self.fake/"osascript").chmod(0o755)
        (self.fake/"form.json").write_text(json.dumps(answer))
        p = self.ag("setup", "--ui", "mac", *args, env={"AG_OSASCRIPT": str(self.fake/"osascript"), "AG_PROBE": "0", **env})
        log = [json.loads(l) for l in (self.fake/"osa.log").read_text().splitlines()]
        return p, log

    def routing(self): return json.loads((self.cfg/"routing.json").read_text())

    # ---- Python <-> form JSON contract ----
    def test_form_save_writes_jobs_and_backends(self):
        p, log = self.form({"harnesses": ["claude"], "claude_md": True, "jobs": {"review": {"ag": {"backend": "codex", "model": "gpt-x"}}},
            "backends": {"opencode": {"model": "anthropic/c"}, "codex": {"mode": "edits", "plan": True}, "gemini": {"enabled": False}}})
        self.assertEqual(p.returncode, 0, p.stderr); self.assertIn("wrote", p.stdout)
        self.assertEqual(log[0][:3], ["-l", "JavaScript", "-e"]); self.assertEqual(len(log[0]), 5)
        self.assertNotIn("opencode/a", log[0][2])             # data only via argv JSON, never in the source
        pl = json.loads((self.fake/"payload.json").read_text())
        self.assertEqual(pl["backend_order"], ["claude", "opencode", "codex", "gemini", "cursor"])
        self.assertIn("anthropic/c", pl["models"]["opencode"]); self.assertEqual(pl["models"]["codex"][-2:], ["gpt-x", "gpt-y"])
        self.assertEqual(pl["caps"]["gemini"]["modes"], ["ro", "edits", "full"])
        self.assertEqual(set(pl["status"]), set(pl["backend_order"]))
        self.assertEqual(pl["preset_jobs"]["balanced"]["review"]["claude"], "sonnet")
        doc = self.routing()
        self.assertEqual(doc["profiles"]["claude"]["jobs"]["review"], {"claude": "opus", "ag": {"backend": "codex", "model": "gpt-x"}})
        self.assertEqual(doc["backends"], {"opencode": {"model": "anthropic/c"}, "codex": {"mode": "edits", "plan": True},
            "gemini": {"enabled": False}})                    # sparse: only non-default fields
        sk = (self.home/".claude"/"skills"/"ag-agents"/"SKILL.md").read_text()
        self.assertIn("codex: default (mode edits, plan)", sk); self.assertIn("gemini: off", sk)
        self.assertNotIn("Backend defaults", (self.home/".claude"/"CLAUDE.md").read_text())
        self.assertIn("display notification", log[-1][1]); self.assertTrue(log[-1][-1].startswith("wrote 4 file(s)") and log[-1][-1].endswith("· routing " + str(self.cfg/"routing.json")), log[-1])
        self.assertEqual(len(log), 2)                         # form + notification, no confirm dialog

    def test_form_cancel_writes_nothing(self):
        p, log = self.form({"cancel": 1})
        self.assertIn("cancelled", p.stdout); self.assertEqual(len(log), 1)
        self.assertFalse((self.cfg/"routing.json").exists() or (self.home/".claude").exists())

    def test_form_disabled_backend_rejected(self):
        self.env["AG_HARNESS"] = "opencode"  # opencode profile routes every job to opencode
        p, _ = self.form({"backends": {"opencode": {"enabled": False}}}, "--json")
        o = json.loads(p.stdout)
        self.assertFalse(o["ok"]); self.assertIn("mechanical: ag target opencode is disabled", o["error"])
        self.assertFalse((self.cfg/"routing.json").exists())

    def test_form_preset_and_dry_run(self):
        p, log = self.form({"preset": "quality-first", "jobs": {}}, "--dry-run")
        self.assertIn("would write", p.stdout); self.assertFalse((self.cfg/"routing.json").exists())
        self.assertEqual(len(log), 1)                         # dry-run: no notification

    # ---- real JXA: construction + serialization, never modal ----
    @unittest.skipUnless(gui_ok(), "needs macOS GUI session (osascript + WindowServer)")
    def test_jxa_selftest(self):
        self.env["AG_HARNESS"] = "opencode"  # opencode profile routes every job to opencode
        m = ui.load_ag()
        src = self.root/"form.js"; src.write_text(m._JXA_FORM)
        c = subprocess.run(["osacompile", "-l", "JavaScript", "-o", str(self.root/"form.scpt"), str(src)], capture_output=True, text=True)
        self.assertEqual(c.returncode, 0, c.stderr)
        p = self.ag("--json", "setup", "--ui", "mac", "--dry-run", "--harness", "none",
            env={"AG_FORM_SELFTEST": "1", "AG_PROBE": "0", "PATH": f"{self.fake}:/usr/bin:/bin"})
        o = json.loads(p.stdout)
        self.assertTrue(o["ok"], (p.stdout, p.stderr)); self.assertEqual(o["data"]["routing"]["review"]["claude"], "sonnet")
        self.assertEqual(o["data"]["backends"]["opencode"]["enabled"], True)
        st = m.setup_state_from("global", None, {"mechanical": {"ag": {"backend": "opencode", "model": "x"}}}, ["claude"], None, proj="/p", existing={"global": {}, "project": {}},
            bsets={"gemini": {"mode": "ro"}, "opencode": {"enabled": False}}, profile="claude")
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", m._JXA_FORM, json.dumps(m.setup_form_payload(st))],
            capture_output=True, text=True, env={**os.environ, "AG_FORM_SELFTEST": "1"}, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertEqual(out["backends"]["gemini"], {"enabled": True, "model": "", "mode": "ro", "plan": False})
        self.assertEqual(out["jobs"]["mechanical"]["ag"]["backend"], "opencode")
        self.assertEqual(out["_errors"], ["mechanical: ag target opencode is disabled (enable it on the opencode tab or pick another target)"])

    # ---- --set backend.* ----
    def test_set_backend_flags(self):
        self.env["AG_HARNESS"] = "opencode"  # opencode profile routes every job to opencode
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
        self.env["AG_HARNESS"] = "opencode"  # opencode profile routes every job to opencode
        code, out = self.pty_run(["--ui", "cli", "--harness", "none"], [UP, UP, UP, UP, LEFT, ENTER, "s", "x", "q", "y"])
        self.assertEqual(code, 0, out[-1500:]); self.assertIn("Can't save yet", out); self.assertIn("cancelled", out)
        self.assertFalse((self.cfg/"routing.json").exists())


if __name__ == "__main__":
    unittest.main()
