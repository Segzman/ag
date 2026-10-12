#!/usr/bin/env python3
"""`ag setup --yes` terminal path saves sparsely (like the web form): only
jobs that differ from the inherited profile are written; explicit --set
overrides are kept even when equal to the default.
Run: python3 tests/test_ag_setup_sparse.py
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_ag_setup as base  # noqa: E402

AG = base.AG


class SetupSparse(unittest.TestCase):
    setUp, ag, agj = base.AgSetup.setUp, base.AgSetup.ag, base.AgSetup.agj

    def rt(self):
        return json.loads((self.cfg / "routing.json").read_text())

    def test_fresh_yes_writes_no_frozen_jobs(self):
        self.agj("setup", "--scope", "global", "--yes", "--harness", "none", "--no-claude-md")
        doc = self.rt()
        self.assertEqual((doc["version"], doc["preset"]), (2, "cost-first"))
        self.assertEqual(doc.get("profiles", {}), {})
        # effective routing still resolves through the preset
        j = self.agj("route", "review")["data"]["jobs"]["review"]
        self.assertEqual((j["claude"], j["ag"]), ("opus", {"backend": "claude", "model": "opus"}))

    def test_set_equal_to_default_is_kept_alone(self):
        self.agj("setup", "--scope", "global", "--yes", "--harness", "none",
                 "--no-claude-md", "--set", "review=claude:opus")
        doc = self.rt()
        self.assertEqual(set(doc["profiles"]), {"claude"})
        jobs = doc["profiles"]["claude"]["jobs"]
        self.assertEqual(list(jobs), ["review"])
        self.assertEqual(jobs["review"]["claude"], "opus")

    def test_set_differing_writes_only_that_job(self):
        self.agj("setup", "--scope", "global", "--yes", "--harness", "none",
                 "--no-claude-md", "--set", "review=claude:sonnet")
        doc = self.rt()
        self.assertEqual(list(doc["profiles"]["claude"]["jobs"]), ["review"])
        self.assertEqual(doc["profiles"]["claude"]["jobs"]["review"]["claude"], "sonnet")
        j = self.agj("route", "review")["data"]["jobs"]["review"]
        self.assertEqual(j["claude"], "sonnet")

    def test_rerun_is_stable(self):
        self.agj("setup", "--scope", "global", "--yes", "--harness", "none",
                 "--no-claude-md", "--set", "review=claude:sonnet")
        d2 = self.agj("setup", "--scope", "global", "--yes", "--harness", "none",
                       "--no-claude-md", "--set", "review=claude:sonnet")["data"]
        self.assertTrue(all(f["status"] == "unchanged" for f in d2["files"]), d2["files"])


if __name__ == "__main__":
    unittest.main()
