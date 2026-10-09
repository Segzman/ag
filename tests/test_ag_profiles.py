#!/usr/bin/env python3
"""Harness detection, routing v2 profiles (+v1 migration), `route --check`, `setup --answers`.
Same isolation as test_ag_setup (temp AG_HOME etc.). Run: python3 tests/test_ag_profiles.py
"""
import json, os, subprocess, sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ag_setup as base  # noqa: E402
import test_ag_setup_ui as ui  # noqa: E402

AG = base.AG


class Profiles(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj

    def noharness(self): self.env.pop("AG_HARNESS", None); self.env.pop("CLAUDECODE", None)
    def rt(self, scope="global"):
        return json.loads(((self.cfg if scope == "global" else self.proj/".agent")/"routing.json").read_text())

    # ---- A: detection ----
    def test_override_and_none(self):
        for h in ("claude", "opencode", "codex", "none"):
            d = self.agj("route", env={"AG_HARNESS": h})["data"]
            self.assertEqual((d["harness"], d["via"]), (h, "override"))
        self.assertIn("harness: codex (via override)", self.ag("route", env={"AG_HARNESS": "codex"}).stdout)

    def test_process_walk_innermost_wins_and_env_is_fallback(self):
        self.noharness()
        for name in ("opencode", "claude"):   # wrapper scripts named like the harness: comm=sh, args name them
            w = self.root/name; w.write_text(f'#!/bin/sh\n"$@"\n'); w.chmod(0o755)
        py = [sys.executable, AG, "--json", "route"]
        run = lambda *wrap, env=None: json.loads(subprocess.run([*wrap, *py], capture_output=True, text=True, cwd=self.proj,
            env={**self.env, **(env or {})}, stdin=subprocess.DEVNULL).stdout)["data"]
        d = run(str(self.root/"opencode"))
        self.assertEqual((d["harness"], d["via"], d["profile"]), ("opencode", "process", "opencode"))
        # opencode launched from claude (env leaks CLAUDECODE): innermost process wins over env
        d = run(str(self.root/"claude"), str(self.root/"opencode"), env={"CLAUDECODE": "1"})
        self.assertEqual((d["harness"], d["via"]), ("opencode", "process"))
        # no matching ancestor (stubbed ps): env fallback, then none
        m = ui.load_ag(); none = lambda pid: None
        self.assertEqual(m.detect_harness({"CODEX_CI": "1"}, 5, none), ("codex", "env"))
        self.assertEqual(m.detect_harness({"OPENCODE": "1", "CLAUDECODE": "1"}, 5, none), ("opencode", "env"))
        self.assertEqual(m.detect_harness({}, 5, none)[0], "none")
        self.assertEqual(m.detect_harness({"AG_HARNESS": "bogus"}, 5, none)[0], "none")   # invalid override ignored

    # ---- B: profiles ----
    def test_builtin_profiles(self):
        c = self.agj("route", env={"AG_HARNESS": "claude"})["data"]["jobs"]
        self.assertEqual((c["mechanical"]["claude"], c["mechanical"]["ag"]["backend"], c["hardest"]["ag"]["model"]), ("haiku", "claude", "fable"))
        o = self.agj("route", env={"AG_HARNESS": "opencode"})["data"]["jobs"]
        self.assertTrue(all(e["ag"]["backend"] == "opencode" for e in o.values()))
        x = self.agj("route", env={"AG_HARNESS": "codex"})["data"]["jobs"]
        self.assertTrue(all(e["ag"]["backend"] == "codex" for e in x.values()))
        d = self.agj("route", env={"AG_HARNESS": "cursor"})["data"]
        self.assertEqual(d["profile"], "default")
        self.assertEqual(self.agj("route", "--profile", "codex")["data"]["profile"], "codex")
        self.assertEqual(self.agj("route", env={"AG_PROFILE": "opencode"})["data"]["profile"], "opencode")

    def test_v1_file_migrates_and_project_overlays_per_profile(self):
        self.cfg.mkdir()
        (self.cfg/"routing.json").write_text(json.dumps({"version": 1, "preset": "cost-first",
            "jobs": {"plan": {"claude": "haiku", "ag": {"backend": "codex", "model": "gpt-x"}}}}))
        for h in ("claude", "cursor"):   # claude + default pick up v1 jobs; opencode does not
            e = self.agj("route", "plan", env={"AG_HARNESS": h})["data"]["jobs"]["plan"]
            self.assertEqual((e["claude"], e["ag"]["backend"], e["source"]["ag"]), ("haiku", "codex", "global"))
        self.assertEqual(self.agj("route", "plan", env={"AG_HARNESS": "opencode"})["data"]["jobs"]["plan"]["ag"]["backend"], "opencode")
        (self.proj/".agent").mkdir()
        (self.proj/".agent"/"routing.json").write_text(json.dumps({"version": 2, "profiles": {"claude": {"jobs": {"plan": {"ag": {"backend": "claude", "model": "opus"}}}}}}))
        e = self.agj("route", "plan", env={"AG_HARNESS": "claude"})["data"]["jobs"]["plan"]
        self.assertEqual((e["claude"], e["ag"]["model"], e["source"]), ("haiku", "opus", {"claude": "global", "ag": "project"}))
        # saving rewrites as v2, keeping migrated profiles
        self.agj("setup", "--yes", "--harness", "none", env={"AG_HARNESS": "opencode"})
        doc = self.rt()
        self.assertEqual(doc["version"], 2); self.assertNotIn("jobs", doc)
        self.assertEqual(set(doc["profiles"]), {"default", "claude", "opencode"})
        self.assertEqual(doc["profiles"]["claude"]["jobs"]["plan"]["claude"], "haiku")

    def test_agents_add_job_uses_active_profile(self):
        self.agj("agents", "add", "w1", "--job", "review", env={"AG_HARNESS": "codex"})
        self.agj("agents", "add", "w2", "--job", "review", "--profile", "claude", env={"AG_HARNESS": "codex"})
        ag = {x["name"]: x for x in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual((ag["w1"]["backend"], ag["w2"]["backend"], ag["w2"]["model"]), ("codex", "claude", "opus"))

    def test_skills_render_profile_of_each_harness(self):
        self.agj("setup", "--yes", "--scope", "global", "--harness", "claude,opencode,codex")
        for base_, prof in ((".claude", "claude"), (".config/opencode", "opencode"), (".codex", "codex")):
            t = (self.home/base_/"skills"/"ag-agents"/"SKILL.md").read_text()
            self.assertIn(f"Profile: {prof}.", t)
            self.assertEqual("Claude subagent" in t, prof == "claude")

    def test_setup_profile_flag_edits_only_that_profile(self):
        self.agj("setup", "--yes", "--harness", "none", "--profile", "opencode", "--set", "review=ag:codex/gpt-x")
        doc = self.rt()
        self.assertEqual(doc["profiles"]["opencode"]["jobs"]["review"]["ag"], {"backend": "codex", "model": "gpt-x"})
        self.assertNotIn("claude", doc["profiles"])
        self.assertEqual(self.agj("route", "review", env={"AG_HARNESS": "opencode"})["data"]["jobs"]["review"]["ag"]["backend"], "codex")

    def test_state_profile_switch_keeps_edits(self):
        m = ui.load_ag()
        s = m.setup_state_from("global", None, None, [], None, proj="/p", existing={"global": {}, "project": {}}, profile="claude")
        m.setup_edit_job(s, "plan", claude="haiku")
        m.setup_set_profile(s, "opencode"); m.setup_edit_job(s, "debug", ag={"backend": "codex", "model": "g"})
        self.assertEqual(s["jobs"]["plan"]["claude"], "sonnet")           # claude edit not visible in opencode
        m.setup_set_profile(s, "claude"); self.assertEqual(s["jobs"]["plan"]["claude"], "haiku")
        doc = json.loads(m.setup_targets(s)["plan"][0][1])["profiles"]
        self.assertEqual((doc["claude"]["jobs"]["plan"]["claude"], doc["opencode"]["jobs"]["debug"]["ag"]["backend"]), ("haiku", "codex"))

    def test_routing_load_save_roundtrip(self):
        m = ui.load_ag()
        old = {k: os.environ.get(k) for k in ("AG_CONFIG_HOME", "AG_HOME")}
        os.environ.update(AG_CONFIG_HOME=str(self.cfg), AG_HOME=str(self.home))
        try:
            p = m.routing_save("global", {"profiles": {"codex": {"jobs": {"plan": {"ag": {"backend": "codex", "model": "m"}}}}}}, sdir=self.proj/".agent")
            self.assertEqual(p, self.cfg/"routing.json")
            self.assertEqual(m.routing_load("global", self.proj/".agent")["profiles"]["codex"]["jobs"]["plan"]["ag"]["model"], "m")
            self.assertEqual(m.routing_load("effective", self.proj/".agent")["profiles"]["codex"]["jobs"]["plan"]["ag"]["model"], "m")
            self.assertEqual(m.active_profile(self.proj/".agent", "codex")[1]["plan"]["ag"]["model"], "m")
            for bad in ({"profiles": {"nope": {}}}, {"profiles": {"codex": {"jobs": {"zzz": {}}}}}):
                with self.assertRaises(ValueError): m.routing_save("global", bad)
        finally:
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v

    # ---- D: per-project setup ----
    def test_route_check_and_answers(self):
        c = self.agj("route", "--check", env={"AG_HARNESS": "claude"})["data"]
        self.assertFalse(c["configured"]); self.assertEqual(c["harness"], "claude")
        ids = [q["id"] for q in c["questions"]]
        self.assertEqual(ids[:2], ["use", "other_harnesses"]); self.assertEqual(len(ids), 8)
        self.assertEqual([o["value"] for o in c["questions"][1]["options"]], ["opencode", "codex"])
        self.assertEqual([o["value"] for o in c["questions"][0]["options"]], ["inherit", "cheap", "strong", "custom"])
        self.assertIn("claude profile", c["questions"][0]["options"][0]["label"])
        t = self.ag("route", "--check", "--json", env={"AG_HARNESS": "claude"}); self.assertTrue(json.loads(t.stdout)["data"]["questions"])
        env = {"AG_HARNESS": "claude"}
        o = self.agj("setup", "--scope", "project", "--answers", json.dumps({"use": "inherit", "other_harnesses": ["codex"]}), env=env)["data"]
        self.assertEqual(o["harnesses"], ["claude", "codex"])
        doc = self.rt("project")
        self.assertTrue(doc["project"]["answered"]); self.assertEqual(doc["project"]["harnesses"], ["claude", "codex"])
        self.assertFalse(doc["profiles"])                                  # inherit = no overrides
        for sub in (".claude", ".agents"): self.assertTrue((self.proj/sub/"skills"/"ag-agents"/"SKILL.md").exists())
        self.assertFalse((self.proj/".opencode").exists())
        c = self.agj("route", "--check", env=env)["data"]
        self.assertEqual((c["configured"], c["questions"]), (True, []))

    def test_answers_cheap_strong_custom_and_errors(self):
        env = {"AG_HARNESS": "codex"}
        self.agj("setup", "--answers", json.dumps({"use": "cheap", "other_harnesses": ["claude"]}), env=env)
        pr = self.rt("project")["profiles"]
        self.assertEqual(pr["claude"]["jobs"]["plan"]["ag"], {"backend": "claude", "model": "haiku"})
        self.assertEqual(pr["codex"]["jobs"]["plan"]["ag"]["backend"], "codex")
        self.agj("setup", "--answers", json.dumps({"use": "strong"}), env={"AG_HARNESS": "claude"})
        self.assertEqual(self.rt("project")["profiles"]["claude"]["jobs"]["hardest"]["ag"]["model"], "fable")
        self.assertEqual(self.agj("route", "plan", env={"AG_HARNESS": "claude"})["data"]["jobs"]["plan"]["ag"]["model"], "opus")
        self.agj("setup", "--answers", json.dumps({"use": "custom", "jobs": {"review": "codex/gpt-x", "debug": "sonnet"}}), env={"AG_HARNESS": "claude"})
        j = self.agj("route", env={"AG_HARNESS": "claude"})["data"]["jobs"]
        self.assertEqual(j["review"]["ag"], {"backend": "codex", "model": "gpt-x"}); self.assertEqual(j["debug"]["ag"], {"backend": "claude", "model": "sonnet"})
        self.assertFalse((self.cfg/"routing.json").exists())
        for bad in ("{", '{"use":"zzz"}', '{"use":"custom","jobs":{"nope":"x"}}', '{"other_harnesses":["vim"]}', "[]"):
            self.agj("setup", "--answers", bad, ok=False, env=env)
        self.agj("setup", "--scope", "global", "--answers", "{}", ok=False, env=env)


if __name__ == "__main__":
    unittest.main()
