"""The dashboard's non-.py assets must ship: in the ZIP builder, and in pip package-data."""
from __future__ import annotations

import importlib.util
import pathlib

from brain.api import dashboard

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _builder():
    spec = importlib.util.spec_from_file_location("build_package", ROOT / "scripts" / "build_package.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_every_served_asset_exists_and_is_required_by_the_builder():
    bp = _builder()
    for _, (fname, _ctype) in dashboard.ASSETS.items():
        rel = f"brain/api/static/{fname}"
        assert (ROOT / rel).is_file(), rel
        assert rel in bp.REQUIRED_FILES, f"{rel} missing from REQUIRED_FILES"
        assert not bp.is_excluded(rel, fname), f"{rel} would be excluded from the ZIP"
    assert "brain/api/dashboard.py" in bp.REQUIRED_FILES


def test_pip_package_data_includes_static_assets():
    assert "api/static/*" in (ROOT / "pyproject.toml").read_text()


def test_built_zip_contains_the_dashboard():
    bp = _builder()
    names = {p.relative_to(ROOT).as_posix() for p in bp.collect()}
    for _, (fname, _c) in dashboard.ASSETS.items():
        assert f"brain/api/static/{fname}" in names
