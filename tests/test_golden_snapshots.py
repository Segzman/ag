#!/usr/bin/env python3
"""Golden snapshots: --help for every (sub)command, the top-level namespace, read-only --json output.

Baseline for the refactor: snapshots live in tests/golden/. Regenerate with tools/golden_update.py only
when a change to help text, namespace or JSON is intended. Stdlib, hermetic env, temp state dirs.
"""
import argparse, concurrent.futures as cf, difflib, importlib.machinery, importlib.util, json, os, re, subprocess, sys, tempfile, types, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
AG = str(ROOT / "ag")
GOLDEN = ROOT / "tests" / "golden"
# (snapshot name, args after --dir/--json, setup commands run first in the same sandbox; {S}/{W}/{M} = state/work/mail)
JSON_CASES = [
    ("agents_list", ["agents", "list"], []),
    ("route", ["route"], []),
    ("wakes", ["wakes"], []),
    ("sessions", ["sessions"], []),
    ("schedule_list", ["schedule", "list"], []),
    ("providers_list", ["providers", "list"], []),
    ("budget_show", ["budget", "show"], []),
    ("usage", ["usage"], []),
    ("relay_name_list", ["relay", "name", "list"],
     [["relay", "init", "--node", "golden", "--state", "{S}", "--workdir", "{W}", "--dir", "{M}"]]),
]
TS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?")
VOLATILE_KEY = re.compile(r"(^|_)(ts|at|since|age_s)$|(^|_)time$")


def _load_ag():
    loader = importlib.machinery.SourceFileLoader("ag_mod_golden", AG)
    spec = importlib.util.spec_from_loader("ag_mod_golden", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def _command_paths(parser, prefix=()):
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sub in a.choices.items():
                yield prefix + (name,)
                yield from _command_paths(sub, prefix + (name,))


def _env(home):
    return dict(PATH="/usr/bin:/bin", LANG="C.UTF-8", COLUMNS="100", AG_AUTO_UPDATE="0", HOME=home,
                XDG_CONFIG_HOME=home + "/.config", XDG_CACHE_HOME=home + "/.cache", XDG_DATA_HOME=home + "/.local/share",
                AG_HOME=home, AG_CONFIG_HOME=home + "/cfg", AG_CACHE_HOME=home + "/cache")


def _run(args, env, cwd):
    p = subprocess.run([sys.executable, AG, *args], cwd=cwd, env=env, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=40)
    if p.returncode:
        raise RuntimeError("ag %s rc=%d\n%s%s" % (" ".join(args), p.returncode, p.stdout, p.stderr))
    return p.stdout


def _scrub(text, roots):
    for r in roots:
        text = text.replace(r, "<TMP>")
    return TS.sub("<TS>", text)


def _scrub_json(v, roots, key=""):
    if isinstance(v, dict):
        return {k: _scrub_json(x, roots, k) for k, x in v.items()}
    if isinstance(v, list):
        return [_scrub_json(x, roots, key) for x in v]
    if isinstance(v, (int, float)) and not isinstance(v, bool) and VOLATILE_KEY.search(key):
        return "<NUM>"
    if isinstance(v, str):
        return _scrub(v, roots)
    return v


def _write(dest, text):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def _snapshot(root):
    root = Path(root)
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def generate(out):
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="ag-golden-") as tmp:
        roots = sorted({tmp, os.path.realpath(tmp)}, key=len, reverse=True)
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        env = _env(home)

        paths = [()] + list(_command_paths(_load_ag().build()))
        with cf.ThreadPoolExecutor(8) as ex:
            helps = list(ex.map(lambda p: _scrub(_run([*p, "--help"], env, home), roots), paths))
        for path, text in zip(paths, helps):
            _write(out / "help" / (("__".join(path) or "_root") + ".txt"), text)

        names = sorted(k for k, v in vars(_load_ag()).items()
                       if not (k.startswith("__") and k.endswith("__")) and not isinstance(v, types.ModuleType))
        _write(out / "namespace.txt", "".join(n + "\n" for n in names))

        for name, args, setup in JSON_CASES:
            sandbox = os.path.join(tmp, name)
            dirs = {k: os.path.join(sandbox, k) for k in ("home", "state", "work", "mail")}
            for d in ("home", "state", "work"):
                os.makedirs(dirs[d])
            cenv = _env(dirs["home"])
            fill = lambda a: [x.replace("{S}", dirs["state"]).replace("{W}", dirs["work"]).replace("{M}", dirs["mail"]) for x in a]
            for cmd in setup:
                _run(["--dir", dirs["state"], "--json", *fill(cmd)], cenv, dirs["home"])
            data = json.loads(_run(["--dir", dirs["state"], "--json", *args], cenv, dirs["home"]))
            roots_case = sorted({sandbox, os.path.realpath(sandbox)}, key=len, reverse=True)
            _write(out / "json" / (name + ".json"), json.dumps(_scrub_json(data, roots_case), indent=2, ensure_ascii=False) + "\n")


class GoldenSnapshots(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="ag-golden-test-")
        cls.addClassCleanup(cls.tmp.cleanup)
        generate(Path(cls.tmp.name) / "golden")
        cls.fresh = _snapshot(Path(cls.tmp.name) / "golden")
        cls.golden = _snapshot(GOLDEN)

    def _same(self, prefix):
        want = {k: v for k, v in self.golden.items() if k.startswith(prefix)}
        got = {k: v for k, v in self.fresh.items() if k.startswith(prefix)}
        self.assertTrue(want, prefix + ": no golden files, run tools/golden_update.py")
        if set(want) != set(got):
            self.fail("%s: snapshot file set differs; missing %s, unexpected %s (run tools/golden_update.py if intended)"
                      % (prefix, sorted(set(want) - set(got)), sorted(set(got) - set(want))))
        for k in sorted(want):
            if want[k] != got[k]:
                diff = "".join(difflib.unified_diff(want[k].decode("utf-8").splitlines(True), got[k].decode("utf-8").splitlines(True),
                                                    "golden/" + k, "fresh/" + k))
                self.fail("%s differs from golden (run tools/golden_update.py if intended):\n%s" % (k, diff))

    def test_help_identical(self):
        self._same("help/")

    def test_json_identical(self):
        self._same("json/")

    def test_namespace_identical(self):
        self._same("namespace.txt")

    def test_namespace_names_resolve(self):
        mod = _load_ag()
        names = (GOLDEN / "namespace.txt").read_text(encoding="utf-8").split()
        missing = [n for n in names if not hasattr(mod, n)]
        self.assertFalse(missing, "names in namespace.txt no longer resolve: " + ", ".join(missing))


if __name__ == "__main__":
    unittest.main()
