#!/usr/bin/env python3
"""Regenerate tests/golden/ (help, namespace, read-only JSON snapshots).

Run this only when a change to help text, the top-level namespace or read-only
JSON output is intended. Review the diff and commit it together with that change.
"""
import importlib.machinery, importlib.util, shutil, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"


def main():
    name = "golden_snapshots"
    loader = importlib.machinery.SourceFileLoader(name, str(ROOT / "tests" / "test_golden_snapshots.py"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    with tempfile.TemporaryDirectory(prefix="ag-golden-gen-") as tmp:
        fresh = Path(tmp) / "golden"
        mod.generate(fresh)
        shutil.rmtree(GOLDEN, ignore_errors=True)
        shutil.copytree(fresh, GOLDEN)
    print("wrote " + str(GOLDEN.relative_to(ROOT)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
