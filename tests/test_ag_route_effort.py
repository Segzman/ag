#!/usr/bin/env python3
"""Routing effort: job `ag` entry + backend default `effort`, --set ...@EFFORT,
`ag route` display, `agents add --job` copying, built-in highest/xhigh, web selects.
Run: python3 tests/test_ag_route_effort.py
"""
import http.client
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ag_setup as base  # noqa: E402

AG = base.AG


class RouteEffort(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj

    def test_set_job_effort_roundtrip(self):
        self.agj("setup", "--yes", "--harness", "none", "--set", "debug=ag:opencode/opencode/b@highest")
        rt = json.loads((self.cfg / "routing.json").read_text())
        self.assertEqual(rt["profiles"]["claude"]["jobs"]["debug"]["ag"],
                         {"backend": "opencode", "model": "opencode/b", "effort": "highest"})
        d = self.agj("route", "debug")["data"]["jobs"]["debug"]
        self.assertEqual(d["ag"]["effort"], "highest")
        self.assertIn("opencode/opencode/b@highest", self.ag("route", "debug").stdout)
        self.assertNotIn("@", self.ag("route", "review").stdout.split("ag=")[1].split()[0])

    def test_set_bad_claude_effort_rejected(self):
        o = self.agj("setup", "--yes", "--set", "debug=ag:claude/opus@ultra", ok=False)
        self.assertIn("bad --set", o["error"])
        o = self.agj("setup", "--yes", "--set", "backend.claude.effort=ultra", ok=False)
        self.assertIn("bad --set", o["error"])
        # non-claude passes through unvalidated
        self.agj("setup", "--yes", "--harness", "none", "--set", "debug=ag:opencode/m@whatever")
        d = self.agj("route", "debug")["data"]["jobs"]["debug"]
        self.assertEqual(d["ag"]["effort"], "whatever")

    def test_backend_default_effort_and_add(self):
        self.agj("setup", "--yes", "--harness", "none", "--set", "backend.codex.effort=xhigh")
        d = self.agj("route")["data"]["backends"]["codex"]
        self.assertEqual(d["effort"], "xhigh")
        self.assertIn("model=default@xhigh", self.ag("route").stdout)
        self.agj("agents", "add", "w", "--backend", "codex")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual(rows["w"].get("effort"), "xhigh")
        self.agj("agents", "add", "v", "--backend", "codex", "--effort", "low")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual(rows["v"].get("effort"), "low")

    def test_add_job_copies_routing_effort(self):
        self.agj("setup", "--yes", "--harness", "none", "--set", "plan=ag:claude/opus@high")
        self.agj("agents", "add", "w", "--job", "plan")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual((rows["w"]["backend"], rows["w"]["model"], rows["w"].get("effort")),
                         ("claude", "opus", "high"))
        # explicit --effort wins over routing
        self.agj("agents", "add", "v", "--job", "plan", "--effort", "low")
        rows = {r["name"]: r for r in self.agj("agents", "list")["data"]["agents"]}
        self.assertEqual(rows["v"].get("effort"), "low")

    def test_builtin_effort(self):
        d = self.agj("route", "debug", env={"AG_HARNESS": "opencode"})["data"]["jobs"]["debug"]
        self.assertEqual(d["ag"]["effort"], "highest")
        t = self.ag("route", "debug", env={"AG_HARNESS": "opencode"}).stdout
        self.assertIn("@highest", t)
        d = self.agj("route", "hardest", env={"AG_HARNESS": "codex"})["data"]["jobs"]["hardest"]
        self.assertEqual(d["ag"]["effort"], "xhigh")
        d = self.agj("route", "implement", env={"AG_HARNESS": "codex"})["data"]["jobs"]["implement"]
        self.assertNotIn("effort", d["ag"])

    def test_per_field_layering(self):
        self.agj("setup", "--yes", "--harness", "none", "--scope", "global",
                 "--set", "debug=ag:codex/gpt-x@high")
        (self.proj / ".agent").mkdir()
        (self.proj / ".agent" / "routing.json").write_text(json.dumps(
            {"version": 2, "profiles": {"claude": {"jobs": {"debug": {"ag": {"effort": "low"}}}}}}))
        d = self.agj("route", "debug")["data"]["jobs"]["debug"]
        self.assertEqual(d["ag"], {"backend": "codex", "model": "gpt-x", "effort": "low"})
        self.assertEqual(d["source"]["ag"], "project")
        # explicit "" clears back to the preset default
        (self.proj / ".agent" / "routing.json").write_text(json.dumps(
            {"version": 2, "profiles": {"claude": {"jobs": {"debug": {"ag": {"effort": ""}}}}}}))
        d = self.agj("route", "debug")["data"]["jobs"]["debug"]
        self.assertNotIn("effort", d["ag"])
        self.assertEqual(d["ag"]["model"], "gpt-x")

    def test_skill_block_shows_effort(self):
        (self.home / ".claude").mkdir()
        self.agj("setup", "--yes", "--harness", "claude", "--set", "debug=ag:opencode/opencode/b@highest")
        t = (self.home / ".claude" / "CLAUDE.md").read_text()
        self.assertIn("opencode / opencode/b@highest", t)


class WebEffort(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj

    def start(self, *args, **env):
        self.agj("models")
        e = {**self.env, "AG_SETUP_NO_OPEN": "1", "AG_PROBE": "0", "AG_AUTO_UPDATE": "0", **env}
        self.p = subprocess.Popen([sys.executable, AG, "setup", "--ui", "web", "--harness", "claude", *args],
            cwd=self.proj, env=e, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (self.p.kill(), self.p.wait(), self.p.stdout.close(), self.p.stderr.close()))
        line = ""
        while "http://" not in line:
            line = self.p.stderr.readline()
            self.assertTrue(line, "no URL printed")
        self.url = re.search(r"http://\S+", line).group(0)
        self.host, self.port = re.match(r"http://([\d.]+):(\d+)/", self.url).groups()
        return self.url

    def req(self, path="", method="GET", body=None, headers=None):
        c = http.client.HTTPConnection(self.host, int(self.port), timeout=20)
        p = "/" + self.url.split("/", 3)[3] + path
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if body is not None:
            h["Content-Type"] = "application/json"
            body = json.dumps(body)
        c.request(method, p, body, h)
        r = c.getresponse()
        d = r.read()
        c.close()
        return r.status, d

    def data(self):
        s, d = self.req()
        self.assertEqual(s, 200)
        return json.loads(re.search(r'id="data">(.*?)</script>', d.decode(), re.S).group(1))

    def body(self, d, scope="global"):
        s = d["scopes"][scope]
        return {"scope": scope, "profiles": {p: {"jobs": v["jobs"]} for p, v in s["profiles"].items()},
                "backends": s["backends"], "harnesses": ["claude"], "claude_md": True}

    def test_effort_selects_and_save(self):
        self.start()
        d = self.data()
        html = self.req()[1].decode()
        self.assertIn("reasoning effort", html)
        self.assertIn("default", d["efforts"])
        self.assertIn("highest", d["efforts"])
        b = self.body(d)
        b["profiles"]["default"]["jobs"]["debug"]["ag"]["effort"] = "high"
        b["backends"]["codex"]["effort"] = "xhigh"
        s, raw = self.req(method="POST", path="save", body=b)
        self.assertEqual(s, 200, raw)
        out, _ = self.p.communicate(timeout=15)
        self.assertEqual(self.p.returncode, 0)
        doc = json.loads((self.cfg / "routing.json").read_text())
        self.assertEqual(doc["profiles"]["default"]["jobs"]["debug"]["ag"]["effort"], "high")
        self.assertEqual(doc["backends"]["codex"], {"effort": "xhigh"})
        self.assertIn("wrote", out)

    def test_invalid_effort_400(self):
        self.start()
        b = self.body(self.data())
        b["profiles"]["default"]["jobs"]["debug"]["ag"] = {"backend": "claude", "model": "opus", "effort": "ultra"}
        s, raw = self.req(method="POST", path="save", body=b)
        self.assertEqual(s, 400, raw)
        self.req(method="POST", path="cancel", body={})
        self.p.wait(timeout=15)


if __name__ == "__main__":
    unittest.main()
