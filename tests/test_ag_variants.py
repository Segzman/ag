#!/usr/bin/env python3
"""Per-model reasoning variants (effort): opencode `models --verbose` variant names,
codex supported_reasoning_levels + default_reasoning_level, claude per-alias static
table; model_default_variant (codex default, opencode single->it, never synthesized);
resolve_effort (highest|lowest, unknown-value and no-variant errors); agents add/set
validation; --model change drops stale effort; turn-time invalid effort falls back to
the model default (+ one [effort] row); models --json/--rank show variants. Run:
python3 tests/test_ag_variants.py
"""
import json, os, subprocess, sys, tempfile, unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent/"ag"))
SPARK, PLAIN, SOLO = "opencode/spark-1.3-free", "opencode/plain-free", "opencode/solo-free"
FAKE_OPENCODE = ('#!/bin/sh\nif [ "$1" = models ] && [ "$2" = "--verbose" ]; then cat "$FAKE_DIR/oc.verbose"; exit 0; fi\n'
    'if [ "$1" = models ]; then cat "$FAKE_DIR/oc.ids"; exit 0; fi\necho "$@" >> "$FAKE_DIR/argv.log"\n'
    'echo \'{"type":"text","sessionID":"ses_9","part":{"type":"text","text":"ok"}}\'\n')
VERBOSE = (f"{SPARK}\n" + json.dumps({"id": "spark-1.3-free", "providerID": "opencode",
    "variants": {"minimal": {}, "low": {}, "medium": {}, "high": {}, "xhigh": {}}}) + f"\n{PLAIN}\n"
    + json.dumps({"id": "plain-free", "providerID": "opencode", "variants": {}}) + f"\n{SOLO}\n"
    + json.dumps({"id": "solo-free", "providerID": "opencode", "variants": {"high": {}}}) + "\n")
CODEX_CACHE = {"models": [
    {"slug": "gpt-sol", "visibility": "list", "default_reasoning_level": "low",
        "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high", "xhigh", "max", "ultra")]},
    {"slug": "gpt-luna", "visibility": "list", "default_reasoning_level": "medium",
        "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
    {"slug": "gpt-nov", "visibility": "list"}]}


class Variants(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cache", "cfg", "state"): (r/d).mkdir(parents=True)
        (r/"fake"/"opencode").write_text(FAKE_OPENCODE); (r/"fake"/"opencode").chmod(0o755)
        (r/"fake"/"oc.ids").write_text(f"{SPARK}\n{PLAIN}\n{SOLO}\n")
        (r/"fake"/"oc.verbose").write_text(VERBOSE)
        (r/"home/.codex/models_cache.json").write_text(json.dumps(CODEX_CACHE))
        self.env = dict(os.environ, AG_HOME=str(r/"home"), AG_CONFIG_HOME=str(r/"cfg"), AG_CACHE_HOME=str(r/"cache"),
            AGENT_CLI_DIR=str(r/"state"), AG_PROBE="0", AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_PRIVACY="off", HOME=str(r/"home"),
            FAKE_DIR=str(r/"fake"), PATH=f"{r/'fake'}:/usr/bin:/bin",
            AG_MODELSDEV_URL="http://127.0.0.1:1/api.json", AG_OPENROUTER_RANKINGS_URL="http://127.0.0.1:1/or")
        for k in ("AG_AA_KEY", "OPENCODE_CONFIG", "CODEX_HOME"): self.env.pop(k, None)
        self.run_ag("--json", "models", "--refresh", "--backend", "opencode")
        self.run_ag("--json", "models", "--refresh", "--backend", "codex")

    def run_ag(self, *args, env=None):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.root/"state"), *args],
            capture_output=True, text=True, timeout=60, cwd=self.root/"proj", env={**self.env, **(env or {})},
            stdin=subprocess.DEVNULL)
        try: o = json.loads(p.stdout)
        except ValueError: self.fail(f"not JSON: {p.stdout[:500]} {p.stderr[:500]}")
        return p, o

    def agj(self, *args, env=None):
        p, o = self.run_ag("--json", *args, env=env)
        self.assertTrue(o["ok"], (p.stdout, p.stderr)); return o["data"]

    def lib(self):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update(self.env)
        def restore():
            for k, v in old.items(): os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(restore)
        return SourceFileLoader(f"ag_vr_{id(self)}", AG).load_module()

    def agents(self):
        return {a["name"]: a for a in self.agj("agents", "list", "--json")["agents"]}

    def test_cache_and_json(self):
        mc = json.loads((self.root/"cache"/"models.json").read_text())["backends"]
        self.assertEqual(mc["opencode"]["variants"][SPARK], ["minimal", "low", "medium", "high", "xhigh"])
        self.assertEqual(mc["opencode"]["variants"][PLAIN], [])
        self.assertEqual(mc["opencode"]["variants"][SOLO], ["high"])  # single variant, never synthesized
        self.assertEqual(mc["opencode"]["defaults"], {SOLO: "high"})  # multi/none get no default
        self.assertEqual(mc["codex"]["variants"]["gpt-sol"], ["low", "medium", "high", "xhigh", "max", "ultra"])
        self.assertEqual(mc["codex"]["variants"]["gpt-luna"], ["low", "high"])
        self.assertEqual(mc["codex"]["defaults"], {"gpt-sol": "low", "gpt-luna": "medium"})
        d = self.agj("models", "--backend", "opencode", "--json")
        self.assertEqual(d["backends"]["opencode"]["variants"][SPARK], ["minimal", "low", "medium", "high", "xhigh"])
        self.assertEqual(d["backends"]["opencode"]["defaults"][SOLO], "high")
        self.assertIsNone(d["backends"]["opencode"]["defaults"][SPARK])
        d = self.agj("models", "--backend", "codex", "--json")
        self.assertEqual(d["backends"]["codex"]["defaults"]["gpt-sol"], "low")

    def test_resolve(self):
        ag = self.lib()
        self.assertEqual(ag.model_variants("claude", "sonnet"), ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual(ag.model_variants("claude", "opus"), ag.model_variants("claude", "haiku"))
        self.assertEqual(ag.model_variants("claude", "fable"), ["low", "medium", "high", "xhigh", "max"])
        self.assertIsNone(ag.model_variants("gemini", "x"))
        self.assertEqual(ag.resolve_effort("claude", "s", "highest"), ("max", None))
        self.assertEqual(ag.resolve_effort("opencode", SPARK, "lowest"), ("minimal", None))
        self.assertEqual(ag.resolve_effort("gemini", "x", "whatever"), ("whatever", None))
        with self.assertRaises(ValueError) as c: ag.resolve_effort("opencode", SPARK, "max")
        self.assertIn("allowed minimal|low|medium|high|xhigh", str(c.exception))
        with self.assertRaises(ValueError) as c: ag.resolve_effort("opencode", PLAIN, "low")
        self.assertIn(f"model {PLAIN} has no variants", str(c.exception))
        with self.assertRaises(ValueError) as c: ag.resolve_effort("codex", "gpt-nov", "low")
        self.assertIn("has no variants", str(c.exception))
        self.assertEqual(ag.resolve_effort("codex", "gpt-sol", "highest"), ("ultra", None))

    def test_defaults_and_turn_fallback(self):
        ag = self.lib()
        self.assertEqual(ag.model_default_variant("codex", "gpt-sol"), "low")
        self.assertEqual(ag.model_default_variant("codex", "gpt-luna"), "medium")
        self.assertIsNone(ag.model_default_variant("codex", "gpt-nov"))
        self.assertEqual(ag.model_default_variant("opencode", SOLO), "high")
        self.assertIsNone(ag.model_default_variant("opencode", SPARK))  # multi -> none
        self.assertIsNone(ag.model_default_variant("opencode", PLAIN))  # none -> none
        self.assertIsNone(ag.model_default_variant("claude", "opus"))
        self.assertIsNone(ag.model_default_variant("gemini", "x"))
        v, w = ag.effort_for_turn("codex", "gpt-sol", "bogus")  # invalid -> model default + one row
        self.assertEqual(v, "low"); self.assertIn("[effort]", w); self.assertIn("using default 'low'", w)
        v, w = ag.effort_for_turn("opencode", SPARK, "max")  # invalid, no default -> dropped
        self.assertEqual(v, ""); self.assertIn("dropping --effort", w)
        v, w = ag.effort_for_turn("opencode", PLAIN, "low")
        self.assertEqual(v, ""); self.assertIn("has no variants", w)
        self.assertEqual(ag.effort_for_turn("opencode", SOLO, "high"), ("high", None))
        self.assertEqual(ag.effort_for_turn("codex", "gpt-sol", "highest"), ("ultra", None))

    def test_add_set(self):
        self.agj("agents", "add", "oc1", "--backend", "opencode", "--model", SPARK, "--dir", ".", "--effort", "highest")
        self.assertEqual(self.agents()["oc1"]["effort"], "xhigh")
        _, o = self.run_ag("--json", "agents", "add", "oc2", "--backend", "opencode", "--model", SPARK, "--dir", ".", "--effort", "max")
        self.assertFalse(o["ok"]); self.assertIn("allowed", o.get("error", "")); self.assertNotIn("oc2", self.agents())
        self.agj("agents", "add", "oc3", "--backend", "opencode", "--model", SPARK, "--dir", ".", "--effort", "")
        self.assertNotIn("effort", self.agents()["oc3"])
        self.agj("agents", "add", "cx1", "--backend", "claude", "--dir", ".", "--effort", "max")
        self.assertEqual(self.agents()["cx1"]["effort"], "max")
        _, o = self.run_ag("--json", "agents", "set", "cx1", "--effort", "bogus")
        self.assertFalse(o["ok"]); self.assertIn("allowed", o.get("error", ""))
        d = self.agj("agents", "set", "oc1", "--model", PLAIN, "--json")
        self.assertIn("dropped", d["_text"]); self.assertNotIn("effort", self.agents()["oc1"])
        _, o = self.run_ag("--json", "agents", "set", "oc1", "--effort", "low")
        self.assertFalse(o["ok"]); self.assertIn("has no variants", o.get("error", ""))
        self.agj("agents", "add", "oc4", "--backend", "opencode", "--model", SPARK, "--dir", ".", "--effort", "low")
        self.agj("agents", "set", "oc4", "--effort", "default", "--json")
        self.assertNotIn("effort", self.agents()["oc4"])

    def test_turn_drops_stale_effort(self):
        ag = self.lib()
        sdir = self.root/"state"
        self.agj("agents", "add", "oc5", "--backend", "opencode", "--model", SPARK, "--dir", str(self.root/"proj"), "--effort", "high")
        evs = []
        r = ag.run_turn(sdir, "oc5", "hi", on_event=lambda n, k, t: evs.append((k, t)))
        self.assertEqual(r.get("exit"), 0, r)
        self.assertIn("--variant high", (self.root/"fake"/"argv.log").read_text())
        mc = json.loads((self.root/"cache"/"models.json").read_text())  # catalog moved: spark lost its variants
        mc["backends"]["opencode"]["variants"][SPARK] = []
        (self.root/"cache"/"models.json").write_text(json.dumps(mc))
        (self.root/"fake"/"argv.log").write_text("")
        evs.clear()
        r = ag.run_turn(sdir, "oc5", "hi again", on_event=lambda n, k, t: evs.append((k, t)))
        self.assertEqual(r.get("exit"), 0, r)
        self.assertNotIn("--variant", (self.root/"fake"/"argv.log").read_text())
        self.assertTrue(any(k == "tool" and t.startswith("[effort]") for k, t in evs), evs)
        self.assertEqual(self.agents()["oc5"]["effort"], "high")  # roster keeps it; drop is per-turn
        # fallback: stored effort invalid but the model has a default -> default flag + one row
        self.agj("agents", "add", "oc6", "--backend", "opencode", "--model", SOLO, "--dir", str(self.root/"proj"), "--effort", "high")
        mc = json.loads((self.root/"cache"/"models.json").read_text())
        mc["backends"]["opencode"]["variants"][SOLO] = ["low"]
        mc["backends"]["opencode"]["defaults"][SOLO] = "low"
        (self.root/"cache"/"models.json").write_text(json.dumps(mc))
        (self.root/"fake"/"argv.log").write_text("")
        evs.clear()
        r = ag.run_turn(sdir, "oc6", "hi", on_event=lambda n, k, t: evs.append((k, t)))
        self.assertEqual(r.get("exit"), 0, r)
        self.assertIn("--variant low", (self.root/"fake"/"argv.log").read_text())
        self.assertTrue(any(k == "tool" and "using default 'low'" in t for k, t in evs), evs)

    def test_rank_shows_variants(self):
        ag = self.lib()
        ag.modelsdev_load = lambda refresh=False: {}; ag.aa_load = lambda refresh=False: {}; ag.rankings_load = lambda refresh=False: {}
        rows = {r["id"]: r for r in ag.catalog_rows("opencode")}
        self.assertEqual(rows[SPARK]["variants"], ["minimal", "low", "medium", "high", "xhigh"])
        self.assertEqual(rows[PLAIN]["variants"], [])
        txt = ag.catalog_rank_text([dict(rows[SPARK], tier="big", tier_source="score", score=48.1, coding=1,
            agentic=1, speed=75, latency_ms=1000, context=200000, cost_in=0, cost_out=0, free=True,
            released="2026-09-01", new=False, fast=False)], sources=False)
        self.assertIn("variants", txt); self.assertIn("minimal|low|medium|high|xhigh", txt)

    def test_docs_fixed(self):
        readme = (HERE.parent/"README.md").read_text()
        self.assertNotIn("`max` = Muse Spark Max", readme)  # the false claim; AA's "(Max)" display name stays
        eff = [l for l in readme.splitlines() if "--effort LEVEL" in l][0]
        self.assertIn("xhigh", eff)
        skill = (HERE.parent/"skills"/"ag-agents"/"SKILL.md").read_text()
        self.assertNotIn("--effort max", skill)


if __name__ == "__main__": unittest.main()
