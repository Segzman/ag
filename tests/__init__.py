"""Test package. Shims for new-ish Python on dev boxes:

- importlib.machinery.SourceFileLoader.load_module was removed (3.12+); many
  tests load the single-file `ag` with it. Re-add a minimal implementation.
- Tests must stay offline: force AG_AUTO_UPDATE=0 before any test imports ag
  (self-update check could still fire from subprocess CLI calls otherwise).
"""
import os

os.environ.setdefault("AG_AUTO_UPDATE", "0")

import importlib.util
from importlib.machinery import SourceFileLoader

if not hasattr(SourceFileLoader, "load_module"):
    def _load_module(self, fullname=None):
        fullname = fullname or self.name
        spec = importlib.util.spec_from_loader(fullname, self)
        m = importlib.util.module_from_spec(spec)
        self.exec_module(m)
        return m
    SourceFileLoader.load_module = _load_module
