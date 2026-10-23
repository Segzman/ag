#!/usr/bin/env python3
"""ag link: port-map math, ss/lsof parse fixtures, ssh argv, authorized_keys line, init/mac-key CLI,
supervisor restart + backoff with a fake ssh on PATH, watch server (Host guard, /api/*) against a real
`ag spawn -- sh -c 'echo hi; sleep 1'` session. Stdlib, temp dirs, offline."""
import http.client, importlib.machinery, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, threading, time, unittest, urllib.parse
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_link_mod", AG); _s = importlib.util.spec_from_loader("ag_link_mod", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


class Env(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-link-t-"))
        self.home = self.td / "home"; self.sd = self.td / "state"; self.bin = self.td / "bin"
        for d in (self.home, self.bin): d.mkdir()
        self.env = dict(os.environ, AG_HOME=str(self.home), AG_CONFIG_HOME=str(self.home / "cfg"),
            AG_CACHE_HOME=str(self.home / "cache"), AG_AUTO_UPDATE="0",
            PATH=f"{self.bin}{os.pathsep}{os.environ['PATH']}")
        self.addCleanup(shutil.rmtree, self.td, True)
        self.addCleanup(self._reset_dirs)

    def _reset_dirs(self):
        ag._LINK_DIRS = []

    def run_ag(self, *a, timeout=90):
        return subprocess.run([sys.executable, AG, "--dir", str(self.sd), *a], capture_output=True, text=True, env=self.env, timeout=timeout)

    def j(self, *a):
        p = self.run_ag("--json", *a)
        return p, (json.loads(p.stdout) if p.stdout.strip() else {})

    def init_cfg(self, *extra):
        key = self.td / "keys" / "k"; key.parent.mkdir(parents=True, exist_ok=True); key.write_text("fakekey")
        return self.j("link", "init", "--to", "sekun@host.example", "--key", str(key), *extra)

    def cfgpath(self): return self.home / "cfg" / "link.json"
    def statuspath(self): return self.home / "cache" / "link-status.json"
    def pidpath(self): return self.home / "cache" / "link.pid"


class Pure(unittest.TestCase):
    def test_ports_map_and_collisions(self):
        m = ag._link_ports_map([3000, 8080], {"base": 41000})
        self.assertEqual([(f["vm"], f["mac"]) for f in m], [(3000, 41000), (8080, 41080)])
        m = ag._link_ports_map([100, 1100], {"base": 40000})  # base+(P%1000) collision -> bump
        self.assertEqual([(f["vm"], f["mac"]) for f in m], [(100, 40100), (1100, 40101)])
        self.assertEqual(ag._link_ports_map([100, 100], {"base": 40000}), [{"vm": 100, "mac": 40100, "title": "port 100"}])

    def test_ports_map_pinned(self):
        m = ag._link_ports_map([1100], {"base": 40000, "map": {"1100": 50000}})
        self.assertEqual([(f["vm"], f["mac"]) for f in m], [(1100, 50000)])
        m = ag._link_ports_map([1100, 1200], {"base": 50000, "map": {"1100": 50000}})  # pin is used: 1200 skips it
        self.assertEqual([(f["vm"], f["mac"]) for f in m], [(1100, 50000), (1200, 50200)])

    def test_forwards_watch_title(self):
        f = ag._link_forwards({"ports": [3000], "base": 41000, "watch_port": 7790})
        self.assertEqual([(x["vm"], x["mac"], x["title"]) for x in f], [(3000, 41000, "port 3000"), (7790, 41790, "watch")])
        f = ag._link_forwards({"ports": [3000], "base": 41000, "watch_port": 7790}, live=[3000], watch=False)
        self.assertEqual([(x["vm"], x["mac"], x["title"]) for x in f], [(3000, 41000, "port 3000")])

    def test_parse_ss(self):
        s = "\n".join([
            "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:* users:(\"x\",pid=1)",
            "LISTEN 0 4096 *:22 0.0.0.0:* ",
            "LISTEN 0 511 [::]:3000 [::]:* "])
        self.assertEqual(ag._link_ss_ports(s), {8080, 22, 3000})

    def test_parse_lsof(self):
        s = "\n".join(["p1001", "n*:22", "n127.0.0.1:3000", "n[::1]:5000", "p1002", "n4", "n*:abc"])
        self.assertEqual(ag._link_lsof_ports(s), {22, 3000, 5000})

    def test_auto_exclude(self):
        old = ag._link_scan_ports; ag._link_scan_ports = lambda: {22, 3000, 9999}
        try:
            self.assertEqual(ag._link_auto_ports({"exclude": [9999]}), [3000])
        finally:
            ag._link_scan_ports = old

    def test_ssh_argv(self):
        fws = [{"vm": 3000, "mac": 41000, "title": "port 3000"}, {"vm": 7790, "mac": 41790, "title": "watch"}]
        cfg = {"to": "sekun@100.88.16.70", "key": "~/.ssh/ag_link", "base": 41000}
        self.assertEqual(ag._link_ssh_argv(cfg, fws),
            ["ssh", "-N", "-T", "-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes",
             "-o", "ServerAliveInterval=20", "-o", "ServerAliveCountMax=3",
             "-i", str(Path.home() / ".ssh" / "ag_link"),
             "-R", "localhost:41000:localhost:3000", "-R", "localhost:41790:localhost:7790",
             "sekun@100.88.16.70"])

    def test_ak_line(self):
        pub = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample ag-link@vm"
        self.assertEqual(ag._link_ak(pub + "\n"),
            'restrict,port-forwarding,permitlisten="localhost:*",permitopen="127.0.0.1:1",command="/bin/echo ag-link only" ' + pub)
        self.assertIsNone(ag._link_ak("not-a-key AAA comment"))
        self.assertIsNone(ag._link_ak(""))

    def test_tail_strip_and_offset(self):
        d = Path(tempfile.mkdtemp()); d.joinpath("sessions", "abc123").mkdir(parents=True)
        d.joinpath("sessions", "abc123", "output.log").write_bytes(b"\x1b[31mhi\x1b[0m\nworld\n")  # 18 bytes
        ag._LINK_DIRS[:] = [str(d)]; self.addCleanup(ag._LINK_DIRS.clear)
        other = Path(tempfile.mkdtemp()); other.joinpath("sessions", "x").mkdir(parents=True); other.joinpath("sessions", "x", "output.log").write_text("secret")
        self.assertEqual(ag._link_tail(other, "x", 0)["text"], "")       # not a watched dir
        self.assertEqual(ag._link_tail(d, "../../etc", 0)["text"], "")   # no traversal via id
        self.assertEqual(ag._link_tail(d, "abc123", 0), {"text": "hi\nworld\n", "next": 18})
        self.assertEqual(ag._link_tail(d, "abc123", 18), {"text": "", "next": 18})
        self.assertEqual(ag._link_tail(d, "abc123", 5), {"text": "hi\nworld\n", "next": 18})
        self.assertEqual(ag._link_tail(d, "abc123", 99), {"text": "", "next": 99})
        self.assertEqual(ag._link_tail(d, "nope", 0), {"text": "", "next": 0})
        shutil.rmtree(d, True)


class Cli(Env):
    def test_init_writes_cfg_and_key(self):
        key = self.td / "keys" / "k"
        p, o = self.j("link", "init", "--to", "sekun@host.example", "--key", str(key), "--ports", "3000,8080")
        self.assertEqual(p.returncode, 0, p.stderr)
        cfg = json.loads(self.cfgpath().read_text())
        self.assertEqual(cfg, {"to": "sekun@host.example", "key": str(key), "ports": [3000, 8080], "exclude": [22],
            "base": 41000, "watch_port": 7790, "map": {}})
        self.assertTrue(key.exists() and key.with_suffix(".pub").exists())  # ssh-keygen created it
        self.assertIn('command="/bin/echo ag-link only" ssh-', o["data"]["authorized_keys"])
        self.assertIn("authorized_keys", o["data"]["_text"])
        bad = self.run_ag("link", "init", "--to", "nohost", "--ports", "x")
        self.assertNotEqual(bad.returncode, 0)

    def test_mac_key_prints_only(self):
        pub = self.td / "pubkey"
        pub.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample ag-link@vm\n")
        p, o = self.j("link", "mac-key", str(pub))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(o["data"]["authorized_keys"], ag._link_ak(pub.read_text()))
        self.assertIn("restrict,port-forwarding", o["data"]["authorized_keys"])
        pub.write_text("garbage\n")
        self.assertNotEqual(self.run_ag("--json", "link", "mac-key", str(pub)).returncode, 0)

    def test_status(self):
        p, o = self.j("link", "status")
        self.assertEqual(p.returncode, 0)
        self.assertFalse(o["data"]["live"])
        self.assertIn("not running", self.run_ag("link", "status").stdout)
        (self.home / "cache").mkdir(parents=True, exist_ok=True)
        self.statuspath().write_text(json.dumps({"pid": os.getpid(), "up": True, "restarts": 3,
            "last_error": None, "forwards": [{"vm": 3000, "mac": 41000, "title": "port 3000"}]}))
        self.pidpath().write_text(f"{os.getpid()}\n")
        p, o = self.j("link", "status")
        self.assertTrue(o["data"]["live"])
        self.assertEqual(o["data"]["restarts"], 3)
        self.assertIn("localhost:41000 -> vm 3000", self.run_ag("link", "status").stdout)
        self.statuspath().write_text(json.dumps({"pid": 99999999}))
        self.pidpath().unlink()
        p, o = self.j("link", "status")
        self.assertFalse(o["data"]["live"])

    def test_run_url_and_spawn(self):
        p, o = self.init_cfg("--ports", "3000,8080")
        self.assertEqual(p.returncode, 0, p.stderr)
        p, o = self.j("link", "run", "--name", "t1", "--", "echo", "hi")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(re.fullmatch(r"http://localhost:41790/#\w+", o["data"]["url"]), o["data"]["url"])
        sid = o["data"]["id"]
        st = json.loads((self.sd / "sessions" / sid / "status.json").read_text())
        self.assertEqual(st["cmd"], "echo hi")
        self.assertNotEqual(self.run_ag("link", "run").returncode, 0)  # no command -> error

    def test_up_needs_cfg(self):
        self.assertNotEqual(self.run_ag("--json", "link", "up").returncode, 0)
        self.assertNotEqual(self.run_ag("--json", "link", "run", "--", "echo").returncode, 0)

    def test_supervise_restart_backoff_fake_ssh(self):
        self.assertEqual(self.init_cfg("--ports", "3000,8080")[0].returncode, 0)
        calls = self.td / "ssh-calls.txt"
        fake = self.bin / "ssh"
        fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> '{calls}'\nexit 3\n")
        fake.chmod(0o755)
        p = subprocess.Popen([sys.executable, AG, "--dir", str(self.sd), "link", "up", "--foreground", "--no-watch"],
            env=self.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (p.poll() is None and os.kill(p.pid, 9), p.wait(10)))
        deadline = time.time() + 30
        while time.time() < deadline:
            try: n = len(calls.read_text().splitlines())
            except OSError: n = 0
            if n >= 3: break
            if p.poll() is not None: self.fail(f"supervisor exited early: {n} calls")
            time.sleep(0.3)
        self.assertGreaterEqual(len(calls.read_text().splitlines()), 3)
        self.assertIsNone(p.poll(), "supervisor should still be running")
        st = json.loads(self.statuspath().read_text())
        self.assertTrue(st["up"])
        self.assertGreaterEqual(st["restarts"], 2)
        self.assertEqual([(f["vm"], f["mac"], f["title"]) for f in st["forwards"]],
            [(3000, 41000, "port 3000"), (8080, 41080, "port 8080")])
        self.assertIn("-o BatchMode=yes", calls.read_text())
        self.assertIn("-i ", calls.read_text())
        self.assertIn("-R localhost:41000:localhost:3000", calls.read_text())
        self.assertIn("-R localhost:41080:localhost:8080", calls.read_text())
        self.assertTrue(self.pidpath().exists())
        dr = self.run_ag("link", "down")
        self.assertEqual(dr.returncode, 0, dr.stderr)
        self.assertEqual(p.wait(timeout=15), 0)
        self.assertFalse(self.pidpath().exists())
        st = json.loads(self.statuspath().read_text())
        self.assertFalse(st["up"])
        dr = self.run_ag("link", "down")
        self.assertEqual(dr.returncode, 0)
        self.assertIn("not running", dr.stdout)

    def test_watch_server_guard_and_apis(self):
        p, o = self.j("--json", "spawn", "--", "sh", "-c", "echo hi; sleep 1")
        sid = json.loads(p.stdout)["data"]["id"]
        for _ in range(80):
            st = json.loads((self.sd / "sessions" / sid / "status.json").read_text())
            if not st.get("running"): break
            time.sleep(0.1)
        self.assertFalse(st["running"])
        self.sd.joinpath("wake", "jobs").mkdir(parents=True, exist_ok=True)
        (self.sd / "wake" / "jobs" / "j000001.json").write_text(json.dumps(
            {"id": "j000001", "agent": "w", "status": "done", "ts": "2026-10-23T00:00:00+00:00", "message": "m"}))
        ag._LINK_DIRS = [str(self.sd)]
        srv = ag._link_watch(0)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def get(path, host=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            h = {"Host": host} if host else {}
            c.request("GET", path, headers=h)
            r = c.getresponse(); body = r.read().decode(); c.close()
            return r.status, body
        try:
            self.assertEqual(get("/", "evil.example")[0], 403)          # DNS-rebinding guard
            self.assertEqual(get("/", "evil.example:7790")[0], 403)
            self.assertEqual(get("/")[0], 200)                           # default Host is 127.0.0.1:<port>
            self.assertEqual(get("/", host="localhost")[0], 200)
            s, b = get("/api/sessions")
            self.assertEqual(s, 200)
            row = [r for r in json.loads(b) if r["id"] == sid][0]
            self.assertEqual(sorted(row), sorted(["dir", "id", "name", "cmd", "running", "exit", "idle_s"]))
            self.assertFalse(row["running"]); self.assertEqual(row["exit"], 0); self.assertEqual(row["dir"], str(self.sd))
            s, b = get(f"/api/tail?dir={urllib.parse.quote(str(self.sd))}&id={sid}&since=0")
            self.assertEqual(s, 200)
            t = json.loads(b)
            self.assertIn("hi", t["text"]); self.assertIsInstance(t["next"], int)
            self.assertEqual(get(f"/api/tail?dir={urllib.parse.quote(str(self.sd))}&id={sid}&since={t['next']}&")[0], 200)
            self.assertEqual(get(f"/api/tail?dir=/etc&id={sid}")[0], 403)
            self.assertEqual(get(f"/api/tail?dir={urllib.parse.quote(str(self.sd))}&id=../etc")[0], 400)
            self.assertEqual(get("/api/nope")[0], 404)
            s, b = get("/api/status")
            self.assertEqual(s, 200); self.assertEqual(json.loads(b), {})
            page = get("/")[1]
            self.assertIn("api/tail", page); self.assertIn("ag link", page); self.assertIn("j000001", page)
        finally:
            srv.shutdown(); srv.server_close()

    def test_port_math_end_to_end_via_forwards(self):
        self.assertEqual(self.init_cfg("--ports", "3000,8080", "--base", "41000")[0].returncode, 0)
        f = ag._link_forwards(ag._link_cfg(), live=[3000, 8080])
        self.assertEqual([(x["vm"], x["mac"]) for x in f], [(3000, 41000), (7790, 41790), (8080, 41080)])


if __name__ == "__main__":
    unittest.main()
