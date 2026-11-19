#!/usr/bin/env python3
"""ag feedback: manual append, prompt hint + opt-out, harvest dedupe, report, relay sync redaction.

Stdlib only, temp dirs, no network, no real backends (echo only). Run:
python3 -m pytest -q tests/test_ag_feedback.py
"""
import importlib.machinery, importlib.util, json, os, subprocess, sys, tempfile, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AG = str(ROOT / "ag")
_l = importlib.machinery.SourceFileLoader("ag_mod_feedback", AG)
_s = importlib.util.spec_from_loader("ag_mod_feedback", _l)
ag = importlib.util.module_from_spec(_s); _l.exec_module(ag)

SECRET = "sk-ant-SECRET1234567890"


def _env(home):
    home = Path(home); (home / "cfg").mkdir(parents=True, exist_ok=True); (home / "cache").mkdir(parents=True, exist_ok=True)
    return dict(os.environ, AG_HOME=str(home), AG_CONFIG_HOME=str(home / "cfg"), AG_CACHE_HOME=str(home / "cache"),
                AG_AUTO_UPDATE="0", AG_SECRET_POPUP="0", AG_FEEDBACK_PROMPT="")


class Env(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="ag-feedback-t-"))
        self.addCleanup(__import__("shutil").rmtree, self.td, True)
        self.state = self.td / "state"; self.state.mkdir()
        self.env = _env(self.td / "home")

    def run_ag(self, *a, ok=True, env=None):
        p = subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                           capture_output=True, text=True, env=env or self.env, timeout=60)
        if ok: self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return p

    def j(self, *a, **kw):
        p = self.run_ag("--json", *a, **kw)
        return p, (json.loads(p.stdout) if p.stdout.strip() else {})

    def rows(self):
        f = self.state / "feedback.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines() if l.strip()] if f.exists() else []

    def write_chat(self, name, rows):
        d = self.state / "chats"; d.mkdir(parents=True, exist_ok=True)
        (d / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def write_job(self, jid, **kw):
        d = self.state / "wake" / "jobs"; d.mkdir(parents=True, exist_ok=True)
        job = {"id": jid, "agent": "w1", "status": "failed", "ts": ag.now_iso(), **kw}
        (d / f"{jid}.json").write_text(json.dumps(job))
        return job


class Manual(Env):
    def test_append_fields(self):
        p, d = self.j("feedback", "wake", "crashed", "on", "flag", "--severity", "high", "--agent", "w1")
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["text"], "wake crashed on flag")
        self.assertEqual(r["severity"], "high")
        self.assertEqual(r["agent"], "w1")
        self.assertEqual(r["origin"], "unknown")  # provenance (_origin) not implemented in ag
        self.assertEqual(r["version"], ag.VERSION)
        self.assertTrue(r["node"]); self.assertTrue(r["ts"]); self.assertTrue(r["signature"])
        self.assertEqual(d["data"]["feedback"]["signature"], r["signature"])

    def test_defaults_and_empty(self):
        self.j("feedback", "something broke")
        r = self.rows()[0]
        self.assertEqual(r["severity"], "med")
        self.assertEqual(r["agent"], os.environ.get("AG_PARENT_AGENT") or "")
        p = self.run_ag("feedback", ok=False)
        self.assertEqual(p.returncode, 1)
        self.assertIn("no feedback text", p.stdout + p.stderr)
        p = self.run_ag("feedback", "x", "--severity", "bogus", ok=False)
        self.assertNotEqual(p.returncode, 0)  # argparse choices reject it

    def test_appends_not_overwrites(self):
        self.j("feedback", "failure number 123")
        self.j("feedback", "failure number 456")
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["signature"], rows[1]["signature"])  # numbers normalized away


class Signature(unittest.TestCase):
    def test_normalizes_ids_numbers_paths_urls(self):
        a = ag.feedback_signature("Error at /home/x/proj/ag.py line 421, id deadbeefcafe1234, see https://x.io/t/9")
        b = ag.feedback_signature("error at /other/place/ag.py line 7, id 0011223344556677, see https://y.io/z")
        self.assertEqual(a, b)
        self.assertNotIn("421", a)

    def test_redact_row_uses_secret_scanner(self):
        r = ag._feedback_redact_row({"text": f"boom {SECRET}", "api_key": "whatever", "signature": "s"})
        self.assertNotIn(SECRET, json.dumps(r))
        self.assertEqual(r["api_key"], "REDACTED")


class PromptHint(Env):
    def test_sentence_in_turn_prompt_and_opt_out(self):
        old = os.environ.get("AG_CONFIG_HOME"); oldfp = os.environ.get("AG_FEEDBACK_PROMPT")
        os.environ["AG_CONFIG_HOME"] = self.env["AG_CONFIG_HOME"]  # in-process ag reads os.environ
        os.environ["AG_FEEDBACK_PROMPT"] = ""  # conftest sets 0; this test exercises the config switch
        self.addCleanup(lambda: os.environ.__setitem__("AG_CONFIG_HOME", old) if old else os.environ.pop("AG_CONFIG_HOME", None))
        self.addCleanup(lambda: os.environ.__setitem__("AG_FEEDBACK_PROMPT", oldfp) if oldfp is not None else os.environ.pop("AG_FEEDBACK_PROMPT", None))
        a = {"name": "w1", "dir": str(self.td), "persona": "", "system": ""}
        prompt, sys_txt, _ = ag._turn_prompt(self.state, a, "echo", "", "hi", None, None)
        self.assertIn(ag.FEEDBACK_SENTENCE, sys_txt)
        self.assertEqual(sys_txt.count(ag.FEEDBACK_SENTENCE), 1)
        cfg = Path(self.env["AG_CONFIG_HOME"]) / "config.json"
        cfg.write_text(json.dumps({"feedback": {"prompt": False}}))
        prompt, sys_txt, _ = ag._turn_prompt(self.state, a, "echo", "", "hi", None, None)
        self.assertNotIn(ag.FEEDBACK_SENTENCE, sys_txt)

    def test_echo_chat_carries_sentence(self):
        self.run_ag("agents", "add", "e1", "--backend", "echo", "--role", "sub")
        # echo backend reflects its prompt: the system text leads the first turn for non-claude/codex
        p, d = self.j("chat", "send", "e1", "hello")
        self.assertIn(ag.FEEDBACK_SENTENCE, d["data"]["reply"])
        rows = [json.loads(l) for l in (self.state / "chats" / "e1.jsonl").read_text().splitlines() if l.strip()]
        self.assertTrue(any(ag.FEEDBACK_SENTENCE in (r.get("text") or "") for r in rows))


class Harvest(Env):
    def test_harvest_kinds_and_dedupe(self):
        self.write_chat("w1", [
            {"ts": ag.now_iso(), "role": "error", "text": "Traceback (most recent call last):\n  File \"ag\", line 9\nValueError: bad state 42"},
            {"ts": ag.now_iso(), "role": "toolout", "text": "usage: ag [-h]\nag: error: Unrecognized arguments: --bogus"},
            {"ts": ag.now_iso(), "role": "error", "text": "You have hit your usage limit; resets 7pm"},
            {"ts": ag.now_iso(), "role": "user", "text": "Traceback (most recent call last):\nValueError: mine, not ag's"},
            {"ts": ag.now_iso(), "role": "agent", "text": "all good"},
        ])
        self.write_job("j1", error="backend died error=exit 3")
        self.write_job("j2", error="", stalled=True, status="running")
        p, d = self.j("feedback", "harvest")
        self.assertEqual(d["data"]["added"], 5, d)  # traceback, unrecognized, usage-limit, error-field, stall
        rows = self.rows()
        self.assertTrue(all(r["origin"] == "harvest" for r in rows))
        self.assertEqual({r["kind"] for r in rows}, {"traceback", "unrecognized-flag", "usage-limit", "error-field", "stall"})
        p, d = self.j("feedback", "harvest")  # second run: everything already known
        self.assertEqual(d["data"]["added"], 0)

    def test_harvest_since_filters_old(self):
        old = "2019-01-01T00:00:00+00:00"
        self.write_chat("w1", [{"ts": old, "role": "error", "text": "Traceback (most recent call last):\nValueError: ancient"}])
        p, d = self.j("feedback", "harvest", "--since", "7d")
        self.assertEqual(d["data"]["added"], 0)
        p, d = self.j("feedback", "harvest", "--since", "9000d")
        self.assertEqual(d["data"]["added"], 1)

    def test_harvest_same_failure_two_agents_one_entry(self):
        for name in ("w1", "w2"):
            self.write_chat(name, [{"ts": ag.now_iso(), "role": "error",
                "text": "ag: error: Unrecognized arguments: --bogus"}])
        p, d = self.j("feedback", "harvest")
        self.assertEqual(d["data"]["added"], 1)
        r = self.rows()[0]
        self.assertEqual(r["count"], 2)
        self.assertEqual(sorted(r["agents"]), ["w1", "w2"])


class Report(Env):
    def test_report_groups(self):
        self.j("feedback", "crash in widget 12", "--agent", "w1")
        self.j("feedback", "crash in widget 99", "--agent", "w2")
        self.j("feedback", "totally different problem", "--agent", "w1")
        p, d = self.j("feedback", "report")
        groups = d["data"]["groups"]
        self.assertEqual(d["data"]["total"], 3)
        self.assertEqual(len(groups), 2)
        top = groups[0]
        self.assertEqual(top["count"], 2)
        self.assertEqual(sorted(top["agents"]), ["w1", "w2"])
        self.assertLessEqual(top["first"], top["last"])

    def test_report_md_pasteable(self):
        self.j("feedback", "crash in widget 12", "--agent", "w1")
        p = self.run_ag("feedback", "report", "--md")
        self.assertIn("## ag feedback report", p.stdout)
        self.assertIn("### crash in widget #", p.stdout)
        self.assertIn("count: 1", p.stdout)

    def test_report_empty(self):
        p, d = self.j("feedback", "report")
        self.assertEqual(d["data"]["total"], 0)
        self.assertIn("no feedback yet", p.stdout)


class Relay(Env):
    def node(self, name, mail):
        d = self.td / name; home = d / "home"
        for x in (home, home / "cfg", home / "cache", d / "state", d / "work"): x.mkdir(parents=True, exist_ok=True)
        return {"name": name, "state": d / "state", "env": _env(home)}

    def init(self, n, mail):
        p = subprocess.run([sys.executable, AG, "--dir", str(n["state"]), "--json", "relay", "init",
                            "--node", n["name"], "--state", str(n["state"]), "--workdir", str(self.td / n["name"] / "work"),
                            "--dir", str(mail)], capture_output=True, text=True, env=n["env"], timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def sync(self, n):
        p = subprocess.run([sys.executable, AG, "--dir", str(n["state"]), "--json", "relay", "sync"],
                           capture_output=True, text=True, env=n["env"], timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def test_feedback_syncs_redacted_into_results(self):
        mail = self.td / "mail"; mail.mkdir()
        o, t = self.node("mac", mail), self.node("muse", mail)
        self.init(o, mail); self.init(t, mail)
        # origin files feedback with a secret in the text (as a crash dump might)
        p = subprocess.run([sys.executable, AG, "--dir", str(o["state"]), "--json", "feedback",
                            f"crash with key {SECRET} in log", "--agent", "w1"],
                           capture_output=True, text=True, env=o["env"], timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.sync(o)   # mac publishes feedback/mac.jsonl
        pub = (mail / "feedback" / "mac.jsonl").read_text()
        self.assertNotIn(SECRET, pub)
        self.sync(t)   # muse copies remote feedback into results/mac/feedback.jsonl
        got = mail / "results" / "mac" / "feedback.jsonl"
        self.assertTrue(got.exists())
        txt = got.read_text()
        self.assertNotIn(SECRET, txt)
        rows = [json.loads(l) for l in txt.splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "w1")
        self.assertIn("REDACTED", rows[0]["text"])


if __name__ == "__main__":
    unittest.main()
