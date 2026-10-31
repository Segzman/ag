#!/usr/bin/env python3
"""ag models / route / setup / --job. Isolated: every path (AG_HOME, AG_CONFIG_HOME,
AG_CACHE_HOME, AG_SKILLS_DIR, state dir, PATH) points into a temp dir; fake
opencode/agent binaries; tiny skill template fixtures.
Run: python3 tests/test_ag_setup.py
"""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path

AG = os.environ.get("AG_BIN", str(Path(__file__).resolve().parent.parent / "ag"))
START, END = "<!-- ag:routing:start -->", "<!-- ag:routing:end -->"

FAKE_OPENCODE = """#!/bin/sh
n=$(cat "$FAKE_DIR/oc.count" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "$FAKE_DIR/oc.count"
if [ "$1" != "models" ]; then echo "opencode-fake 0.0"; exit 0; fi
if [ -f "$FAKE_DIR/oc.locked" ]; then rm "$FAKE_DIR/oc.locked"; echo "Error: database is locked" >&2; exit 1; fi
if [ -f "$FAKE_DIR/oc.fail" ]; then echo "boom: provider down" >&2; exit 2; fi
cat "$FAKE_DIR/oc.ids"
"""
FAKE_AGENT = """#!/bin/sh
printf '\\033[2K\\033[GLoading models\\342\\200\\246\\n\\033[2K\\033[1A\\033[2K\\033[GNo models available for this account.\\n'
"""
TPL_CLI = "---\nname: ag-cli\n---\nrun {{AG}} models ({{SCOPE}}) keep {{OTHER}}\n"
TPL_AGENTS = "---\nname: ag-agents\n---\n{{ROUTING}}\nuse {{AG}} route\n"


class AgSetup(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        self.home, self.cfg, self.cache, self.fake = r/"home", r/"cfg", r/"cache", r/"fake"
        self.proj, self.skills, self.state = r/"proj", r/"skills", r/"state"
        for d in (self.home, self.fake, self.proj): d.mkdir()
        subprocess.run(["git", "init", "-q", str(self.proj)], check=True)
        for name, body in (("opencode", FAKE_OPENCODE), ("agent", FAKE_AGENT)):
            (self.fake/name).write_text(body); (self.fake/name).chmod(0o755)
        (self.fake/"oc.ids").write_text("opencode/a\nopencode/b\nanthropic/c\n")
        (self.home/".codex").mkdir()
        (self.home/".codex"/"models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-x", "visibility": "list"}, {"slug": "gpt-hidden", "visibility": "hide"},
            {"slug": "gpt-y", "visibility": "list"}]}))
        for n, t in (("ag-cli", TPL_CLI), ("ag-agents", TPL_AGENTS)):
            (self.skills/n).mkdir(parents=True); (self.skills/n/"SKILL.md").write_text(t)
        self.env = dict(os.environ, AG_HOME=str(self.home), AG_CONFIG_HOME=str(self.cfg),
            AG_CACHE_HOME=str(self.cache), AG_SKILLS_DIR=str(self.skills), FAKE_DIR=str(self.fake),
            AGENT_CLI_DIR=str(self.state), AG_HARNESS="claude", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json", AG_OPENROUTER_RANKINGS_URL="off", AG_CATALOG_AUTO="0", PATH=f"{self.fake}:/usr/bin:/bin", HOME=str(self.home))

    def ag(self, *args, env=None):
        return subprocess.run([sys.executable, AG, *args], capture_output=True, text=True, timeout=60,
            cwd=self.proj, env={**self.env, **(env or {})}, stdin=subprocess.DEVNULL)

    def agj(self, *args, ok=True, **kw):
        p = self.ag("--json", *args, **kw)
        o = json.loads(p.stdout)
        self.assertEqual(o["ok"], ok, (args, p.stdout, p.stderr))
        return o

    def oc_calls(self):
        return int((self.fake/"oc.count").read_text()) if (self.fake/"oc.count").exists() else 0

    # ---- models ----
    def test_models_sources_ttl_and_failure(self):
        bk = self.agj("models")["data"]["backends"]
        self.assertEqual(bk["opencode"]["models"], ["opencode/a", "opencode/b", "anthropic/c"])
        self.assertEqual(bk["codex"]["models"], ["gpt-x", "gpt-y"])
        self.assertEqual((bk["cursor"]["models"], bk["cursor"]["error"]), ([], ""))
        self.assertIn("fable", bk["claude"]["models"]); self.assertIn("static", bk["claude"]["source"])
        self.assertIn("gemini-2.5-pro", bk["gemini"]["models"])
        n = self.oc_calls()
        self.agj("models", "--backend", "opencode")              # fresh cache: no refetch
        self.assertEqual(self.oc_calls(), n)
        (self.fake/"oc.fail").touch()                            # failed source keeps last good
        e = self.agj("models", "--backend", "opencode", "--refresh")["data"]["backends"]["opencode"]
        self.assertEqual(e["models"], ["opencode/a", "opencode/b", "anthropic/c"]); self.assertIn("boom", e["error"])
        (self.fake/"oc.fail").unlink()
        c = json.loads((self.cache/"models.json").read_text())   # stale (> TTL) refreshes on plain read
        c["backends"]["opencode"]["checked_at"] = "2000-01-01T00:00:00+00:00"
        (self.cache/"models.json").write_text(json.dumps(c))
        (self.fake/"oc.ids").write_text("opencode/new\n")
        (self.fake/"oc.locked").touch()                          # first try locked -> one retry
        e = self.agj("models", "--backend", "opencode")["data"]["backends"]["opencode"]
        self.assertEqual((e["models"], e["error"]), (["opencode/new"], ""))
        txt = self.ag("models", "--backend", "opencode").stdout
        self.assertTrue(txt.startswith("opencode (1, opencode models, fetched"), txt)

    # ---- routing ----
    def test_route_global_project_overlay(self):
        self.cfg.mkdir()
        (self.cfg/"routing.json").write_text(json.dumps({"version": 1, "preset": "cost-first",
            "jobs": {"review": {"claude": "sonnet", "ag": {"backend": "claude", "model": "sonnet"}}}}))
        (self.proj/".agent").mkdir()
        (self.proj/".agent"/"routing.json").write_text(json.dumps({"version": 1,
            "jobs": {"review": {"ag": {"backend": "codex", "model": "gpt-x"}}}}))
        j = self.agj("route", "review")["data"]["jobs"]
        self.assertEqual(list(j), ["review"])
        self.assertEqual(j["review"]["claude"], "sonnet"); self.assertEqual(j["review"]["ag"], {"backend": "codex", "model": "gpt-x"})
        self.assertEqual(j["review"]["source"], {"claude": "global", "ag": "project"})
        allj = self.agj("route")["data"]["jobs"]
        self.assertEqual(list(allj), ["mechanical", "implement", "review", "debug", "plan", "hardest"])
        self.assertEqual(allj["hardest"]["source"]["claude"], "preset-default")

    def test_agents_add_job_uses_routing(self):
        self.agj("agents", "add", "r1", "--job", "review")
        self.agj("setup", "--yes", "--harness", "none", "--set", "debug=ag:opencode/opencode/b")
        self.agj("agents", "add", "d1", "--job", "debug")
        self.agj("agents", "add", "d2", "--job", "debug", "--model", "opencode/a")
        self.agj("agents", "add", "x1", ok=False)                 # neither --backend nor --job
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual((rows["r1"]["backend"], rows["r1"]["model"], rows["r1"]["job"]), ("claude", "opus", "review"))
        self.assertEqual((rows["d1"]["backend"], rows["d1"]["model"]), ("opencode", "opencode/b"))
        self.assertEqual(rows["d2"]["model"], "opencode/a")

    # ---- setup ----
    def test_setup_requires_yes_without_tty(self):
        self.assertIn("--yes", self.agj("setup", ok=False)["error"])

    def test_setup_dry_run_writes_nothing(self):
        (self.home/".claude").mkdir()
        snap = lambda: sorted(p for p in self.root.rglob("*") if not {self.cache, self.fake} & {p, *p.parents})  # catalog picks may fill the cache / call the fake opencode
        before = snap()
        d = self.agj("setup", "--yes", "--dry-run", "--harness", "all")["data"]
        self.assertEqual(snap(), before)
        self.assertTrue(d["dry_run"] and all(f["status"] == "new" for f in d["files"]))
        self.assertTrue(any("+| mechanical" in (f.get("diff") or "") for f in d["files"]))

    def test_setup_global_writes_and_rerun_replaces_only_block(self):
        cm = self.home/".claude"/"CLAUDE.md"; cm.parent.mkdir()
        cm.write_text("# mine\nkeep me\n")
        agents_md = self.home/"AGENTS.md"
        agents_md.write_text(f"head\n{START}\nold table\n{END}\ntail\n")
        old_skill = self.home/".claude"/"skills"/"using-ag"/"SKILL.md"; old_skill.parent.mkdir(parents=True)
        old_skill.write_text("old")
        d = self.agj("setup", "--yes", "--scope", "global", "--harness", "claude,opencode,codex")["data"]
        rt = json.loads((self.cfg/"routing.json").read_text())
        self.assertEqual((rt["version"], rt["preset"]), (2, "cost-first"))
        self.assertEqual(rt.get("profiles", {}), {})  # sparse: defaults are inherited, not frozen into the file
        sh = self.home/".agents"/"skills"
        cli = (sh/"ag-cli"/"SKILL.md").read_text()
        self.assertEqual(cli, f"---\nname: ag-cli\n---\nrun {Path(AG).resolve()} models (global) keep {{{{OTHER}}}}\n")
        ags = (sh/"ag-agents"/"SKILL.md").read_text()
        self.assertNotIn("{{ROUTING}}", ags); self.assertNotIn("{{AG}}", ags)
        self.assertTrue((sh/"ag-agents"/"SKILL.md").is_file() and not (sh/"ag-agents").is_symlink())
        for n in ("ag-cli", "ag-agents"):
            self.assertEqual(os.readlink(self.home/".claude"/"skills"/n), str(sh/n))
        for gone in (self.home/".config"/"opencode"/"skills", self.home/".codex"/"skills"): self.assertFalse(gone.exists())
        self.assertIn("| review — code review, verification | opus | claude / opus |", (self.home/".claude"/"CLAUDE.md").read_text())
        for base, fn, pat in ((".config/opencode", "AGENTS.md", "opencode / opencode/"), (".codex", "AGENTS.md", "codex / ")):
            ins = (self.home/base/fn).read_text()
            self.assertTrue(ins.startswith(START) and ins.endswith(f"{END}\n"))
            self.assertIn("| review — code review, verification | " + pat, ins); self.assertIn("Skills: ag-cli, ag-agents.", ins)
            self.assertIn(f"Profile: {base.split('/')[-1].lstrip('.')}.", ins)
        self.assertEqual(old_skill.read_text(), "old")
        self.assertTrue(any("using-ag" in n for n in d["notes"]))
        t1 = cm.read_text()
        self.assertTrue(t1.startswith(f"# mine\nkeep me\n\n{START}\nag is installed at ") and "\n## Model routing" in t1, t1)
        self.assertTrue(t1.endswith(f"{END}\n"))
        a1 = agents_md.read_text()
        self.assertTrue(a1.startswith(f"head\n{START}\n## Model routing") and a1.endswith(f"{END}\ntail\n"))
        # user edits around the block survive a re-run byte-for-byte; only the block changes
        cm.write_text("PRE é\n\n" + t1 + "POST\n  trailing  \n")
        self.agj("setup", "--yes", "--scope", "global", "--harness", "claude", "--set", "review=claude:sonnet")
        t2 = cm.read_text()
        i, j = t2.index(START), t2.index(END) + len(END)
        self.assertEqual(t2[:i], "PRE é\n\n# mine\nkeep me\n\n")
        self.assertEqual(t2[j:], "\nPOST\n  trailing  \n")
        self.assertIn("| review — code review, verification | sonnet | claude / opus |", t2[i:j])
        self.assertEqual(t2.count(START), 1)
        a2 = agents_md.read_text()
        self.assertTrue(a2.startswith(f"head\n{START}\n") and a2.endswith(f"{END}\ntail\n"))
        self.assertIn("| sonnet | claude / opus |", a2)
        # unchanged rerun -> nothing rewritten
        d3 = self.agj("setup", "--yes", "--scope", "global", "--harness", "claude", "--set", "review=claude:sonnet")["data"]
        self.assertTrue(all(f["status"] == "unchanged" for f in d3["files"]), d3["files"])

    def test_agents_md_without_markers_untouched(self):
        (self.home/"AGENTS.md").write_text("no markers here\n")
        self.agj("setup", "--yes", "--harness", "claude")
        self.assertEqual((self.home/"AGENTS.md").read_text(), "no markers here\n")
        self.assertIn(START, (self.home/".claude"/"CLAUDE.md").read_text())
        self.agj("setup", "--yes", "--harness", "claude", "--no-claude-md", "--preset", "balanced")
        self.assertIn("| review — code review, verification | opus |", (self.home/".claude"/"CLAUDE.md").read_text())

    def test_setup_project_scope_and_set_parsing(self):
        d = self.agj("setup", "--yes", "--scope", "project", "--harness", "all", "--preset", "quality-first",
            "--set", "mechanical=ag:opencode/opencode/a", "--set", "plan=claude:opus")["data"]
        rt = json.loads((self.proj/".agent"/"routing.json").read_text())
        self.assertEqual(rt["profiles"]["claude"]["jobs"]["mechanical"], {"claude": "sonnet", "ag": {"backend": "opencode", "model": "opencode/a"}})
        self.assertEqual(rt["profiles"]["claude"]["jobs"]["plan"]["claude"], "opus")
        for sub in (".claude", ".agents"):
            self.assertIn("(project)", (self.proj/sub/"skills"/"ag-cli"/"SKILL.md").read_text())
        self.assertTrue((self.proj/".claude"/"skills"/"ag-cli").is_symlink() and not (self.proj/".agents"/"skills"/"ag-cli").is_symlink())
        self.assertFalse((self.proj/".opencode").exists())
        self.assertFalse((self.home/".claude").exists() or (self.cfg/"routing.json").exists())
        self.assertEqual(self.agj("route", "plan")["data"]["jobs"]["plan"]["source"]["claude"], "project")
        for bad in ("plan=opus", "nope=claude:opus", "debug=ag:bogus/x"):
            self.assertIn("bad --set", self.agj("setup", "--yes", "--set", bad, ok=False)["error"])

    def test_setup_missing_templates_errors_but_writes_routing(self):
        o = self.agj("setup", "--yes", "--harness", "claude", ok=False, env={"AG_SKILLS_DIR": str(self.root/"none")})
        self.assertIn("skill template missing", o["error"])
        self.assertTrue((self.cfg/"routing.json").exists())
        self.assertIn(START, (self.home/".claude"/"CLAUDE.md").read_text())
        self.assertFalse((self.home/".claude"/"skills").exists())

    # ---- shared skills + instruction-file pointers ----
    def test_shared_skills_references_links_and_stale(self):
        (self.skills/"ag-agents"/"references").mkdir()
        (self.skills/"ag-agents"/"references"/"r.md").write_text("ref {{AG}} {{SCOPE}}\n")
        sh = self.home/".agents"/"skills"/"ag-agents"
        sh.mkdir(parents=True); (sh/"old.md").write_text("stale")
        self.agj("setup", "--yes", "--harness", "claude,amp")
        self.assertEqual((sh/"references"/"r.md").read_text(), f"ref {Path(AG).resolve()} global\n")
        self.assertFalse((sh/"old.md").exists())  # ag owns this dir: files the template no longer has go
        for base in (self.home/".claude"/"skills", self.home/".config"/"agents"/"skills"):
            self.assertEqual(os.readlink(base/"ag-agents"), str(sh))
            self.assertTrue((base/"ag-agents"/"references"/"r.md").is_file())
        d = self.agj("setup", "--yes", "--harness", "claude,amp", "--dry-run")["data"]
        self.assertTrue(all(f["status"] == "unchanged" for f in d["files"]), d["files"])

    def test_dry_run_shows_links_and_removals(self):
        leg = self.home/".codex"/"skills"/"ag-cli"; leg.mkdir(parents=True); (leg/"SKILL.md").write_text("---\nname: ag-cli\n---\nold\n")
        d = self.agj("setup", "--yes", "--harness", "claude,codex", "--dry-run")["data"]
        ops = {(f.get("op"), f["path"]) for f in d["files"] if f.get("op")}
        self.assertIn(("link", str(self.home/".claude"/"skills"/"ag-cli")), ops)
        self.assertIn(("rmdir", str(leg)), ops)
        self.assertTrue(leg.exists() and not (self.home/".claude"/"skills").exists())

    def test_legacy_copies_removed_foreign_untouched(self):
        oc, cx = self.home/".config"/"opencode"/"skills", self.home/".codex"/"skills"
        for base in (oc, cx):
            for n in ("ag-cli", "ag-agents"):
                (base/n).mkdir(parents=True, exist_ok=True); (base/n/"SKILL.md").write_text(f"---\nname: {n}\n---\nold copy\n")
        (cx/"ag-cli"/"extra.md").write_text("mine")           # not a plain ag copy
        (oc/"other").mkdir(); (oc/"other"/"SKILL.md").write_text("---\nname: other\n---\n")
        cl = self.home/".claude"/"skills"/"ag-cli"; cl.mkdir(parents=True); (cl/"SKILL.md").write_text("---\nname: ag-cli\n---\nprev ag copy\n")
        cl2 = self.home/".claude"/"skills"/"ag-agents"; cl2.mkdir(); (cl2/"SKILL.md").write_text("---\nname: something-else\n---\n")
        d = self.agj("setup", "--yes", "--harness", "claude,opencode,codex")["data"]
        self.assertFalse((oc/"ag-cli").exists() or (oc/"ag-agents").exists() or (cx/"ag-agents").exists())
        self.assertTrue((cx/"ag-cli"/"extra.md").exists() and (oc/"other"/"SKILL.md").exists())
        self.assertTrue(any(str(cx/"ag-cli") in n for n in d["notes"]))
        self.assertTrue(cl.is_symlink())                       # earlier ag copy replaced by the link
        self.assertTrue(not cl2.is_symlink() and "something-else" in (cl2/"SKILL.md").read_text())
        self.assertTrue(any(str(cl2) in n for n in d["notes"]))

    def test_pointer_blocks_in_each_harness_file(self):
        files = {".codex/AGENTS.md": "codex", ".config/opencode/AGENTS.md": "opencode", ".gemini/GEMINI.md": "gemini"}
        for f in list(files)[:2]:
            (self.home/f).parent.mkdir(parents=True, exist_ok=True); (self.home/f).write_text("# mine é\nkeep\n")
        self.agj("setup", "--yes", "--harness", "codex,opencode,gemini,cursor")
        after = {}
        for f, prof in files.items():
            t = (self.home/f).read_text(); after[f] = t
            self.assertEqual((t.count(START), t.count(END)), (1, 1))
            self.assertIn(f"ag is installed at {Path(AG).resolve()}", t); self.assertIn("`ag wake`", t); self.assertIn("Skills: ag-cli, ag-agents.", t)
            self.assertIn(f"Profile: {'default' if prof == 'gemini' else prof}.", t)
        for f in list(files)[:2]: self.assertTrue(after[f].startswith("# mine é\nkeep\n\n" + START))
        self.assertFalse((self.home/".claude"/"CLAUDE.md").exists())
        self.assertTrue((self.home/".agents"/"skills"/"ag-cli"/"SKILL.md").is_file() and not (self.home/".claude"/"skills").exists())
        # text around the markers survives; re-run changes nothing
        g = self.home/".gemini"/"GEMINI.md"; g.write_text("TOP\n" + after[".gemini/GEMINI.md"] + "BOTTOM\n"); before = g.read_text()
        d = self.agj("setup", "--yes", "--harness", "codex,opencode,gemini,cursor")["data"]
        self.assertTrue(all(f["status"] == "unchanged" for f in d["files"]), d["files"]); self.assertEqual(g.read_text(), before)

    def test_unknown_harness_error_lists_all(self):
        e = self.agj("setup", "--yes", "--harness", "nope", ok=False)["error"]
        for h in ("gemini", "cursor", "amp", "copilot"): self.assertIn(h, e)

    def test_project_scope_layout_and_legacy(self):
        leg = self.proj/".opencode"/"skills"/"ag-cli"; leg.mkdir(parents=True); (leg/"SKILL.md").write_text("---\nname: ag-cli\n---\nold\n")
        self.agj("setup", "--yes", "--scope", "project", "--harness", "claude,opencode,codex")
        self.assertFalse(leg.exists())
        self.assertTrue((self.proj/".agents"/"skills"/"ag-agents"/"SKILL.md").is_file())
        self.assertEqual(os.readlink(self.proj/".claude"/"skills"/"ag-cli"), str(self.proj/".agents"/"skills"/"ag-cli"))
        self.assertFalse((self.home/".agents").exists() or (self.home/".codex"/"AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
