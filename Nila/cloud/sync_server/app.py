#!/usr/bin/env python3
"""Reference E2EE blob-store server (dependency-free stdlib).

This is the "cloud back-end" the ZIP talks to. It is deliberately dumb: it
stores opaque JSON blobs keyed by an opaque id, maintains a per-key version
counter for optimistic concurrency, and knows nothing about your data.

What the server can see:  blob sizes, key names (HMAC-derived, unlinkable to
                          your record ids), access times, your bearer token.
What it cannot see:       plaintext, record ids, relationships between records,
                          which devices belong to you, or your master key.

Run::

    export BRAIN_SYNC_TOKEN=$(python -c "import secrets;print(secrets.token_urlsafe(32))")
    python cloud/sync_server/app.py --port 8790 --data /var/lib/brain-sync

Client::

    brain config set sync.backend http
    brain config set sync.url http://127.0.0.1:8790
    brain config set sync.token_env BRAIN_SYNC_TOKEN
    brain sync

Endpoints::

    GET    /health
    GET    /v1/blobs?prefix=      -> {"keys": [...]}
    GET    /v1/blobs/<key>        -> blob JSON (404 if absent)
    PUT    /v1/blobs/<key>        -> {"ok":true,"version":N}
           If-Match: N            require current version == N (412 otherwise)
           If-Match: 0            require the key to NOT exist (412 otherwise)
           If-Match: *            unconditional
    DELETE /v1/blobs/<key>
    GET    /v1/manifest.json      (optional: serve an update channel too)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from brain.util.safe_paths import UnsafePath, open_within  # noqa: E402

MAX_BODY = 16 * 1024 * 1024
KEY_RE = re.compile(r"^[A-Za-z0-9_\-.%=]{1,220}$")
LOCK = threading.RLock()


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / f"{quote(key, safe='')}.blob.json"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        try:
            return json.loads(p.read_bytes())
        except Exception:
            return None

    def put(self, key: str, value: dict, expect=None) -> tuple[bool, int, str]:
        """Returns (ok, version, error)."""
        with LOCK:
            p = self._path(key)
            current = self.get(key)
            cur_v = int((current or {}).get("version", 0))
            if expect is not None and expect != "*" and cur_v != int(expect):
                return False, cur_v, f"version is {cur_v}, If-Match said {expect}"
            value = dict(value)
            value["version"] = cur_v + 1
            value["server_ts"] = int(time.time() * 1000)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(json.dumps(value).encode())
            os.replace(tmp, p)
            return True, value["version"], ""

    def delete(self, key: str) -> bool:
        p = self._path(key)
        if p.exists():
            p.unlink()
            return True
        return False

    def keys(self, prefix: str = "") -> list:
        out = []
        for p in sorted(self.root.glob("*.blob.json")):
            key = unquote(p.name[: -len(".blob.json")])
            if key.startswith(prefix):
                out.append(key)
        return out


class Handler(BaseHTTPRequestHandler):
    server_version = "brain-sync/1.0"
    protocol_version = "HTTP/1.1"
    store: Store = None
    tokens: set = set()
    update_dir: Path = None

    def log_message(self, fmt, *args) -> None:
        pass

    # -- helpers -------------------------------------------------------- #
    def _send(self, status: int, payload, raw: bytes | None = None) -> None:
        data = raw if raw is not None else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _authed(self) -> bool:
        if not self.tokens:
            return True                      # open mode (dev only)
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return header[7:].strip() in self.tokens

    def _key(self) -> str | None:
        path = urlparse(self.path).path
        if not path.startswith("/v1/blobs/"):
            return None
        key = unquote(path[len("/v1/blobs/"):])
        if not key or not KEY_RE.match(key):
            return None
        return key

    # -- verbs ---------------------------------------------------------- #
    def do_GET(self) -> None:     # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, {"ok": True, "service": "brain-sync",
                                    "keys": len(self.store.keys())})
        if parsed.path in ("/v1/manifest.json", "/manifest.json") and self.update_dir:
            mf = self.update_dir / "manifest.json"
            if mf.exists():
                return self._send(200, None, raw=mf.read_bytes())
            return self._send(404, {"error": "no manifest published"})
        if self.update_dir and parsed.path.startswith("/v1/"):
            # Boundary-safe containment: a string prefix let /srv/updates-private
            # masquerade as /srv/updates, and resolve() follows a planted symlink
            # before the comparison. open_within refuses both.
            try:
                candidate = open_within(self.update_dir, unquote(parsed.path[len("/v1/"):]),
                                        what="update file")
                return self._send(200, None, raw=candidate.read_bytes())
            except UnsafePath:
                pass            # fall through to the authenticated blob routes
        if not self._authed():
            return self._send(401, {"error": "invalid bearer token"})
        if parsed.path == "/v1/blobs":
            prefix = (parse_qs(parsed.query).get("prefix") or [""])[0]
            return self._send(200, {"keys": self.store.keys(prefix)})
        key = self._key()
        if key is None:
            return self._send(400, {"error": "bad key"})
        blob = self.store.get(key)
        if blob is None:
            return self._send(404, {"error": "not found"})
        return self._send(200, blob)

    def do_PUT(self) -> None:     # noqa: N802
        if not self._authed():
            return self._send(401, {"error": "invalid bearer token"})
        key = self._key()
        if key is None:
            return self._send(400, {"error": "bad key"})
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._send(413, {"error": "blob too large"})
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "body must be JSON"})
        if not isinstance(value, dict):
            return self._send(400, {"error": "body must be a JSON object"})
        expect = self.headers.get("If-Match")
        ok, version, err = self.store.put(key, value, expect=expect)
        if not ok:
            return self._send(412, {"error": err, "version": version})
        log(f"PUT {key[:16]}… -> v{version} ({length} B)")
        return self._send(200, {"ok": True, "version": version})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authed():
            return self._send(401, {"error": "invalid bearer token"})
        key = self._key()
        if key is None:
            return self._send(400, {"error": "bad key"})
        removed = self.store.delete(key)
        log(f"DELETE {key[:16]}… -> {removed}")
        return self._send(200 if removed else 404, {"ok": removed})

    do_HEAD = do_GET


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--data", default="./brain-sync-data")
    ap.add_argument("--token-env", default="BRAIN_SYNC_TOKEN",
                    help="env var holding the bearer token(s); comma-separated for several")
    ap.add_argument("--open", action="store_true", help="no auth (local development only)")
    ap.add_argument("--updates", default="", help="also serve this directory as an update channel")
    args = ap.parse_args()

    tokens = set()
    if not args.open:
        raw = os.environ.get(args.token_env, "")
        tokens = {t.strip() for t in raw.split(",") if t.strip()}
        if not tokens:
            print(f"refusing to start: ${args.token_env} is empty. Either set it or pass --open.",
                  file=sys.stderr)
            return 1

    Handler.store = Store(Path(args.data).expanduser())
    Handler.tokens = tokens
    Handler.update_dir = Path(args.updates).expanduser() if args.updates else None

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    log(f"brain-sync listening on http://{args.host}:{args.port} "
        f"data={args.data} tokens={len(tokens)} updates={args.updates or '-'}")
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log("WARNING: bound to a non-loopback interface. Put TLS in front of this.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("stopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
