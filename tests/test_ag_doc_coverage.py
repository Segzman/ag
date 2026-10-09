"""Docs must cover every user-facing command (README + a skills/ template).

Command rule: top-level `ag X` needs word X; `ag X Y` needs a line that mentions
both X and Y as words. Flag rule: key commands' long flags must appear in README.
"""
import argparse
import os
import re
import unittest
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FLAG_CMDS = [["setup"], ["models"], ["schedule", "add"], ["agents", "add"], ["wake"],
                 ["chat", "send"], ["queue"], ["stop"]]
SKIP_FLAGS = {"--help", "--json"}


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


def _subs(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def _walk(parser, path, out):
    for name, sp in _subs(parser).items():
        if name.startswith("_"):
            continue
        out[tuple(path + [name])] = sp
        _walk(sp, path + [name], out)


def _commands():
    ag = SourceFileLoader("ag_doc_cov", os.path.join(ROOT, "ag")).load_module()
    out = {}
    _walk(ag.build(), [], out)
    return out


def _word(w, text):
    return re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text) is not None


def _covered(path, text):
    if len(path) == 1:
        return _word(path[0], text)
    return any(_word(path[0], l) and _word(path[-1], l) for l in text.splitlines())


class DocCoverage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cmds = _commands()
        cls.readme = _read("README.md")
        cls.skills = [_read("skills", n, "SKILL.md") for n in ("ag-cli", "ag-agents")]

    def test_every_command_in_readme(self):
        miss = ["ag " + " ".join(p) for p in self.cmds if not _covered(p, self.readme)]
        self.assertFalse(miss, "README missing: " + ", ".join(miss))

    def test_every_command_in_a_skill(self):
        miss = ["ag " + " ".join(p) for p in self.cmds
                if not any(_covered(p, s) for s in self.skills)]
        self.assertFalse(miss, "skills missing: " + ", ".join(miss))

    def test_key_flags_in_readme(self):
        miss = []
        for path in KEY_FLAG_CMDS:
            sp = self.cmds[tuple(path)]
            for a in sp._actions:
                for f in a.option_strings:
                    if f.startswith("--") and f not in SKIP_FLAGS and f not in self.readme:
                        miss.append("ag %s %s" % (" ".join(path), f))
        self.assertFalse(miss, "README missing flags: " + ", ".join(miss))

    def test_skill_placeholders(self):
        for n, s in zip(("ag-cli", "ag-agents"), self.skills):
            self.assertIn("{{AG}}", s, n)
            self.assertIn("{{SCOPE}}", s, n)
        self.assertIn("{{ROUTING}}", self.skills[1])


if __name__ == "__main__":
    unittest.main()
