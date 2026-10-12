#!/usr/bin/env python3
"""ag crosscheck: same task to 2+ agents in parallel, compare answers.

Isolated only: temp state dirs, echo backend (keyless) + in-process
run_turn stubs. No real models/providers.
Run: python3 tests/test_ag_crosscheck.py
"""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from importlib.machinery import SourceFileLoader
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")


def load_ag():
    return SourceFileLoader("ag_crosscheck", AG).load_module()


class Pure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ag = load_ag()

    def test_verdict_first_line(self):
        v = self.ag.crosscheck_verdict
        self.assertEqual(v("OK all good"), "pass")
        self.assertEqual(v("ok: done"), "pass")
        self.assertEqual(v("PASS 3/3"), "pass")
        self.assertEqual(v("yes, correct"), "pass")
        self.assertEqual(v("FAIL nope"), "fail")
        self.assertEqual(v("no — wrong"), "fail")
        self.assertEqual(v("BUG: null deref in x"), "fail")
        self.assertEqual(v("  bug:  spaced"), "fail")
        self.assertIsNone(v("plain prose answer"))
        self.assertIsNone(v(""))
        self.assertIsNone(v("\n\n"))

    def test_verdict_json(self):
        v = self.ag.crosscheck_verdict
        self.assertEqual(v('{"verdict": "yes", "why": "x"}'), "pass")
        self.assertEqual(v('{"verdict": "FAIL"}'), "fail")
        self.assertEqual(v('{"verdict": "OK"}'), "pass")
        self.assertIsNone(v('{"answer": 42}'))

    def test_normalize(self):
        n = self.ag.crosscheck_normalize
        self.assertEqual(n("  a  \n\nb \n"), "  a\n\nb")
        self.assertEqual(n("\n\nx\n\n"), "x")
        self.assertEqual(n(""), "")

    def test_compare_verdicts(self):
        c = self.ag.crosscheck_compare
        r = c({"a": "OK done", "b": "PASS done"})
        self.assertTrue(r["verdict_mode"] and r["agree"])
        self.assertEqual(r["similarity"], 1.0)
        r = c({"a": "OK done", "b": "FAIL nope"})
        self.assertTrue(r["verdict_mode"] and not r["agree"])
        self.assertEqual(r["verdicts"], {"a": "pass", "b": "fail"})

    def test_compare_prose(self):
        c = self.ag.crosscheck_compare
        r = c({"a": "same text", "b": "same text"})
        self.assertTrue(r["agree"] and not r["verdict_mode"])
        self.assertEqual(r["similarity"], 1.0)
        r = c({"a": "alpha", "b": "beta"})
        self.assertFalse(r["agree"])
        self.assertLess(r["similarity"], 1.0)
        self.assertIn("alpha", r["diff"])
        r = c({"a": "x", "b": "x", "c": "y"})
        self.assertFalse(r["agree"])
        self.assertIn("c", r["diff"])


class Args:
    def __init__(self, **k):
        self.__dict__.update({"dir": None, "json": False, "aname": "", "bname": "",
                              "task": [], "more": [], "judge": None, "timeout": 30})
        self.__dict__.update(k)


class DoCrosscheck(unittest.TestCase):
    def setUp(self):
        self.ag = load_ag()
        self.td = Path(tempfile.mkdtemp(prefix="agcc-"))
        self.state = self.td / ".agent"
        self.ag.ensure_state(self.state)
        rows = self.ag.load_agents(self.state)
        rows = [r for r in rows if r.get("name") not in ("ca", "cb", "cj")]
        for n in ("ca", "cb", "cj"):
            rows.append({"name": n, "backend": "echo", "model": "default",
                         "dir": str(self.td), "role": "sub"})
        self.ag.save_agents(self.state, rows)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)
        self.ag.model_privacy = getattr(self.ag, "model_privacy", None)
        try:
            delattr(self.ag, "model_privacy")
        except Exception:
            pass
        os.environ.pop("AG_PRIVACY", None)

    def cc(self, **k):
        a = Args(dir=str(self.state), **k)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.ag.do_crosscheck(a)
        return rc, buf.getvalue()

    def stub(self, mapping):
        real = self.ag.run_turn

        def fake(sdir, name, text, **kw):
            if name in mapping:
                v = mapping[name]
                if isinstance(v, dict):
                    return v
                return {"sid": "", "exit": 0, "reply": v}
            return real(sdir, name, text, **kw)
        self.ag.run_turn = fake
        return real

    def events(self):
        p = self.state / "events.jsonl"
        if not p.exists():
            return []
        return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]

    def test_agree_verdicts_exit0(self):
        real = self.stub({"ca": "OK done", "cb": "PASS done"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["is", "it", "ok?"])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 0, out)
        self.assertIn("AGREE", out)
        evs = [e for e in self.events() if e.get("type") == "crosscheck"]
        self.assertEqual(len(evs), 1)
        self.assertEqual(sorted(evs[0]["agents"]), ["ca", "cb"])
        self.assertIn("verdict", evs[0])

    def test_disagree_verdicts_exit3(self):
        real = self.stub({"ca": "OK done", "cb": "BUG: broken"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["review", "x"])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 3, out)
        self.assertIn("DISAGREE", out)

    def test_prose_diff_and_similarity(self):
        real = self.stub({"ca": "alpha answer here", "cb": "beta answer here"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["explain"])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 3, out)
        self.assertIn("similarity", out)
        self.assertIn("---", out)

    def test_more_flag_third_agent(self):
        rows = self.ag.load_agents(self.state)
        rows.append({"name": "cc", "backend": "echo", "model": "default",
                     "dir": str(self.td), "role": "sub"})
        self.ag.save_agents(self.state, rows)
        real = self.stub({"ca": "OK a", "cb": "OK b", "cc": "FAIL c"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["t"], more=[["cc"]])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 3, out)
        self.assertIn("=== cc ===", out)

    def test_judge(self):
        real = self.stub({"ca": "OK done", "cb": "FAIL nope",
                          "cj": "BUG: cb is right, ca missed the null check"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["review", "x"], judge="cj")
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 3, out)
        self.assertIn("(judge)", out)
        self.assertIn("null check", out)

    def test_missing_agent(self):
        rc, out = self.cc(aname="ca", bname="nosuch", task=["hi"])
        self.assertEqual(rc, 1)
        evs = [e for e in self.events() if e.get("type") == "crosscheck"]
        self.assertFalse(evs)

    def test_close_name_hint(self):
        a = Args(dir=str(self.state), aname="ca", bname="ca1", task=["hi"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.ag.do_crosscheck(a)
        self.assertEqual(rc, 1)

    def test_empty_task(self):
        rc, out = self.cc(aname="ca", bname="cb", task=[])
        self.assertEqual(rc, 1)

    def test_bad_timeout(self):
        rc, out = self.cc(aname="ca", bname="cb", task=["hi"], timeout=float("inf"))
        self.assertEqual(rc, 1)

    def test_agent_error_exit1(self):
        real = self.stub({"ca": {"error": "boom"}, "cb": "OK fine"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["hi"])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 1)

    def test_privacy_refuses_train(self):
        import io
        from contextlib import redirect_stderr
        self.ag.model_privacy = lambda b, m: "train"
        a = Args(dir=str(self.state), aname="ca", bname="cb", task=["secret code"])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = self.ag.do_crosscheck(a)
        self.assertEqual(rc, 1)
        self.assertIn("privacy", (out.getvalue() + err.getvalue()).lower())

    def test_privacy_off_allows(self):
        self.ag.model_privacy = lambda b, m: "train"
        os.environ["AG_PRIVACY"] = "off"
        real = self.stub({"ca": "OK a", "cb": "OK b"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["hi"])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 0, out)

    def test_privacy_public_data_allows(self):
        self.ag.model_privacy = lambda b, m: "train"
        rows = self.ag.load_agents(self.state)
        for r in rows:
            if r["name"] in ("ca", "cb"):
                r["data"] = "public"
        self.ag.save_agents(self.state, rows)
        real = self.stub({"ca": "OK a", "cb": "OK b"})
        try:
            rc, out = self.cc(aname="ca", bname="cb", task=["hi"])
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 0, out)

    def test_json_shape(self):
        real = self.stub({"ca": "OK a", "cb": "OK b"})
        a = Args(dir=str(self.state), json=True, aname="ca", bname="cb", task=["hi"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = self.ag.do_crosscheck(a)
        obj = json.loads(buf.getvalue())
        try:
            pass
        finally:
            self.ag.run_turn = real
        self.assertEqual(rc, 0)
        self.assertTrue(obj["ok"])
        self.assertIn("replies", obj["data"])
        self.assertTrue(obj["data"]["agree"])


class E2E(unittest.TestCase):
    def test_echo_agree_exit0(self):
        td = Path(tempfile.mkdtemp(prefix="agcc-e2e-"))
        try:
            env = dict(os.environ, AGENT_CLI_DIR=str(td), AG_AUTO_UPDATE="0", AG_PROBE="0")
            for n in ("ea", "eb"):
                p = subprocess.run([sys.executable, AG, "--dir", str(td), "agents",
                                    "add", n, "--backend", "echo", "--role", "sub",
                                    "--dir", str(td)], capture_output=True, text=True,
                                   timeout=30, env=env)
                self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
            p = subprocess.run([sys.executable, AG, "--dir", str(td), "crosscheck",
                                "ea", "eb", "say hello"], capture_output=True, text=True,
                               timeout=120, env=env)
            self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
            self.assertIn("AGREE", p.stdout)
            evs = [json.loads(l) for l in (td / "events.jsonl").read_text().splitlines()]
            self.assertTrue(any(e.get("type") == "crosscheck" for e in evs))
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)

    def test_echo_disagree_seeded_history_exit3(self):
        td = Path(tempfile.mkdtemp(prefix="agcc-e2e2-"))
        try:
            env = dict(os.environ, AGENT_CLI_DIR=str(td), AG_AUTO_UPDATE="0", AG_PROBE="0")
            for n in ("ea", "eb"):
                p = subprocess.run([sys.executable, AG, "--dir", str(td), "agents",
                                    "add", n, "--backend", "echo", "--role", "sub",
                                    "--dir", str(td)], capture_output=True, text=True,
                                   timeout=30, env=env)
                self.assertEqual(p.returncode, 0, (p.stdout, p.stderr))
            ag = load_ag()
            sdir = td
            ag.append_chat(sdir, "ea", "user", "history ALPHA marker")
            ag.append_chat(sdir, "ea", "agent", "ack alpha")
            ag.append_chat(sdir, "eb", "user", "history BETA marker")
            ag.append_chat(sdir, "eb", "agent", "ack beta")
            p = subprocess.run([sys.executable, AG, "--dir", str(td), "crosscheck",
                                "ea", "eb", "same question"], capture_output=True, text=True,
                               timeout=120, env=env)
            self.assertEqual(p.returncode, 3, (p.stdout, p.stderr))
            self.assertIn("DISAGREE", p.stdout)
        finally:
            import shutil
            shutil.rmtree(td, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
