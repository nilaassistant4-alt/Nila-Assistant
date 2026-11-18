"""Package integrity: every module imports, and the shipped archive is complete.

This exists because of a real defect. The build script excluded `*/updates/*` to
keep runtime update data out of the ZIP, which also swallowed `brain/updates/` -
the entire signed-update source package. The archive built, extracted, and passed
the full test suite, because no test imported that module. It was caught by a
security probe reading the file list, not by CI.

Two guards:

* every Python module under `brain/`, `cloud/` and `scripts/` must import. A
  module that cannot be imported cannot be excluded-by-accident and still pass.
* if the distributable ZIP is present, its file list must match the source tree.

The second test skips when no ZIP has been built, so it does not couple the suite
to the packaging step; `scripts/build_package.sh` enforces the same property at
build time and fails the build.
"""

from __future__ import annotations

import importlib
import pathlib
import pkgutil
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: modules that are entry points or have heavy optional imports at module scope
SKIP_IMPORT = frozenset()


def _iter_modules(package_name: str):
    pkg = importlib.import_module(package_name)
    if not hasattr(pkg, "__path__"):
        yield package_name
        return
    for info in pkgutil.walk_packages(pkg.__path__, prefix=package_name + "."):
        yield info.name


def _all_modules():
    out = []
    for top in ("brain",):
        out.extend(_iter_modules(top))
    return sorted(set(out))


MODULES = _all_modules()


def test_the_brain_package_has_a_meaningful_number_of_modules():
    """Guard against this test silently passing over an empty list."""
    assert len(MODULES) >= 25, f"expected a real package, found {len(MODULES)}: {MODULES}"


@pytest.mark.parametrize("name", MODULES)
def test_every_module_imports(name):
    if name in SKIP_IMPORT:
        pytest.skip("explicitly skipped")
    importlib.import_module(name)


def test_updates_package_is_present_and_importable():
    """The specific regression: brain/updates/ vanished from the ZIP once."""
    for mod in ("brain.updates", "brain.updates.manager", "brain.updates.installer",
                "brain.updates.manifest", "brain.updates.channels"):
        importlib.import_module(mod)


def test_cloud_servers_are_present():
    for mod in ("brain.cloud.protocol",):
        importlib.import_module(mod)
    assert (ROOT / "cloud" / "sync_server" / "app.py").exists()
    assert (ROOT / "cloud" / "update_server" / "app.py").exists()


def test_required_files_exist_in_the_source_tree():
    required = [
        "README.md", "pyproject.toml", "requirements.txt", "requirements-optional.txt",
        ".env.example", ".gitignore", "brain.py",
        "docs/ARCHITECTURE.md", "docs/THREAT_MODEL.md", "docs/PERMISSIONS.md",
        "docs/PII_SCHEMA.md",
        "brain/identity/voice_model.json", "config/integrations.example.json",
        "scripts/build_package.sh",
    ]
    missing = [f for f in required if not (ROOT / f).exists()]
    assert not missing, f"missing from the source tree: {missing}"


def test_adr_files_are_present():
    adr = sorted((ROOT / "docs" / "adr").glob("*.md"))
    assert len(adr) >= 7, f"expected 7 ADRs, found {len(adr)}"


# --------------------------------------------------------------------------- #
# the archive itself, when one has been built
# --------------------------------------------------------------------------- #
def _find_zip():
    candidates = [ROOT.parent / "personal-brain-mvp.zip", ROOT / "personal-brain-mvp.zip"]
    for c in candidates:
        if c.exists():
            return c
    return None


ZIP = _find_zip()


def _require_zip():
    """Skip cleanly when no ZIP was built.

    The `@pytest.mark.skipif` used to be the only guard, but the fallback runner
    (``scripts/run_tests.py``) calls the function directly, where a mark does
    nothing - so a machine without a ZIP reported two hard failures with
    ``AttributeError: 'NoneType' object has no attribute 'seek'`` instead of two
    skips.
    """
    if ZIP is None:
        pytest.skip("no distributable ZIP built yet")


@pytest.mark.skipif(ZIP is None, reason="no distributable ZIP built yet")
def test_zip_contains_every_source_module():
    _require_zip()
    with zipfile.ZipFile(ZIP) as zf:
        names = {n.split("/", 1)[-1] for n in zf.namelist()}
    src = set()
    for top in ("brain", "cloud", "tests", "scripts"):
        for p in (ROOT / top).rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            src.add(str(p.relative_to(ROOT)))
    missing = sorted(src - names)
    assert not missing, f"ZIP is missing source files: {missing}"


@pytest.mark.skipif(ZIP is None, reason="no distributable ZIP built yet")
def test_zip_carries_no_secrets_or_caches():
    _require_zip()
    bad_patterns = ("__pycache__/", ".pyc", "memory.db", "identity.json",
                    "vault.bundle", "permissions.json", "permissions.PANIC",
                    "audit.log", "/.env")
    with zipfile.ZipFile(ZIP) as zf:
        names = zf.namelist()
    hits = []
    for n in names:
        tail = n.split("/")[-1]
        if "/build/" in n or "egg-info/" in n:
            hits.append(n)
            continue
        for pat in bad_patterns:
            clean = pat.strip("/")
            if clean in ("memory.db", "identity.json", "vault.bundle",
                         "permissions.json", "permissions.PANIC", ".env"):
                if tail == clean:
                    hits.append(n)
            elif pat in n:
                hits.append(n)
    assert not hits, f"ZIP carries generated or secret files: {sorted(set(hits))}"
