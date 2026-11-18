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
            if condition:
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
    wanted = argv[1] if len(argv) > 1 else None

    # provide a `pytest` shim only if the real one is absent
    try:
        import pytest  # noqa: F401
        have_pytest = True
    except ImportError:
        have_pytest = False
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
                            next(gen, None)
                        except Exception:
                            pass
        passed += mod_pass; failed += mod_fail; skipped += mod_skip
        flag = "FAIL" if mod_fail else "ok"
        print(f"{path.stem:34s} {flag:5s} pass={mod_pass:<4d} fail={mod_fail:<3d} "
              f"skip={mod_skip}")

    print()
    for mod, label, tb in failures[:12]:
        print(f"--- {mod}::{label}\n{tb}")
    print(f"total: {passed} passed, {failed} failed, {skipped} skipped "
          f"({'with pytest shim' if not have_pytest else 'real pytest present'})")
    if not have_pytest and skipped:
        top = ", ".join(f"{k} ({v})" for k, v in
                        sorted(skip_reasons.items(), key=lambda kv: -kv[1])[:5])
        print(f"note: {skipped} test(s) could not be built by the fallback runner"
              f"{': ' + top if top else ''}.")
    if have_pytest:
        print("note: real pytest is installed - `python3 -m pytest tests/ -q` runs "
              "the whole suite including fixtures this fallback skips.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
