#!/usr/bin/env python3
"""Rate-limiter death spiral (ag plan item 20): stalls counted as rate limits,
strikes that never decay, a per-model cap that never recovers, and `ag wakes`
showing "running" for jobs that are really waiting on a slot.

Run: python3 -m pytest -q tests/test_ag_limiter.py
or:  python3 tests/test_ag_limiter.py

Fake opencode behaviour driven by FAKE_MODE + calls.jsonl counter:
- stall:   n<=1 silent sleep 5s (AG_STALL_S=1 kills, retry); later ok
- stall2:  n<=2 silent sleep 5s (killed twice); later ok
- rlimit:  n==1 JSON {"type":"error","error":"Rate limit exceeded"} exit 1; later ok
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
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
if mode == "stall2":
    if n <= 2:
        time.sleep(5)
        sys.exit(1)
    ok()
if mode == "rlimit":
    if n == 1:
        rl()
    ok()
rl("unexpected mode " + mode)
'''


class LimiterUnit(unittest.TestCase):
    """Pure-function tests: classify / decay / wait-state display."""

    @classmethod
    def setUpClass(cls):
        from importlib.machinery import SourceFileLoader
        cls.mod = _load_src(SourceFileLoader("ag_limiter", AG))

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aglim-"))
        self.cache = self.td / "cache"
        self._old = dict(os.environ)
        os.environ["AG_CACHE_HOME"] = str(self.cache)
        os.environ.pop("AG_RATELIMIT_BASE_S", None)

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._old)
        shutil.rmtree(self.td, ignore_errors=True)

    def _cooldown_file(self, key, until, hits):
        p = self.mod._rl_cooldown_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"until": until, "hits": hits}))
        return p

    def test_stall_is_not_ratelimit(self):
        m = self.mod
        self.assertEqual(m.classify_turn_failure("opencode", 1, "stalled: no output for 5s (backend killed)", False), "stall")
        self.assertEqual(m.classify_turn_failure("opencode", 1, "Rate limit exceeded", False), "ratelimit")
        self.assertEqual(m.classify_turn_failure("opencode", 1, "API Error: 429 rate_limit_error", False), "ratelimit")
        self.assertEqual(m.classify_turn_failure("opencode", 1, "Endpoint is unavailable", False), "ratelimit")
        self.assertFalse(m._RATELIMIT_RE.search("stalled: no output for 5s (backend killed)"))
        self.assertIsNone(m.classify_turn_failure("opencode", 1, "stalled: no output for 5s (backend killed)", True))

    def test_hits_decay_after_expiry(self):
        m = self.mod
        now = time.time()
        key = "opencode/test-free"
        # expired 130s ago with 5 hits: 2 decay windows (60s each) -> 3 left
        self._cooldown_file(key, now - 130, 5)
        self.assertEqual(m._rl_read_cooldown(key)["hits"], 3)
        # expired 400s ago with 5 hits: 6 windows -> 0 left
        self._cooldown_file(key, now - 400, 5)
        self.assertEqual(m._rl_read_cooldown(key)["hits"], 0)
        # still cooling: no decay yet
        self._cooldown_file(key, now + 120, 3)
        d = m._rl_read_cooldown(key)
        self.assertEqual(d["hits"], 3)
        self.assertGreater(d["remaining"], 0)

    def test_new_hit_counts_from_decayed_value(self):
        m = self.mod
        now = time.time()
        key = "opencode/test-free"
        self._cooldown_file(key, now - 400, 5)  # long-quiet, fully decayed
        # exactly what _retry_plan does on a fresh 429 for this key:
        m._rl_write_cooldown(key, (m._rl_read_cooldown(key).get("hits") or 0) + 1)
        d = json.loads(m._rl_cooldown_path(key).read_text())
        self.assertEqual(d["hits"], 1)
        self.assertGreater(float(d["until"]), now)

    def test_wait_display(self):
        m = self.mod
        now = time.time()
        key = m._rl_key("opencode", "opencode/test-free")
        m._rl_write_cooldown(key, 1)  # default base 30s -> eta ~30s
        m._rl_wait_state_write("w1", "opencode/test-free", "cooldown", key)
        d = m._rl_wait_display({"status": "running", "agent": "w1"})
        self.assertEqual(d["kind"], "cooldown")
        self.assertEqual(d["model"], "opencode/test-free")
        self.assertTrue(1 <= (d["eta_s"] or 0) <= 31, d)
        self.assertIn("waiting-slot (opencode/test-free, ", m._wakes_display_status({"status": "running", "agent": "w1"}))
        # slot wait: no eta computable from lock files
        m._rl_wait_state_write("w1", "opencode/test-free", "slot", key)
        d = m._rl_wait_display({"status": "running", "agent": "w1"})
        self.assertEqual(d["kind"], "slot")
        self.assertIsNone(d["eta_s"])
        self.assertEqual(m._wakes_display_status({"status": "running", "agent": "w1"}),
                         "waiting-slot (opencode/test-free, ?)")
        # no marker / not running -> plain status
        m._rl_wait_state_clear("w1")
        self.assertIsNone(m._rl_wait_display({"status": "running", "agent": "w1"}))
        self.assertEqual(m._wakes_display_status({"status": "running", "agent": "w1"}), "running")
        self.assertIsNone(m._rl_wait_display({"status": "done", "agent": "w1"}))
        # stale marker (crashed wait) is ignored
        p = m._rl_wait_state_path("w1")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"agent": "w1", "model": "opencode/test-free", "kind": "slot",
                                 "key": key, "since": now - 120}))
        self.assertIsNone(m._rl_wait_display({"status": "running", "agent": "w1"}))

    def test_wait_cooldown_marks_and_clears(self):
        m = self.mod
        key = m._rl_key("opencode", "opencode/test-free")
        m._rl_write_cooldown(key, 1)
        os.environ["AG_RATELIMIT_BASE_S"] = "0.2"
        # shorten the cooldown to ~0.6s so the test stays fast
        p = m._rl_cooldown_path(key)
        d = json.loads(p.read_text()); d["until"] = time.time() + 0.6
        p.write_text(json.dumps(d))
        seen = []
        def run():
            seen.append(m._rl_wait_cooldown("opencode", "opencode/test-free", self.td, "w1", None))
        th = threading.Thread(target=run); th.start()
        deadline = time.time() + 5
        while time.time() < deadline and not m._rl_wait_state("w1"):
            time.sleep(0.05)
        self.assertIsNotNone(m._rl_wait_state("w1"), "no wait marker while cooling down")
        self.assertEqual(m._rl_wait_state("w1")["kind"], "cooldown")
        th.join(timeout=5)
        self.assertFalse(th.is_alive())
        self.assertGreater(seen[0], 0.3, seen)
        self.assertIsNone(m._rl_wait_state("w1"), "marker must clear when the wait ends")

    def test_acquire_one_marks_and_clears(self):
        m = self.mod
        key = "test-key"
        d = m._rl_slots_dir(key); d.mkdir(parents=True, exist_ok=True)
        lockp = d / "0.lock"
        lockp.touch(exist_ok=True)
        holder = open(lockp, "r+b")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX)  # hold the only slot
        out = []
        def run():
            out.append(m._rl_acquire_one("opencode", "opencode/test-free", self.td, "w1", None, _key=key, _lim=1))
        th = threading.Thread(target=run); th.start()
        try:
            deadline = time.time() + 5
            while time.time() < deadline and not m._rl_wait_state("w1"):
                time.sleep(0.05)
            self.assertIsNotNone(m._rl_wait_state("w1"), "no wait marker while waiting for a slot")
            self.assertEqual(m._rl_wait_state("w1")["kind"], "slot")
            self.assertIsNone(m._rl_wait_display({"status": "running", "agent": "w1"})["eta_s"])
            fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            holder.close()
            th.join(timeout=5)
            self.assertFalse(th.is_alive())
            self.assertIsNotNone(out[0][0], out)  # slot handle acquired
            m._rl_release_slot(out[0][0])
            self.assertIsNone(m._rl_wait_state("w1"), "marker must clear when the slot is acquired")
        finally:
            try: fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
            except Exception: pass
            try: holder.close()
            except Exception: pass


class LimiterE2E(unittest.TestCase):
    """Subprocess tests against the real CLI with a fake opencode backend."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="aglim-e2e-"))
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
                              capture_output=True, text=True, timeout=180, env=env)

    def calls(self):
        p = self.td / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def events(self):
        p = self.state / "events.jsonl"
        return p.read_text() if p.exists() else ""

    def log(self, name):
        p = self.state / "chats" / f"{name}.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def test_stall_writes_no_ratelimit_event(self):
        # Bug 1: a stall must not be recorded as a rate-limit hit.
        self.ag("agents", "add", "s1", "--backend", "opencode", "--role", "sub", "--dir", str(self.td))
        p = self.ag("--json", "chat", "send", "s1", "hello", mode="stall")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("FAKE_OK", p.stdout)
        self.assertEqual(len(self.calls()), 2, self.calls())
        self.assertNotIn('"type": "ratelimit"', self.events(), self.events())
        cd = self.cache / "ratelimit" / "cooldown"
        self.assertEqual(list(cd.glob("*.json")) if cd.is_dir() else [], [])

    def test_double_stall_stays_on_primary(self):
        # Bug 1 (escalation): two stalls must not trigger the fallback switch
        # (pre-fix: each stall counted as a ratelimit hit; rl hit 2 -> fallback).
        self.ag("agents", "add", "f1", "--backend", "opencode", "--model", "opencode/primary-free",
                "--role", "sub", "--dir", str(self.td), "--data", "public")
        self.ag("agents", "set", "f1", "--fallback", "opencode/fallback-free")
        p = self.ag("--json", "chat", "send", "f1", "hello", mode="stall2")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertIn("FAKE_OK", p.stdout)
        self.assertEqual(len(self.calls()), 3, self.calls())
        argv_text = " ".join(" ".join(c) for c in self.calls())
        self.assertIn("primary-free", argv_text)
        self.assertNotIn("fallback-free", argv_text, argv_text)
        rows = self.log("f1")
        self.assertFalse(any(r.get("role") == "tool" and "using fallback" in (r.get("text") or "") for r in rows),
                         [r.get("text") for r in rows if r.get("role") == "tool"])

    def test_rlimit_then_success_recovers(self):
        # Bug 3 (recovery path): a real 429 cools down, then a success clears it.
        self.ag("agents", "add", "r1", "--backend", "opencode", "--role", "sub", "--dir", str(self.td))
        p = self.ag("--json", "chat", "send", "r1", "hello", mode="rlimit")
        self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
        self.assertEqual(len(self.calls()), 2, self.calls())
        cd = self.cache / "ratelimit" / "cooldown"
        self.assertEqual(list(cd.glob("*.json")) if cd.is_dir() else [], [])

    def _wakes_job(self, jid="w-test1", agent="w1"):
        from importlib.machinery import SourceFileLoader
        mod = _load_src(SourceFileLoader("ag_limiter_wakes", AG))
        jobs = self.state / "wake" / "jobs"
        jobs.mkdir(parents=True, exist_ok=True)
        sess = self.state / "sessions" / "ses-test1"
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "status.json").write_text(json.dumps({"running": True, "exit": None}))
        (jobs / f"{jid}.json").write_text(json.dumps({
            "id": jid, "status": "running", "agent": agent, "session": "ses-test1",
            "ts": datetime.now(timezone.utc).isoformat(), "message": "hello", "trigger": "test"}))
        return mod

    def _refresh_marker(self, mod, agent, model, kind, key):
        """Keep a wait marker fresh in a background thread, like a real wait loop
        (2s refresh). Returns the stop event; the caller must set it in finally."""
        stop = threading.Event()
        def loop():
            while not stop.wait(1.0):
                try: mod._rl_wait_state_write(agent, model, kind, key)
                except Exception: pass
        threading.Thread(target=loop, daemon=True).start()
        return stop

    def test_wakes_shows_waiting_slot_cooldown(self):
        # Bug 4: a running job waiting out a cooldown shows waiting-slot (model, eta).
        mod = self._wakes_job()
        env = dict(self.env)
        key = mod._rl_key("opencode", "opencode/test-free")
        os.environ["AG_CACHE_HOME"] = str(self.cache)  # in-process module reads env dynamically
        stop = None
        try:
            mod._rl_write_cooldown(key, 1)  # base 30s -> eta ~30s
            mod._rl_wait_state_write("w1", "opencode/test-free", "cooldown", key)
            stop = self._refresh_marker(mod, "w1", "opencode/test-free", "cooldown", key)
            p = self.ag("wakes")
            self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
            self.assertIn("waiting-slot (opencode/test-free, ", p.stdout, p.stdout)
            pj = self.ag("--json", "wakes")
            row = [r for r in json.loads(pj.stdout)["data"]["wakes"] if r.get("id") == "w-test1"][0]
            self.assertEqual(row["status"], "running")
            self.assertEqual(row["waiting_slot"]["kind"], "cooldown")
            self.assertTrue(1 <= (row["waiting_slot"]["eta_s"] or 0) <= 31, row)
            # marker cleared -> plain running again
            stop.set(); stop = None
            mod._rl_wait_state_clear("w1")
            p2 = self.ag("wakes")
            self.assertIn("running", p2.stdout, p2.stdout)
            self.assertNotIn("waiting-slot", p2.stdout, p2.stdout)
        finally:
            if stop is not None: stop.set()
            os.environ.pop("AG_CACHE_HOME", None)

    def test_wakes_shows_waiting_slot_unknown_eta(self):
        # Bug 4: a slot wait has no computable eta -> "?" (never an invented number).
        mod = self._wakes_job()
        os.environ["AG_CACHE_HOME"] = str(self.cache)
        stop = None
        try:
            mod._rl_wait_state_write("w1", "opencode/test-free", "slot", "opencode/test-free")
            stop = self._refresh_marker(mod, "w1", "opencode/test-free", "slot", "opencode/test-free")
            p = self.ag("wakes")
            self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
            self.assertIn("waiting-slot (opencode/test-free, ?)", p.stdout, p.stdout)
        finally:
            if stop is not None: stop.set()
            os.environ.pop("AG_CACHE_HOME", None)


if __name__ == "__main__":
    unittest.main()
