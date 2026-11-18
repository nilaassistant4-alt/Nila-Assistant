"""Regression tests for the audit findings that touched update/path handling.

Each test here exists because a probe reproduced a real defect on the old code:

* ``extract_to`` from a manifest could write anywhere on disk (F1a)
* the "import test" executed package code before the install completed (F1b)
* ``tofu`` accepted unsigned manifests forever and never pinned (F1c)
* zip containment used a string prefix, so ``/x/y-private`` passed (F2)
* an unauthenticated HMAC key could verify a signature (F1c-2)

If you are reading this because a test failed: do not relax the assertion.
Every one of these was demonstrated against the shipped code with a synthetic
fixture, and the comments say which line was wrong.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from brain.config import Config
from brain.updates.installer import Installer, InstallError
from brain.updates.manager import UpdateManager
from brain.updates.manifest import (
    Manifest, ManifestError, Artifact, generate_signing_key, sign_manifest, verify_signature,
)
from brain.util.safe_paths import (
    UnsafePath, is_within, open_within, resolve_within, safe_relative,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _manifest(art: dict, version: str = "9.9.9") -> dict:
    return {"schema": 1, "channel": "stable", "version": version, "released_at": 1,
            "min_client": "0.0.0", "notes": "test", "artifacts": [art]}


def _publish(tmp: Path, raw: dict, artifact: bytes, name: str = "pkg.zip") -> Path:
    feed = tmp / "feed"
    feed.mkdir(exist_ok=True)
    (feed / name).write_bytes(artifact)
    path = feed / "manifest.json"
    path.write_text(json.dumps(raw))
    return path


# --------------------------------------------------------------------------- #
# F2 — containment is boundary-safe, not string-prefix
# --------------------------------------------------------------------------- #
def test_sibling_with_shared_prefix_is_not_within(tmp_path):
    root = tmp_path / "releases"
    sibling = tmp_path / "releases-private"
    root.mkdir()
    sibling.mkdir()
    assert is_within(sibling / "x", root) is False
    assert is_within(root / "x", root) is True
    assert is_within(root, root) is True


def test_safe_relative_refuses_traversal_absolute_and_windows_paths():
    for bad in ("../escape", "a/../../b", "/etc/passwd", "C:/Windows/System32",
                "a\\..\\b", "", ".", "x\x00y"):
        with pytest.raises(UnsafePath):
            safe_relative(bad)
    assert safe_relative("runtime/agent").as_posix() == "runtime/agent"
    assert safe_relative("runtime\\agent").as_posix() == "runtime/agent"


def test_open_within_refuses_a_planted_symlink(tmp_path):
    root = tmp_path / "public"
    outside = tmp_path / "public-private"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("SYNTHETIC")
    (root / "link.txt").symlink_to(secret)
    with pytest.raises(UnsafePath):
        open_within(root, "link.txt")
    (root / "real.txt").write_text("fine")
    assert open_within(root, "real.txt").read_text() == "fine"


# --------------------------------------------------------------------------- #
# F1a — extract_to is validated at parse time and again at install time
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["../escape", "../../../../outside", "/tmp/absolute",
                                 "C:/win", "a/../../b"])
def test_manifest_rejects_traversing_extract_to(bad):
    raw = _manifest({"name": "p", "kind": "package", "path": "p.zip",
                     "sha256": "0" * 64, "bytes": 1, "extract_to": bad})
    with pytest.raises(ManifestError):
        Manifest.parse(raw)


def test_install_writes_only_inside_the_version_directory(tmp_path):
    payload = _zip({"marker.txt": b"fixture"})
    raw = _manifest({"name": "p", "kind": "package", "path": "pkg.zip",
                     "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                     "extract_to": "runtime/agent"})
    manifest = Manifest.parse(raw)
    installer = Installer(tmp_path / "updates")
    installer.install(manifest, {"p": payload}, import_test=False)
    target = tmp_path / "updates" / "installed" / manifest.version
    assert (target / "runtime" / "agent" / "marker.txt").read_bytes() == b"fixture"
    assert not (tmp_path / "escaped").exists()


def test_installer_rechecks_containment_even_if_parse_was_bypassed(tmp_path):
    """A symlinked install root must not let extract_to escape."""
    payload = _zip({"marker.txt": b"fixture"})
    art = Artifact(name="p", kind="package", path="pkg.zip",
                   sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                   extract_to="runtime")
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "updates"
    root.mkdir()
    # installed/<version> -> outside, so a naive join would write into `outside`
    (root / "installed").mkdir()
    (root / "installed" / "9.9.9").symlink_to(outside)
    manifest = Manifest(channel="stable", version="9.9.9", released_at=1, artifacts=[art])
    installer = Installer(root)
    with pytest.raises(InstallError):
        installer.install(manifest, {"p": payload}, import_test=False)
    assert not (outside / "runtime" / "marker.txt").exists()


# --------------------------------------------------------------------------- #
# F2 — zip entries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("entry", ["../evil.txt", "/abs.txt", "a/../../evil.txt"])
def test_zip_entries_cannot_escape(tmp_path, entry):
    payload = _zip({entry: b"evil"})
    art = Artifact(name="p", kind="package", path="pkg.zip",
                   sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                   extract_to="runtime")
    manifest = Manifest(channel="stable", version="1.2.3", released_at=1, artifacts=[art])
    installer = Installer(tmp_path / "updates")
    with pytest.raises(InstallError):
        installer.install(manifest, {"p": payload}, import_test=False)
    assert not (tmp_path / "evil.txt").exists()


def test_zip_symlink_entries_are_refused(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    payload = buf.getvalue()
    art = Artifact(name="p", kind="package", path="pkg.zip",
                   sha256=hashlib.sha256(payload).hexdigest(), bytes=len(payload),
                   extract_to="runtime")
    manifest = Manifest(channel="stable", version="1.2.4", released_at=1, artifacts=[art])
    with pytest.raises(InstallError):
        Installer(tmp_path / "updates").install(manifest, {"p": payload}, import_test=False)


# --------------------------------------------------------------------------- #
# F1b — import test must not run untrusted code
# --------------------------------------------------------------------------- #
def test_unauthenticated_install_does_not_execute_package_code(tmp_path):
    marker = tmp_path / "EXECUTED.txt"
    module = (f"import pathlib\npathlib.Path({str(marker)!r}).write_text('ran')\n"
              "VALUE = 1\n")
    payload = _zip({"auditpkg/__init__.py": b"", "auditpkg/probe.py": module.encode()})
    raw = _manifest({"name": "p", "kind": "package", "path": "pkg.zip",
                     "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                     "extract_to": "runtime", "meta": {"import_module": "auditpkg.probe"}})
    feed_manifest = _publish(tmp_path, raw, payload)
    manager = UpdateManager(Config(home=tmp_path / "home"))
    # `insecure` is the explicit escape hatch: nothing is verified, so nothing
    # it ships may be imported. (An unsigned manifest is refused outright in
    # tofu mode - see test_tofu_refuses_an_unsigned_manifest.)
    manager.add_channel("stable", feed_manifest.as_uri(), mode="insecure")
    manager.check("stable")
    result = manager.apply("stable", force=True, hot_load=False)
    assert result["ok"] is True
    assert not marker.exists(), "unverified package code was executed during install"
    assert any("import test skipped" in n for n in result["notes"])


def test_signed_tofu_channel_runs_the_import_test(tmp_path):
    key = generate_signing_key()
    payload = _zip({"auditpkg/__init__.py": b"", "auditpkg/probe.py": b"VALUE = 1\n"})
    raw = _manifest({"name": "p", "kind": "package", "path": "pkg.zip",
                     "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload),
                     "extract_to": "runtime", "meta": {"import_module": "auditpkg.probe"}})
    manifest = sign_manifest(Manifest.parse(raw), key["private"], key["alg"], key["key_id"])
    feed = tmp_path / "feed"
    feed.mkdir()
    (feed / "pkg.zip").write_bytes(payload)
    (feed / "manifest.json").write_text(manifest.to_json())
    manager = UpdateManager(Config(home=tmp_path / "home"))
    # strict + pre-pinned key = the only configuration that is authenticated on
    # the very first check, so it is the one allowed to execute an import test.
    manager.add_channel("stable", (feed / "manifest.json").as_uri(), mode="strict",
                        alg=key["alg"], public=key["public"], key_id=key["key_id"])
    assert manager.check("stable")["ok"] is True
    result = manager.apply("stable", force=True, hot_load=False)
    assert result["ok"] is True, result
    assert any("import test ok" in n for n in result["notes"]), result["notes"]


# --------------------------------------------------------------------------- #
# F1c — unsigned manifests are refused in tofu, and pins really pin
# --------------------------------------------------------------------------- #
def test_tofu_refuses_an_unsigned_manifest(tmp_path):
    manifest = Manifest.parse(_manifest({"name": "p", "kind": "config", "path": "c.json",
                                         "sha256": "0" * 64, "bytes": 1}))
    result = verify_signature(manifest, {"alg": "ed25519", "key_id": "", "public": "",
                                         "mode": "tofu"})
    assert result["ok"] is False
    assert "unsigned" in result["reason"]


def test_insecure_mode_is_the_only_place_unsigned_is_accepted():
    manifest = Manifest.parse(_manifest({"name": "p", "kind": "config", "path": "c.json",
                                         "sha256": "0" * 64, "bytes": 1}))
    result = verify_signature(manifest, {"alg": "ed25519", "key_id": "", "public": "",
                                         "mode": "insecure"})
    assert result["ok"] is True


def test_tofu_pins_then_rejects_a_different_key(tmp_path):
    key = generate_signing_key()
    other = generate_signing_key()
    raw = _manifest({"name": "p", "kind": "config", "path": "c.json",
                     "sha256": "0" * 64, "bytes": 1})
    signed = sign_manifest(Manifest.parse(raw), key["private"], key["alg"], key["key_id"])
    pin = verify_signature(signed, {"alg": key["alg"], "mode": "tofu", "key_id": "", "public": ""})
    assert pin["ok"] and pin["pin"]["key_id"] == key["key_id"]

    impostor = sign_manifest(Manifest.parse(raw), other["private"], other["alg"], other["key_id"])
    rejected = verify_signature(impostor, {"alg": key["alg"], "mode": "tofu",
                                           "key_id": pin["pin"]["key_id"], "public": ""})
    assert rejected["ok"] is False
    assert "key_id mismatch" in rejected["reason"]


def test_hmac_without_a_shared_secret_is_refused():
    """A tofu pin records only key_id for hmac; that must not verify anything."""
    key = generate_signing_key()
    if key["alg"] != "hmac-sha256":
        pytest.skip("ed25519 available; this defect only affects hmac channels")
    raw = _manifest({"name": "p", "kind": "config", "path": "c.json",
                     "sha256": "0" * 64, "bytes": 1})
    signed = sign_manifest(Manifest.parse(raw), key["private"], "hmac-sha256", key["key_id"])
    result = verify_signature(signed, {"alg": "hmac-sha256", "mode": "tofu",
                                       "key_id": key["key_id"], "public": ""})
    assert result["ok"] is False
    assert "no shared secret" in result["reason"]
