#!/usr/bin/env python3
"""OpenAI-compatible endpoints + local models (ollama/lmstudio) on the api backend. Fake stdlib server only.
Run: python3 tests/test_ag_api_compat.py
"""
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")


class H(http.server.BaseHTTPRequestHandler):
    seen = []
    def log_message(self, *a): pass
    def _send(self, body, ctype="application/json"):
        self.send_response(200); self.send_header("Content-Type", ctype); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        H.seen.append(("GET", self.path, self.headers.get("Authorization")))
        if self.path == "/v1/models": self._send(json.dumps({"data": [{"id": "qwen3:8b"}, {"id": "llama3"}]}).encode())
        else: self.send_error(404)
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); body = json.loads(self.rfile.read(n) or b"{}")
        H.seen.append(("POST", self.path, self.headers.get("Authorization"), body))
        ch = lambda d: "data: " + json.dumps({"choices": [{"delta": d}]}) + "\n\n"
        self._send((ch({"content": "local "}) + ch({"content": "reply"}) + "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3}}) + "\n\ndata: [DONE]\n\n").encode(), "text/event-stream")


class Compat(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H); cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.port}/v1"

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()

    def setUp(self):
        H.seen = []
        self.td = Path(tempfile.mkdtemp(prefix="agcompat-")); self.state = self.td / ".agent"
        self.env = dict(os.environ, AG_PROBE="0", AG_AUTO_UPDATE="0", AG_RTK="0", AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
            AG_CACHE_HOME=str(self.td / "cache"), NO_PROXY="*", no_proxy="*")

    def tearDown(self): shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a):
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a], capture_output=True, text=True, timeout=120, env=self.env)

    def test_custom_add_list_privacy_default(self):
        p = self.ag("--json", "providers", "add", "gw", "--base-url", "https://gw.example.com/v1", "--no-key"); self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = json.loads(p.stdout)["data"]; self.assertEqual((d["key_ref"], d["privacy"]), ("", "unknown")); self.assertNotIn("local", d)
        rows = json.loads(self.ag("--json", "providers", "list").stdout)["data"]["providers"]
        self.assertEqual([(r["id"], r["privacy"]) for r in rows], [("gw", "unknown")])
        self.assertEqual(json.loads(self.ag("--json", "providers", "add", "gz", "--base-url", "https://gz/v1", "--no-key", "--zdr").stdout)["data"]["privacy"], "zdr")
        self.assertNotEqual(self.ag("providers", "add", "x", "--base-url", "https://x/v1").returncode, 0)  # key still required unless --no-key

    def test_preset_show(self):
        d = json.loads(self.ag("--json", "providers", "show", "ollama").stdout)["data"]
        self.assertEqual((d["base_url"], d["privacy"]), ("http://localhost:11434/v1", "zdr"))
        self.assertEqual(json.loads(self.ag("--json", "providers", "show", "lmstudio").stdout)["data"]["base_url"], "http://localhost:1234/v1")

    def test_chat_keyless_work_ledger(self):
        self.ag("providers", "add", "ollama", "--base-url", self.base)  # override preset URL to the fake; stays keyless local
        self.assertEqual(self.ag("agents", "add", "l", "--backend", "api", "--model", "ollama/qwen3:8b", "--dir", str(self.td)).returncode, 0)  # --data work default
        p = self.ag("--json", "chat", "send", "l", "hi"); self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(json.loads(p.stdout)["data"]["reply"], "local reply")
        post = [s for s in H.seen if s[0] == "POST"][0]
        self.assertEqual(post[1], "/v1/chat/completions"); self.assertIsNone(post[2]); self.assertEqual(post[3]["model"], "qwen3:8b")
        rows = [json.loads(l) for l in (self.td / "cache" / "usage.jsonl").read_text().splitlines()]
        self.assertEqual((rows[-1]["cost"], rows[-1]["in"], rows[-1]["out"]), (0.0, 7, 3))

    def test_work_refused_for_unmarked_custom(self):
        self.ag("providers", "add", "gw", "--base-url", "https://gw.example.com/v1", "--no-key")
        p = self.ag("agents", "add", "w", "--backend", "api", "--model", "gw/m", "--dir", str(self.td))
        self.assertNotEqual(p.returncode, 0); self.assertIn("may train", p.stdout + p.stderr)
        self.assertEqual(self.ag("agents", "add", "p", "--backend", "api", "--model", "gw/m", "--data", "public", "--dir", str(self.td)).returncode, 0)
        self.ag("providers", "add", "gw", "--base-url", "https://gw.example.com/v1", "--no-key", "--zdr")
        self.assertEqual(self.ag("agents", "add", "w2", "--backend", "api", "--model", "gw/m", "--dir", str(self.td)).returncode, 0)

    def test_localhost_proxy_and_ollama_cloud_not_trusted(self):
        # a localhost URL may be a proxy (LiteLLM) to a cloud API; ollama *-cloud models run on ollama.com
        d = json.loads(self.ag("--json", "providers", "add", "px", "--base-url", "http://localhost:4000/v1", "--no-key").stdout)["data"]
        self.assertEqual(d["privacy"], "unknown"); self.assertNotIn("local", d)
        self.assertNotEqual(self.ag("agents", "add", "w", "--backend", "api", "--model", "px/m", "--dir", str(self.td)).returncode, 0)
        d = json.loads(self.ag("--json", "providers", "add", "px", "--base-url", "http://localhost:4000/v1", "--no-key", "--zdr").stdout)["data"]
        self.assertEqual((d["privacy"], d.get("local")), ("zdr", True))
        self.assertNotEqual(self.ag("agents", "add", "c", "--backend", "api", "--model", "ollama/gpt-oss:120b-cloud", "--dir", str(self.td)).returncode, 0)
        self.assertEqual(self.ag("agents", "add", "l", "--backend", "api", "--model", "ollama/qwen3:8b", "--dir", str(self.td)).returncode, 0)

    def test_models_listing_and_down(self):
        self.ag("providers", "add", "ollama", "--base-url", self.base)
        d = json.loads(self.ag("--json", "models", "--backend", "api", "--provider", "ollama").stdout)["data"]
        self.assertEqual(d["models"], ["qwen3:8b", "llama3"])
        self.ag("providers", "add", "ollama", "--base-url", "http://127.0.0.1:1/v1")
        p = self.ag("models", "--backend", "api", "--provider", "ollama"); self.assertNotEqual(p.returncode, 0)
        self.assertIn("cannot reach", p.stdout + p.stderr); self.assertIn("is the server running", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
