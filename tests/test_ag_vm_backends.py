#!/usr/bin/env python3
"""VM backend death: a dying opencode backend must surface its real error.

Defect (verified 2026-11-15 against Segzman/ag dc379ed): opencode reports
provider errors as stdout JSON events ({"type":"error",...}), with EMPTY
stderr and exit 1. ag dropped that message everywhere that matters:
- _parse_opencode had no "error" branch (event dropped; only the sessionID meta survived);
- parse_timeline_opencode stringified the whole error dict (Python repr in the chat log);
- run_turn built the failure text from process stderr only, so the reply was
  "(exit 1) (failed)", the chat-log error row was "(exit 1) turn failed", and
  no "stderr" field was set;
- do__wake_run's "error" branch dropped backend_stderr, and the worker printed
  nothing, so the session log (ag snap) was 0 bytes and `ag wakes` showed
  "exit=1" with no reason.
Plus: a wake proceeded against a retired model (opencode/muse-spark-1.3-
contributor-free) with no complaint anywhere, even though `ag models
--refresh` had already dropped it from the catalog.

Fake opencode behaviour is driven by env FAKE_MODE and a counter file:
- retired: JSON error event (UnknownError, real 2026-11-15 shape) + exit 1, empty stderr
- quota:   JSON error event ("Free usage limit reached") + exit 1, empty stderr
- ok:      text event FAKE_OK + exit 0
Every invocation appends its argv to calls.jsonl.
Run: python3 tests/test_ag_vm_backends.py
"""
import importlib.machinery, importlib.util, json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path

AG = str(Path(__file__).resolve().parent.parent / "ag")

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
d = os.environ["FAKE_DIR"]; mode = os.environ["FAKE_MODE"]
open(os.path.join(d, "calls.jsonl"), "a").write(json.dumps(sys.argv[1:]) + "\n")
if mode == "retired":
    print(json.dumps({"type": "error", "timestamp": 1, "sessionID": "ses-1", "error": {"name": "UnknownError", "data": {"message": "Unexpected server error. Check server logs for details.", "ref": "err_46d45e02"}}}))
    sys.exit(1)
if mode == "quota":
    print(json.dumps({"type": "error", "timestamp": 1, "sessionID": "ses-1", "error": {"name": "APIError", "data": {"message": "Free usage limit reached", "statusCode": 429}}}))
    sys.exit(1)
print(json.dumps({"type": "text", "timestamp": 2, "sessionID": "ses-1", "part": {"type": "text", "text": "FAKE_OK"}})); sys.exit(0)
'''

RETIRED = "opencode/muse-spark-1.3-contributor-free"
LISTED = "opencode/longcat-2.5-free"


def _load_ag():
    loader = importlib.machinery.SourceFileLoader("ag_mod_vm_backends", AG)
    spec = importlib.util.spec_from_loader("ag_mod_vm_backends", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _models_json(models, fetched_at):
    return {"backends": {"opencode": {"models": models, "fetched_at": fetched_at,
                                       "checked_at": fetched_at}}}


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "+00:00")


class ErrorMessageUnit(unittest.TestCase):
    def setUp(self):
        self.mod = _load_ag()

    def test_opencode_error_message_shapes(self):
        m = self.mod._opencode_error_message
        self.assertEqual(m({"name": "UnknownError", "data": {"message": "Unexpected server error. Check server logs for details.", "ref": "err_46d45e02"}}),
                         "Unexpected server error. Check server logs for details. (ref err_46d45e02)")
        self.assertEqual(m({"name": "APIError", "data": {"message": "Free usage limit reached", "statusCode": 429}}),
                         "Free usage limit reached")
        self.assertEqual(m({"message": "plain message"}), "plain message")
        self.assertEqual(m("raw string error"), "raw string error")
        self.assertEqual(m({"name": "OnlyName"}), "OnlyName")
        self.assertEqual(m({}), "")
        self.assertEqual(m(None), "")

    def test_parse_events_opencode_error_is_not_text(self):
        # deliberate: an error event must NOT become reply text -- run_turn
        # treats any chunk text as output, and classify_turn_failure refuses
        # to retry once output exists. The message surfaces via the timeline
        # op (below) and run_turn's _fail_text, not via chunks.
        ev = json.dumps({"type": "error", "timestamp": 1, "sessionID": "ses-1",
                         "error": {"name": "UnknownError", "data": {"message": "Unexpected server error. Check server logs for details.", "ref": "err_1"}}})
        got = self.mod.parse_events("opencode", ev)
        self.assertEqual(got, [("meta", "ses-1")])
        self.assertFalse(any("Unexpected server error" in v for _, v in got))

    def test_parse_timeline_opencode_error_is_message_not_repr(self):
        ev = json.dumps({"type": "error", "timestamp": 1, "sessionID": "ses-1",
                         "error": {"name": "UnknownError", "data": {"message": "Unexpected server error. Check server logs for details.", "ref": "err_1"}}})
        ops = self.mod.parse_timeline_opencode(ev)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0]["op"], "error")
        self.assertNotIn("{'name'", ops[0]["text"])
        self.assertIn("Unexpected server error", ops[0]["text"])


class ModelCheckUnit(unittest.TestCase):
    def setUp(self):
        self.mod = _load_ag()
        self.td = Path(tempfile.mkdtemp(prefix="agmb-unit-"))
        self.old = dict(os.environ)
        os.environ["AG_CACHE_HOME"] = str(self.td / "cache")
        os.environ.pop("AG_MODEL_CHECK", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old)
        shutil.rmtree(self.td, ignore_errors=True)

    def _write_cache(self, models, fetched_at):
        p = self.td / "cache"
        p.mkdir(parents=True, exist_ok=True)
        (p / "models.json").write_text(json.dumps(_models_json(models, fetched_at)))

    def test_retired_model_fails_fast_on_fresh_catalog(self):
        self._write_cache([LISTED], _iso_now())
        err = self.mod.model_check("opencode", RETIRED)
        self.assertIsNotNone(err, "retired model must fail fast on a fresh catalog")
        self.assertIn(RETIRED, err)
        self.assertIn("ag models --refresh", err)
        self.assertIn("AG_MODEL_CHECK=0", err)

    def test_listed_and_default_models_pass(self):
        self._write_cache([LISTED], _iso_now())
        self.assertIsNone(self.mod.model_check("opencode", LISTED))
        self.assertIsNone(self.mod.model_check("opencode", "default"))
        self.assertIsNone(self.mod.model_check("opencode", ""))

    def test_stale_or_missing_catalog_proceeds(self):
        stale = "2026-11-01T00:00:00+00:00"
        self._write_cache([LISTED], stale)
        self.assertIsNone(self.mod.model_check("opencode", RETIRED))
        shutil.rmtree(self.td / "cache", ignore_errors=True)
        self.assertIsNone(self.mod.model_check("opencode", RETIRED))

    def test_opt_out_and_other_backends(self):
        self._write_cache([LISTED], _iso_now())
        os.environ["AG_MODEL_CHECK"] = "0"
        self.assertIsNone(self.mod.model_check("opencode", RETIRED))
        os.environ.pop("AG_MODEL_CHECK")
        self.assertIsNone(self.mod.model_check("claude", "some-custom-model"))
        self.assertIsNone(self.mod.model_check("codex", "gpt-5.6-luna"))


class FakeOpencodeE2E(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="agmb-"))
        self.state = self.td / ".agent"
        (self.td / "bin").mkdir()
        (self.td / "bin" / "opencode").write_text(FAKE)
        (self.td / "bin" / "opencode").chmod(0o755)
        self.work = self.td / "work"
        self.work.mkdir()
        self.env = dict(os.environ,
                        PATH=str(self.td / "bin") + os.pathsep + os.environ["PATH"],
                        FAKE_DIR=str(self.td), AG_PROBE="0", AG_AUTO_UPDATE="0",
                        AG_CACHE_HOME=str(self.td / "cache"))
        self.env.pop("AG_MODEL_CHECK", None)
        # empty models cache: model_check must proceed (unknown = proceed)
        (self.td / "cache").mkdir()
        (self.td / "cache" / "models.json").write_text(json.dumps({"backends": {}}))
        self.ag("agents", "add", "b1", "--backend", "opencode", "--role", "sub",
               "--dir", str(self.work))

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def ag(self, *a, mode="retired", timeout=120):
        env = dict(self.env, FAKE_MODE=mode)
        return subprocess.run([sys.executable, AG, "--dir", str(self.state), *a],
                              capture_output=True, text=True, timeout=timeout, env=env)

    def calls(self):
        p = self.td / "calls.jsonl"
        return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []

    def log(self):
        return [json.loads(l) for l in (self.state / "chats" / "b1.jsonl").read_text().splitlines()]

    def job(self, jid):
        return json.loads((self.state / "wake" / "jobs" / f"{jid}.json").read_text())

    def test_retired_model_turn_surfaces_provider_error(self):
        p = self.ag("--json", "chat", "send", "b1", "Say OK", mode="retired")
        out = (p.stdout or "") + (p.stderr or "")
        self.assertIn("Unexpected server error", out, out)
        self.assertNotIn("(exit 1) (failed)", out, out)
        rows = self.log()
        err_rows = [r["text"] for r in rows if r.get("role") == "error"]
        self.assertTrue(any("Unexpected server error" in t for t in err_rows), err_rows)
        self.assertFalse(any(t.startswith("{'name'") for t in err_rows), err_rows)

    def test_retired_model_wake_result_and_log(self):
        p = self.ag("--json", "wake", "b1", "Say OK", mode="retired")
        jid = json.loads(p.stdout)["data"]["job"]
        w = self.ag("--json", "wakes", "--wait", jid, "--timeout", "120", mode="retired")
        data = json.loads(w.stdout)["data"]
        self.assertEqual(data["status"], "failed", data)
        self.assertEqual(data["backend_exit"], 1, data)
        self.assertIn("Unexpected server error", data.get("error", ""), data)
        self.assertNotIn("(failed)", data.get("error", ""), data)
        j = self.job(jid)
        self.assertIn("Unexpected server error", j.get("backend_stderr", ""), j)
        txt = self.ag("wakes", mode="retired").stdout
        self.assertIn("error=", txt, txt)
        self.assertIn("Unexpected server error", txt, txt)
        snap = self.ag("snap", data["session"], mode="retired").stdout
        self.assertIn("failed:", snap, snap)
        self.assertIn("Unexpected server error", snap, snap)

    def test_quota_error_surfaces_not_failed_silently(self):
        # quota is classified as a usage limit (label at the top level, by design);
        # the provider's own wording must still be in the chat log, not lost.
        p = self.ag("--json", "chat", "send", "b1", "Say OK", mode="quota")
        o = json.loads(p.stdout)
        self.assertTrue(o.get("data", {}).get("usage_limit"), o)
        self.assertIn("usage limit", o.get("error", ""), o)
        rows = self.log()
        texts = [r["text"] for r in rows]
        self.assertTrue(any("Free usage limit" in t for t in texts), texts)

    def test_retired_model_fails_fast_without_spawning_backend(self):
        # fresh catalog without the retired model: the wake must fail before opencode runs
        cache = self.td / "cache"
        (cache / "models.json").write_text(json.dumps(_models_json([LISTED], _iso_now())))
        self.ag("agents", "add", "b2", "--backend", "opencode", "--model", RETIRED,
               "--role", "sub", "--data", "public", "--dir", str(self.work))
        p = self.ag("--json", "wake", "b2", "Say OK", mode="retired")
        jid = json.loads(p.stdout)["data"]["job"]
        w = self.ag("--json", "wakes", "--wait", jid, "--timeout", "120", mode="retired")
        data = json.loads(w.stdout)["data"]
        self.assertEqual(data["status"], "failed", data)
        self.assertIn("not in the opencode catalog", data.get("error", ""), data)
        self.assertIn(RETIRED, data.get("error", ""), data)
        self.assertEqual(self.calls(), [], "fail-fast must not spawn the backend")


if __name__ == "__main__":
    unittest.main()
