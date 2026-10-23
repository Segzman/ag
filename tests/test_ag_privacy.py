#!/usr/bin/env python3
"""Data-retention guard (privacy): model_privacy table + zen refresh, --data, turn-time refuse, safe default.
Run: python3 tests/test_ag_privacy.py
"""
import http.server, json, os, subprocess, sys, tempfile, threading, unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

HERE = Path(__file__).resolve().parent
AG = os.environ.get("AG_BIN", str(HERE.parent / "ag"))
FAKE_OPENCODE = """#!/bin/sh
echo "launched $@" >> "$FAKE_DIR/launched.log"
if [ "$1" = models ]; then cat "$FAKE_DIR/oc.ids"; exit 0; fi
echo '{"type":"text","sessionID":"ses_1","part":{"type":"text","text":"hello"}}}'
"""
IDS = ["opencode/space-bunny-free", "opencode/muse-spark-1.3-contributor-free", "opencode/mystery-zzz-free"]


class ZH(http.server.BaseHTTPRequestHandler):
    body = b'{}'
    hits = []
    def do_GET(self):
        ZH.hits.append(self.path)
        self.send_response(200); self.end_headers(); self.wfile.write(ZH.body)
    def log_message(self, *a): pass


class Privacy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = http.server.HTTPServer(("127.0.0.1", 0), ZH)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        ZH.hits.clear(); ZH.body = b'{}'
        self._td = tempfile.TemporaryDirectory(); self.addCleanup(self._td.cleanup)
        r = self.root = Path(self._td.name).resolve()
        for d in ("home/.codex", "fake", "proj", "cfg", "cache", "state"): (r / d).mkdir(parents=True)
        (r / "fake" / "opencode").write_text(FAKE_OPENCODE); (r / "fake" / "opencode").chmod(0o755)
        (r / "fake" / "oc.ids").write_text("\n".join(IDS) + "\n")
        (r / "home/.codex/models_cache.json").write_text(json.dumps({"models": []}))
        self.env = dict(os.environ, AG_HOME=str(r / "home"), AG_CONFIG_HOME=str(r / "cfg"),
            AG_CACHE_HOME=str(r / "cache"), AGENT_CLI_DIR=str(r / "state"), AG_PROBE="0",
            AG_AUTO_UPDATE="0", AG_CATALOG_AUTO="0", AG_ZEN_URL="off", HOME=str(r / "home"),
            PATH=f"{r / 'fake'}:/usr/bin:/bin", AG_MODELSDEV_URL="http://127.0.0.1:1/api.json",
            AG_OPENROUTER_RANKINGS_URL="off", FAKE_DIR=str(r / "fake"))
        self.env.pop("AG_PRIVACY", None); self.env.pop("AG_AA_KEY", None)

    def run_ag(self, *args, env=None, want_ok=True):
        p = subprocess.run([sys.executable, AG, *args], capture_output=True, text=True, timeout=60,
            cwd=self.root / "proj", env={**self.env, **(env or {})}, stdin=subprocess.DEVNULL)
        return p

    def agj(self, *args, env=None, ok=True):
        p = self.run_ag("--json", *args, env=env)
        o = json.loads(p.stdout)
        self.assertEqual(o["ok"], ok, (args, p.stdout, p.stderr))
        return o

    def lib(self, **env):
        old = {k: os.environ.get(k) for k in self.env}; os.environ.update({**self.env, **env})
        self.addCleanup(lambda: [os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v) for k, v in old.items()])
        return _load_src(SourceFileLoader(f"ag_priv_{id(self)}_{len(old)}", AG))

    # ---- pure table ----
    def test_builtin_table_and_failsafe(self):
        ag = self.lib()
        self.assertEqual(ag.OPENCODE_MODEL_DEFAULT, "opencode/space-bunny-free")
        for m in ("opencode/space-bunny-free", "opencode/longcat-2.5-preview-free", "opencode/step-5-preview-free"):
            self.assertEqual(ag.model_privacy("opencode", m), "zdr", m)
        for m in ("opencode/muse-spark-1.3-contributor-free", "opencode/big-pickle", "opencode/exo-free",
                "opencode/mimo-v2.6-flash-free", "opencode/ling-3.1-flash-free", "opencode/ling-3.0-flash-fin-free",
                "opencode/nemotron-3-ultra-free", "opencode/nemotron-3.5-lightning-free"):
            self.assertEqual(ag.model_privacy("opencode", m), "train", m)
        self.assertEqual(ag.model_privacy("opencode", "opencode/mystery-zzz-free"), "train")  # unknown free = train
        self.assertEqual(ag.model_privacy("claude", "opus"), "retain")
        self.assertEqual(ag.model_privacy("codex", "gpt-x"), "retain")
        self.assertEqual(ag.model_privacy("echo", "default"), "zdr")
        self.assertEqual(ag.model_privacy("opencode", "default"), "zdr")
        self.assertEqual(ag._zen_parse(json.dumps({"zdr": ["opencode/a-free"]}))["zdr"], ["opencode/a-free"])
        e = ag.privacy_error("opencode", "opencode/muse-spark-1.3-contributor-free", "work")
        self.assertIn("may train on your data (opencode docs)", e)
        self.assertIn("space-bunny-free, longcat-2.5-preview-free, step-5-preview-free", e)
        self.assertIsNone(ag.privacy_error("opencode", "opencode/space-bunny-free", "work"))
        self.assertIsNone(ag.privacy_error("opencode", "opencode/muse-spark-1.3-contributor-free", "public"))

    def test_zen_refresh_cache_and_failure(self):
        ZH.body = json.dumps({"zdr": ["opencode/mystery-zzz-free"], "retain": [], "train": []}).encode()
        ag = self.lib(AG_ZEN_URL=f"http://127.0.0.1:{self.port}/zen")
        d = ag.zen_privacy_load(refresh=True)
        self.assertEqual(d["zdr"], ["opencode/mystery-zzz-free"])
        self.assertEqual(ag.model_privacy("opencode", "opencode/mystery-zzz-free"), "zdr")  # live overrides failsafe
        ZH.body = b'not json {{{'
        d2 = ag.zen_privacy_load(refresh=True)  # parse failure keeps last good
        self.assertEqual(ag.model_privacy("opencode", "opencode/mystery-zzz-free"), "zdr", d2)
        (self.root / "cache2").mkdir(exist_ok=True)
        ag2 = self.lib(AG_ZEN_URL="off", AG_CACHE_HOME=str(self.root / "cache2"))
        self.assertEqual(ag2.model_privacy("opencode", "opencode/mystery-zzz-free"), "train")

    # ---- agents add/set ----
    def test_add_default_work_and_refuse_train(self):
        o = self.agj("agents", "add", "w1", "--backend", "opencode", "--model", "opencode/muse-spark-1.3-contributor-free", ok=False)
        self.assertIn("may train on your data", o["error"])
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertNotIn("w1", rows)  # refused: nothing written
        self.agj("agents", "add", "w1", "--backend", "opencode", "--model", "opencode/muse-spark-1.3-contributor-free", "--data", "public")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual(rows["w1"]["data"], "public")
        self.agj("agents", "add", "w2", "--backend", "opencode", "--model", "opencode/space-bunny-free")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual(rows["w2"]["data"], "work")  # default work
        o = self.agj("agents", "set", "w2", "--model", "opencode/muse-spark-1.3-contributor-free", ok=False)
        self.assertIn("may train on your data", o["error"])
        self.agj("agents", "set", "w2", "--data", "public")
        self.agj("agents", "set", "w2", "--model", "opencode/muse-spark-1.3-contributor-free")
        o = self.agj("agents", "set", "w2", "--data", "work", ok=False)  # back to work on train = refuse
        self.assertIn("may train on your data", o["error"])

    def test_privacy_off_and_default_override(self):
        self.agj("agents", "add", "w", "--backend", "opencode", "--model", "opencode/muse-spark-1.3-contributor-free",
            env={"AG_PRIVACY": "off"})
        (self.root / "cfg").mkdir(exist_ok=True)
        (self.root / "cfg" / "routing.json").write_text(json.dumps({"version": 2, "privacy": {"default": "public"}}))
        self.agj("agents", "add", "p1", "--backend", "opencode", "--model", "opencode/muse-spark-1.3-contributor-free")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual(rows["p1"]["data"], "public")

    # ---- turn time ----
    def test_turn_refuses_without_launch(self):
        self.agj("agents", "add", "w", "--backend", "opencode", "--model", "opencode/muse-spark-1.3-contributor-free",
            "--data", "public")
        self.agj("agents", "set", "w", "--data", "work", env={"AG_PRIVACY": "off"})  # force work+train combo
        launched = self.root / "fake" / "launched.log"
        if launched.exists(): launched.unlink()
        p = self.run_ag("--json", "chat", "send", "w", "hi")
        o = json.loads(p.stdout)
        self.assertFalse(o["ok"]); self.assertIn("may train on your data", o["error"])
        self.assertEqual(p.returncode, 2)
        self.assertFalse(launched.exists(), "train model must never launch")
        log = self.agj("chat", "log", "w")["data"]["log"]
        self.assertTrue(any(r.get("role") == "error" and "may train on your data" in r.get("text", "") for r in log))
        # public sends
        self.agj("agents", "set", "w", "--data", "public")
        p = self.run_ag("--json", "chat", "send", "w", "hi")
        o = json.loads(p.stdout)
        self.assertTrue(o["ok"], (p.stdout, p.stderr))
        self.assertTrue(launched.exists())

    def test_wake_refuses_at_call_time(self):
        self.agj("agents", "add", "w", "--backend", "opencode", "--model", "opencode/muse-spark-1.3-contributor-free",
            "--data", "public")
        self.agj("agents", "set", "w", "--data", "work", env={"AG_PRIVACY": "off"})
        p = self.run_ag("--json", "wake", "w", "hi")
        o = json.loads(p.stdout)
        self.assertFalse(o["ok"]); self.assertIn("may train on your data", o["error"])
        self.assertEqual(p.returncode, 2)

    # ---- catalog ----
    def test_catalog_data_and_zdr_filter(self):
        d = self.agj("models", "--backend", "opencode", "--rank")["data"]
        rows = {r["id"]: r for r in d["models"]}
        self.assertEqual(rows["opencode/space-bunny-free"]["data"], "zdr")
        self.assertEqual(rows["opencode/muse-spark-1.3-contributor-free"]["data"], "train")
        self.assertEqual(rows["opencode/mystery-zzz-free"]["data"], "train")
        z = self.agj("models", "--backend", "opencode", "--rank", "--zdr")["data"]
        self.assertTrue(z["models"] and all(r["data"] == "zdr" for r in z["models"]))
        self.assertIn("data", self.run_ag("models", "--backend", "opencode", "--rank").stdout)
        ag = self.lib()
        real = ag.catalog_rows
        ag.catalog_rows = lambda *a, **k: [
            {"backend": "opencode", "id": "opencode/train-big", "tier": "big", "free": True, "score": 90.0, "coding": 90.0, "speed": 10.0, "data": "train", "source": "heuristic", "released": None, "context": None},
            {"backend": "opencode", "id": "opencode/zdr-big", "tier": "big", "free": True, "score": 10.0, "coding": 10.0, "speed": 10.0, "data": "zdr", "source": "heuristic", "released": None, "context": None}]
        try: self.assertEqual(ag.catalog_pick("opencode", "big", free_only=True), "opencode/zdr-big")
        finally: ag.catalog_rows = real

    # ---- project setup ----
    def test_route_check_and_answers(self):
        (self.root / "proj" / ".agent").mkdir(exist_ok=True)
        d = self.agj("route", "--check")
        self.assertFalse(d["data"]["configured"])
        self.assertTrue(any(q["id"] == "data" for q in d["data"]["questions"]))
        self.assertIn('"data": "work|public"', d["data"]["answers_format"])
        self.agj("setup", "--scope", "project", "--answers", json.dumps({"use": "inherit", "other_harnesses": [], "data": "public"}))
        rt = json.loads((self.root / "proj" / ".agent" / "routing.json").read_text())
        self.assertEqual(rt["privacy"], {"default": "public"})
        self.assertTrue(rt["project"]["answered"])
        d = self.agj("route", "--check")
        self.assertTrue(d["data"]["configured"])
        o = self.agj("setup", "--scope", "project", "--answers", json.dumps({"use": "inherit", "data": "nope"}), ok=False)
        self.assertIn("bad answers.data", o["error"])


if __name__ == "__main__":
    unittest.main()
