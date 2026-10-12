#!/usr/bin/env python3
"""OpenRouter catalog: tools-support rows, endpoint dataPolicy classes, work routing.

Fake servers only (never openrouter.ai, never keys): AG_OR_MODELS_URL serves
tests/orcat_models.json (verbatim live entries, trimmed), AG_OR_SITE_URL serves
tests/orcat_providers_{zdr,train}.html (escaped Next.js dataPolicy payloads),
/orcat-key serves the account tier, AG_OPENROUTER_RANKINGS_URL serves the shared
or_fixture_* rankings. Run: python3 tests/test_ag_openrouter.py
"""
import http.server
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent / "ag"))
ORCAT_MODELS = (HERE / "orcat_models.json").read_bytes()
ORCAT_HTML = {"apodex/apodex-1.1-mini:free": (HERE / "orcat_providers_zdr.html").read_bytes(),
    "dots-studio/dots-3-note-preview:free": (HERE / "orcat_providers_train.html").read_bytes()}
ORFIX = {n: (HERE / f"or_fixture_{n}.json").read_bytes() for n in ("benchmarks", "performance", "models")}
SECRET = "or-sekrit-1"


class H(http.server.BaseHTTPRequestHandler):
    hits = []
    bodies = []

    def do_GET(self):
        H.hits.append((self.path, self.headers.get("Authorization")))
        if self.path == "/orcat-models":
            body = ORCAT_MODELS
        elif self.path.startswith("/orcat-models/") and self.path.endswith("/endpoints"):
            mid = self.path[len("/orcat-models/"):-len("/endpoints")]
            n = {"apodex/apodex-1.1-mini:free": 2, "dots-studio/dots-3-note-preview:free": 1}.get(mid)
            if n is None:
                self.send_response(404)
                self.end_headers()
                return
            body = json.dumps({"data": {"endpoints": [{"provider_name": f"p{i}"} for i in range(n)]}}).encode()
        elif self.path.startswith("/site/") and self.path.endswith("/providers"):
            body = ORCAT_HTML.get(self.path[len("/site/"):-len("/providers")])
            if body is None or not self.headers.get("User-Agent", "").startswith("Mozilla/"):
                self.send_response(404)
                self.end_headers()
                return
        elif self.path == "/orcat-key":
            if self.headers.get("Authorization") != "Bearer " + SECRET:
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"data": {"label": "t", "is_free_tier": True}}).encode()
        elif self.path.startswith("/or/") and self.path[4:] in ORFIX and self.headers.get("User-Agent", "").startswith("Mozilla/"):
            body = ORFIX[self.path[4:]]
        elif self.path == "/empty":
            body = b"{}"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            H.bodies.append(json.loads(raw.decode("utf-8", "replace") or "{}"))
        except Exception:
            H.bodies.append({})
        H.hits.append((self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != "Bearer " + SECRET:
            self.send_response(401)
            self.end_headers()
            return
        payload = json.dumps({"choices": [{"message": {"content": "world"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


class Orcat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        H.hits.clear()
        H.bodies.clear()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        (r / "proj").mkdir()
        self.state = r / "state"
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"), AG_CACHE_HOME=str(r / "cache"),
            AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0", AG_AUTO_UPDATE="0", HOME=str(r / "home"),
            AG_MODELSDEV_URL=f"http://127.0.0.1:{self.port}/empty",
            AG_OPENROUTER_RANKINGS_URL=f"http://127.0.0.1:{self.port}/or",
            AG_OR_MODELS_URL=f"http://127.0.0.1:{self.port}/orcat-models",
            AG_OR_SITE_URL=f"http://127.0.0.1:{self.port}/site",
            AG_OR_KEY_URL=f"http://127.0.0.1:{self.port}/orcat-key", ORKEY=SECRET)
        for k in ("AG_AA_KEY", "AG_CATALOG_AUTO"):
            self.env.pop(k, None)

    def ag(self, *args, extra=None):
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *args], capture_output=True, text=True,
            timeout=120, cwd=self.root / "proj", env={**self.env, **(extra or {})}, stdin=subprocess.DEVNULL)

    def agj(self, *args, extra=None):
        p = self.ag("--json", *args, extra=extra)
        o = json.loads(p.stdout)
        self.assertTrue(o["ok"], (p.stdout, p.stderr))
        return o["data"]

    def lib(self):
        old = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)

        def restore():
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.addCleanup(restore)
        return SourceFileLoader(f"ag_orcat_{id(self)}", AG).load_module()

    def rows(self, *args):
        return {r["id"]: r for r in self.agj("models", "--backend", "openrouter", *args)["models"]}

    def test_rows_tools_data_endpoints(self):
        rows = self.rows()
        self.assertEqual(set(rows), {"openrouter/apodex/apodex-1.1-mini:free", "openrouter/dots-studio/dots-3-note-preview:free",
            "openrouter/inclusionai/ling-3.1-flash", "openrouter/stepfun/step-5-preview"})  # switchyard has no tools
        apo, dots, ling, step = (rows[k] for k in sorted(rows))
        self.assertEqual((apo["data"], apo["endpoints"]), ("zdr", 2))
        self.assertEqual((dots["data"], dots["endpoints"]), ("train", 1))
        self.assertTrue(apo["free"] and dots["free"] and ling["free"] and not step["free"])  # ling $0/$0, step paid
        self.assertEqual((ling["tier"], ling["tier_source"], ling["score"]), ("big", "score", 41.1))
        self.assertIsNone(step["score"])
        self.assertIsNone(apo["score"])
        self.assertEqual(apo["tier_source"], "guess")
        self.assertEqual((ling["cost_in"], ling["cost_out"]), (0, 0))
        self.assertEqual((step["cost_in"], step["cost_out"]), (1e-06, 2.7e-06))

    def test_free_zdr_filters_and_sort(self):
        self.assertEqual(len(self.rows("--free")), 3)
        self.assertEqual([r["id"] for r in self.agj("models", "--backend", "openrouter", "--zdr")["models"]],
            ["openrouter/apodex/apodex-1.1-mini:free"])
        by_intel = [r["id"] for r in self.agj("models", "--backend", "openrouter", "--sort", "intel")["models"]]
        self.assertEqual(by_intel[0], "openrouter/inclusionai/ling-3.1-flash")  # only scored row first
        by_speed = [r["id"] for r in self.agj("models", "--backend", "openrouter", "--sort", "speed")["models"]]
        self.assertEqual(by_speed[:2], ["openrouter/inclusionai/ling-3.1-flash", "openrouter/stepfun/step-5-preview"])

    def test_text_and_status(self):
        p = self.ag("models", "--backend", "openrouter")
        self.assertIn("openrouter (4", p.stdout)
        self.assertIn("zdr", p.stdout)
        self.assertIn("eps", p.stdout)
        p = self.ag("models", "--status")
        self.assertIn("openrouter models", p.stdout)
        self.assertIn("openrouter policies", p.stdout)

    def test_privacy_classes(self):
        self.rows()  # populate the policies cache first (or_data_class is cache-only)
        ag = self.lib()
        self.assertEqual(ag.model_privacy("api", "openrouter/apodex/apodex-1.1-mini:free"), "zdr")
        self.assertEqual(ag.model_privacy("api", "openrouter/dots-studio/dots-3-note-preview:free"), "train")
        self.assertEqual(ag.model_privacy("api", "openrouter/zzz-no-such-model"), "train")  # unknown fails safe
        self.assertEqual(ag.model_privacy("opencode", "openrouter/apodex/apodex-1.1-mini:free"), "zdr")
        e = ag.privacy_error("opencode", "openrouter/apodex/apodex-1.1-mini:free", "work")
        self.assertIn("--backend api", e)  # opencode cannot pass provider routing
        self.assertIsNone(ag.privacy_error("opencode", "openrouter/apodex/apodex-1.1-mini:free", "public"))
        self.assertIsNone(ag.privacy_error("api", "openrouter/apodex/apodex-1.1-mini:free", "work"))

    def test_opencode_add_refused_api_work_routes_zdr(self):
        self.rows()  # populate the policies cache (add-time guard is cache-only)
        base = f"http://127.0.0.1:{self.port}/v1nope"
        self.assertEqual(self.ag("providers", "add", "openrouter", "--base-url", base,
            "--key-ref", "${ORKEY}").returncode, 0)
        p = self.ag("agents", "add", "orw", "--backend", "opencode", "--model", "openrouter/apodex/apodex-1.1-mini:free",
            "--dir", str(self.root / "proj"))
        self.assertNotEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("--backend api", p.stdout + p.stderr)
        self.assertEqual(self.ag("providers", "add", "openrouter", "--base-url", f"http://127.0.0.1:{self.port}",
            "--key-ref", "${ORKEY}").returncode, 0)
        self.assertEqual(self.ag("agents", "add", "orv", "--backend", "api",
            "--model", "openrouter/apodex/apodex-1.1-mini:free", "--dir", str(self.root / "proj")).returncode, 0)
        p = self.ag("--json", "chat", "send", "orv", "hello")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("world", p.stdout, (p.stdout, p.stderr))
        sent = H.bodies[-1]
        self.assertEqual(sent.get("model"), "apodex/apodex-1.1-mini:free")
        self.assertEqual(sent.get("provider"), {"data_collection": "deny", "zdr": True})
        self.assertEqual(self.ag("agents", "add", "orp", "--backend", "api",
            "--model", "openrouter/apodex/apodex-1.1-mini:free", "--data", "public",
            "--dir", str(self.root / "proj")).returncode, 0)
        H.bodies.clear()
        self.assertEqual(self.ag("--json", "chat", "send", "orp", "hello").returncode, 0)
        self.assertNotIn("provider", H.bodies[-1])
        if self.state.is_dir():
            blob = "".join(f.read_text(errors="replace") for f in self.state.rglob("*.jsonl"))
            self.assertNotIn(SECRET, blob)

    def test_slots_and_rpd(self):
        ag = self.lib()
        self.assertEqual(ag._rl_limit("api", "openrouter/apodex/apodex-1.1-mini:free"), 2)
        self.assertEqual(ag._rl_limit("api", "groq/some-model"), 0)
        self.assertTrue(ag._rl_is_free("api", "openrouter/x:free"))
        self.assertFalse(ag._rl_is_free("api", "groq/x"))
        self.assertEqual(self.ag("providers", "add", "openrouter", "--base-url", f"http://127.0.0.1:{self.port}",
            "--key-ref", "${ORKEY}").returncode, 0)
        d = ag.or_key_load(refresh=True)
        self.assertEqual(d.get("rpd"), 50)
        self.assertEqual(ag.or_account_rpd(), 50)
        self.assertEqual(ag._usage_rpd().get("openrouter"), 50)
        self.assertTrue(any(h[0] == "/orcat-key" and h[1] == "Bearer " + SECRET for h in H.hits), H.hits)

    def test_network_failure_degrades(self):
        rows = self.agj("models", "--backend", "openrouter",
            extra={"AG_OR_MODELS_URL": "http://127.0.0.1:1/none", "AG_CACHE_HOME": str(self.root / "c2")})["models"]
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
