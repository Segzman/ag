#!/usr/bin/env python3
"""api backend + opencode context-overflow guard. Fakes only, never real keys.

Fake opencode `overflow` prints a ContextOverflowError event then keeps logging
(stall watchdog blind) -> ag must kill immediately, fail non-retryably with an
api hint (calls==1). Fake HTTP /chat/completions covers SSE + non-stream JSON,
429 with Retry-After -> cooldown + retry, usage -> ledger. Secrets never land in
argv/logs/chat/events.
Run: python3 tests/test_ag_api.py
"""
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
SECRET = "sekrit-123"
KEYNAME = "TESTAPIKEY"


def load_ag():
    return SourceFileLoader("ag_api", AG).load_module()


FAKE_SECURITY = r'''#!/usr/bin/env python3
import json, os, shlex, sys
from pathlib import Path
d = Path(os.environ["FAKE_DIR"])
(d/"argv.log").open("a").write(json.dumps(sys.argv[1:]) + "\n")
raw = sys.stdin.read() if not sys.stdin.isatty() else ""
try: (d/"stdin.log").open("a").write(raw + "\n---\n")
except Exception: pass
store_p = d/"store.json"
try: store = json.loads(store_p.read_text())
except Exception: store = {}
def save(): store_p.write_text(json.dumps(store))
a = sys.argv[1:]
if a[:1] == ["-i"]:
    try: parts = shlex.split(raw.strip().splitlines()[0] if raw.strip() else "")
    except Exception: parts = []
    name = val = ""
    if parts[:1] == ["add-generic-password"]:
        for i, t in enumerate(parts):
            if t == "-a" and i + 1 < len(parts): name = parts[i + 1]
            if t == "-w" and i + 1 < len(parts): val = parts[i + 1]
        if name: store[name] = val; save(); sys.exit(0)
    sys.exit(1)
if a[:1] == ["find-generic-password"]:
    name = ""
    for i, t in enumerate(a):
        if t == "-a" and i + 1 < len(a): name = a[i + 1]
    if name in store: sys.stdout.write(store[name]); sys.exit(0)
    sys.stderr.write("not found"); sys.exit(44)
if a[:1] == ["delete-generic-password"]:
    name = ""
    for i, t in enumerate(a):
        if t == "-a" and i + 1 < len(a): name = a[i + 1]
    store.pop(name, None); save(); sys.exit(0)
sys.exit(1)
'''

FAKE_OPENCODE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
d = Path(os.environ["FAKE_DIR"]); mode = os.environ.get("FAKE_MODE", "ok")
open(d/"calls.jsonl", "a").write(json.dumps(sys.argv[1:]) + "\n")
def txt(t, sid="ses-1"):
    print(json.dumps({"type": "text", "sessionID": sid, "part": {"type": "text", "text": t}}), flush=True)
if mode == "overflow":
    print(json.dumps({"type": "error", "sessionID": "ses-9", "error": {"name": "ContextOverflowError",
        "data": {"message": "Request too large for model groq/llama: Limit 8000, Requested 12345 tokens per minute (TPM)"}}}), flush=True)
    for _ in range(50):
        txt("still compacting logs")
        time.sleep(0.2)
    sys.exit(0)
txt("FAKE_OK")
'''


class Handler(http.server.BaseHTTPRequestHandler):
    mode = "sse"
    expected = SECRET
    hits = []
    bodies = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            body = {}
        Handler.hits.append(self.path)
        Handler.bodies.append(body)
        if self.path != "/v1/chat/completions":
            self.send_response(404)
            self.end_headers()
            return
        if self.headers.get("Authorization") != "Bearer " + Handler.expected:
            self.send_response(401)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if Handler.mode == "ratelimit" and len(Handler.hits) == 1:
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Retry-After", "1")
            self.send_header("x-ratelimit-reset-tokens", "1")
            self.end_headers()
            self.wfile.write(b'{"error":"rate limited"}')
            return
        if Handler.mode == "json" or not body.get("stream"):
            payload = json.dumps({"choices": [{"message": {"content": "world"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for chunk in ({"choices": [{"delta": {"content": "hello "}}]},
                      {"choices": [{"delta": {"content": "there"}}]},
                      {"usage": {"prompt_tokens": 10, "completion_tokens": 5}}):
            self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


class ApiT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.srv.server_address[1]
        cls.thr = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thr.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.srv.shutdown()
        except Exception:
            pass

    def setUp(self):
        Handler.mode = "sse"
        Handler.hits = []
        Handler.bodies = []
        self.td = Path(tempfile.mkdtemp(prefix="agapi-"))
        self.state = self.td / ".agent"
        (self.td / "bin").mkdir()
        (self.td / "bin" / "opencode").write_text(FAKE_OPENCODE)
        (self.td / "bin" / "opencode").chmod(0o755)
        (self.td / "bin" / "security").write_text(FAKE_SECURITY)
        (self.td / "bin" / "security").chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.td / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_DIR=str(self.td), AG_PROBE="0", AG_AUTO_UPDATE="0", AG_RTK="0",
                        AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
                        AG_CACHE_HOME=str(self.td / "cache"), AG_RATELIMIT_BASE_S="0.2",
                        AG_KEYS_ALLOW_NONMAC="1", AG_SECURITY_BIN=str(self.td / "bin" / "security"),
                        FAKEKEY=SECRET)
        base = f"http://127.0.0.1:{self.port}/v1"
        self.ag("keys", "set", KEYNAME, "--from-env", "FAKEKEY")
        p = self.ag("providers", "add", "groq", "--base-url", base,
                    "--key-ref", f"keychain:{KEYNAME}", "--privacy", "zdr")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a, mode="ok", extra=None):
        env = dict(self.env, FAKE_MODE=mode)
        if extra:
            env.update(extra)
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=120, env=env)

    def log(self, name):
        p = self.state / "chats" / f"{name}.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def calls(self):
        p = self.td / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def no_secret_in_state(self):
        for sub in ("chats",):
            d = self.state / sub
            if not d.is_dir():
                continue
            for f in d.glob("*.jsonl"):
                self.assertNotIn(SECRET, f.read_text(), f.name)
        ev = self.state / "events.jsonl"
        if ev.exists():
            self.assertNotIn(SECRET, ev.read_text())

    def test_overflow_kills_no_retry(self):
        self.ag("agents", "add", "ov", "--backend", "opencode", "--role", "sub", "--dir", str(self.td))
        t0 = time.monotonic()
        p = self.ag("--json", "chat", "send", "ov", "hello", mode="overflow")
        dt = time.monotonic() - t0
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        d = json.loads(p.stdout)
        self.assertEqual(d["data"].get("exit"), 1, d)
        self.assertIn("backend=api", d["data"].get("reply", ""), d)
        self.assertEqual(len(self.calls()), 1, self.calls())
        self.assertLess(dt, 30, dt)
        blob = d["data"].get("reply", "")
        self.assertIn("backend=api", blob, blob)
        rows = self.log("ov")
        errs = [r.get("text", "") for r in rows if r.get("role") == "error"]
        self.assertTrue(any("[provider]" in e and "backend=api" in e for e in errs), errs)
        self.no_secret_in_state()

    def test_api_sse_turn(self):
        self.assertEqual(self.ag("agents", "add", "a1", "--backend", "api",
                                 "--model", "groq/llama-test", "--dir", str(self.td)).returncode, 0)
        Handler.mode = "sse"
        p = self.ag("--json", "chat", "send", "a1", "hello")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("hello", p.stdout, (p.stdout, p.stderr))
        d = json.loads(p.stdout)
        self.assertEqual(d["data"].get("reply"), "hello there")
        self.assertEqual(d["data"].get("usage"), {"in": 10, "out": 5}, d["data"])
        rows = self.log("a1")
        self.assertTrue(any(r.get("role") == "agent" and "hello there" in r.get("text", "") for r in rows), rows)
        sent = Handler.bodies[-1]
        self.assertEqual(sent.get("model"), "llama-test")
        self.assertTrue(sent.get("stream"))
        self.assertTrue(any(m.get("role") == "user" and m.get("content") == "hello" for m in sent["messages"]))
        self.no_secret_in_state()
        # key never in argv
        argv_log = (self.td / "argv.log")
        if argv_log.exists():
            self.assertNotIn(SECRET, argv_log.read_text())

    def test_api_nonstream_helper(self):
        ag = load_ag()
        Handler.mode = "json"
        txt, us, rls, err = ag._api_http_once(f"http://127.0.0.1:{self.port}/v1", SECRET,
                                              "llama-test", [{"role": "user", "content": "hi"}], 10, stream=False)
        self.assertEqual(err, "")
        self.assertEqual(txt, "world")
        self.assertEqual(us, {"in": 7, "out": 3}, us)

    def test_api_429_cooldown_retry(self):
        self.assertEqual(self.ag("agents", "add", "a2", "--backend", "api",
                                 "--model", "groq/llama-test", "--dir", str(self.td)).returncode, 0)
        Handler.mode = "ratelimit"
        p = self.ag("--json", "chat", "send", "a2", "hello")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(len(Handler.hits), 2, Handler.hits)
        rows = self.log("a2")
        self.assertTrue(any(r.get("role") == "tool" and "[ratelimit] cooling down" in r.get("text", "")
                            for r in rows), [r.get("text") for r in rows])
        self.assertIn("hello there", p.stdout)
        self.no_secret_in_state()

    def test_api_validate_privacy_access(self):
        ag = load_ag()
        self.assertIn("PROVIDER/MODEL", ag.api_validate("api", "default") or "")
        self.assertIn("no such provider", ag.api_validate("api", "nosuch/x") or "")
        self.assertIsNone(ag.api_validate("api", "groq/llama-test"))
        # train provider refused for work, allowed for public
        base = f"http://127.0.0.1:{self.port}/v1"
        self.assertEqual(self.ag("providers", "add", "evil", "--base-url", base,
                                 "--key-ref", f"keychain:{KEYNAME}", "--privacy", "train").returncode, 0)
        p = self.ag("agents", "add", "w1", "--backend", "api", "--model", "evil/m",
                    "--dir", str(self.td))
        self.assertNotEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("train", p.stdout + p.stderr)
        self.assertEqual(self.ag("agents", "add", "p1", "--backend", "api", "--model", "evil/m",
                                 "--data", "public", "--dir", str(self.td)).returncode, 0)
        # access deny wins
        self.assertEqual(self.ag("access", "deny", "groq/*", "--scope", "global").returncode, 0)
        try:
            p = self.ag("agents", "add", "blocked", "--backend", "api", "--model", "groq/m",
                        "--dir", str(self.td))
            self.assertNotEqual(p.returncode, 0, (p.stdout, p.stderr))
        finally:
            self.ag("access", "rm", "groq/*", "--scope", "global")

    def test_api_crosscheck_agree(self):
        self.assertEqual(self.ag("agents", "add", "x1", "--backend", "api",
                                 "--model", "groq/llama-test", "--dir", str(self.td)).returncode, 0)
        self.assertEqual(self.ag("agents", "add", "x2", "--backend", "api",
                                 "--model", "groq/llama-test", "--dir", str(self.td)).returncode, 0)
        Handler.mode = "sse"
        p = self.ag("crosscheck", "x1", "x2", "say hi")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("AGREE", p.stdout, p.stdout)


if __name__ == "__main__":
    unittest.main()
