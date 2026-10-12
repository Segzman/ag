#!/usr/bin/env python3
"""Access control + usage/billing: policy globs, ledger, budgets.
Run: python3 tests/test_ag_access.py
"""
import json, os, subprocess, sys, tempfile, unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent / "ag"))
FAKE_OPENCODE = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/launched.log"
if [ "$1" = models ]; then cat "$FAKE_DIR/oc.ids"; exit 0; fi
echo '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"hello"}}}'
echo '{"type":"step_finish","sessionID":"ses_1","part":{"tokens":{"input":100,"output":50,"reasoning":5,"cache":{"read":10,"write":2}},"cost":0.02}}'
"""
IDS = ["opencode/space-bunny-free", "opencode/muse-spark-1.3-contributor-free"]


class Access(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cfg", "cache", "state"): (r / d).mkdir(parents=True)
        (r / "fake" / "opencode").write_text(FAKE_OPENCODE); (r / "fake" / "opencode").chmod(0o755)
        (r / "fake" / "oc.ids").write_text("\n".join(IDS) + "\n")
        (r / "home/.codex/models_cache.json").write_text(json.dumps({"models": []}))
        subprocess.run(["git", "init", "-q", str(r / "proj")], check=True)
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"),
            AG_CACHE_HOME=str(r / "cache"), AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0",
            AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_ZEN_URL="off", HOME=str(r / "home"),
            PATH=f"{r / 'fake'}:/usr/bin:/bin", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json",
            AG_OPENROUTER_RANKINGS_URL="off", FAKE_DIR=str(r / "fake"))
        self.env.pop("AG_PRIVACY", None)

    def run_ag(self, *args, env=None):
        return subprocess.run([sys.executable, AG, *args], capture_output=True, text=True, timeout=60,
            cwd=self.root / "proj", env={**self.env, **(env or {})}, stdin=subprocess.DEVNULL)

    def agj(self, *args, env=None, ok=True):
        p = self.run_ag("--json", *args, env=env)
        o = json.loads(p.stdout)
        self.assertEqual(o["ok"], ok, (args, p.stdout, p.stderr))
        return o

    def lib(self, **env):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update({**self.env, **env})
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        return SourceFileLoader(f"ag_acc_{id(self)}", AG).load_module()

    def test_pure_match(self):
        ag = self.lib()
        self.assertEqual(ag._access_clean({"allow": [" a ", "a", 1]}), {"allow": ["a"], "deny": []})
        ok, pat = ag.access_match("opencode", "opencode/m", ["opencode/*"], [])
        self.assertTrue(ok)
        ok, _ = ag.access_match("opencode", "opencode/m", [], ["opencode/*"])
        self.assertFalse(ok)
        ok, _ = ag.access_match("opencode", "opencode/m", ["claude/*"], [])
        self.assertFalse(ok)
        ok, _ = ag.access_match("claude", "opus", [], [])
        self.assertTrue(ok)
        self.assertIn("opencode/space-bunny-free", ag._access_candidates("opencode", "default"))
        self.assertIsNone(ag.access_error("echo", "default", "work"))
        self.assertIsNotNone(ag._usage_parse_since("7d"))
        try: ag._usage_parse_since("nope"); self.fail("bad since")
        except ValueError: pass
        self.assertEqual(ag.usage_provider("opencode", "opencode/x"), "opencode")
        c, sub = ag.usage_cost("claude", "opus", {})
        self.assertIsNone(c); self.assertTrue(sub)

    def test_access_cli_scope_data(self):
        self.agj("access", "allow", "opencode/*", "--scope", "global")
        g = json.loads((self.root / "cfg" / "routing.json").read_text())
        self.assertIn("opencode/*", g["access"]["allow"])
        self.agj("access", "deny", "opencode/bad-*", "--scope", "global", "--data", "work")
        g = json.loads((self.root / "cfg" / "routing.json").read_text())
        self.assertIn("opencode/bad-*", g["access"]["work"]["deny"])
        d = self.agj("access", "show")["data"]
        self.assertIn("work", d)
        o = self.agj("access", "rm", "opencode/*", "--scope", "global", ok=True)
        g = json.loads((self.root / "cfg" / "routing.json").read_text())
        self.assertNotIn("opencode/*", g.get("access", {}).get("allow", []))
        p = self.run_ag("access", "allow", "x", "--scope", "bogus")
        self.assertNotEqual(p.returncode, 0)

    def test_access_enforced_add_set_turn(self):
        self.agj("access", "deny", "opencode/*", "--scope", "global")
        o = self.agj("agents", "add", "w1", "--backend", "opencode", "--model", "opencode/space-bunny-free", ok=False)
        self.assertIn("blocked by access policy", o["error"])
        self.agj("access", "rm", "opencode/*", "--scope", "global")
        self.agj("agents", "add", "w1", "--backend", "echo", "--model", "default")
        self.agj("access", "deny", "echo/*", "--scope", "global")
        o = self.agj("agents", "set", "w1", "--model", "other", ok=False)
        self.assertIn("blocked by access policy", o["error"])
        launched = self.root / "fake" / "launched.log"
        p = self.run_ag("--json", "chat", "send", "w1", "hi")
        o = json.loads(p.stdout)
        self.assertFalse(o["ok"]); self.assertIn("blocked by access policy", o["error"])
        self.assertEqual(p.returncode, 2)
        log = self.agj("chat", "log", "w1")["data"]["log"]
        self.assertTrue(any(r.get("role") == "error" and "blocked by access policy" in r.get("text", "") for r in log))
        p = self.run_ag("--json", "wake", "w1", "hi")
        self.assertIn("blocked by access policy", json.loads(p.stdout)["error"])

    def test_allow_list_restricts(self):
        self.agj("access", "allow", "echo/*", "--scope", "global")
        o = self.agj("agents", "add", "a1", "--backend", "opencode", "--model", "opencode/space-bunny-free", ok=False)
        self.assertIn("not allowed by access policy", o["error"])
        self.agj("agents", "add", "a1", "--backend", "echo", "--model", "default")

    def test_usage_ledger_and_command(self):
        self.agj("agents", "add", "u1", "--backend", "opencode", "--model", "opencode/space-bunny-free", "--data", "public")
        p = self.run_ag("--json", "chat", "send", "u1", "hi")
        self.assertTrue(json.loads(p.stdout)["ok"], (p.stdout, p.stderr))
        rows = (self.root / "cache" / "usage.jsonl").read_text().splitlines()
        self.assertTrue(rows)
        r = json.loads(rows[-1])
        for k in ("ts", "agent", "project", "backend", "model", "provider", "requests", "exit"):
            self.assertIn(k, r)
        self.assertEqual((r["agent"], r["requests"]), ("u1", 1))
        self.assertEqual(r["in"], 112)  # 100 + read 10 + write 2
        self.assertEqual(r["out"], 50)
        d = self.agj("usage")["data"]
        self.assertEqual(d["total"]["requests"], 1)
        d = self.agj("usage", "--by", "agent")["data"]
        self.assertIn("u1", d["groups"])
        d = self.agj("usage", "--since", "1d")["data"]
        self.assertEqual(d["total"]["requests"], 1)
        bad = self.agj("usage", "--since", "nope", ok=False)
        self.assertIn("bad --since", bad["error"])
        self.assertIn("usage", self.run_ag("usage").stdout)

    def test_usage_rpd_pct(self):
        (self.root / "cfg").mkdir(exist_ok=True)
        (self.root / "cfg" / "providers.json").write_text(json.dumps({"opencode": {"rpd": 10}}))
        self.agj("agents", "add", "u1", "--backend", "opencode", "--model", "opencode/space-bunny-free", "--data", "public")
        self.run_ag("--json", "chat", "send", "u1", "hi")
        d = self.agj("usage")["data"]
        self.assertEqual(d["rpd"].get("opencode"), 10)
        txt = self.run_ag("usage").stdout
        self.assertIn("1/10", txt)

    def test_budget_set_show_enforce_force(self):
        self.agj("agents", "add", "b1", "--backend", "opencode", "--model", "my-paid", "--data", "public")
        self.agj("budget", "set", "--daily", "0.01")
        d = self.agj("budget", "show")["data"]
        self.assertEqual(d["daily"], 0.01)
        self.run_ag("--json", "chat", "send", "b1", "hi")  # cost 0.02, over daily
        p = self.run_ag("--json", "chat", "send", "b1", "hi")
        o = json.loads(p.stdout)
        self.assertFalse(o["ok"]); self.assertIn("daily budget exceeded", o["error"])
        self.assertEqual(p.returncode, 2)
        p = self.run_ag("--json", "chat", "send", "b1", "hi", "--force")
        self.assertTrue(json.loads(p.stdout)["ok"], (p.stdout, p.stderr))
        bad = self.agj("budget", "set", "--daily", "-1", ok=False)
        self.assertIn("bad --daily", bad["error"])
        bad = self.agj("budget", "set", "--provider", "P", ok=False)
        self.assertIn("need both", bad["error"])

    def test_budget_provider_rpd(self):
        self.agj("agents", "add", "b1", "--backend", "opencode", "--model", "my-paid", "--data", "public")
        self.agj("budget", "set", "--provider", "opencode", "--rpd", "1")
        self.run_ag("--json", "chat", "send", "b1", "hi")
        p = self.run_ag("--json", "chat", "send", "b1", "hi")
        o = json.loads(p.stdout)
        self.assertFalse(o["ok"]); self.assertIn("daily limit reached", o["error"])
        p = self.run_ag("--json", "chat", "send", "b1", "hi", "--force")
        self.assertTrue(json.loads(p.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
