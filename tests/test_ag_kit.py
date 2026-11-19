#!/usr/bin/env python3
"""ag kit: token-saving toolkit delivered per agent (codegraph/ctx/rtk/style).
Stdlib, temp dirs, stub binaries on a temp PATH, no network, no real backends."""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AG = str(ROOT / "ag")
STUB_TOOLS = ("codegraph", "context-mode", "rtk", "toksave")


class Env(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-kit-t-"))
        self.home = self.td / "home"
        self.cfg = self.home / "cfg"
        self.cache = self.home / "cache"
        self.bin = self.td / "bin"
        for x in (self.home, self.cfg, self.cache, self.bin, self.td / "state"):
            x.mkdir(parents=True)
        self.calls = self.td / "calls.log"
        # controlled PATH: stubs first, system dirs for git/printf, homebrew
        # hidden so real codegraph/toksave/rtk never leak into install checks
        self.env = dict(os.environ, AG_HOME=str(self.home), AG_CONFIG_HOME=str(self.cfg),
            AG_CACHE_HOME=str(self.cache), AG_AUTO_UPDATE="0", AG_KIT_WT="fg",
            PATH=f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin")
        self.env.pop("AG_KIT", None)  # conftest sets none; these tests exercise the real default
        self.addCleanup(shutil.rmtree, self.td, True)

    # -- helpers -------------------------------------------------------------
    def stub(self, *names, log=True):
        for n in names:
            p = self.bin / n
            body = f'#!/bin/sh\n' + (f'echo "{n} $*" >> {sh_q(str(self.calls))}\n' if log else "") + 'exit 0\n'
            p.write_text(body)
            p.chmod(0o755)

    def run_ag(self, *a, timeout=60, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.td / "state"), *a],
            capture_output=True, text=True, env=self.env, timeout=timeout)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def j(self, *a, timeout=60, ok=True):
        p = self.run_ag("--json", *a, timeout=timeout, ok=ok)
        return p, (json.loads(p.stdout) if p.stdout.strip() else {})

    def add(self, name, backend="echo", extra=()):
        self.run_ag("agents", "add", name, "--backend", backend, *extra)

    def kit_dir(self, name):
        return self.td / "state" / "kit" / name

    def conf(self):
        return json.loads((self.cfg / "config.json").read_text())

    def events(self):
        p = self.td / "state" / "events.jsonl"
        if not p.exists(): return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def sh_q(s):
    return "'" + s.replace("'", "'\\''") + "'"


class KitCli(Env):
    def test_list_status_on_off_default(self):
        self.stub(*STUB_TOOLS)
        p, d = self.j("kit", "list")
        tools = {t["tool"]: t for t in d["data"]["tools"]}
        self.assertEqual(sorted(tools), ["codegraph", "ctx", "rtk", "style"])
        self.assertTrue(all(tools[t]["installed"] for t in tools))
        self.assertEqual(d["data"]["default"], "all")
        p, d = self.j("kit", "on")
        self.assertEqual(self.conf()["kit"]["default"], "all")
        p, d = self.j("kit", "on", "--tools", "codegraph,style")
        self.assertEqual(self.conf()["kit"]["default"], "codegraph,style")
        self.assertIn("codegraph,style", p.stdout)
        p, d = self.j("kit", "status")
        self.assertEqual(d["data"]["default"], "codegraph,style")
        self.run_ag("kit", "off")
        self.assertEqual(self.conf()["kit"]["default"], "none")
        p, d = self.j("kit", "status")
        self.assertEqual(d["data"]["default"], "none")

    def test_on_rejects_unknown_tool(self):
        self.stub(*STUB_TOOLS)
        p, d = self.j("kit", "on", "--tools", "bogus,style", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("unknown tool", p.stdout + p.stderr)

    def test_list_reports_missing_binaries(self):
        p, d = self.j("kit", "list")
        tools = {t["tool"]: t for t in d["data"]["tools"]}
        self.assertFalse(tools["codegraph"]["installed"])
        self.assertFalse(tools["ctx"]["installed"])
        self.assertFalse(tools["rtk"]["installed"])
        self.assertTrue(tools["style"]["installed"])  # text-only, always present

    def test_skipped_tools_reported(self):
        self.add("e1")  # no stubs on PATH: codegraph/ctx/rtk missing
        p, d = self.j("kit", "status")
        row = next(a for a in d["data"]["agents"] if a["name"] == "e1")
        self.assertEqual(row["tools"], ["style"])
        self.assertEqual(row["skipped"], ["codegraph", "ctx", "rtk"])


class KitPerAgent(Env):
    def test_agents_set_kit(self):
        self.stub(*STUB_TOOLS)
        self.add("e1")
        self.run_ag("agents", "set", "e1", "--kit", "codegraph,ctx")
        p, d = self.j("kit", "status")
        row = next(a for a in d["data"]["agents"] if a["name"] == "e1")
        self.assertEqual(row["spec"], "codegraph,ctx")
        self.assertEqual(row["tools"], ["codegraph", "ctx"])
        self.run_ag("agents", "set", "e1", "--kit", "none")
        p, d = self.j("kit", "status")
        row = next(a for a in d["data"]["agents"] if a["name"] == "e1")
        self.assertEqual(row["spec"], "none")
        self.assertEqual(row["tools"], [])

    def test_agents_set_kit_clear_falls_back_to_config(self):
        self.stub(*STUB_TOOLS)
        self.run_ag("kit", "on", "--tools", "style")
        self.add("e1")
        self.run_ag("agents", "set", "e1", "--kit", "codegraph")
        self.run_ag("agents", "set", "e1", "--kit", "")
        p, d = self.j("kit", "status")
        row = next(a for a in d["data"]["agents"] if a["name"] == "e1")
        self.assertEqual(row["spec"], "style")  # config default wins after clear
        self.assertEqual(row["tools"], ["style"])

    def test_agents_set_kit_rejects_unknown(self):
        self.add("e1")
        p = self.run_ag("agents", "set", "e1", "--kit", "bogus", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("bad --kit", p.stdout + p.stderr)

    def test_config_none_default_agent_all_wins(self):
        self.stub(*STUB_TOOLS)
        self.run_ag("kit", "off")
        self.add("e1")
        self.run_ag("agents", "set", "e1", "--kit", "all")
        p, d = self.j("kit", "status")
        row = next(a for a in d["data"]["agents"] if a["name"] == "e1")
        self.assertEqual(row["tools"], ["codegraph", "ctx", "rtk", "style"])

    def test_fork_inherits_kit(self):
        self.stub(*STUB_TOOLS)
        self.add("e1")
        self.run_ag("agents", "set", "e1", "--kit", "codegraph,ctx")
        self.run_ag("agents", "fork", "e1", "f1")
        p, d = self.j("kit", "status")
        row = next(a for a in d["data"]["agents"] if a["name"] == "f1")
        self.assertEqual(row["spec"], "codegraph,ctx")
        self.assertEqual(row["tools"], ["codegraph", "ctx"])


class KitScopedConfigs(Env):
    """Generated config contents per backend, asserted from the scoped files."""

    def test_claude_mcp_json_toksave_proxy(self):
        self.stub(*STUB_TOOLS)
        self.add("c1", backend="claude")
        self.run_ag("kit", "doctor", "--agent", "c1")
        cfg = json.loads((self.kit_dir("c1") / "claude_mcp.json").read_text())
        cg, cm = cfg["mcpServers"]["codegraph"], cfg["mcpServers"]["context-mode"]
        self.assertEqual(cg["command"], str(self.bin / "toksave"))
        self.assertEqual(cg["args"], ["runmcp", "codegraph", "serve", "--mcp"])
        self.assertEqual(cm["args"], ["runmcp", "context-mode", "server", "--agent", "claude-code"])

    def test_direct_binaries_when_toksave_absent(self):
        self.stub("codegraph", "context-mode", "rtk")  # no toksave on PATH
        self.add("c1", backend="claude")
        self.run_ag("kit", "doctor", "--agent", "c1")
        cfg = json.loads((self.kit_dir("c1") / "claude_mcp.json").read_text())
        self.assertEqual(cfg["mcpServers"]["codegraph"]["command"], str(self.bin / "codegraph"))
        self.assertEqual(cfg["mcpServers"]["codegraph"]["args"], ["serve", "--mcp"])
        self.assertEqual(cfg["mcpServers"]["context-mode"]["args"], ["server", "--agent", "claude-code"])

    def test_opencode_mcp_section(self):
        self.stub(*STUB_TOOLS)
        self.add("o1", backend="opencode")
        self.run_ag("kit", "doctor", "--agent", "o1")
        cfg = json.loads((self.kit_dir("o1") / "opencode.json").read_text())
        self.assertEqual(cfg["$schema"], "https://opencode.ai/config.json")
        for name in ("codegraph", "context-mode"):
            self.assertEqual(cfg["mcp"][name]["type"], "local")
            self.assertTrue(cfg["mcp"][name]["enabled"])
        self.assertEqual(cfg["mcp"]["context-mode"]["command"][-1], "opencode")  # --agent id

    def test_codex_toml_servers(self):
        self.stub(*STUB_TOOLS)
        self.add("x1", backend="codex")
        self.run_ag("kit", "doctor", "--agent", "x1")
        toml = (self.kit_dir("x1") / "codex-home" / "config.toml").read_text()
        self.assertIn('[mcp_servers."codegraph"]', toml)
        self.assertIn('[mcp_servers."context-mode"]', toml)
        self.assertIn('"runmcp", "codegraph", "serve", "--mcp"', toml)
        self.assertIn('"--agent", "codex"', toml)

    def test_gemini_cursor_todo_skip(self):
        self.stub(*STUB_TOOLS)
        self.add("g1", backend="gemini")
        self.add("u1", backend="cursor")
        p, d = self.j("kit", "doctor", "--agent", "g1")
        rows = {t["tool"]: t for t in d["data"]["agents"][0]["tools"]}
        self.assertFalse(rows["codegraph"]["ok"])
        self.assertIn("TODO", rows["codegraph"]["status"])
        self.assertTrue(rows["style"]["ok"])  # style still delivered via prompt

    def test_doctor_regenerates_and_reports_reach(self):
        self.stub(*STUB_TOOLS)
        self.add("c1", backend="claude")
        p, d = self.j("kit", "doctor", "--agent", "c1")
        rows = {t["tool"]: t for t in d["data"]["agents"][0]["tools"]}
        self.assertTrue(all(rows[t]["ok"] for t in ("codegraph", "ctx", "style")))

    def test_kit_off_generates_no_files(self):
        self.stub(*STUB_TOOLS)
        self.add("c1", backend="claude")
        self.run_ag("agents", "set", "c1", "--kit", "none")
        self.run_ag("kit", "doctor", "--agent", "c1")
        self.assertFalse((self.kit_dir("c1") / "claude_mcp.json").exists())

    def test_profile_and_kit_merge_into_one_config(self):
        self.stub(*STUB_TOOLS)
        pmcp = self.td / "pmcp.json"
        pmcp.write_text(json.dumps({"mcpServers": {"linear": {"command": "npx", "args": ["-y", "linear-mcp"]}}}))
        self.run_ag("harness", "profile", "add", "docs", "--mcp", str(pmcp))
        self.add("c2", backend="claude")
        self.run_ag("harness", "profile", "set", "c2", "--profile", "docs")
        self.run_ag("kit", "doctor", "--agent", "c2")
        cfg = json.loads((self.kit_dir("c2") / "claude_mcp.json").read_text())
        self.assertEqual(sorted(cfg["mcpServers"]), ["codegraph", "context-mode", "linear"])
        # one --mcp-config flag total: kit file carries the profile servers
        kf = {"claude_mcp": str(self.kit_dir("c2") / "claude_mcp.json")}
        kit = {"tools": ["codegraph", "ctx"], "skipped": []}
        argv = ag_module()._turn_argv(self.td / "state", {"name": "c2", "backend": "claude", "dir": "."},
            "claude", "default", ".", "", "hi", "sys", "", "docs", kit, kf)
        self.assertEqual(argv.count("--mcp-config"), 1)


def ag_module():
    loader = SourceFileLoader("_ag_kit_mod", AG)
    spec = importlib.util.spec_from_loader("_ag_kit_mod", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class KitStyle(Env):
    def test_style_block_in_echo_turn(self):
        self.stub(*STUB_TOOLS)
        self.add("e1")
        self.run_ag("chat", "send", "e1", "hi", timeout=90)
        p = self.run_ag("chat", "log", "e1", "--last")
        self.assertIn("Token-saving working style", p.stdout)
        self.assertIn("caveman", p.stdout)

    def test_kit_none_no_style(self):
        self.add("e1")
        self.run_ag("agents", "set", "e1", "--kit", "none")
        self.run_ag("chat", "send", "e1", "hi", timeout=90)
        p = self.run_ag("chat", "log", "e1", "--last")
        self.assertNotIn("Token-saving working style", p.stdout)

    def test_toksave_block_preferred_over_builtin(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "AGENTS.md").write_text(
            "ignore\n<!-- TOKSAVE:caveman,ponytail,principles:START -->\n"
            "CUSTOM-STYLE-MARKER-XYZ\n<!-- TOKSAVE:END -->\ntail\n")
        self.add("e1")
        self.run_ag("chat", "send", "e1", "hi", timeout=90)
        p = self.run_ag("chat", "log", "e1", "--last")
        self.assertIn("CUSTOM-STYLE-MARKER-XYZ", p.stdout)

    def test_preloaded_global_not_duplicated(self):
        (self.home / ".config" / "opencode").mkdir(parents=True)
        (self.home / ".config" / "opencode" / "AGENTS.md").write_text(
            "<!-- TOKSAVE:caveman:START -->\nX\n<!-- TOKSAVE:END -->\n")
        self.add("o1", backend="opencode")
        p, d = self.j("kit", "doctor", "--agent", "o1")
        rows = {t["tool"]: t for t in d["data"]["agents"][0]["tools"]}
        self.assertTrue(rows["style"]["ok"])
        self.assertIn("preloaded", rows["style"]["status"])

    def test_project_agents_md_preloaded_not_duplicated(self):
        proj = self.td / "proj"
        proj.mkdir()
        (proj / "AGENTS.md").write_text("<!-- TOKSAVE:caveman:START -->\nX\n<!-- TOKSAVE:END -->\n")
        self.add("g1", backend="gemini")
        self.run_ag("agents", "set", "g1", "--dir", str(proj))
        p, d = self.j("kit", "doctor", "--agent", "g1")
        rows = {t["tool"]: t for t in d["data"]["agents"][0]["tools"]}
        self.assertIn("preloaded", rows["style"]["status"])


class KitLedger(Env):
    def test_turn_tagged_kit_on_with_tools(self):
        self.stub(*STUB_TOOLS)
        self.add("e1")
        self.run_ag("chat", "send", "e1", "hi", timeout=90)
        ev = [e for e in self.events() if e["type"] == "chat"]
        self.assertTrue(ev)
        self.assertEqual(ev[-1]["kit"], "on")
        self.assertEqual(ev[-1]["kit_tools"], "codegraph,ctx,rtk,style")
        rows = [json.loads(l) for l in (self.cache / "usage.jsonl").read_text().splitlines() if l.strip()]
        self.assertTrue(rows)
        self.assertEqual(rows[-1]["kit"], "on")
        self.assertEqual(rows[-1]["kit_tools"], "codegraph,ctx,rtk,style")

    def test_turn_tagged_kit_off(self):
        self.add("e1")
        self.run_ag("agents", "set", "e1", "--kit", "none")
        self.run_ag("chat", "send", "e1", "hi", timeout=90)
        ev = [e for e in self.events() if e["type"] == "chat"]
        self.assertEqual(ev[-1]["kit"], "off")
        self.assertEqual(ev[-1]["kit_tools"], "")


class KitWorktree(Env):
    def repo(self, with_index):
        r = self.td / "repo"
        r.mkdir()
        subprocess.run(["git", "-C", str(r), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(r), "commit", "-q", "--allow-empty", "-m", "i"], check=True)
        if with_index:
            (r / ".codegraph").mkdir()
            (r / ".codegraph" / "db.sqlite").write_text("idx")
        return r

    def test_index_copied_and_synced(self):
        self.stub("codegraph", "context-mode", "rtk")
        r = self.repo(with_index=True)
        self.run_ag("agents", "add", "w1", "--backend", "echo", "--dir", str(r), "--worktree")
        wt = r.parent / f"{r.name}.ag-wt" / "w1"
        self.assertEqual((wt / ".codegraph" / "db.sqlite").read_text(), "idx")
        calls = self.calls.read_text()
        self.assertIn("sync", calls)
        ev = [e for e in self.events() if e["type"] == "kit_wt"]
        self.assertEqual(ev[-1]["action"], "copy+sync")

    def test_no_index_runs_init(self):
        self.stub("codegraph", "context-mode", "rtk")
        r = self.repo(with_index=False)
        self.run_ag("agents", "add", "w2", "--backend", "echo", "--dir", str(r), "--worktree")
        self.assertIn("init", self.calls.read_text())
        ev = [e for e in self.events() if e["type"] == "kit_wt"]
        self.assertEqual(ev[-1]["action"], "init")

    def test_kit_none_no_seeding(self):
        self.stub("codegraph", "context-mode", "rtk")
        r = self.repo(with_index=True)
        self.run_ag("kit", "off")
        self.run_ag("agents", "add", "w3", "--backend", "echo", "--dir", str(r), "--worktree")
        self.assertFalse(self.calls.exists())
        wt = r.parent / f"{r.name}.ag-wt" / "w3"
        self.assertFalse((wt / ".codegraph").exists())

    def test_no_codegraph_binary_skips(self):
        # no stubs + homebrew hidden: codegraph filtered out of the effective
        # kit (reported by `ag kit status`), so the worktree hook is a silent no-op
        r = self.repo(with_index=True)
        self.run_ag("agents", "add", "w4", "--backend", "echo", "--dir", str(r), "--worktree")
        ev = self.events()
        self.assertEqual([e for e in ev if e["type"] == "kit_wt"], [])
        wt = r.parent / f"{r.name}.ag-wt" / "w4"
        self.assertFalse((wt / ".codegraph").exists())


if __name__ == "__main__":
    unittest.main()
