#!/usr/bin/env python3
"""Quota awareness: `ag usage` windows/limits, opencode stats parsing, budget-context
injection (opt-in), adaptive routing (opt-in), limit-hit recording. Stdlib, temp dirs,
fake backends on PATH, no network. Run: python3 tests/test_ag_usage.py
"""
import json, os, subprocess, sys, tempfile, time, unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

def _load_src(loader):
    import importlib.util
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent / "ag"))
FAKE_CLAUDE = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/claude.log"
echo '{"type":"system","subtype":"init","session_id":"cl_1"}'
echo '{"type":"assistant","message":{"content":[{"type":"text","text":"claude ok"}]}}'
echo '{"type":"result","subtype":"success","is_error":false,"result":"claude ok","total_cost_usd":0}'
"""
FAKE_OPENCODE = """#!/bin/sh
if [ "$1" = "stats" ]; then
cat <<'EOF'
OpenCode Stats
Sessions: 12
Total Cost: $3.50
Model Usage
opencode-go/glm-5.1  1000  2000  300  $1.25
opencode/big-pickle  4000  500  0  $0.00
EOF
exit 0
fi
if [ "$1" = "models" ]; then cat "$FAKE_DIR/oc.ids"; exit 0; fi
echo "launched $@" >> "$FAKE_DIR/opencode.log"
echo '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"opencode ok"}}'
echo '{"type":"step_finish","sessionID":"ses_1","part":{"tokens":{"input":10,"output":5,"reasoning":0,"cache":{"read":0,"write":0}},"cost":0}}'
"""
OC_IDS = ["opencode-go/glm-5.1", "opencode/big-pickle", "opencode/space-bunny-free"]


class UsageEnv(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cfg", "cache", "state"): (r / d).mkdir(parents=True)
        for n, body in (("claude", FAKE_CLAUDE), ("opencode", FAKE_OPENCODE)):
            (r / "fake" / n).write_text(body); (r / "fake" / n).chmod(0o755)
        (r / "home/.codex/models_cache.json").write_text(json.dumps({"models": []}))
        (r / "fake" / "oc.ids").write_text("\n".join(OC_IDS) + "\n")
        subprocess.run(["git", "init", "-q", str(r / "proj")], check=True)
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"),
            AG_CACHE_HOME=str(r / "cache"), AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0",
            AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_ZEN_URL="off", HOME=str(r / "home"),
            PATH=f"{r / 'fake'}:{os.path.dirname(sys.executable)}:/usr/bin:/bin",
            AG_HARNESS="claude", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json",
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

    def lib(self):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update(self.env)
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        return _load_src(SourceFileLoader(f"ag_usage_{id(self)}", AG))

    def ledger(self, rows):
        p = self.root / "cache" / "usage.jsonl"; p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def row(self, ago_s, backend="opencode", model="opencode-go/glm-5.1", inn=100, out=50, cost=None, provider="opencode-go"):
        ts = datetime.fromtimestamp(time.time() - ago_s, timezone.utc).isoformat()
        r = {"ts": ts, "agent": "w", "project": str(self.root / "proj"), "backend": backend, "model": model,
             "provider": provider, "in": inn, "out": out, "cache_read": 0, "cache": 0, "reasoning": 0,
             "requests": 1, "exit": 0, "subscription": False}
        if cost is not None: r["cost"] = cost
        return r

    def routing(self, doc):
        (self.root / "cfg" / "routing.json").write_text(json.dumps(doc))

    def add(self, backend="opencode", model="opencode-go/glm-5.1", *extra):
        self.agj("agents", "add", "w", "--backend", backend, "--model", model, "--dir", str(self.root / "proj"), *extra)

    def launches(self, n):
        f = self.fake / f"{n}.log"
        return [x for x in f.read_text().split("launched ")[1:]] if f.exists() else []

    def tools(self):
        rows = self.agj("chat", "log", "w", "--limit", "50")["data"]["log"]
        return [r["text"] for r in rows if r["role"] == "tool"]


class TestUsageWindows(UsageEnv):
    def test_windows_and_groups(self):
        self.ledger([self.row(60), self.row(2 * 3600), self.row(3 * 86400), self.row(20 * 86400),
                     self.row(60, backend="claude", model="sonnet", provider="anthropic", cost=0.5)])
        d = self.agj("usage")["data"]
        w = d["windows"]
        self.assertEqual(w["rolling_5h"]["total"]["requests"], 3)       # 60s, 2h, + claude 60s
        self.assertEqual(w["rolling_5h"]["by_backend"]["opencode"]["requests"], 2)
        self.assertEqual(w["7d"]["total"]["requests"], 4)
        self.assertEqual(w["30d"]["total"]["requests"], 5)
        t = time.localtime(); since_midnight = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
        self.assertEqual(w["today"]["total"]["requests"], sum(1 for age in (60, 2 * 3600, 60) if age < since_midnight))  # local midnight, not 24h
        bm = w["30d"]["by_model"]["opencode-go/glm-5.1"]
        self.assertEqual((bm["backend"], bm["in"], bm["out"]), ("opencode", 400, 200))
        self.assertEqual(w["30d"]["by_backend"]["claude"]["cost"], 0.5)
        self.assertIn("rolling_5h", self.run_ag("usage").stdout)

    def test_limits_pct_and_no_limits_line(self):
        self.ledger([self.row(60, inn=600, out=300), self.row(120, inn=300, out=100)])  # 1300 tokens in 5h
        self.routing({"usage": {"limits": {"opencode-go": {"rolling_5h": 2000, "weekly": {"tokens": 10000}}}}})
        d = self.agj("usage")["data"]
        st = d["limits"]["opencode-go"]
        self.assertEqual(st["rolling_5h"]["used"]["tokens"], 1300)
        self.assertEqual(st["rolling_5h"]["pct"], 65.0)
        self.assertTrue(st["rolling_5h"]["resets_at"])
        self.assertEqual(st["weekly"]["pct"], 13.0)
        txt = self.run_ag("usage").stdout
        self.assertIn("opencode-go", txt); self.assertIn("65%", txt)
        self.routing({})
        d = self.agj("usage")["data"]
        self.assertEqual(d["limits"], {})

    def test_opencode_stats_parsed_when_installed(self):
        d = self.agj("usage")["data"]
        oc = d["opencode_stats"]
        self.assertIsNotNone(oc)
        self.assertEqual(oc["total_cost"], 3.50)
        self.assertEqual(oc["models"]["opencode-go/glm-5.1"], {"in": 1000, "out": 2000, "cache": 300, "cost": 1.25})
        ag = self.lib()
        self.assertEqual(ag._opencode_stats_parse("nonsense\nno models here"), {"models": {}, "total_cost": None})

    def test_limit_hit_recorded_and_shown(self):
        ag = self.lib()
        info = ag.usage_limit_scan("claude", "You've hit your usage limit. Try again in 3h.")
        self.assertIsNotNone(info)
        ag._ul_record({"name": "w"}, "claude", info)
        hits = ag._usage_limit_hits()
        self.assertTrue(hits and hits[0]["backend"] == "claude" and hits[0]["active"])
        self.assertTrue(hits[0]["reset_at"].endswith("+00:00"))
        d = self.agj("usage")["data"]
        self.assertEqual(d["limit_hits"]["last_by_backend"]["claude"]["label"], info["label"])


class TestBudgetCtx(UsageEnv):
    def test_off_by_default(self):
        self.ledger([self.row(60)])
        self.add()
        p = self.run_ag("--json", "chat", "send", "w", "hello")
        self.assertIn("opencode ok", p.stdout)
        self.assertNotIn("budget:", self.launches("opencode")[0])

    def test_inject_on_shows_raw_line(self):
        self.ledger([self.row(60)])
        self.routing({"usage": {"inject": True}})
        self.add()
        self.run_ag("--json", "chat", "send", "w", "hello")
        self.assertIn("budget: opencode 5h 150 tokens, 7d 150 tokens", self.launches("opencode")[0])

    def test_per_agent_override(self):
        self.ledger([self.row(60)])
        self.routing({"usage": {"inject": True}})
        self.add()
        self.agj("agents", "set", "w", "--budget-ctx", "off")
        self.run_ag("--json", "chat", "send", "w", "hello")
        self.assertNotIn("budget:", self.launches("opencode")[0])
        self.agj("agents", "set", "w", "--budget-ctx", "on")
        self.routing({})
        self.run_ag("--json", "chat", "send", "w", "hello again")
        self.assertIn("budget:", self.launches("opencode")[1])
        a = next(x for x in self.agj("agents", "list")["data"]["agents"] if x["name"] == "w")
        self.assertIs(a["budget_ctx"], True)

    def test_budget_line_with_limits_pure(self):
        ag = self.lib()
        self.ledger([self.row(60, inn=1700, out=300)])  # 2000 tokens
        self.routing({"usage": {"inject": True, "limits": {"opencode-go": {"rolling_5h": 2500}}}})
        line = ag._usage_budget_line(self.root / "state", {"name": "w", "backend": "opencode", "model": "opencode-go/glm-5.1"})
        self.assertTrue(line.startswith("budget: opencode-go 5h 80%"), line)
        self.assertIn("resets", line)
        self.assertEqual(ag._usage_budget_line(self.root / "state", {"name": "w", "backend": "opencode", "model": "opencode-go/glm-5.1", "budget_ctx": False}), "")


class TestAdapt(UsageEnv):
    def test_adapt_off_by_default_no_change(self):
        self.ledger([self.row(60, inn=990, out=10)])
        self.routing({"usage": {"limits": {"opencode-go": {"rolling_5h": 1000}}}})
        self.add("opencode", "opencode-go/glm-5.1", "--fallback", "opencode/big-pickle")
        self.run_ag("--json", "chat", "send", "w", "hello")
        self.assertIn("opencode-go/glm-5.1", self.launches("opencode")[0])
        self.assertFalse(any("[usage-adapt]" in t for t in self.tools()))

    def test_adapt_prefers_fallback_and_logs(self):
        self.ledger([self.row(60, inn=990, out=10)])  # 100% of the 5h token limit
        self.routing({"usage": {"adapt": True, "limits": {"opencode-go": {"rolling_5h": 1000}}}})
        self.add("opencode", "opencode-go/glm-5.1", "--fallback", "opencode/big-pickle")
        p = self.run_ag("--json", "chat", "send", "w", "hello")
        self.assertIn("opencode ok", p.stdout)
        self.assertIn("opencode/big-pickle", self.launches("opencode")[0])
        self.assertTrue(any("[usage-adapt]" in t for t in self.tools()))
        ev = self.run_ag("--json", "events", "--type", "usage_adapt").stdout
        self.assertIn("usage_adapt", ev)

    def test_adapt_threshold_and_effort_pure(self):
        ag = self.lib()
        self.ledger([self.row(60, inn=800, out=0)])
        self.routing({"usage": {"adapt": True, "adapt_threshold": 85, "limits": {"opencode-go": {"rolling_5h": 1000}}}})
        a = {"name": "w", "backend": "opencode", "model": "opencode-go/glm-5.1", "fallback": ["opencode/big-pickle"], "effort": "high"}
        self.assertIsNone(ag._usage_adapt(self.root / "state", a, "opencode", "opencode-go/glm-5.1", "high"))  # 80% < 85
        self.ledger([self.row(60, inn=800, out=0), self.row(30, inn=100, out=0)])
        out = ag._usage_adapt(self.root / "state", a, "opencode", "opencode-go/glm-5.1", "high")
        self.assertIsNotNone(out)
        self.assertEqual(out[1], "opencode/big-pickle")
        self.assertIn("usage-adapt", out[3])
        self.assertEqual(ag._eff_step_down("opencode", "opencode-go/glm-5.1", "high"), "medium")
        self.assertEqual(ag._eff_step_down("claude", "sonnet", "high"), "medium")
        self.assertEqual(ag._eff_step_down("claude", "sonnet", "low"), "low")
        self.assertEqual(ag._eff_step_down("opencode", "opencode-go/glm-5.1", ""), "")

    def test_route_shows_adaptation_only_when_on(self):
        self.ledger([self.row(60, inn=990, out=10, backend="claude", model="opus", provider="anthropic")])
        one = {"jobs": {"implement": {"ag": {"backend": "claude", "model": "opus", "effort": "high"}}}}
        jobs = {"profiles": {"default": one, "claude": one}}
        self.routing({**jobs, "usage": {"adapt": True, "limits": {"claude": {"rolling_5h": 1000}}}})
        out = self.run_ag("route").stdout
        self.assertIn("usage-adapt", out)  # effort high->medium at 100% of the claude window
        d = self.agj("route")["data"]
        self.assertEqual(d["jobs"]["implement"]["usage_adapt"]["to"]["effort"], "medium")
        self.routing({**jobs, "usage": {"limits": {"claude": {"rolling_5h": 1000}}}})
        self.assertNotIn("usage-adapt", self.run_ag("route").stdout)


if __name__ == "__main__":
    unittest.main()
