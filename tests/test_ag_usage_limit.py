#!/usr/bin/env python3
"""Usage/spend-limit detection, shared cooldown, cross-backend failover (fake claude/codex on PATH).
Run: python3 tests/test_ag_usage_limit.py
"""
import json, os, subprocess, sys, tempfile, time, unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent / "ag"))
MSG = "You've hit your individual spend limit · run /usage-credits to ask your admin for a higher limit · your 5-hour limit resets 7:40pm (America/Toronto)"
FAKE_CLAUDE = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/claude.log"
if [ -f "$FAKE_DIR/claude.stderr" ]; then cat "$FAKE_DIR/claude.stderr" >&2; exit 1; fi
if [ -f "$FAKE_DIR/claude.limit" ]; then
  echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
  python3 -c 'import json,sys; print(json.dumps({"type":"result","subtype":"success","is_error":True,"result":sys.argv[1],"total_cost_usd":0}))' "$(cat "$FAKE_DIR/claude.limit")"
  exit 1
fi
echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"claude ok"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"claude ok","total_cost_usd":0}'
"""
FAKE_CODEX = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/codex.log"
echo '{"type":"thread.started","thread_id":"cx_1"}'
echo '{"type":"item.completed","item":{"type":"agent_message","text":"codex ok"}}'
echo '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}'
"""


class UsageLimit(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cfg", "cache", "state"): (r / d).mkdir(parents=True)
        for n, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
            (r / "fake" / n).write_text(body); (r / "fake" / n).chmod(0o755)
        (r / "home/.codex/models_cache.json").write_text(json.dumps({"models": []}))
        subprocess.run(["git", "init", "-q", str(r / "proj")], check=True)
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"),
            AG_CACHE_HOME=str(r / "cache"), AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0",
            AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_ZEN_URL="off", HOME=str(r / "home"),
            PATH=f"{r / 'fake'}:{os.path.dirname(sys.executable)}:/usr/bin:/bin", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json",
            AG_OPENROUTER_RANKINGS_URL="off", FAKE_DIR=str(r / "fake"), AG_PRIVACY="off")
        self.fake = r / "fake"

    def run_ag(self, *args):
        return subprocess.run([sys.executable, AG, *args], capture_output=True, text=True, timeout=90,
            cwd=self.root / "proj", env=self.env, stdin=subprocess.DEVNULL)

    def agj(self, *args, ok=True):
        p = self.run_ag("--json", *args)
        o = json.loads(p.stdout)
        self.assertEqual(o["ok"], ok, (args, p.stdout, p.stderr))
        return o

    def send(self, text):
        p = self.run_ag("--json", "chat", "send", "w", text)
        o = json.loads(p.stdout)
        return {**o.get("data", {}), **({"error": o["error"]} if "error" in o else {})}

    def launches(self, n):  # one entry per CLI launch (prompts span lines)
        f = self.fake / f"{n}.log"
        return [x for x in f.read_text().split("launched ")[1:]] if f.exists() else []

    def log(self): return self.agj("chat", "log", "w", "--limit", "50")["data"]["log"]
    def tools(self): return [r["text"] for r in self.log() if r["role"] == "tool"]
    def add(self, *extra): self.agj("agents", "add", "w", "--backend", "claude", "--dir", str(self.root / "proj"), *extra)

    def lib(self):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update(self.env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        return _load_src(SourceFileLoader(f"ag_ul_{id(self)}", AG))

    def test_detect_and_reset_parse(self):
        ag = self.lib()
        now = time.time()
        i = ag.usage_limit_scan("claude", MSG, now=now)
        self.assertEqual(i["when"], "19:40 America/Toronto")
        self.assertEqual(i["label"], "claude usage limit (resets 19:40 America/Toronto)")
        self.assertTrue(now < i["until"] <= now + 86400)
        from zoneinfo import ZoneInfo
        from datetime import datetime
        d = datetime.fromtimestamp(i["until"], ZoneInfo("America/Toronto")); self.assertEqual((d.hour, d.minute), (19, 40))
        self.assertEqual(ag.usage_limit_scan("claude", "usage limit, resets at 19:40", now=now)["when"], "19:40")
        i = ag.usage_limit_scan("claude", "Claude usage limit reached. Try again in 3h.", now=now)
        self.assertAlmostEqual(i["until"], now + 3 * 3600, delta=1)
        i = ag.usage_limit_scan("claude", "5-hour limit reached", now=now)
        self.assertAlmostEqual(i["until"], now + 3600, delta=1); self.assertIn("unknown", i["label"])
        self.assertIsNotNone(ag.usage_limit_scan("codex", "You've hit your usage limit"))
        self.assertIsNotNone(ag.usage_limit_scan("gemini", "429 RESOURCE_EXHAUSTED: Quota exceeded for metric"))
        self.assertIsNone(ag.usage_limit_scan("claude", "all good", "", None))
        self.assertEqual(ag._fb_split("codex/default", "claude"), ("codex", "default"))
        self.assertEqual(ag._fb_split("codex:gpt-5", "claude"), ("codex", "gpt-5"))
        self.assertIsNone(ag._fb_split("claude/sonnet", "claude")); self.assertIsNone(ag._fb_split("opus", "claude"))
        a = {"claude_config_dir": "/x/acct2"}
        self.assertNotEqual(ag._ul_key(a, "claude"), ag._ul_key({}, "claude")); self.assertEqual(ag._ul_key(a, "codex"), "codex/usage-limit")

    def test_hit_records_cooldown_and_fails_fast(self):
        (self.fake / "claude.limit").write_text(MSG)
        self.add()
        out = self.send("hi")
        self.assertTrue(out["usage_limit"], out); self.assertTrue(out["reset_at"].endswith("+00:00"))
        self.assertEqual(len(self.launches("claude")), 1)  # no auto-retry
        self.assertEqual(self.tools().count("[limit] claude usage limit (resets 19:40 America/Toronto)"), 1)
        self.assertFalse([r for r in self.log() if "turn failed" in r.get("text", "")])
        rl = self.agj("ratelimit")["data"]["cooldowns"]["claude_usage-limit"]  # cooldown file stem
        self.assertTrue(rl["usage_limit"]); self.assertGreater(rl["remaining"], 0)
        self.assertIn("claude usage limit", self.run_ag("ratelimit").stdout)
        (self.fake / "claude.limit").unlink()  # cooldown: later turn fails fast, CLI not launched
        out = self.send("again")
        self.assertTrue(out["usage_limit"]); self.assertEqual(len(self.launches("claude")), 1)
        self.assertEqual(out["error"], "claude usage limit (resets 19:40 America/Toronto)")
        self.agj("ratelimit", "clear")
        self.assertEqual(self.agj("ratelimit")["data"]["cooldowns"], {})
        self.assertIn("claude ok", self.send("now")["reply"]); self.assertEqual(len(self.launches("claude")), 2)

    def test_stderr_limit_detected(self):
        (self.fake / "claude.stderr").write_text("Claude usage limit reached. Your limit resets at 19:40\n")
        self.add()
        self.assertTrue(self.send("hi")["usage_limit"])

    def test_cross_backend_failover(self):
        (self.fake / "claude.limit").write_text(MSG)
        self.add("--fallback", "codex/default")
        out = self.send("first question")
        self.assertIn("codex ok", out["reply"]); self.assertFalse(out.get("usage_limit"))
        t = self.tools()
        self.assertIn("[limit] claude usage limit (resets 19:40 America/Toronto)", t)
        self.assertIn("[fallback] claude usage limit -> codex/default", t)
        self.assertEqual(len(self.launches("claude")), 1); self.assertEqual(len(self.launches("codex")), 1)
        self.assertNotIn("resume", self.launches("codex")[0])
        # second turn: primary cooling -> straight to codex, fresh, earlier turns replayed
        self.assertIn("codex ok", self.send("second question")["reply"])
        self.assertEqual(len(self.launches("claude")), 1); self.assertEqual(len(self.launches("codex")), 2)
        cl = self.launches("codex")[1]
        self.assertIn("first question", cl); self.assertIn("codex ok", cl); self.assertIn("second question", cl)
        self.assertNotIn("resume", cl)
        self.assertEqual(self.tools().count("[fallback] claude usage limit -> codex/default"), 2)
        a = [x for x in self.agj("agents", "list")["data"]["agents"] if x["name"] == "w"][0]
        self.assertEqual((a["backend"], a["fallback"]), ("claude", ["codex/default"]))
        self.agj("ratelimit", "clear"); (self.fake / "claude.limit").unlink()  # cooldown over -> primary again
        self.assertIn("claude ok", self.send("third")["reply"]); self.assertEqual(len(self.launches("codex")), 2)

    def test_failover_access_policy_applies(self):
        (self.fake / "claude.limit").write_text(MSG)
        self.add("--fallback", "codex:default")
        self.agj("access", "deny", "codex/*", "--scope", "global")
        out = self.send("hi")
        self.assertTrue(out["usage_limit"], out); self.assertEqual(self.launches("codex"), [])  # denied fallback skipped

    def test_same_backend_fallback_unchanged(self):
        ag = self.lib()
        a = {"fallback": ["codex/default", "m2"], "model": "m1"}
        self.assertEqual(ag._rl_pick_fallback(a, "claude", "m1"), "m2")
        self.assertEqual(ag._ul_pick(self.root / "state", a, "claude"), ("codex", "default"))
        self.add("--fallback", "m2")
        self.agj("agents", "set", "w", "--fallback", "codex/default", "--fallback", "m3")


if __name__ == "__main__":
    unittest.main()
