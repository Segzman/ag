#!/usr/bin/env python3
"""ag relay: dir + bare-git mailbox flows between two fake nodes (mac origin / muse target). Stdlib, echo backend, temp dirs."""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, time, unittest
from datetime import datetime
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod_relay", AG); _s = importlib.util.spec_from_loader("ag_mod_relay", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)


class Env(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-relay-t-"))
        self.root = self.mail = self.td / "mail"  # root = mailbox (dir transport: itself; git: the checkout)
        self.o = self.node("mac"); self.t = self.node("muse")  # o = origin, t = target
        self.addCleanup(__import__('shutil').rmtree, self.td, True)

    def node(self, name):
        d = self.td / name; home = d / "home"
        for x in (home, home / "cfg", home / "cache", d / "state", d / "work", d / "bin"): x.mkdir(parents=True)
        return {"name": name, "cfg": home / "cfg", "cache": home / "cache", "state": d / "state", "work": d / "work",
            "env": dict(os.environ, AG_HOME=str(home), AG_CONFIG_HOME=str(home / "cfg"), AG_CACHE_HOME=str(home / "cache"),
                AG_AUTO_UPDATE="0", PATH=f"{d / 'bin'}{os.pathsep}{os.environ['PATH']}")}

    def run_ag(self, n, *a, timeout=60, ok=True):
        p = subprocess.run([sys.executable, AG, "--dir", str(n["state"]), *a], capture_output=True, text=True, env=n["env"], timeout=timeout)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def j(self, n, *a, timeout=60):
        p = self.run_ag(n, "--json", *a, timeout=timeout)
        return p, (json.loads(p.stdout) if p.stdout.strip() else {})

    def rows(self, n): return json.loads((n["state"] / "schedules.json").read_text())
    def row(self, n, rid): return next(r for r in self.rows(n) if r.get("id") == rid)
    def put_rows(self, n, rows): (n["state"] / "schedules.json").write_text(json.dumps(rows))
    def agents(self, n): return ag.load_agents(n["state"])
    def cfg_of(self, n): return json.loads((n["cfg"] / "relay.json").read_text())
    def hb(self, n): return json.loads((self.root / "nodes" / f'{n["name"]}.json').read_text())
    def events(self, n): return (n["state"] / "events.jsonl").read_text()
    def iso(self, ts): return datetime.fromtimestamp(ts).isoformat()

    def setenv(self, n):  # point the in-process ag module at one node's env
        for k in ("AG_HOME", "AG_CONFIG_HOME", "AG_CACHE_HOME", "PATH"):
            if k in n["env"]: os.environ[k] = n["env"][k]
        os.environ["AG_AUTO_UPDATE"] = "0"

    def init(self, n, git=None, extra=()):
        args = ["relay", "init", "--node", n["name"], "--state", str(n["state"]), "--workdir", str(n["work"]), *extra]
        args += (["--git", git] if git else ["--dir", str(self.mail)])
        p, d = self.j(n, *args)
        return d["data"]["cfg"]

    def sync(self, n, ok=True, timeout=120):
        p, d = self.j(n, "relay", "sync", timeout=timeout)
        if ok: self.assertEqual(p.returncode, 0, p.stderr)
        return d.get("data") or {}

    def add_agent(self, n, name):
        p, _ = self.j(n, "agents", "add", name, "--backend", "echo", "--role", "sub")
        self.assertEqual(p.returncode, 0, p.stderr)

    def publish(self, sid, target, agent="w1", spec=None):
        p, d = self.j(self.o, "schedule", "add", agent, "--every", "15m", "--id", sid, "ping")
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = self.rows(self.o); r = next(x for x in rows if x["id"] == sid)
        r["on"], r["sdir"] = target, str(self.o["state"]); self.put_rows(self.o, rows)
        self.setenv(self.o)
        ag.relay_publish_schedule(ag.relay_cfg(), r, dict(spec or {}))

    def fire(self):  # tick the target's materialised row past due and wait for the wake job
        row = next(r for r in self.rows(self.t) if r.get("relay_id"))
        due = row["next_due"]
        self.run_ag(self.t, "schedule", "tick", "--now", self.iso(due + 1))
        row = next(r for r in self.rows(self.t) if r.get("relay_id"))
        self.assertTrue(row["last_job"])
        self.run_ag(self.t, "wakes", "--wait", str(row["last_job"]), "--timeout", "45", timeout=90)
        j = json.loads((self.t["state"] / "wake" / "jobs" / f'{row["last_job"]}.json').read_text())
        return row, j

    def reg(self, n): return json.loads((n["cfg"] / "schedule-dirs.json").read_text())


class Unit(Env):
    def test_relay_cfg_paths(self):
        self.setenv(self.o)
        self.assertIsNone(ag.relay_cfg())  # no relay.json yet
        cfg = self.init(self.o)
        self.assertEqual(cfg["node"], "mac"); self.assertEqual(cfg["transport"], "dir")
        self.assertEqual(cfg["url"], str(self.mail)); self.assertEqual(cfg["max_mode"], "edits")
        self.assertEqual(ag.relay_cfg()["node"], "mac")  # env-backed loader agrees
        self.assertEqual(str(ag.relay_root(ag.relay_cfg())), str(self.mail))
        (self.o["cfg"] / "relay.json").write_text(json.dumps({"node": "x", "transport": "smoke", "url": "u",
            "state": "s", "workdir": "w", "max_mode": "edits"}))
        self.assertIsNone(ag.relay_cfg())  # bad transport
        self.setenv(self.t)
        self.assertEqual(ag.relay_cfg(), None)

    def test_lift_and_node_name(self):
        subs = {"relay", "schedule", "agents"}
        self.assertEqual(ag._lift_trailing_dir(["relay", "init", "--dir", "/mb"], subs), (["relay", "init", "--dir", "/mb"], None))
        self.assertEqual(ag._lift_trailing_dir(["agents", "add", "a", "--dir", "/w"], subs), (["agents", "add", "a", "--dir", "/w"], None))
        self.assertEqual(ag._lift_trailing_dir(["schedule", "list", "--dir", "/s"], subs), (["schedule", "list"], "/s"))  # main() sets a.dir = "/s"
        self.assertTrue(ag._relay_node_name().strip())

    def test_bad_init_errors(self):
        p = self.run_ag(self.o, "relay", "init", "--git", "x", "--dir", str(self.mail), ok=False)
        self.assertIn("exactly one of", p.stdout + p.stderr)
        p = self.run_ag(self.o, "relay", "init", "--node", "Weird Node!", "--git", "x", ok=False)
        self.assertIn("bad --node", p.stdout + p.stderr)
        for sub in ("sync", "status", "results"):
            p = self.run_ag(self.o, "relay", sub, ok=False)
            self.assertIn("relay not set up", p.stdout + p.stderr)

    def test_update_skip(self): self.assertIn("relay", ag.UPDATE_SKIP)


class Flow(Env):
    def test_dir_flow(self): self._flow(git=None)

    def test_git_flow(self): self._flow(git=str(self.td / "bare"))

    def _flow(self, git):
        # git transport: target first = it pushes the empty repo's first commit; origin then clones beyond it
        if git:
            subprocess.run(["git", "init", "-q", "--bare", git], capture_output=True, check=True)
            self.root = self.t["cache"] / "relay" / "bare"
        self.init(self.t, git=git)
        self.init(self.o, git=git)
        if git: self.assertTrue((self.t["cache"] / "relay" / "bare" / ".git").exists())  # checkout under AG_CACHE_HOME
        self.add_agent(self.o, "w1")
        self.publish("s1", "muse", spec={"backend": "echo"})
        self.sync(self.o)  # git transport: pushes the published mail file (dir: extra heartbeat only)
        d = self.sync(self.t)
        self.assertEqual(d["materialized"], ["mac--s1"]); self.assertEqual(d["error"], None)
        r = self.row(self.t, "mac--s1")
        self.assertEqual(r["agent"], "relay-mac-w1"); self.assertEqual(r["message"], "ping")
        self.assertEqual(r["every_s"], 900); self.assertTrue(r["enabled"])
        self.assertTrue(r["next_due"], f"parked: {r!r}")
        self.assertEqual(r["relay_origin"], "mac"); self.assertEqual(r["relay_id"], "s1")
        self.assertNotIn("relay_reported", r)
        a = next(x for x in self.agents(self.t) if x["name"] == "relay-mac-w1")
        self.assertEqual(a["backend"], "echo"); self.assertEqual(a["dir"], str(self.t["work"] / "mac"))
        self.assertIn(str(self.t["state"].resolve()), self.reg(self.t))
        self.assertEqual(self.hb(self.t)["schedules_in"], 1)
        row, j = self.fire()
        self.assertEqual(j["status"], "done")
        self.assertEqual(row["runs"], 1); self.assertEqual(row["last_status"], "fired")
        d = self.sync(self.t)
        self.assertEqual(len(d["reported"]), 1)
        resf = self.root / "results" / "mac" / f'muse--s1--{int(row["last_due"])}.json'
        res = json.loads(resf.read_text())
        self.assertEqual(res["origin"], "mac"); self.assertEqual(res["target"], "muse"); self.assertEqual(res["id"], "s1")
        self.assertEqual(res["job"], row["last_job"]); self.assertIn("ping", res["reply"])
        self.assertEqual(res["status"], "done")
        self.assertEqual(self.row(self.t, "mac--s1")["relay_reported"], row["last_job"])
        self.assertEqual(self.sync(self.t)["reported"], [])  # idempotent: already reported
        d = self.sync(self.o)  # origin pulls the result back
        self.assertEqual(d["ingested"], [resf.name])
        orow = self.row(self.o, "s1")
        self.assertEqual(orow["runs"], 1); self.assertEqual(orow["last_due"], res["due"]); self.assertEqual(orow["last_status"], "remote done")
        self.assertEqual(json.loads((self.o["state"] / "relay" / "results" / resf.name).read_text())["reply"], res["reply"])
        self.assertIn("relay_result", self.events(self.o))
        cache = json.loads((self.o["cache"] / "relay-ingested.json").read_text())
        self.assertEqual(cache[resf.name]["id"], "s1"); self.assertTrue(resf.exists())  # fresh = kept
        p, o = self.j(self.o, "relay", "results")
        self.assertEqual(o["data"]["results"][0]["id"], "s1"); self.assertIn(resf.name[:12], p.stdout)
        p, o = self.j(self.o, "relay", "results", "--limit", "1"); self.assertEqual(len(o["data"]["results"]), 1)
        p = self.run_ag(self.o, "relay", "results", "--last"); self.assertIn("ping", p.stdout)
        p, o = self.j(self.o, "relay", "results", "--id", "zzz"); self.assertEqual(o["data"]["results"], [])
        p, o = self.j(self.t, "relay", "status")
        self.assertEqual(o["data"]["received"][0]["origin"], "mac"); self.assertEqual(o["data"]["sent"], [])
        self.assertIn("relay muse", p.stdout)
        p, o = self.j(self.o, "relay", "status")
        self.assertEqual([x["id"] for x in o["data"]["sent"]], ["s1"]); self.assertEqual(o["data"]["received"], [])
        self.assertEqual(o["data"]["peers"][0]["node"], "muse")
        hb = self.hb(self.t); hb["ag_commit"] = "deadbee"; hb["last_seen"] = time.time() - 8 * 86400  # doctor the peer heartbeat
        hb["backends"] = ["echo"]
        docroot = self.o["cache"] / "relay" / "bare" if git else self.root  # o's own mailbox view (its checkout)
        (docroot / "nodes" / "muse.json").write_text(json.dumps(hb))
        alerts = self.j(self.o, "relay", "status")[1]["data"]["alerts"]
        self.assertTrue(any("unseen" in x for x in alerts))
        self.assertTrue(any("commit differs (deadbee" in x for x in alerts))
        self.setenv(self.o)
        self.assertTrue(ag.relay_unpublish_schedule(self.cfg_of(self.o), "muse", "s1"))
        self.assertFalse(ag.relay_unpublish_schedule(self.cfg_of(self.o), "muse", "s1"))
        self.sync(self.o)  # git: push the deletion commit
        d = self.sync(self.t)
        self.assertEqual(d["removed"], ["mac--s1"])
        self.assertNotIn("mac--s1", [r["id"] for r in self.rows(self.t)])
        self.assertNotIn(str(self.t["state"].resolve()), self.reg(self.t))  # last row gone -> unregistered
        if git:
            out = subprocess.run(["git", "-C", str(self.o["cache"] / "relay" / "bare"), "log", "--oneline"],
                capture_output=True, text=True, check=True).stdout
            self.assertIn("relay: mac sync", out); self.assertIn("relay: muse sync", out)

    def test_bad_json_inbox_skipped(self):
        self.init(self.t); self.init(self.o); self.add_agent(self.o, "w1"); self.publish("s1", "muse", spec={"backend": "echo"})
        (self.root / "schedules" / "muse" / "garbage--x.json").write_text("{nope")
        p = self.run_ag(self.t, "--json", "relay", "sync", ok=False)
        self.assertEqual(p.returncode, 1)
        d = json.loads(p.stdout)["data"]
        self.assertEqual(d["materialized"], ["mac--s1"])
        self.assertIn("bad json", d["error"] or "")

    def test_max_mode_clamp_and_refused_spec(self):
        self.init(self.t, extra=("--max-mode", "ro")); self.init(self.o)
        self.add_agent(self.o, "w1"); self.add_agent(self.o, "w2")
        self.publish("s1", "muse", spec={"backend": "echo", "mode": "full"})
        d = self.sync(self.t)
        self.assertEqual(d["materialized"], ["mac--s1"]); self.assertEqual(d["error"], None)
        a = next(x for x in self.agents(self.t) if x["name"] == "relay-mac-w1")
        self.assertEqual(a.get("mode"), "ro")  # published full -> target clamps to its ceiling
        self.publish("s2", "muse", agent="w2", spec={"backend": "notabackend"})
        self.sync(self.t)
        r = self.row(self.t, "mac--s2")
        self.assertTrue(r["last_status"].startswith("error:"))
        self.assertIsNone(r["next_due"])  # parked: never fires while the agent is missing

    def test_unpublished_mail_pruned(self):  # mail file vanishes without unpublish (user --force rm)
        self.init(self.t); self.init(self.o); self.add_agent(self.o, "w1"); self.publish("s1", "muse", spec={"backend": "echo"})
        self.sync(self.o); self.sync(self.t)
        self.assertIn("mac--s1", [r["id"] for r in self.rows(self.t)])
        self.setenv(self.o); self.assertTrue(ag.relay_unpublish_schedule(self.cfg_of(self.o), "muse", "s1"))
        # skip o push for dir transport; target prunes directly
        d = self.sync(self.t)
        self.assertEqual(d["removed"], ["mac--s1"])


if __name__ == "__main__":
    unittest.main()
