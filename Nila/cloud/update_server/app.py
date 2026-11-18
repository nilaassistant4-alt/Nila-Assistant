#!/usr/bin/env python3
"""Reference update channel server.

Serves ``manifest.json`` and the artifacts next to it, and can *publish* a
signed release built by ``scripts/make_release.py``.

Two modes
---------
``--serve DIR``      serve an already-built release directory (default)
``--watch SRC``      rebuild + sign on demand when ``POST /v1/publish`` is called
                     (needs the signing key in ``$BRAIN_UPDATE_KEY``)

Because the client verifies the manifest signature against a pinned key, this
server can be hosted on an untrusted CDN: compromising it lets an attacker
*deny* updates, not forge them.

    python cloud/update_server/app.py --serve /var/www/brain-releases --port 8791
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent.parent))
from brain.util.safe_paths import UnsafePath, is_within, open_within  # noqa: E402

SAFE = re.compile(r"^[A-Za-z0-9._\-/]+$")


class Handler(BaseHTTPRequestHandler):
    server_version = "brain-updates/1.0"
    protocol_version = "HTTP/1.1"
    root: Path = None
    tokens: set = set()
    publish_root: Path = None

    def log_message(self, fmt, *args) -> None:
        pass

    def _send(self, status: int, payload=None, raw: bytes | None = None,
              ctype: str = "application/json") -> None:
        data = raw if raw is not None else json.dumps(payload or {}).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache" if ctype == "application/json" else "public, max-age=300")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:   # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in ("/health", "/v1/health"):
            return self._send(200, {"ok": True, "service": "brain-updates",
                                    "root": str(self.root)})
        rel = parsed.path.lstrip("/")
        if rel.startswith("v1/"):
            rel = rel[3:]
        if not rel or not SAFE.match(rel) or ".." in rel:
            return self._send(400, {"error": "bad path"})
        # Boundary-safe containment on resolved paths: `str.startswith` would
        # serve /srv/releases-private/x from a root of /srv/releases.
        try:
            candidate = open_within(self.root, unquote(rel), what="requested file")
        except UnsafePath as exc:
            return self._send(403, {"error": f"refused: {exc}"})
        if not candidate.is_file():
            return self._send(404, {"error": "not found"})
        if candidate.name in ("manifest.json",) or candidate.suffix == ".json":
            return self._send(200, None, raw=candidate.read_bytes())
        return self._send(200, None, raw=candidate.read_bytes(),
                          ctype="application/octet-stream")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in ("/v1/publish", "/publish"):
            return self._send(404, {"error": "not found"})
        # AUTHENTICATE FIRST, ALWAYS.
        #
        # This used to be `if self.tokens and ...`, so an unset token variable
        # meant publishing was open to anyone who could reach the port. The
        # server now refuses to start without a token unless --open was passed,
        # and --open is enforced here: with no tokens configured the request is
        # refused rather than waved through.
        if not self.tokens:
            return self._send(403, {"error": "publishing is disabled: the server was "
                                             "started with --open"})
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not any(hmac.compare_digest(token, t) for t in self.tokens):
            return self._send(401, {"error": "invalid token"})
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "body must be JSON"})
        src = Path(str(body.get("release_dir", ""))).expanduser()
        # The path in the request is caller-supplied. It must resolve to a
        # directory inside the configured release-staging root, so even a token
        # holder cannot make the server copy /etc into the public web root.
        if not self.publish_root:
            return self._send(403, {"error": "publishing from a directory is disabled; "
                                             "start the server with --publish-from DIR"})
        if not is_within(src, self.publish_root):
            return self._send(400, {"error": f"release_dir must be inside "
                                             f"{self.publish_root}: {src}"})
        if not src.is_dir():
            return self._send(400, {"error": f"release_dir not found: {src}"})
        copied = []
        for item in src.rglob("*"):
            if item.is_file() and item.name != "keys.json":
                dest = self.root / item.relative_to(src)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(item.read_bytes())
                copied.append(str(item.relative_to(src)))
        return self._send(200, {"ok": True, "published": copied,
                                "manifest": f"{self.root}/manifest.json"})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serve", required=True, help="directory holding manifest.json + artifacts")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--token-env", default="BRAIN_UPDATE_TOKEN",
                    help="env var with the publish token (GET is public)")
    ap.add_argument("--open", action="store_true",
                    help="allow unauthenticated publishing (development only; "
                         "POST /v1/publish is refused while this is set)")
    ap.add_argument("--publish-from", default="",
                    help="directory a publish request may copy FROM (required for "
                         "--publish; the request's release_dir must be inside it)")
    args = ap.parse_args()

    root = Path(args.serve).expanduser().resolve()
    if not (root / "manifest.json").exists():
        print(f"warning: {root}/manifest.json does not exist yet "
              f"(run scripts/make_release.py --out {root})", file=sys.stderr)

    tokens = {t for t in os.environ.get(args.token_env, "").split(",") if t.strip()}
    if not tokens and not args.open:
        print(f"refusing to start: ${args.token_env} is empty. Set it, or pass --open "
              f"to serve read-only (publishing stays disabled).", file=sys.stderr)
        return 1
    if args.open and tokens:
        print("note: --open was passed, so POST /v1/publish is refused even though a "
              "token is configured.", file=sys.stderr)

    Handler.root = root
    Handler.tokens = set() if args.open else tokens
    Handler.publish_root = Path(args.publish_from).expanduser().resolve() if args.publish_from else None
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: bound to a non-loopback interface. Put TLS in front of this.",
              file=sys.stderr)

    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"brain-updates listening on http://{args.host}:{args.port} root={root}", flush=True)
    print(f"  client: brain update-channel-add stable "
          f"http://{args.host}:{args.port}/v1/manifest.json --mode strict --public <b64>")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
