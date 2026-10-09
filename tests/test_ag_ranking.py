#!/usr/bin/env python3
"""OpenRouter rankings (Artificial Analysis scores + speed + usage): matching, score-based tiers, cache TTL/fallback, stale-read background
refresh, `ag models --sort/--auto/--status`, `ag update --quiet` catalog piggyback. Local fake HTTP server via AG_OPENROUTER_RANKINGS_URL;
tests/or_fixture_*.json are trimmed real snapshots (2026-10-09). Run: python3 tests/test_ag_ranking.py
"""
import http.server, json, os, subprocess, sys, tempfile, threading, time, types, unittest
from argparse import Namespace
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent/"ag"))
FIX = {n: (HERE/f"or_fixture_{n}.json").read_bytes() for n in ("benchmarks", "performance", "models")}
FAKE_OPENCODE = "#!/bin/sh\n[ \"$1\" = models ] || { echo fake; exit 0; }\ncat \"$FAKE_DIR/oc.ids\"\n"
IDS = ["opencode/muse-spark-1.3-contributor-free", "opencode/ling-3.1-flash-free", "opencode/exo-free", "azure/claude-haiku-5-5",
       "azure/claude-opus-5-5", "azure/phi-4", "opencode/mystery-flash-free", "opencode/mystery-ultra-free", "opencode/mystery-plain-free"]


class H(http.server.BaseHTTPRequestHandler):
    hits = []; down = False; bad = set()
    def do_GET(self):
        H.hits.append(self.path)
        name = self.path.rsplit("/", 1)[-1]
        if H.down or name in H.bad or name not in FIX or not self.headers.get("User-Agent", "").startswith("Mozilla/"):
            self.send_response(503); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(FIX[name])
    def log_message(self, *a): pass


class Ranking(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        H.hits.clear(); H.down = False; H.bad = set()
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cache", "cfg"): (r/d).mkdir(parents=True)
        (r/"fake"/"opencode").write_text(FAKE_OPENCODE); (r/"fake"/"opencode").chmod(0o755)
        (r/"fake"/"oc.ids").write_text("\n".join(IDS) + "\n")
        (r/"home/.codex/models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-6.1-sol", "visibility": "list"}, {"slug": "gpt-6-luna", "visibility": "list"}, {"slug": "gpt-reserve", "visibility": "list"}]}))
        self.env = dict(os.environ, AG_HOME=str(r/"home"), AG_CONFIG_HOME=str(r/"cfg"), AG_CACHE_HOME=str(r/"cache"),
            FAKE_DIR=str(r/"fake"), AGENT_CLI_DIR=str(r/"state"), AG_PROBE="0", AG_AUTO_UPDATE="0", HOME=str(r/"home"),
            PATH=f"{r/'fake'}:/usr/bin:/bin", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json",
            AG_OPENROUTER_RANKINGS_URL=f"http://127.0.0.1:{self.port}/or")
        for k in ("AG_AA_KEY", "AG_CATALOG_AUTO"): self.env.pop(k, None)

    def run_ag(self, *args, env=None):
        return subprocess.run([sys.executable, AG, *args], capture_output=True, text=True, timeout=60, cwd=self.root/"proj",
            env={**self.env, **(env or {})}, stdin=subprocess.DEVNULL)

    def agj(self, *args, env=None):
        p = self.run_ag("--json", *args, env=env); o = json.loads(p.stdout); self.assertTrue(o["ok"], (p.stdout, p.stderr)); return o["data"]

    def lib(self):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update(self.env)
        def restore():
            for k, v in old.items(): os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(restore)
        return SourceFileLoader(f"ag_rk_{id(self)}", AG).load_module()

    def fake_popen(self, ag, fn):   # shim only ag's view of subprocess (the real module is shared with run_ag)
        real = ag.subprocess; ag.subprocess = types.SimpleNamespace(Popen=fn, DEVNULL=real.DEVNULL); self.addCleanup(setattr, ag, "subprocess", real)

    def rows(self, *args):
        return {r["id"]: r for r in self.agj("models", "--rank", *args)["models"]}

    def test_muse_spark_is_big_with_speed(self):
        r = self.rows("--backend", "opencode")["opencode/muse-spark-1.3-contributor-free"]
        self.assertEqual((r["tier"], r["tier_source"], r["score"]), ("big", "score", 48.1))
        self.assertEqual((r["speed"], r["latency_ms"], r["or_slug"]), (75, 3574.5, "meta/muse-spark-1.3-20260902"))   # contributor perf row, base-slug score
        self.assertEqual(r["variant"], "Muse Spark 1.3 (Max)"); self.assertEqual(r["coding"], 76.5); self.assertFalse(r["fast"])
        self.assertGreater(r["popularity"], 0)
        self.assertEqual(self.rows("--backend", "opencode")["opencode/ling-3.1-flash-free"]["tier"], "big")   # score beats the 'flash' hint
        self.assertEqual(self.rows("--backend", "opencode")["azure/phi-4"]["tier"], "small")

    def test_claude_and_codex_mapping(self):
        c = self.rows("--backend", "claude")
        self.assertEqual(c["opus"]["or_slug"], "anthropic/claude-opus-5.5-20260921"); self.assertEqual(c["opus"]["score"], 57.6)
        self.assertEqual(c["haiku"]["or_slug"], "anthropic/claude-haiku-5.5-20261007"); self.assertTrue(c["haiku"]["fast"])   # 190 tok/s: top quartile
        self.assertEqual(c["fable"]["or_slug"], "anthropic/claude-fable-5.1-20260831")
        self.assertEqual(self.rows("--backend", "opencode")["azure/claude-opus-5-5"]["or_slug"], "anthropic/claude-opus-5.5-20260921")   # 5-5 == 5.5, order-free
        x = self.rows("--backend", "codex")
        self.assertEqual((x["gpt-6.1-sol"]["score"], x["gpt-6.1-sol"]["tier"]), (51.8, "big")); self.assertEqual(x["gpt-6-luna"]["tier"], "balanced")
        self.assertEqual(x["gpt-reserve"]["tier_source"], "guess")

    def test_guess_path_marks_tier_source(self):
        r = self.rows("--backend", "opencode")
        self.assertEqual({k: (r[f"opencode/mystery-{k}-free"]["tier"], r[f"opencode/mystery-{k}-free"]["tier_source"]) for k in ("flash", "ultra", "plain")},
            {"flash": ("small", "guess"), "ultra": ("big", "guess"), "plain": ("balanced", "guess")})
        self.assertIsNone(r["opencode/exo-free"]["score"])
        txt = self.run_ag("models", "--rank", "--backend", "opencode").stdout
        self.assertIn("scores: Artificial Analysis via openrouter.ai/rankings (fetched", txt); self.assertIn("small~", txt); self.assertIn("big ", txt)
        # no rankings at all (endpoint down, no cache): everything guessed, no crash
        r = {x["id"]: x for x in self.agj("models", "--rank", "--backend", "opencode", env={"AG_OPENROUTER_RANKINGS_URL": "http://127.0.0.1:1/or", "AG_CACHE_HOME": str(self.root/"c2")})["models"]}
        self.assertTrue(all(x["tier_source"] == "guess" for x in r.values()))
        ag = self.lib(); self.assertEqual(ag._tier_of("x/spark-luna-sol-mythos", None, None), "balanced")   # bogus hints gone

    def test_cache_ttl_failure_fallback_and_ua(self):
        ag = self.lib(); n = lambda: len([h for h in H.hits if h.startswith("/or/")])
        rk = ag.rankings_load(); self.assertEqual(n(), 3); self.assertEqual(rk["bench"]["intelligence"]["meta/muse-spark-1.3-20260902"][0], 48.1)
        ag.rankings_load(); self.assertEqual(n(), 3)                                     # fresh cache: no refetch
        H.down = True
        rk = ag.rankings_load(refresh=True)                                              # failure keeps last good cache
        self.assertEqual(n(), 6); self.assertIn("bench", rk["error"]); self.assertEqual(rk["bench"]["intelligence"]["meta/muse-spark-1.3-20260902"][0], 48.1)
        self.assertTrue((self.root/"cache"/"openrouter-rankings.json").exists())
        H.down = False; H.bad = {"performance"}                                          # partial failure: other parts update, perf stays
        rk = ag.rankings_load(refresh=True); self.assertIn("perf", rk["error"]); self.assertEqual(set(rk["fetched"]), {"bench", "perf", "pop"})
        self.assertEqual(rk["perf"]["meta/muse-spark-1.3-contributor-20260902"][0], 75)

    def test_no_cache_failure_degrades_and_backs_off(self):
        ag = self.lib(); H.down = True
        t = time.time(); self.assertEqual(ag.rankings_load(), {}); self.assertLess(time.time() - t, 21); k = len(H.hits)
        self.assertEqual(ag.rankings_load(), {}); self.assertEqual(len(H.hits), k)       # recent failed attempt: not re-blocked

    def test_stale_read_returns_old_data_and_spawns_bg_refresh(self):
        ag = self.lib(); ag.rankings_load()
        p = self.root/"cache"/"openrouter-rankings.json"; d = json.loads(p.read_text())
        d["fetched_at"] = "2020-01-01T00:00:00+00:00"; d["bench"]["intelligence"]["meta/muse-spark-1.3-20260902"][0] = 1.5; p.write_text(json.dumps(d))
        H.hits.clear(); spawned = []; self.fake_popen(ag, lambda argv, **kw: spawned.append((argv, kw)))
        t = time.time(); rk = ag.rankings_load()
        self.assertEqual(rk["bench"]["intelligence"]["meta/muse-spark-1.3-20260902"][0], 1.5)   # old data, instantly
        self.assertLess(time.time() - t, 1); self.assertEqual(H.hits, [])                        # no network on the read path
        self.assertEqual(len(spawned), 1); self.assertEqual(spawned[0][0][-2:], ["models", "--bg"]); self.assertTrue(spawned[0][1]["start_new_session"])
        self.assertTrue((self.root/"cache"/"catalog-bg").exists())
        ag.rankings_load(); self.assertEqual(len(spawned), 1)                                   # stamp-guarded: one refresh at a time
        # the spawned command itself: refreshes every source and stamps the cache
        p2 = self.run_ag("models", "--bg"); self.assertEqual(p2.returncode, 0, p2.stderr)
        self.assertEqual(json.loads(p.read_text())["bench"]["intelligence"]["meta/muse-spark-1.3-20260902"][0], 48.1)
        self.assertTrue(json.loads((self.root/"cache"/"models.json").read_text())["backends"]["opencode"]["models"])

    def test_auto_toggle_disables_background_paths(self):
        out = self.run_ag("models", "--auto", "off"); self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(json.loads((self.root/"cfg"/"config.json").read_text())["catalog_auto"])
        ag = self.lib(); self.assertFalse(ag.catalog_auto_on())
        ag.rankings_load(); p = self.root/"cache"/"openrouter-rankings.json"; d = json.loads(p.read_text()); d["fetched_at"] = "2020-01-01T00:00:00+00:00"; p.write_text(json.dumps(d))
        spawned = []; self.fake_popen(ag, lambda *a, **k: spawned.append(a))
        H.hits.clear(); ag.rankings_load(); self.assertEqual((spawned, H.hits), ([], []))        # stale served as-is, nothing spawned
        ag._update_catalog(); self.assertEqual(H.hits, [])                                         # daily piggyback skipped
        self.assertIn("auto-refresh off", self.run_ag("models", "--status").stdout)
        self.run_ag("models", "--auto", "on"); self.assertTrue(self.lib().catalog_auto_on())
        self.env["AG_CATALOG_AUTO"] = "0"; self.assertFalse(self.lib().catalog_auto_on())          # env kill switch

    def test_update_quiet_refreshes_catalog(self):
        ag = self.lib(); calls = []
        ag.self_update = lambda *a, **k: ("current", "x"); lg = ag._update_stamp
        ag.catalog_refresh_all = lambda: calls.append(1)
        self.assertEqual(ag.do_update(Namespace(auto=None, status=False, quiet=True)), 0); self.assertEqual(calls, [1])
        ag.do_update(Namespace(auto=None, status=False, quiet=False, json=True)); self.assertEqual(calls, [1])   # interactive update: untouched
        ag.catalog_refresh_all = lambda: 1 / 0                                                                    # a catalog failure never changes the result
        self.assertEqual(ag.do_update(Namespace(auto=None, status=False, quiet=True)), 0)
        os.environ["AG_CATALOG_AUTO"] = "0"; self.addCleanup(os.environ.pop, "AG_CATALOG_AUTO", None)
        ag.catalog_refresh_all = lambda: calls.append(2); ag.do_update(Namespace(auto=None, status=False, quiet=True)); self.assertEqual(calls, [1])
        # real refresh path end-to-end: nothing in cache -> populated by the helper
        os.environ.pop("AG_CATALOG_AUTO"); ag2 = self.lib(); ag2._update_catalog()
        self.assertTrue((self.root/"cache"/"openrouter-rankings.json").exists())

    def test_sort_and_json_sources(self):
        d = self.agj("models", "--backend", "opencode", "--sort", "speed")
        sp = [r["speed"] for r in d["models"] if r["speed"] is not None]; self.assertEqual(sp, sorted(sp, reverse=True)); self.assertEqual(d["sort"], "speed")
        self.assertIsNone(d["models"][-1]["speed"])
        i = [r["score"] for r in self.agj("models", "--backend", "opencode", "--rank")["models"] if r["score"] is not None]; self.assertEqual(i, sorted(i, reverse=True))
        co = [r["coding"] for r in self.agj("models", "--backend", "opencode", "--sort", "coding")["models"] if r["coding"] is not None]; self.assertEqual(co, sorted(co, reverse=True))
        po = [r["popularity"] for r in self.agj("models", "--backend", "opencode", "--sort", "popular")["models"] if r["popularity"] is not None]
        self.assertEqual(po, sorted(po, reverse=True)); self.assertTrue(po)
        s = d["sources"]["openrouter_rankings"]; self.assertTrue(s["fetched_at"]); self.assertEqual(set(s["parts"]), {"bench", "perf", "pop"})
        self.assertIn("Artificial Analysis", s["what"]); self.assertIn("models.dev", d["sources"])
        t = self.run_ag("models", "--backend", "opencode", "--sort", "speed").stdout.splitlines()
        self.assertTrue(t[0].startswith("scores: Artificial Analysis via openrouter.ai/rankings (fetched")); self.assertIn("tok/s", t[2]); self.assertIn("latency", t[2])

    def test_pick_prefers_intel_then_speed_and_job_coding(self):
        ag = self.lib()
        self.assertEqual(ag.catalog_pick("opencode", "big", free_only=True), "opencode/muse-spark-1.3-contributor-free")  # 48.1 > ling 41.1
        self.assertEqual(ag.catalog_pick("opencode", "big"), "azure/claude-opus-5-5")
        self.assertEqual(ag.catalog_pick("claude", "big", job="implement"), "fable")      # coding 81.6 beats opus (no coding score)
        self.assertEqual(ag.catalog_pick("claude", "big"), "opus")                          # intelligence order
        self.assertIsNone(ag.catalog_pick("claude", "small"))

    def test_status_text(self):
        self.run_ag("models", "--rank", "--backend", "claude")
        t = self.run_ag("models", "--status").stdout
        self.assertIn("auto-refresh on", t); self.assertIn("openrouter rankings  fetched", t); self.assertIn("no AG_AA_KEY", t)


if __name__ == "__main__":
    unittest.main()
