"""Docs must cover every user-facing command (README + a skills/ template).

Command rule: top-level `ag X` needs word X; `ag X Y` needs a line that mentions
both X and Y as words. Flag rule: key commands' long flags must appear in README.
"""
import argparse
import os
import re
import unittest
from importlib.machinery import SourceFileLoader

def _load_src(loader):  # SourceFileLoader.load_module was removed in Python 3.15
    import importlib.util, sys
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEY_FLAG_CMDS = [["setup"], ["models"], ["schedule", "add"], ["agents", "add"], ["agents", "set"], ["agents", "fork"], ["wake"],
                 ["chat", "send"], ["queue"], ["stop"], ["harness", "use"], ["ratelimit"], ["crosscheck"],
                 ["keys", "set"], ["keys", "test"], ["providers", "add"], ["access", "show"], ["access", "allow"], ["access", "deny"], ["access", "rm"], ["usage"], ["budget", "show"], ["budget", "set"]]
SKIP_FLAGS = {"--help", "--json"}


def _read(*p):
    with open(os.path.join(ROOT, *p), encoding="utf-8") as f:
        return f.read()


SKILLS = ("ag-cli", "ag-agents")


def _skill_files(n):
    base = os.path.join(ROOT, "skills", n)
    out = []
    for d, _, fs in os.walk(base):
        out += [os.path.join(d, f) for f in fs if f.endswith(".md")]
    return sorted(out)


def _skill_text(n):
    return "\n".join(_read(os.path.relpath(f, ROOT)) for f in _skill_files(n))


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
    ag = _load_src(SourceFileLoader("ag_doc_cov", os.path.join(ROOT, "ag")))
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
        cls.skills = [_skill_text(n) for n in SKILLS]

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
        for n in SKILLS:
            self.assertNotIn("{{ROUTING}}", _skill_text(n), n)

    def test_skill_frontmatter(self):
        for n in SKILLS:
            m = re.match(r"---\n(.*?)\n---\n", _read("skills", n, "SKILL.md"), re.S)
            self.assertTrue(m, n + ": no frontmatter")
            fm = dict(l.split(": ", 1) for l in m.group(1).splitlines() if ": " in l)
            self.assertEqual(fm.get("name"), n)
            self.assertRegex(fm["name"], r"^[a-z0-9-]+$")
            d = fm.get("description", "")
            self.assertTrue(d, n + ": empty description")
            self.assertLessEqual(len(d), 1024, n)
            self.assertNotRegex(d, r"[<>]", n)

    def test_skill_size_and_references_linked(self):
        for n in SKILLS:
            body = _read("skills", n, "SKILL.md")
            self.assertLessEqual(len(body.splitlines()), 130, n + " SKILL.md too long")
            refdir = os.path.join(ROOT, "skills", n, "references")
            for f in sorted(os.listdir(refdir)) if os.path.isdir(refdir) else []:
                self.assertIn("references/" + f, body, n + ": unlinked " + f)


if __name__ == "__main__":
    unittest.main()
