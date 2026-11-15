"""Every `ag` command line in skills/*/SKILL.md must use real flags.

Parses each `{{AG}} ...` invocation (code-block lines and inline backticked
spans), resolves the subcommand path with the real parser (`ag.build()`), and
checks every long flag against that subcommand's option strings - the same
content `--help` prints. `--dir`/`--json` are accepted at any depth because
main() pre-scans argv for them (they work after the subcommand too).
"""
import argparse
import os
import re
import shlex
import unittest
from importlib.machinery import SourceFileLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = ("ag-cli", "ag-agents")
SKIP_FLAGS = {"--help"}  # --json is a global flag accepted on every command


def _load_ag():
    import importlib.util, sys
    loader = SourceFileLoader("ag_skill_flags", os.path.join(ROOT, "ag"))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = mod
    loader.exec_module(mod)
    return mod.build()


def _subs(parser):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            return a.choices
    return {}


def _walk(parser, path, out):
    out[tuple(path)] = parser
    for name, sp in _subs(parser).items():
        if not name.startswith("_"):
            _walk(sp, path + [name], out)


def _node_options(parser):
    """Long flags -> whether they consume a value (what --help prints)."""
    out = {}
    for a in parser._actions:
        for f in a.option_strings:
            if f.startswith("--"):
                if a.nargs is None:
                    consumes = True
                elif isinstance(a.nargs, int):
                    consumes = a.nargs > 0
                else:
                    consumes = True  # '+'/'*' take values
                out[f] = consumes
    return out


def _command_spans(text):
    spans = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("{{AG}}"):
            spans.append(s[len("{{AG}}"):])
        else:
            spans += [m.group(1) for m in re.finditer(r"`\{\{AG\}\} ([^`]+)`", s)]
    return spans


def _resolve(nodes, tokens):
    """Longest prefix of child names; returns (path, chain, resolved options)."""
    path, chain, node = [], [], nodes[()]
    opts = _node_options(node)
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--":
            i += 1
            continue
        if t.startswith("--"):
            flag = t.split("=", 1)[0]
            if opts.get(flag) and "=" not in t and i + 1 < len(tokens):
                i += 2  # flag plus its value
            else:
                i += 1
            continue
        subs = _subs(node)
        if t in subs and not t.startswith("_"):
            node = subs[t]
            path.append(t)
            chain.append(node)
            opts = _node_options(node)
        i += 1
    return path, chain, opts


class SkillFlags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nodes = {}
        _walk(_load_ag(), [], cls.nodes)

    def _check_span(self, skill, raw):
        toks = [t for t in shlex.split(re.sub(r"[\[\]]", " ", raw).split("#")[0]) if t]
        if not toks:
            return
        path, chain, opts = _resolve(self.nodes, toks)
        self.assertTrue(path, "%s: no subcommand in %r" % (skill, raw))
        valid = set(SKIP_FLAGS)
        for node in [self.nodes[()]] + chain:
            valid |= set(_node_options(node))
        for t in toks:
            if not t.startswith("--") or t == "--":
                continue
            f = t.split("=", 1)[0]
            self.assertNotIn(f, SKIP_FLAGS, "%s: skipped flag used: %s" % (skill, f))
            self.assertIn(f, valid, "%s: ag %s: flag %s not on this subcommand"
                          % (skill, " ".join(path), f))

    def test_skill_command_flags(self):
        for n in SKILLS:
            with open(os.path.join(ROOT, "skills", n, "SKILL.md"), encoding="utf-8") as f:
                spans = _command_spans(f.read())
            self.assertGreaterEqual(len(spans), 10, n + ": too few command lines parsed")
            for s in spans:
                self._check_span(n, s)

    def test_checker_rejects_cross_command_flags(self):
        # Guard for the checker itself: flags that exist only on other
        # subcommands must not pass for these.
        for span, want_path in [("chat send w x --reap", ["chat", "send"]),
                                ("agents list --verify cmd", ["agents", "list"])]:
            toks = shlex.split(span)
            path, _, opts = _resolve(self.nodes, toks)
            self.assertEqual(path, want_path)
            self.assertTrue(all(t not in opts for t in toks if t.startswith("--")))


if __name__ == "__main__":
    unittest.main()
