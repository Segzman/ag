#!/usr/bin/env python3
"""Rate-limit manager: slots + stall watchdog + cooldown + fallback, e2e with fake `opencode`.

Fake opencode behaviour driven by FAKE_MODE + calls.jsonl counter + argv model:
- stall:      n==1 silent sleep 5s (AG_STALL_S=1 kills, retry); later ok
- rlimit:     n==1 JSON {"type":"error","error":"Rate limit exceeded"} exit 1; later ok
- conc:       track max concurrency via conc.json flock; sleep 0.8s; ok
- primaryfail: argv -m contains PRIMARY_SUB -> rate-limit error; else ok
Run: python3 tests/test_ag_ratelimit.py
"""
import json, os, shutil, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE = r'''#!/usr/bin/env python3
import fcntl, json, os, sys, time
d = os.environ["FAKE_DIR"]; mode = os.environ.get("FAKE_MODE", "ok")
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(sys.argv[1:]) + "\n")
n = len(open(os.path.join(d, "calls.jsonl")).read().splitlines())
def ok(tag="FAKE_OK"):
    print(json.dumps({"type": "text", "timestamp": 2, "sessionID": "ses-1", "part": {"type": "text", "text": tag}}))
    sys.exit(0)
def rl(msg="Rate limit exceeded"):
    print(json.dumps({"type": "error", "timestamp": 1, "sessionID": "ses-1", "error": msg}))
    sys.exit(1)
if mode == "stall":
    if n == 1:
        time.sleep(5)
        sys.exit(1)
    ok()
if mode == "rlimit":
    if n == 1:
        rl()
    ok()
if mode == "conc":
    cj = os.path.join(d, "conc.json"); lk = os.path.join(d, "conc.lock")
    open(lk, "a").close()
    with open(lk, "r+b") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try: st = json.loads(open(cj).read()) if os.path.exists(cj) else {"cur": 0, "max": 0}
        except Exception: st = {"cur": 0, "max": 0}
        st["cur"] += 1; st["max"] = max(st["max"], st["cur"])
        open(cj, "w").write(json.dumps(st))
        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    time.sleep(0.8)
    with open(lk, "r+b") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
        try: st = json.loads(open(cj).read())
        except Exception: st = {"cur": 1, "max": 1}
        st["cur"] -= 1
        open(cj, "w").write(json.dumps(st))
        fcntl.flock(lf.fileno(), fcntl.LOCK_UN)
    ok()
if mode == "primaryfail":
    prim = os.environ.get("PRIMARY_SUB", "primary-free")
    if prim in " ".join(sys.argv[1:]):
        rl("Rate limit exceeded for primary")
    ok("FALLBACK_OK")
rl("unexpected mode " + mode)
'''


class RatelimitE2E(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agrl-"))
        self.state = self.td / ".agent"
        (self.td / "bin").mkdir()
        (self.td / "bin" / "opencode").write_text(FAKE)
        (self.td / "bin" / "opencode").chmod(0o755)
        self.cache = self.td / "cache"
        self.env = dict(os.environ, PATH=str(self.td / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_DIR=str(self.td), AG_PROBE="0", AG_AUTO_UPDATE="0", AG_RTK="0",
                        AG_HOME=str(self.td / "h"), AG_CONFIG_HOME=str(self.td / "c"),
                        AG_CACHE_HOME=str(self.cache),
                        AG_STALL_S="1", AG_RATELIMIT_BASE_S="0.2")

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a, mode="ok", extra=None):
        env = dict(self.env, FAKE_MODE=mode)
        if extra: env.update(extra)
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=120, env=env)

    def calls(self):
        p = self.td / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def log(self, name):
        p = self.state / "chats" / f"{name}.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_a_stall_kill_retry_succeeds(self):
        self.ag("agents", "add", "s1", "--backend", "opencode", "--role", "sub", "--dir", str(self.td))
        p = self.ag("--json", "chat", "send", "s1", "hello", mode="stall")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("FAKE_OK", p.stdout)
        self.assertEqual(len(self.calls()), 2, self.calls())

    def test_b_json_ratelimit_cooldown_and_wait(self):
        self.ag("agents", "add", "r1", "--backend", "opencode", "--role", "sub", "--dir", str(self.td))
        p = self.ag("--json", "chat", "send", "r1", "hello", mode="rlimit")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(len(self.calls()), 2, self.calls())
        rows = self.log("r1")
        self.assertTrue(any(r.get("role") == "tool" and "[ratelimit] cooling down" in (r.get("text") or "") for r in rows),
                        [r.get("text") for r in rows if r.get("role") == "tool"])
        # shared cooldown file is cleared after success
        ev = (self.state / "events.jsonl").read_text() if (self.state / "events.jsonl").exists() else ""
        self.assertIn("ratelimit", ev)

    def test_c_three_parallel_limit_two(self):
        self.env["AG_MAX_CONCURRENT"] = "2"  # explicit cap (default for free models is 4)
        for n in ("c1", "c2", "c3"):
            self.ag("agents", "add", n, "--backend", "opencode", "--role", "sub", "--dir", str(self.td))
        if (self.td / "calls.jsonl").exists(): (self.td / "calls.jsonl").unlink()
        if (self.td / "conc.json").exists(): (self.td / "conc.json").unlink()
        outs = {}

        def one(n):
            outs[n] = self.ag("chat", "send", n, "hi " + n, mode="conc")
        ths = [threading.Thread(target=one, args=(n,)) for n in ("c1", "c2", "c3")]
        for t in ths: t.start()
        for t in ths: t.join(timeout=60)
        for n, p in outs.items():
            self.assertEqual(p.returncode, 0, (n, p.stdout, p.stderr))
        mx = json.loads((self.td / "conc.json").read_text())["max"]
        self.assertEqual(mx, 2, mx)

    def test_d_fallback_after_two_hits(self):
        self.ag("agents", "add", "f1", "--backend", "opencode", "--model", "opencode/primary-free",
                "--role", "sub", "--dir", str(self.td), "--data", "public")  # fake free models count as training models
        self.ag("agents", "set", "f1", "--fallback", "opencode/fallback-free")
        p = self.ag("--json", "chat", "send", "f1", "hello", mode="primaryfail",
                    extra={"PRIMARY_SUB": "primary-free"})
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("FALLBACK_OK", p.stdout)
        argv_text = " ".join(" ".join(c) for c in self.calls())
        self.assertIn("fallback-free", argv_text)
        rows = self.log("f1")
        self.assertTrue(any(r.get("role") == "tool" and "using fallback" in (r.get("text") or "") for r in rows),
                        [r.get("text") for r in rows if r.get("role") == "tool"])
        # next turn tries the primary again (fallback not persisted as model)
        ags = {r["name"]: r for r in json.loads(self.ag("--json", "agents", "list").stdout)["data"]["agents"]}
        self.assertEqual(ags["f1"]["model"], "opencode/primary-free")

    def test_e_privacy_refusal_worse_fallback(self):
        from importlib.machinery import SourceFileLoader
        mod = _load_src(SourceFileLoader("ag_rl", AG))
        mod.model_privacy = lambda b, m: "zdr" if "good" in m else "train"
        a = {"name": "x", "backend": "opencode", "model": "opencode/good-free", "data": "work",
             "fallback": ["opencode/bad-free"]}
        self.assertEqual(mod._rl_pick_fallback(a, "opencode", "opencode/good-free"), "")
        a2 = {"name": "x", "backend": "opencode", "model": "opencode/good-free", "data": "public",
              "fallback": ["opencode/bad-free"]}
        # public agents may use the worse fallback (or it is at least considered; cooldown empty)
        self.assertEqual(mod._rl_pick_fallback(a2, "opencode", "opencode/good-free"), "opencode/bad-free")


if __name__ == "__main__":
    unittest.main()
