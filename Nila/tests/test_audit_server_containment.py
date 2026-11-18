"""Regression tests for the two reference servers.

Both servers used ``str(candidate).startswith(str(root))`` to decide containment.
``/srv/releases-private`` passes that test against a root of ``/srv/releases``,
and ``Path.resolve()`` follows a planted symlink *before* the prefix is
compared - so a link inside the public directory served a private sibling.
Reproduced against both servers with a synthetic fixture.

The update server additionally performed its publish POST with no credential at
all whenever its token variable was unset (the default).
"""
from __future__ import annotations

import importlib.util
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent


def _load(rel: str, name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # type: ignore[union-attr]
    return mod


def _serve(handler_cls):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _post(url: str, payload: dict, token: str = ""):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


# --------------------------------------------------------------------------- #
# update server
# --------------------------------------------------------------------------- #
@pytest.fixture()
def update_server(tmp_path):
    mod = _load("cloud/update_server/app.py", "audit_update_server")
    public = tmp_path / "releases"
    private = tmp_path / "releases-private"
    staging = tmp_path / "staging"
    for d in (public, private, staging):
        d.mkdir()
    (private / "secret.txt").write_text("SYNTHETIC-PRIVATE")
    (public / "artifact.bin").write_bytes(b"public-bytes")
    (public / "manifest.json").write_text(json.dumps({"schema": 1}))
    (public / "link.txt").symlink_to(private / "secret.txt")

    mod.Handler.root = public
    mod.Handler.tokens = {"AUDIT-TOKEN"}
    mod.Handler.publish_root = staging
    srv, thread = _serve(mod.Handler)
    try:
        yield mod, srv.server_port, public, private, staging
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_update_server_serves_normal_files(update_server):
    _, port, _, _, _ = update_server
    status, body = _get(f"http://127.0.0.1:{port}/v1/artifact.bin")
    assert status == 200 and body == b"public-bytes"


def test_update_server_refuses_a_symlink_out_of_the_root(update_server):
    _, port, _, private, _ = update_server
    status, body = _get(f"http://127.0.0.1:{port}/v1/link.txt")
    assert status != 200
    assert b"SYNTHETIC-PRIVATE" not in body


def test_update_server_refuses_dotdot(update_server):
    _, port, _, _, _ = update_server
    status, _ = _get(f"http://127.0.0.1:{port}/v1/../releases-private/secret.txt")
    assert status in (400, 403)


def test_publish_requires_a_valid_token(update_server):
    _, port, _, _, staging = update_server
    (staging / "new.bin").write_bytes(b"new")
    url = f"http://127.0.0.1:{port}/v1/publish"
    assert _post(url, {"release_dir": str(staging)})[0] == 401
    assert _post(url, {"release_dir": str(staging)}, token="wrong")[0] == 401


def test_publish_refuses_a_release_dir_outside_the_staging_root(update_server):
    _, port, _, private, staging = update_server
    status, body = _post(f"http://127.0.0.1:{port}/v1/publish",
                         {"release_dir": str(private)}, token="AUDIT-TOKEN")
    assert status == 400
    assert b"outside" in body or b"inside" in body
    assert not (staging / "secret.txt").exists()


def test_publish_with_an_empty_token_set_is_refused_entirely(tmp_path):
    """The old code treated "no tokens configured" as "no authentication"."""
    mod = _load("cloud/update_server/app.py", "audit_update_server_open")
    root = tmp_path / "releases"
    staging = tmp_path / "staging"
    root.mkdir()
    staging.mkdir()
    (staging / "x.bin").write_bytes(b"x")
    mod.Handler.root = root
    mod.Handler.tokens = set()          # what --open leaves behind
    mod.Handler.publish_root = staging
    srv, thread = _serve(mod.Handler)
    try:
        status, _ = _post(f"http://127.0.0.1:{srv.server_port}/v1/publish",
                          {"release_dir": str(staging)})
        assert status == 403
        assert not (root / "x.bin").exists()
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


# --------------------------------------------------------------------------- #
# sync server
# --------------------------------------------------------------------------- #
@pytest.fixture()
def sync_server(tmp_path):
    mod = _load("cloud/sync_server/app.py", "audit_sync_server")
    updates = tmp_path / "updates"
    updates_private = tmp_path / "updates-private"
    updates.mkdir()
    updates_private.mkdir()
    (updates_private / "secret.txt").write_text("SYNTHETIC-PRIVATE")
    (updates / "artifact.bin").write_bytes(b"public-bytes")
    (updates / "link.txt").symlink_to(updates_private / "secret.txt")

    mod.Handler.store = mod.Store(tmp_path / "blobs")
    mod.Handler.tokens = {"AUDIT-TOKEN"}
    mod.Handler.update_dir = updates
    srv, thread = _serve(mod.Handler)
    try:
        yield srv.server_port, updates_private
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def test_sync_server_serves_update_files(sync_server):
    port, _ = sync_server
    status, body = _get(f"http://127.0.0.1:{port}/v1/artifact.bin")
    assert status == 200 and body == b"public-bytes"


def test_sync_server_refuses_a_symlink_out_of_the_update_dir(sync_server):
    port, _ = sync_server
    status, body = _get(f"http://127.0.0.1:{port}/v1/link.txt")
    assert b"SYNTHETIC-PRIVATE" not in body
    assert status in (400, 401, 403, 404)


def test_sync_server_still_requires_a_token_for_blobs(sync_server):
    port, _ = sync_server
    status, _ = _get(f"http://127.0.0.1:{port}/v1/blobs")
    assert status == 401
