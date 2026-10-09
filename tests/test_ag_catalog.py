#!/usr/bin/env python3
"""ag models --rank/--tier/--free + catalog_* helpers. models.dev + Artificial Analysis served by a local
fake HTTP server (AG_MODELSDEV_URL / AG_AA_URL); tests/modelsdev_fixture.json is a trimmed real snapshot.
Run: python3 tests/test_ag_catalog.py
"""
import http.server, json, os, subprocess, sys, tempfile, threading, unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent/"ag"))
FIX = (HERE/"modelsdev_fixture.json").read_bytes()
AA = json.dumps({"data": [{"slug": "claude-haiku-5-5", "name": "Claude Haiku 5.5", "median_output_tokens_per_second": 123.4,
    "evaluations": {"artificial_analysis_intelligence_index": 61.5}}]}).encode()
FAKE_OPENCODE = "#!/bin/sh\n[ \"$1\" = models ] || { echo fake; exit 0; }\ncat \"$FAKE_DIR/oc.ids\"\n"
IDS = ["opencode/big-pickle", "opencode/nemotron-3-ultra-free", "opencode/muse-spark-1.3-contributor-free",
       "opencode/ling-3.1-flash-free", "opencode/exo-free", "opencode/gpt-5.4", "mystery/zzz-free"]


class H(http.server.BaseHTTPRequestHandler):
    hits = []
    def do_GET(self):
        H.hits.append((self.path, self.headers.get("x-api-key")))
        if self.path == "/api.json": body = FIX
        elif self.path == "/aa" and self.headers.get("x-api-key") == "k123": body = AA
        else: self.send_response(401); self.end_headers(); return
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


class Catalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), H); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        H.hits.clear()
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj"): (r/d).mkdir(parents=True)
        (r/"fake"/"opencode").write_text(FAKE_OPENCODE); (r/"fake"/"opencode").chmod(0o755)
        (r/"fake"/"oc.ids").write_text("\n".join(IDS) + "\n")
        (r/"home/.codex/models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-5.6-sol", "visibility": "list"}, {"slug": "gpt-5.6-luna", "visibility": "list"}]}))
        self.env = dict(os.environ, AG_HOME=str(r/"home"), AG_CONFIG_HOME=str(r/"cfg"), AG_CACHE_HOME=str(r/"cache"),
            FAKE_DIR=str(r/"fake"), AGENT_CLI_DIR=str(r/"state"), AG_PROBE="0", AG_AUTO_UPDATE="0", HOME=str(r/"home"),
            PATH=f"{r/'fake'}:/usr/bin:/bin", AG_MODELSDEV_URL=f"http://127.0.0.1:{self.port}/api.json",
            AG_AA_URL=f"http://127.0.0.1:{self.port}/aa")
        self.env.pop("AG_AA_KEY", None)

    def agj(self, *args, env=None):
        p = subprocess.run([sys.executable, AG, "--json", *args], capture_output=True, text=True, timeout=60,
            cwd=self.root/"proj", env={**self.env, **(env or {})}, stdin=subprocess.DEVNULL)
        o = json.loads(p.stdout); self.assertTrue(o["ok"], (p.stdout, p.stderr)); return o["data"]

    def lib(self, **env):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update({**self.env, **env})
        def restore():
            for k, v in old.items(): os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(restore)
        return SourceFileLoader(f"ag_cat_{id(self)}", AG).load_module()

    def test_rank_tiers_free_and_mapping(self):
        rows = {r["id"]: r for r in self.agj("models", "--backend", "opencode", "--rank")["models"]}
        self.assertEqual(rows["opencode/big-pickle"]["tier"], "big")
        self.assertEqual(rows["opencode/nemotron-3-ultra-free"]["tier"], "big")
        self.assertEqual(rows["opencode/ling-3.1-flash-free"]["tier"], "small")
        self.assertEqual(rows["opencode/muse-spark-1.3-contributor-free"]["tier"], "small")
        self.assertTrue(rows["opencode/exo-free"]["free"]); self.assertFalse(rows["opencode/gpt-5.4"]["free"])
        self.assertEqual(rows["opencode/gpt-5.4"]["source"], "models.dev:exact")
        self.assertEqual(rows["mystery/zzz-free"]["source"], "heuristic"); self.assertTrue(rows["mystery/zzz-free"]["free"])
        self.assertIsNone(rows["opencode/big-pickle"]["score"])
        free = self.agj("models", "--backend", "opencode", "--free")["models"]
        self.assertTrue(free and all(r["free"] for r in free))
        small = self.agj("models", "--backend", "opencode", "--tier", "small")["models"]
        self.assertTrue(small and all(r["tier"] == "small" for r in small))

    def test_text_output_and_cache(self):
        p = subprocess.run([sys.executable, AG, "models", "--backend", "opencode", "--rank"], capture_output=True, text=True,
            cwd=self.root/"proj", env=self.env, stdin=subprocess.DEVNULL)
        self.assertIn("opencode (7)", p.stdout); self.assertIn("$in/$out", p.stdout); self.assertIn("free", p.stdout)
        n = len([h for h in H.hits if h[0] == "/api.json"]); self.assertEqual(n, 1)
        self.agj("models", "--backend", "opencode", "--rank")                 # cached 24h: no refetch
        self.assertEqual(len([h for h in H.hits if h[0] == "/api.json"]), 1)
        self.agj("models", "--backend", "opencode", "--rank", "--refresh")
        self.assertEqual(len([h for h in H.hits if h[0] == "/api.json"]), 2)

    def test_claude_alias_and_codex(self):
        ag = self.lib()
        i = ag.catalog_info("claude", "haiku")
        self.assertEqual((i["tier"], i["source"]), ("small", "models.dev:alias")); self.assertEqual(i["released"], "2026-10-07")
        self.assertEqual(ag.catalog_info("claude", "opus")["tier"], "big")
        self.assertEqual(ag.catalog_pick("codex", "big"), "gpt-5.6-sol")
        self.assertEqual(ag.catalog_pick("codex", "small"), "gpt-5.6-luna")
        self.assertIsNone(ag.catalog_pick("codex", "balanced"))
        self.assertIn(ag.catalog_pick("opencode", "big", free_only=True), ("opencode/big-pickle", "opencode/nemotron-3-ultra-free"))

    def test_pick_free_only_excludes_paid(self):
        ag = self.lib()
        self.assertNotEqual(ag.catalog_pick("opencode", "balanced", free_only=True), "opencode/gpt-5.4")

    def test_aa_scores_with_key_and_without(self):
        self.assertIsNone(self.agj("models", "--backend", "claude", "--rank")["models"][0]["score"])
        self.assertFalse([h for h in H.hits if h[0] == "/aa"])               # no key: never called
        rows = {r["id"]: r for r in self.agj("models", "--backend", "claude", "--rank", "--refresh", env={"AG_AA_KEY": "k123"})["models"]}
        self.assertEqual(rows["haiku"]["score"], 61.5); self.assertEqual(rows["haiku"]["speed"], 123.4)
        self.assertEqual(rows["haiku"]["tier"], "big")                       # score overrides name hint
        self.assertIn(("/aa", "k123"), H.hits)
        self.assertNotIn("k123", (self.root/"cache"/"aa.json").read_text())  # key never stored

    def test_aa_key_ref(self):
        (self.root/"cfg").mkdir(); (self.root/"cfg"/"routing.json").write_text(json.dumps({"catalog": {"aa_key_ref": "${MY_AA}"}}))
        rows = self.agj("models", "--backend", "claude", "--rank", env={"MY_AA": "k123"})["models"]
        self.assertTrue(any(r["score"] == 61.5 for r in rows))

    def test_network_failure_keeps_cache(self):
        self.agj("models", "--backend", "claude", "--rank")
        rows = self.agj("models", "--backend", "claude", "--rank", "--refresh",
            env={"AG_MODELSDEV_URL": "http://127.0.0.1:1/api.json"})["models"]
        self.assertEqual({r["id"]: r["source"] for r in rows}["haiku"], "models.dev:alias")
        rows = self.agj("models", "--backend", "claude", "--rank", env={"AG_CACHE_HOME": str(self.root/"c2"),
            "AG_MODELSDEV_URL": "http://127.0.0.1:1/api.json"})["models"]    # no cache, no net: heuristic, no crash
        self.assertEqual({r["id"]: r["source"] for r in rows}["haiku"], "heuristic")


if __name__ == "__main__":
    unittest.main()
