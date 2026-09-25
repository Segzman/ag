#!/usr/bin/env python3
"""Tests for install.sh. Stdlib only, temp dirs only.
Run: python3 tests/test_install.py
Env: AG_INSTALL_SH overrides script (default: live repo install.sh).

Covers: default HOME/.local/bin/ag and custom --prefix with spaces,
repeated-install refusal, --force replacement (incl. symlink referent
untouched, dangling symlink, atomic force when copy fails), directory
always refused, invalid args, missing/old Python (via fake PATH python3),
installed CLI --help. PATH/PATH-advice output quotes prefix paths.
Never writes real HOME or makes model calls.
"""
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
INSTALL = Path(os.environ.get("AG_INSTALL_SH", str(_REPO / "install.sh")))
REPO_AG = _REPO / "ag"


def run_install(*args, home=None, extra_env=None, path=None):
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    if path is not None:
        env["PATH"] = str(path)
    if extra_env:
        env.update(extra_env)
    p = subprocess.run(["/bin/sh", str(INSTALL)] + list(args),
                       capture_output=True, text=True, timeout=60, env=env)
    return p


def repo_bytes():
    return REPO_AG.read_bytes()


# Tools install.sh needs from PATH besides python3 (dirname for $0,
# uname, mkdir/mktemp/cp/chmod/mv/rm for install, cat for usage).
_UTILS = ("dirname", "uname", "mkdir", "mktemp", "cp", "chmod", "mv", "rm", "cat")


def make_bin_dir(*, python3=None, cp_override=None):
    """Fake PATH dir with symlinks to real utils; optional python3/cp shims.

    cp_override: int exit code for a failing cp shim, or "interrupt" for a
    shim that SIGINTs the installer then exits 0 (trap robustness check).
    """
    bindir = Path(tempfile.mkdtemp(prefix="aginst-bin-"))
    for name in _UTILS:
        real = shutil.which(name)
        assert real, f"need {name} on PATH to build fake bin"
        (bindir / name).symlink_to(real)
    if python3 == "missing":
        pass
    elif python3 == "old":
        (bindir / "python3").write_text("#!/bin/sh\nexit 1\n")
        (bindir / "python3").chmod(0o755)
    elif python3 == "real":
        real = shutil.which("python3")
        assert real, "need python3 on PATH to build fake bin"
        (bindir / "python3").symlink_to(real)
    else:
        raise AssertionError(f"unknown python3 mode: {python3}")
    if cp_override is not None:
        (bindir / "cp").unlink()
        if cp_override == "interrupt":
            (bindir / "cp").write_text("#!/bin/sh\nkill -INT $PPID\nexit 0\n")
        else:
            (bindir / "cp").write_text("#!/bin/sh\nexit %d\n" % cp_override)
        (bindir / "cp").chmod(0o755)
    return bindir


def test_help():
    for flag in ("--help", "-h"):
        p = run_install(flag)
        assert p.returncode == 0, (flag, p.stderr)
        assert "--prefix" in p.stdout and "--force" in p.stdout, p.stdout
    print("ok test_help")


def test_default_prefix():
    home = Path(tempfile.mkdtemp(prefix="aginst-home-"))
    p = run_install(home=home)
    assert p.returncode == 0, p.stderr
    target = home / ".local" / "bin" / "ag"
    assert target.is_file() and not target.is_symlink(), p.stderr
    assert target.read_bytes() == repo_bytes()
    assert os.access(target, os.X_OK), "installed ag not executable"
    assert "--dir" in p.stdout, p.stdout
    assert '"%s"' % target in p.stdout, p.stdout
    print("ok test_default_prefix")


def test_custom_prefix_with_spaces():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    prefix = base / "my dir" / "tool home"
    p = run_install("--prefix", str(prefix))
    assert p.returncode == 0, p.stderr
    target = prefix / "bin" / "ag"
    assert target.is_file(), p.stderr
    assert target.read_bytes() == repo_bytes()
    assert os.access(target, os.X_OK)
    assert '"%s"' % target in p.stdout, p.stdout
    assert '"%s:' % (prefix / "bin") in p.stdout, p.stdout
    print("ok test_custom_prefix_with_spaces")


def test_repeated_install_refusal_and_force():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    prefix = base / "p"
    assert run_install("--prefix", str(prefix)).returncode == 0
    target = prefix / "bin" / "ag"
    p = run_install("--prefix", str(prefix))
    assert p.returncode != 0, "second install without --force must refuse"
    assert "exists" in (p.stdout + p.stderr).lower() or "--force" in (p.stdout + p.stderr)
    assert target.read_bytes() == repo_bytes()
    target.write_bytes(b"corrupted")
    p = run_install("--prefix", str(prefix), "--force")
    assert p.returncode == 0, p.stderr
    assert target.read_bytes() == repo_bytes()
    assert os.access(target, os.X_OK)
    print("ok test_repeated_install_refusal_and_force")


def test_force_atomic_when_copy_fails():
    # Failing cp must leave existing regular file and symlink (plus
    # referent) unchanged, with no temp leftovers in bindir.
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    fakebin = make_bin_dir(python3="real", cp_override=1)
    prefix = base / "p"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    target = bindir / "ag"
    old = b"old-binary-content"
    target.write_bytes(old)
    p = run_install("--prefix", str(prefix), "--force", path=fakebin)
    assert p.returncode != 0, "failing cp must fail the install"
    assert target.is_file() and not target.is_symlink()
    assert target.read_bytes() == old, "old regular file must survive"
    assert list(bindir.glob(".ag.tmp.*")) == [], "no temp leftovers"
    # Same, but target is a symlink: link itself must survive, referent too.
    referent = base / "real-ag"
    referent.write_bytes(b"referent-content")
    target.unlink()
    target.symlink_to(referent)
    p = run_install("--prefix", str(prefix), "--force", path=fakebin)
    assert p.returncode != 0
    assert target.is_symlink(), "old symlink must survive failed force"
    assert referent.read_bytes() == b"referent-content"
    assert list(bindir.glob(".ag.tmp.*")) == [], "no temp leftovers"
    print("ok test_force_atomic_when_copy_fails")


def test_force_interrupt_survives():
    # SIGINT during copy must abort, not continue: forced target keeps old
    # bytes and no temp is left behind. Bounded by run_install timeout.
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    fakebin = make_bin_dir(python3="real", cp_override="interrupt")
    prefix = base / "p"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    target = bindir / "ag"
    old = b"old-binary-content"
    target.write_bytes(old)
    p = run_install("--prefix", str(prefix), "--force", path=fakebin)
    assert p.returncode != 0, "interrupted install must fail, not continue"
    assert target.is_file() and not target.is_symlink()
    assert target.read_bytes() == old, "interrupted force must not touch target"
    assert list(bindir.glob(".ag.tmp.*")) == [], "no temp leftovers"
    print("ok test_force_interrupt_survives")


def test_symlink_force_replaces_link_not_referent():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    prefix = base / "p"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    referent = base / "real-ag"
    referent.write_bytes(b"referent-content")
    link = bindir / "ag"
    link.symlink_to(referent)
    p = run_install("--prefix", str(prefix))
    assert p.returncode != 0, "symlink without --force must refuse"
    assert referent.read_bytes() == b"referent-content"
    p = run_install("--prefix", str(prefix), "--force")
    assert p.returncode == 0, p.stderr
    assert not link.is_symlink(), "force must replace symlink itself"
    assert link.read_bytes() == repo_bytes()
    assert referent.read_bytes() == b"referent-content", "referent must be untouched"
    print("ok test_symlink_force_replaces_link_not_referent")


def test_dangling_symlink():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    prefix = base / "p"
    bindir = prefix / "bin"
    bindir.mkdir(parents=True)
    link = bindir / "ag"
    link.symlink_to(base / "does-not-exist")
    assert run_install("--prefix", str(prefix)).returncode != 0
    p = run_install("--prefix", str(prefix), "--force")
    assert p.returncode == 0, p.stderr
    assert link.is_file() and not link.is_symlink()
    assert link.read_bytes() == repo_bytes()
    print("ok test_dangling_symlink")


def test_directory_always_refused():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    prefix = base / "p"
    target = prefix / "bin" / "ag"
    target.mkdir(parents=True)
    assert run_install("--prefix", str(prefix)).returncode != 0
    assert run_install("--prefix", str(prefix), "--force").returncode != 0
    assert target.is_dir()
    print("ok test_directory_always_refused")


def test_invalid_args():
    cases = [
        ["--bogus"],
        ["--prefix"],
        ["--prefix", "a", "extra-positional"],
        ["--force", "--bogus"],
    ]
    for argv in cases:
        p = run_install(*argv)
        assert p.returncode != 0, argv
    print("ok test_invalid_args")


def test_missing_python():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    fakebin = make_bin_dir(python3="missing")
    prefix = base / "p"
    p = run_install("--prefix", str(prefix), path=fakebin)
    assert p.returncode != 0, "missing python must fail before modifying target"
    assert not (prefix / "bin" / "ag").exists(), "target must not be created"
    print("ok test_missing_python")


def test_old_python():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    fakebin = make_bin_dir(python3="old")
    prefix = base / "p"
    p = run_install("--prefix", str(prefix), path=fakebin)
    assert p.returncode != 0, "old python must fail"
    assert not (prefix / "bin" / "ag").exists(), "target must not be created"
    print("ok test_old_python")


def test_installed_cli_help():
    base = Path(tempfile.mkdtemp(prefix="aginst-"))
    prefix = base / "p"
    assert run_install("--prefix", str(prefix)).returncode == 0
    target = prefix / "bin" / "ag"
    mode = target.stat().st_mode
    assert mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), oct(mode)
    p = subprocess.run([str(target), "--help"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "usage" in p.stdout.lower() or "ag" in p.stdout.lower(), p.stdout
    print("ok test_installed_cli_help")


if __name__ == "__main__":
    test_help()
    test_default_prefix()
    test_custom_prefix_with_spaces()
    test_repeated_install_refusal_and_force()
    test_force_atomic_when_copy_fails()
    test_force_interrupt_survives()
    test_symlink_force_replaces_link_not_referent()
    test_dangling_symlink()
    test_directory_always_refused()
    test_invalid_args()
    test_missing_python()
    test_old_python()
    test_installed_cli_help()
    print("all install tests passed")
