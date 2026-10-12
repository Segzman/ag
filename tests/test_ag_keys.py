#!/usr/bin/env python3
"""Keys + providers (Keychain via `security -i`, ${keychain:NAME} refs, opencode registry).
Fake `security` on PATH records argv+stdin and simulates a keychain in $FAKE_DIR/store.json.
Fake HTTP /models server checks Bearer auth. Never real secrets; fakes only.
Run: python3 tests/test_ag_keys.py"""
import http.server, importlib.machinery, importlib.util, json, os, shlex, shutil, subprocess, sys, tempfile, threading, unittest
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

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
    if "add-generic-password" in (parts[:1] or [""]):
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
import json, os
from pathlib import Path
d = Path(os.environ["FAKE_DIR"])
(d/"env.json").write_text(json.dumps(dict(os.environ)))
try:
    cfg = os.environ.get("OPENCODE_CONFIG", "")
    (d/"opencode_cfg_path.txt").write_text(cfg)
    if cfg and Path(cfg).is_file(): (d/"opencode_cfg.json").write_text(Path(cfg).read_text())
except Exception: pass
print(json.dumps({"type": "text", "sessionID": "ses-1", "part": {"type": "text", "text": "hello"}}))
'''


class MH(http.server.BaseHTTPRequestHandler):
    expected = "sekrit-123"
    hits = []
    def do_GET(self):
        MH.hits.append((self.path, self.headers.get("Authorization")))
        if self.path.endswith("/models") and self.headers.get("Authorization") == "Bearer " + MH.expected:
            body = b'{"data":[]}'
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
        else:
            body = b'{"error":"unauthorized"}'
            self.send_response(401); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass


class KeysT(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), MH)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        MH.hits.clear()
        self.td = tempfile.mkdtemp(prefix="agkeys-"); td = Path(self.td)
        (td / "bin").mkdir()
        (td / "bin" / "security").write_text(FAKE_SECURITY); (td / "bin" / "security").chmod(0o755)
        (td / "bin" / "opencode").write_text(FAKE_OPENCODE); (td / "bin" / "opencode").chmod(0o755)
        for d in ("home", "cfg", "cache", "state"): (td / d).mkdir()
        self.old = dict(os.environ)
        os.environ.update(PATH=str(td / "bin") + os.pathsep + os.environ["PATH"], FAKE_DIR=self.td,
            AG_AUTO_UPDATE="0", AG_PROBE="0", AG_RTK="0", AG_SECRET_POPUP="0",
            AG_HOME=str(td / "home"), AG_CONFIG_HOME=str(td / "cfg"), AG_CACHE_HOME=str(td / "cache"),
            AG_KEYS_ALLOW_NONMAC="1", AG_SECURITY_BIN=str(td / "bin" / "security"),
            AG_ZEN_URL="off", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json", AG_OPENROUTER_RANKINGS_URL="off",
            TEST_SECRET="sekrit-123")
        os.environ.pop("AG_PRIVACY", None)
        self.env = dict(os.environ)
        self.sd = td / "state"
        _l = importlib.machinery.SourceFileLoader("ag_keys_%d" % id(self), AG); _s = importlib.util.spec_from_loader("ag_keys_%d" % id(self), _l)
        self.ag = importlib.util.module_from_spec(_s); _l.exec_module(self.ag)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.old)
        shutil.rmtree(self.td, ignore_errors=True)

    def cli(self, *a, ok=True, extra=None):
        env = dict(self.env, **(extra or {}))
        p = subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a], capture_output=True, text=True, env=env, timeout=60)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def argv_log(self): return (Path(self.td) / "argv.log").read_text() if (Path(self.td) / "argv.log").exists() else ""
    def stdin_log(self): return (Path(self.td) / "stdin.log").read_text() if (Path(self.td) / "stdin.log").exists() else ""

    def test_set_from_env_argv_never_secret(self):
        p = self.cli("keys", "set", "MYKEY", "--from-env", "TEST_SECRET", "--json")
        d = json.loads(p.stdout)["data"]; self.assertEqual(d["name"], "MYKEY")
        self.assertNotIn("sekrit-123", self.argv_log(), "secret must never be in argv")
        self.assertIn("-i", self.argv_log())
        store = json.loads((Path(self.td) / "store.json").read_text()); self.assertEqual(store["MYKEY"], "sekrit-123")
        meta = json.loads((Path(self.td) / "cfg" / "keys.json").read_text())
        self.assertIn("MYKEY", meta); self.assertNotIn("sekrit-123", json.dumps(meta))

    def test_set_dialog_via_askpass_cmd(self):
        p = self.cli("keys", "set", "DKEY", "--json", extra={"AG_ASKPASS_CMD": "printf sekrit-123", "AG_SECRET_POPUP": "1"})
        self.assertEqual(json.loads(p.stdout)["data"]["name"], "DKEY")
        self.assertNotIn("sekrit-123", self.argv_log())
        store = json.loads((Path(self.td) / "store.json").read_text()); self.assertEqual(store["DKEY"], "sekrit-123")

    def test_list_never_values(self):
        self.cli("keys", "set", "A", "--from-env", "TEST_SECRET")
        p = self.cli("keys", "list", "--json"); d = json.loads(p.stdout)["data"]
        self.assertTrue(any(r["name"] == "A" for r in d["keys"]))
        self.assertNotIn("sekrit-123", p.stdout)
        t = self.cli("keys", "list"); self.assertNotIn("sekrit-123", t.stdout + t.stderr)

    def test_rm(self):
        self.cli("keys", "set", "R", "--from-env", "TEST_SECRET")
        self.cli("keys", "rm", "R")
        p = self.cli("keys", "list", "--json"); self.assertFalse(any(r["name"] == "R" for r in json.loads(p.stdout)["data"]["keys"]))

    def test_keys_test_ok_and_401_never_key(self):
        self.cli("keys", "set", "K1", "--from-env", "TEST_SECRET")
        base = f"http://127.0.0.1:{self.port}/prov"
        self.cli("providers", "add", "myprov", "--base-url", base, "--key-ref", "keychain:K1", "--privacy", "retain")
        p = self.cli("keys", "test", "K1", "--provider", "myprov")
        self.assertIn("ok:", p.stdout); self.assertNotIn("sekrit-123", p.stdout + p.stderr)
        self.assertTrue(any(h[0].endswith("/models") and h[1] == "Bearer sekrit-123" for h in MH.hits))
        MH.expected = "other-value"
        p2 = self.cli("keys", "test", "K1", "--provider", "myprov")
        self.assertIn("401", p2.stdout); self.assertNotIn("sekrit-123", p2.stdout + p2.stderr)
        MH.expected = "sekrit-123"

    def test_keychain_ref_in_agent_env(self):
        self.cli("keys", "set", "EKEY", "--from-env", "TEST_SECRET")
        self.assertEqual(self.ag.env_check({"MY_TOKEN": "${keychain:EKEY}"}, []), "")
        self.assertTrue(self.ag.env_check({"MY_TOKEN": "literal"}, []))
        self.cli("agents", "add", "w", "--backend", "echo", "--env", "MY_TOKEN=${keychain:EKEY}")
        p = self.cli("agents", "env", "w", "--json"); d = json.loads(p.stdout)["data"]
        self.assertEqual(d["set"]["MY_TOKEN"], "REDACTED"); self.assertNotIn("sekrit-123", p.stdout)

    def test_providers_preset_and_no_secrets(self):
        self.cli("keys", "set", "GKEY", "--from-env", "TEST_SECRET")
        p = self.cli("providers", "add", "groq", "--key-ref", "keychain:GKEY", "--json")
        d = json.loads(p.stdout)["data"]
        self.assertEqual(d["base_url"], "https://api.groq.com/openai/v1"); self.assertEqual(d["privacy"], "zdr"); self.assertEqual(d["max_concurrent"], 2)
        raw = (Path(self.td) / "cfg" / "providers.json").read_text()
        self.assertNotIn("sekrit-123", raw); self.assertIn("keychain:GKEY", raw)
        self.assertEqual(self.ag.model_privacy("opencode", "groq/gpt-oss-120b"), "zdr")
        self.assertEqual(self.ag._rl_limit("opencode", "groq/gpt-oss-120b"), 2)
        q = self.cli("providers", "show", "groq", "--json"); self.assertNotIn("sekrit-123", q.stdout)
        self.cli("providers", "add", "custom", "--base-url", "https://example.com/v1", "--key-ref", "keychain:GKEY", "--privacy", "retain", "--max-concurrent", "3", "--rpd", "99", "--model", "custom/m1", "--model", "custom/m2")
        self.assertEqual(self.ag.provider_limit("opencode", "custom/m1"), 3)
        self.assertEqual(self.ag.model_privacy("opencode", "custom/m1"), "retain")
        bad = self.cli("providers", "add", "bad", "--base-url", "https://x/v1", "--key-ref", "sekrit-literal-123456", ok=False)
        self.assertNotEqual(bad.returncode, 0)

    def test_opencode_fragment_and_child_env(self):
        self.cli("keys", "set", "PKEY", "--from-env", "TEST_SECRET")
        self.cli("providers", "add", "mygw", "--base-url", "https://example.com/v1", "--key-ref", "keychain:PKEY", "--privacy", "retain")
        self.cli("agents", "add", "w", "--backend", "opencode", "--model", "mygw/m1", "--data", "public", "--env", "MY_TOKEN=${keychain:PKEY}")
        p = self.cli("chat", "send", "w", "hi", extra={"FAKE_DIR": self.td})
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        env = json.loads((Path(self.td) / "env.json").read_text())
        self.assertEqual(env.get("MY_TOKEN"), "sekrit-123"); self.assertEqual(env.get("AG_KEY_Mygw".upper()), "sekrit-123")
        cfg = json.loads((Path(self.td) / "opencode_cfg.json").read_text())
        self.assertEqual(cfg["provider"]["mygw"]["options"]["apiKey"], "{env:AG_KEY_MYGW}")
        self.assertNotIn("sekrit-123", (Path(self.td) / "opencode_cfg.json").read_text())
        self.assertNotIn("sekrit-123", (Path(self.td) / "cfg" / "providers.json").read_text())

    def test_nonmac_refuse(self):
        ag = self.ag
        old_plat, old_allow = ag.sys.platform, os.environ.get("AG_KEYS_ALLOW_NONMAC")
        try:
            ag.sys.platform = "linux"
            if "AG_KEYS_ALLOW_NONMAC" in os.environ: del os.environ["AG_KEYS_ALLOW_NONMAC"]
            self.assertIn("macOS", ag._keys_need_mac() or "")
            self.assertIn("macOS", ag.keychain_set("X", "v"))
            os.environ["AG_KEYS_ALLOW_NONMAC"] = "1"
            self.assertIsNone(ag._keys_need_mac())
        finally:
            ag.sys.platform = old_plat
            if old_allow is None: os.environ.pop("AG_KEYS_ALLOW_NONMAC", None)
            else: os.environ["AG_KEYS_ALLOW_NONMAC"] = old_allow

    def test_pure_helpers(self):
        self.ag.keys_selfcheck()


if __name__ == "__main__": unittest.main()
