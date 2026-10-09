#!/usr/bin/env python3
"""`ag setup --ui web`: real server in a child process (AG_SETUP_NO_OPEN=1), driven with urllib. Token/Host/Origin gates,
save writes files, invalid -> 400 + nothing written, dry-run diff, cancel, idle timeout, one <select> per job per profile.
Run: python3 tests/test_ag_web.py
"""
import http.client, json, os, re, subprocess, sys, unittest, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ag_setup as base  # noqa: E402

AG = base.AG


class Web(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj

    def start(self, *args, **env):
        self.agj("models")
        e = {**self.env, "AG_SETUP_NO_OPEN": "1", "AG_PROBE": "0", "AG_AUTO_UPDATE": "0", **env}
        self.p = subprocess.Popen([sys.executable, AG, "setup", "--ui", "web", "--harness", "claude", *args], cwd=self.proj, env=e,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (self.p.kill(), self.p.wait(), self.p.stdout.close(), self.p.stderr.close()))
        line = ""
        while "http://" not in line: line = self.p.stderr.readline(); self.assertTrue(line, "no URL printed")
        self.url = re.search(r"http://\S+", line).group(0)
        self.host, self.port = re.match(r"http://([\d.]+):(\d+)/", self.url).groups()
        return self.url

    def req(self, path="", method="GET", body=None, headers=None, base_path=None):
        c = http.client.HTTPConnection(self.host, int(self.port), timeout=20)
        p = (base_path if base_path is not None else "/" + self.url.split("/", 3)[3]) + path
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if body is not None: h["Content-Type"] = "application/json"; body = json.dumps(body)
        c.request(method, p, body, h); r = c.getresponse(); d = r.read(); c.close()
        return r.status, d, r

    def data(self):
        s, d, _ = self.req(); self.assertEqual(s, 200)
        return json.loads(re.search(r'id="data">(.*?)</script>', d.decode(), re.S).group(1))

    def body(self, d, scope="global"):
        s = d["scopes"][scope]
        return {"scope": scope, "profiles": {p: {"jobs": v["jobs"]} for p, v in s["profiles"].items()}, "backends": s["backends"],
            "harnesses": ["claude"], "claude_md": True}

    def test_gates(self):
        self.start()
        s, d, r = self.req(); self.assertEqual(s, 200)
        self.assertNotIn("Access-Control-Allow-Origin", dict(r.getheaders()))
        self.assertIn("default-src 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(self.req(base_path="/")[0], 403)
        self.assertEqual(self.req(base_path="/wrongtoken/")[0], 403)
        self.assertEqual(self.req(headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.req(headers={"Host": f"127.0.0.1:{int(self.port)+1}"})[0], 403)
        self.assertEqual(self.req(headers={"Host": f"localhost:{self.port}"})[0], 200)
        self.assertEqual(self.req(method="POST", path="save", body={}, headers={"Origin": "http://evil.example"})[0], 403)
        self.assertEqual(self.req(path="nope")[0], 404)
        self.assertEqual(self.req(method="POST", path="save", body={}, headers={"Host": "evil.example"})[0], 403)
        self.assertNotIn("http://", re.sub(r"http://(127\.0\.0\.1|localhost)", "", d.decode().split('id="data"')[0] + d.decode().split("</script>", 2)[-1]).replace("http://www.w3.org", ""))
        self.assertEqual(self.req(method="POST", path="cancel", body={})[0], 200)

    def test_page_has_a_select_per_job(self):
        self.start()
        d = self.data(); html = self.req()[1].decode()
        self.assertEqual(d["jobs"], ["mechanical", "implement", "review", "debug", "plan", "hardest"])
        self.assertIn("optgroup", html); self.assertIn("'data-ag'", html)   # one select per job per profile, built per tab
        self.assertNotRegex(html, r"(src|href)=\"https?://")
        self.assertIn("prefers-color-scheme:dark", html)
        self.assertIn("default", d["profile_names"]); self.assertEqual(d["caps"]["gemini"]["modes"], ["ro", "edits", "full"])
        self.req(method="POST", path="cancel", body={}); self.p.wait(timeout=10)

    def test_save_writes(self):
        self.start()
        b = self.body(self.data())
        b["profiles"]["default"]["jobs"]["review"]["ag"] = {"backend": "codex", "model": "gpt-x"}
        b["backends"]["codex"].update(mode="edits", plan=True); b["backends"]["gemini"]["enabled"] = False
        s, d, _ = self.req(method="POST", path="save", body=b); r = json.loads(d)
        self.assertEqual(s, 200, d); self.assertTrue(r["ok"]); self.assertTrue(all("path" in f for f in r["files"]))
        out, _ = self.p.communicate(timeout=15); self.assertEqual(self.p.returncode, 0)
        doc = json.loads((self.cfg/"routing.json").read_text())
        self.assertEqual(doc["jobs"]["review"]["ag"], {"backend": "codex", "model": "gpt-x"})
        self.assertEqual(doc["backends"], {"codex": {"mode": "edits", "plan": True}, "gemini": {"enabled": False}})
        self.assertTrue((self.home/".claude"/"skills"/"ag-agents"/"SKILL.md").exists())
        self.assertIn("wrote", out)

    def test_invalid_400_nothing_written_keeps_running(self):
        self.start()
        good = self.body(self.data())
        for mut in (lambda b: b.update(scope="nowhere"), lambda b: b["profiles"]["default"]["jobs"]["plan"]["ag"].update(backend="nope"),
                    lambda b: b["backends"]["codex"].update(mode="zzz"), lambda b: b["backends"]["opencode"].update(enabled=False),
                    lambda b: b["profiles"].update(evil={"jobs": {}}), lambda b: b["profiles"]["default"]["jobs"]["plan"]["ag"].update(model="a b")):
            b = json.loads(json.dumps(good)); mut(b)
            s, d, _ = self.req(method="POST", path="save", body=b); r = json.loads(d)
            self.assertEqual(s, 400, d); self.assertFalse(r["ok"]); self.assertTrue(r["errors"])
        self.assertIsNone(self.p.poll()); self.assertFalse((self.cfg/"routing.json").exists() or (self.home/".claude").exists())
        s, d, _ = self.req(method="POST", path="save", body=good); self.assertEqual(s, 200)   # still usable
        self.p.wait(timeout=15)
        # non-JSON content type
    def test_content_type_required(self):
        self.start()
        c = http.client.HTTPConnection(self.host, int(self.port)); c.request("POST", "/" + self.url.split("/", 3)[3] + "save", "{}", {"Host": f"127.0.0.1:{self.port}", "Content-Type": "text/plain"})
        self.assertEqual(c.getresponse().status, 415)

    def test_dry_run_diff(self):
        self.start("--dry-run")
        self.assertTrue(self.data()["dry_run"])
        s, d, _ = self.req(method="POST", path="save", body=self.body(self.data())); r = json.loads(d)
        self.assertTrue(r["dry_run"]); self.assertIn("+++", r["files"][0]["diff"])
        out, _ = self.p.communicate(timeout=15); self.assertIn("would write", out)
        self.assertFalse((self.cfg/"routing.json").exists())

    def test_cancel_and_idle(self):
        self.start(); self.assertEqual(self.req(method="POST", path="cancel", body={})[0], 200)
        out, _ = self.p.communicate(timeout=15); self.assertIn("nothing written", out); self.assertFalse((self.cfg/"routing.json").exists())

    def test_idle_timeout(self):
        self.start(AG_SETUP_WEB_IDLE="1")
        out, _ = self.p.communicate(timeout=20); self.assertIn("idle timeout", out); self.assertEqual(self.p.returncode, 0)
        self.assertFalse((self.cfg/"routing.json").exists())

    def test_refresh(self):
        self.start()
        s, d, _ = self.req(method="POST", path="refresh", body={}); r = json.loads(d)
        self.assertEqual(s, 200); self.assertTrue(any(x["id"] == "opencode/a" for x in r["rows"]))
        self.req(method="POST", path="cancel", body={})

    def test_mac_alias(self):
        p = self.ag("setup", "--ui", "mac", "--dry-run", "--harness", "none", env={"AG_SETUP_NO_OPEN": "1", "AG_SETUP_WEB_IDLE": "1", "AG_PROBE": "0"})
        self.assertIn("ag setup form: http://127.0.0.1:", p.stderr); self.assertIn("idle timeout", p.stdout)

    def test_v2_shape_with_stub_profiles_api(self):
        import test_ag_setup_ui as ui
        m = ui.load_ag(); m.routing_load = lambda scope: {"version": 2, "profiles": {"opencode": {"jobs": {}}}, "project": {"answered": "x"}}
        os.environ.update(AG_HOME=str(self.home), AG_CONFIG_HOME=str(self.cfg), AG_CACHE_HOME=str(self.cache), AG_SKILLS_DIR=str(self.skills))
        self.cfg.mkdir(parents=True, exist_ok=True); (self.cfg/"routing.json").write_text(json.dumps({"version": 2, "project": {"answered": "x"}}))
        st = m.setup_state_from("global", None, None, [], None, proj=str(self.proj), existing={"global": {}, "project": {}})
        pl = m.setup_web_payload(st); self.assertEqual(pl["profile_names"], ["claude", "opencode", "codex", "default"])
        b = {"scope": "global", "harnesses": [], "backends": {}, "profiles": {p: {"jobs": v["jobs"]} for p, v in pl["scopes"]["global"]["profiles"].items()}}
        b["profiles"]["codex"]["jobs"]["plan"]["ag"] = {"backend": "codex", "model": "gpt-x"}
        e, st2, profs = m.setup_web_apply(st, b); self.assertFalse(e, e)
        doc = json.loads(m.setup_web_plan(st2, profs)["plan"][0][1])
        self.assertEqual(doc["version"], 2); self.assertEqual(doc["project"], {"answered": "x"}); self.assertNotIn("jobs", doc)
        self.assertEqual(doc["profiles"]["codex"]["jobs"]["plan"]["ag"]["model"], "gpt-x"); self.assertEqual(set(doc["profiles"]), set(m.WEB_PROFILES))


if __name__ == "__main__":
    unittest.main()
