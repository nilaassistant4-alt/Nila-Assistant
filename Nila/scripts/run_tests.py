#!/usr/bin/env python3
"""Run the test suite without pytest installed.

An external reviewer could not run the suite at all: their environment has no
pytest, so every claim in the README was unverifiable to them. That is a
packaging defect, not a reviewer problem — a shipped archive must be checkable
with the interpreter it ships against.

This discovers ``tests/test_*.py``, imports each one, and calls every module-level
``test_*`` function. pytest-style ``@pytest.mark.parametrize`` cases are expanded
by reading the decorator's argument list, so the parametrised security tests still
run rather than being silently skipped. Fixtures are **not** emulated: a test that
takes unrecognised arguments is reported as SKIPPED with a reason, never as a pass.

    python3 scripts/run_tests.py            # whole suite
    python3 scripts/run_tests.py test_voice # one module
"""

from __future__ import annotations

import builtins
import importlib
import importlib.util
import inspect
import pathlib
import sys
import tempfile
import traceback

try:                       # real pytest exposes its skip exception
    from _pytest.outcomes import Skipped as _Skipped  # type: ignore
except Exception:          # pragma: no cover - standalone runs
    class _Skipped(Exception):
        pass

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"


def load(path: pathlib.Path):
    name = "nila_tests_" + path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def expand(module, fn):
    """Yield (label, kwargs) for a test, expanding simple parametrize decorators."""
    cases: list[tuple[str, dict]] = [(fn.__name__, {})]
    for dec in getattr(fn, "_nila_params", []):
        argnames, values = dec
        names = [a.strip() for a in argnames.split(",")]
        out = []
        for label, kw in cases:
            for v in values:
                vs = v if len(names) > 1 else (v,)
                extra = dict(zip(names, vs))
                out.append((f"{label}[{v!r}]", {**kw, **extra}))
        cases = out
    return cases


class _Raises:
    """`with pytest.raises(Err):` - assert the block raises, else fail the test."""

    def __init__(self, expected):
        self.expected = expected
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(
                f"DID NOT RAISE {getattr(self.expected, '__name__', self.expected)}")
        if isinstance(self.expected, tuple):
            ok = issubclass(exc_type, self.expected)
        else:
            ok = issubclass(exc_type, self.expected)
        if not ok:
            return False                      # wrong exception: let it propagate
        self.value = exc
        return True


class _Approx:
    def __init__(self, expected, rel=None, abs=None):
        self.expected = expected
        self.tol = abs if abs is not None else 1e-6

    def __eq__(self, other):
        try:
            return abs(other - self.expected) <= self.tol
        except TypeError:
            return False


class Param:
    """Stand-in for `pytest.mark`.

    Supports the marks the suite actually uses. Everything else degrades to a
    no-op decorator, so an unrecognised mark never turns into an import error -
    a fallback runner that crashes is worse than one that is honest about limits.
    Each mark works bare (`@pytest.mark.skip`) or called (`@pytest.mark.skip()`).
    """

    def parametrize(self, argnames, values, **kwargs):
        def wrap(fn):
            existing = getattr(fn, "_nila_params", [])
            fn._nila_params = [(argnames, list(values))] + existing
            return fn
        return wrap

    def skipif(self, condition, reason="", **kwargs):
        def wrap(fn):
            # The condition may be a plain bool, a string that pytest would evaluate,
            # or a callable. BUG-4 was the second case: the shim skipped only when the
            # expression was already True at import time, so `skipif(ZIP is None, ...)`
            # with ZIP bound later ran the test and reported a false failure.
            if isinstance(condition, str):
                try:
                    module = sys.modules.get(getattr(fn, "__module__", ""), None)
                    scope = dict(getattr(module, "__dict__", {}))
                    scope.setdefault("ZIP", None)
                    decided = bool(eval(condition, scope, {}))   # noqa: S307 - shim
                except Exception:
                    # Cannot decide -> do not claim it passes and do not fail it: run
                    # it and let it decide, which is what the old shim did by accident.
                    decided = False
            else:
                decided = bool(condition)
            if decided:
                fn._nila_skip = reason or "skipif condition true"
            return fn
        return wrap

    def skip(self, reason=""):
        if callable(reason):                      # bare @pytest.mark.skip
            reason._nila_skip = "skip"
            return reason
        def wrap(fn):
            fn._nila_skip = reason or "skip"
            return fn
        return wrap

    def xfail(self, *args, **kwargs):
        if args and callable(args[0]):
            return args[0]
        return lambda fn: fn

    def __getattr__(self, item):
        if item.startswith("_"):
            raise AttributeError(item)
        def deco(*args, **kwargs):
            if args and callable(args[0]):
                return args[0]
            return lambda fn: fn
        return deco


class _MonkeyPatch:
    """The four operations the suite uses, so `monkeypatch` fixtures still run.

    Not a reimplementation of pytest: only `setattr` / `setitem` / `delattr` /
    `delenv` / `setenv` exist, each recorded and undone when the test ends.
    """

    def __init__(self, cleanups: list):
        self._undo: list = []
        cleanups.append(("monkeypatch", self._restore()))

    def _restore(self):
        # The runner walks cleanups with next(gen, None): the FIRST next starts the
        # generator (nothing to do yet), the second one runs the undo. A bare `yield`
        # here meant the undo never ran and the patched function stayed in place for
        # every later test in the module - 7 false failures in test_session_expiry.
        yield
        self.undo()

    def setattr(self, target, name, value=None, raising: bool = True):
        if isinstance(target, str) and value is None:
            # `monkeypatch.setattr("module.attr", value)` string form. pytest imports
            # the LONGEST importable prefix and then walks attribute by attribute,
            # which is what lets "brain.memory.vault.time.monotonic" work: `time` is a
            # module imported by vault, not a submodule of it. rpartition alone failed
            # with "No module named 'brain.memory.vault.time'".
            parts = target.split(".")
            for cut in range(len(parts) - 1, 0, -1):
                try:
                    obj = importlib.import_module(".".join(parts[:cut]))
                except ImportError:
                    continue
                for attr_name in parts[cut:-1]:
                    obj = getattr(obj, attr_name)
                target, attr = obj, parts[-1]
                break
            else:
                raise AttributeError(f"could not resolve {target!r}")
            name, value = attr, name
        old = getattr(target, name, _MISSING)
        setattr(target, name, value)
        self._undo.append(lambda: (setattr(target, name, old) if old is not _MISSING
                                   else delattr(target, name)))

    def setitem(self, mapping, key, value):
        old = mapping.get(key, _MISSING)
        mapping[key] = value
        self._undo.append(lambda: (mapping.__setitem__(key, old) if old is not _MISSING
                                   else mapping.pop(key, None)))

    def delattr(self, target, name, raising: bool = True):
        old = getattr(target, name)
        delattr(target, name)
        self._undo.append(lambda: setattr(target, name, old))

    def setenv(self, name, value, prepend=None):
        import os

        old = os.environ.get(name, _MISSING)
        os.environ[name] = str(value)
        self._undo.append(lambda: (os.environ.__setitem__(name, old) if old is not _MISSING
                                   else os.environ.pop(name, None)))

    def delenv(self, name, raising: bool = True):
        import os

        # `raising=False` is the common form: the variable may simply not be set.
        # Pop unconditionally and remember whether it existed, or a machine WITH a
        # GEMINI_API_KEY in its environment fails tests that only wanted it absent.
        old = os.environ.pop(name, _MISSING)
        if old is not _MISSING:
            self._undo.append(lambda: os.environ.__setitem__(name, old))

    def undo(self):
        for fn in reversed(self._undo):
            try:
                fn()
            except Exception:
                pass
        self._undo.clear()


_MISSING = object()


class _CapSys:
    """Capture what the test writes, so `capsys.readouterr()` works without pytest."""

    def __init__(self, cleanups: list):
        import io

        self._out, self._err = io.StringIO(), io.StringIO()
        self._saved = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self._out, self._err
        cleanups.append(("capsys", self._restore()))

    def _restore(self):
        yield
        sys.stdout, sys.stderr = self._saved

    def readouterr(self):
        import types

        out, err = self._out.getvalue(), self._err.getvalue()
        self._out.seek(0), self._out.truncate(0)
        self._err.seek(0), self._err.truncate(0)
        return types.SimpleNamespace(out=out, err=err)


class FixtureError(Exception):
    """A test needs a fixture this runner cannot build."""


def _tmp_factory():
    class _F:
        def mktemp(self, name="t"):
            return pathlib.Path(tempfile.mkdtemp(prefix=f"nila-{name}-"))
        def getbasetemp(self):
            return pathlib.Path(tempfile.gettempdir())
    return _F()


def resolve_fixtures(names, conftest, tmp_path, cleanups, module=None):
    """Build pytest fixtures by hand, following their dependency chain.

    conftest.py's fixtures are plain generator functions once the `pytest` shim
    replaces `@pytest.fixture` with the identity, so they can be called directly.
    Generators are advanced to their `yield`; teardown is registered so the
    fixture's cleanup still runs.
    """
    cache: dict = {}

    def get(name):
        if name in cache:
            return cache[name]
        if name == "tmp_path":
            val = tmp_path
        elif name == "tmp_path_factory":
            val = _tmp_factory()
        elif name == "monkeypatch":
            # BUG-4's second half: 129 tests take `monkeypatch`, and a fallback runner
            # that cannot build it reports them as "not built" - honest, but it means
            # the runner proves less every year. This is the four methods the suite
            # actually uses, undone in reverse after the test.
            val = _MonkeyPatch(cleanups)
        elif name == "capsys":
            val = _CapSys(cleanups)
        else:
            # module-local fixtures live in the test file itself
            fn = getattr(conftest, name, None)
            if fn is None or not callable(fn):
                fn = getattr(module, name, None)
            if fn is None or not callable(fn):
                raise FixtureError(name)
            sig = inspect.signature(fn)
            args = {p: get(p) for p in sig.parameters}
            res = fn(**args)
            if inspect.isgenerator(res):
                val = next(res)
                cleanups.append((name, res))
            else:
                val = res
        cache[name] = val
        return val

    return {n: get(n) for n in names}


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(ROOT))
    # `tests/` on the path too: some modules do `from conftest import PASSPHRASE`, which
    # pytest allows because it puts the test file's own directory on sys.path. Without
    # this the fallback reported a false "IMPORT ERROR" for a module that is fine.
    sys.path.insert(0, str(TESTS))
    wanted = argv[1] if len(argv) > 1 else None

    # provide a `pytest` shim only if the real one is absent
    try:
        import pytest  # noqa: F401
        have_pytest = True
    except ImportError:
        have_pytest = False
    if have_pytest and "--shim" not in argv:
        # BUG-4. This file exists for machines WITHOUT pytest (the owner's laptop may
        # not have it). When pytest IS installed, imitating it is how a runner reports
        # "2 failed" for tests that pass: `monkeypatch` string targets, `capsys`, and
        # `skipif` evaluated at a different moment all behave slightly differently.
        # So hand over to the real thing and say so, instead of guessing at it. The
        # shim is still testable and still used where it belongs: `--shim`.
        import subprocess

        cmd = [sys.executable, "-m", "pytest", "tests/", "-q"]
        if wanted:
            cmd = [sys.executable, "-m", "pytest", "-q", "-k", wanted]
        print("real pytest is installed, so it runs the suite (this fallback is for "
              "machines without it; force the shim with --shim)")
        return subprocess.call(cmd)

    if not have_pytest:
        shim = type(sys)("pytest")
        shim.mark = Param()
        # @pytest.fixture may be used bare or called: both must pass the fn through
        shim.fixture = lambda *a, **k: (a[0] if a and callable(a[0])
                                        else (lambda f: f))
        shim.raises = lambda expected, **k: _Raises(expected)
        shim.approx = lambda expected, **k: _Approx(expected, **k)
        shim.fail = lambda msg="": (_ for _ in ()).throw(AssertionError(msg))
        shim.param = lambda *a, **k: a
        shim.skip = lambda reason="": None
        shim.__version__ = "0.0-fallback"
        sys.modules["pytest"] = shim

    files = sorted(TESTS.glob("test_*.py"))
    if wanted:
        files = [f for f in files if wanted in f.stem]
    if not files:
        print(f"no test modules matched {wanted!r}")
        return 2

    if "--one" not in argv:
        # BUG-4's last layer. Running every module in ONE process meant a module's
        # leaked global (a patched clock, a cached config, a migrated vector store)
        # decided whether the NEXT module passed: `test_instruction_memory_acceptance`
        # reported 28 failures in the full run and 28 passes on its own. pytest gets
        # isolation from its fixture teardown and from collecting fresh; the fallback
        # gets it the blunt, reliable way - one process per module - and adds up the
        # results. The owner's laptop pays a second or so per file for a runner that
        # cannot lie about a pass.
        import re

        import subprocess

        total_pass = total_fail = total_skip = 0
        failing: list = []
        skip_reasons_all: dict = {}
        for path in files:
            proc = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()),
                                   path.stem, "--one"],
                                  capture_output=True, text=True, cwd=str(ROOT))
            out = proc.stdout or ""
            m = re.search(r"^RESULT pass=(\d+) fail=(\d+) skip=(\d+)$", out, re.M)
            if not m:
                total_fail += 1
                failing.append((path.stem, "<module>",
                                (proc.stdout + proc.stderr)[-2000:]))
                print(f"{path.stem:34s} FAIL  (module did not report)")
                continue
            p_, f_, s_ = (int(x) for x in m.groups())
            total_pass += p_
            total_fail += f_
            total_skip += s_
            flag = "FAIL" if f_ else "ok"
            print(f"{path.stem:34s} {flag:5s} pass={p_:<4d} fail={f_:<3d} skip={s_}")
            if f_:
                # The child prints its tracebacks; collect them so a failure can be
                # read from the parent's output instead of being counted and hidden.
                for note in re.findall(r"^--- .*?(?=^--- |\Z)", out, re.M | re.S):
                    failing.append((path.stem, "", note))
                if not re.search(r"^--- ", out, re.M):
                    failing.append((path.stem, "<no traceback>", out[-3000:]))
            for line in re.findall(r"^SKIP (.+)$", out, re.M):
                skip_reasons_all[line] = skip_reasons_all.get(line, 0) + 1
        print()
        for mod, label, tb in failing[:12]:
            print(f"--- {mod}::{label}\n{tb}"[:4000])
        print(f"total: {total_pass} passed, {total_fail} failed, {total_skip} skipped "
              f"(with pytest shim, one process per module)")
        if total_skip:
            top = ", ".join(f"{k} ({v})" for k, v in
                            sorted(skip_reasons_all.items(), key=lambda kv: -kv[1])[:5])
            print(f"note: {top}")
        return 1 if total_fail else 0

    conftest = None
    cf = TESTS / "conftest.py"
    if cf.exists() and not have_pytest:
        try:
            conftest = load(cf)
        except Exception:
            conftest = None

    passed = failed = skipped = 0
    failures = []
    skip_reasons: dict = {}
    for path in files:
        try:
            mod = load(path)
        except Exception:
            failed += 1
            failures.append((path.stem, "<import>", traceback.format_exc(limit=3)))
            print(f"{path.stem}: IMPORT ERROR")
            continue
        names = [n for n in dir(mod) if n.startswith("test_")]
        mod_pass = mod_fail = mod_skip = 0
        for n in sorted(names):
            fn = getattr(mod, n)
            if not callable(fn):
                continue
            if getattr(fn, "_nila_skip", None):
                mod_skip += 1
                continue
            sig = inspect.signature(fn)
            needs = [p for p in sig.parameters
                     if sig.parameters[p].default is inspect.Parameter.empty]
            for label, kwargs in expand(mod, fn):
                # A test that takes `capsys` replaces sys.stdout; if it raises before
                # its own setup finishes, the recorder is never registered and real
                # stdout is lost - the runner then prints nothing and looks like it
                # hung. Snapshot the streams per test and put them back unconditionally
                # (BUG-4's second half: 759 skips printed no reason at all).
                stdout_before, stderr_before = sys.stdout, sys.stderr
                unresolved = [p for p in needs if p not in kwargs]
                if unresolved and have_pytest:
                    # Real pytest is installed: it resolves fixtures properly, so
                    # this fallback has nothing honest to add for this test.
                    mod_skip += 1
                    continue
                cleanups: list = []
                try:
                    if unresolved:
                        try:
                            kwargs.update(resolve_fixtures(
                                unresolved, conftest,
                                pathlib.Path(tempfile.mkdtemp(prefix="nila-tmp-")),
                                cleanups, module=mod))
                        except FixtureError as fe:
                            mod_skip += 1
                            skip_reasons[f"fixture {fe}"] = skip_reasons.get(
                                f"fixture {fe}", 0) + 1
                            continue
                    fn(**kwargs)
                    mod_pass += 1
                except _Skipped as exc:
                    # `pytest.skip()` raises; the fallback must honour it as a
                    # skip, not report a failing test. Without this, a machine
                    # with no distributable ZIP showed two FAILs from
                    # test_package_integrity (audit F11).
                    mod_skip += 1
                    skip_reasons[f"skip: {exc}"] = skip_reasons.get(f"skip: {exc}", 0) + 1
                except Exception:
                    mod_fail += 1
                    failures.append((path.stem, label, traceback.format_exc(limit=4)))
                finally:
                    for _fname, gen in reversed(cleanups):
                        try:
                            # Two steps: the first `next` starts the generator (a yield
                            # fixture's setup), the second runs its teardown. Calling it
                            # once - which is what this did - left every fixture and
                            # every monkeypatch in place for the next test.
                            next(gen, None)
                            next(gen, None)
                        except StopIteration:
                            pass
                        except Exception:
                            pass
                    if sys.stdout is not stdout_before:
                        sys.stdout = stdout_before
                    if sys.stderr is not stderr_before:
                        sys.stderr = stderr_before
        passed += mod_pass; failed += mod_fail; skipped += mod_skip
        flag = "FAIL" if mod_fail else "ok"
        print(f"{path.stem:34s} {flag:5s} pass={mod_pass:<4d} fail={mod_fail:<3d} "
              f"skip={mod_skip}")

    print()
    for mod, label, tb in failures[:12]:
        print(f"--- {mod}::{label}\n{tb}")
    if "--one" in argv:
        # read by the parent that runs one module per process
        print(f"RESULT pass={passed} fail={failed} skip={skipped}")
    print(f"total: {passed} passed, {failed} failed, {skipped} skipped "
          f"({'with pytest shim' if not have_pytest else 'real pytest present'})")
    if not have_pytest and skipped:
        top = ", ".join(f"{k} ({v})" for k, v in
                        sorted(skip_reasons.items(), key=lambda kv: -kv[1])[:5])
        print(f"note: {skipped} test(s) could not be built by the fallback runner"
              f"{': ' + top if top else ''}.")
        if "--one" in argv:
            for reason in sorted(skip_reasons, key=lambda k: -skip_reasons[k])[:8]:
                print(f"SKIP {reason}")
    if have_pytest:
        print("note: real pytest is installed - `python3 -m pytest tests/ -q` runs "
              "the whole suite including fixtures this fallback skips.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
