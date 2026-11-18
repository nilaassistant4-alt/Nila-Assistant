#!/usr/bin/env python3
"""Build + sign an update release, and serve it as a local channel.

This is the *publisher* side of "the ZIP connects to a cloud back-end for
continuous updates". It shows the whole contract in one runnable script:

    python scripts/make_release.py --version 0.2.0 --out /tmp/release

creates::

    /tmp/release/manifest.json                signed manifest
    /tmp/release/agent-0.2.0.zip              package artifact
    /tmp/release/voice_model.json             hot-loadable artifact
    /tmp/release/prompts/system.txt           hot-loadable artifact
    /tmp/release/keys.json                    signing key (KEEP SECRET - 0600)

Then on the client::

    brain update-channel-add stable file:///tmp/release/manifest.json \
        --alg ed25519 --public <b64 from keys.json> --key-id <id> --mode strict
    brain update-check
    brain update-apply
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from brain.updates.manifest import (  # noqa: E402
    Artifact, Manifest, generate_signing_key, sign_manifest,
)
from brain.util.misc import ensure_dir, now_ms, write_private  # noqa: E402


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_package_zip(out: Path, version: str) -> Path:
    zip_path = out / f"agent-{version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted((ROOT / "brain").rglob("*.py")):
            zf.write(p, p.relative_to(ROOT))
        for extra in ("brain/identity/voice_model.json",):
            fp = ROOT / extra
            if fp.exists():
                zf.write(fp, extra)
        zf.writestr("VERSION", version + "\n")
    return zip_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--channel", default="stable")
    ap.add_argument("--out", required=True)
    ap.add_argument("--min-client", default="0.1.0")
    ap.add_argument("--notes", default="")
    ap.add_argument("--key", default="", help="reuse an existing keys.json")
    ap.add_argument("--alg", default="", help="force ed25519 or hmac-sha256")
    args = ap.parse_args()

    out = ensure_dir(Path(args.out).expanduser())
    ensure_dir(out / "prompts")

    keys_path = Path(args.key).expanduser() if args.key else out / "keys.json"
    if keys_path.exists():
        keys = json.loads(keys_path.read_bytes())
    else:
        keys = generate_signing_key()
        if args.alg and keys["alg"] != args.alg:
            print(f"note: generated {keys['alg']} (requested {args.alg} unavailable)")
        write_private(keys_path, json.dumps(keys, indent=2).encode())

    pkg = build_package_zip(out, args.version)

    voice_src = ROOT / "brain/identity/voice_model.json"
    artifacts = [Artifact(name="agent", kind="package", path=pkg.name, sha256=sha(pkg),
                          bytes=pkg.stat().st_size, extract_to="agent",
                          meta={"import_module": "brain", "version": args.version})]
    if voice_src.exists():
        dst = out / "voice_model.json"
        dst.write_bytes(voice_src.read_bytes())
        artifacts.append(Artifact(name="voice-model", kind="voice_model", path=dst.name,
                                  sha256=sha(dst), bytes=dst.stat().st_size,
                                  meta={"backend": "mfcc-cal-v1"}))
    prompt = out / "prompts" / "system.txt"
    prompt.write_text("You are the user's private, local-first personal brain.\n"
                      "Be concise, use tools for anything about the user's life, and never\n"
                      "invent facts. This prompt was delivered by the signed update channel.\n")
    artifacts.append(Artifact(name="system-prompt", kind="prompt", path=f"prompts/{prompt.name}",
                              sha256=sha(prompt), bytes=prompt.stat().st_size))
    cfg = out / "release-config.json"
    cfg.write_text(json.dumps({"agent": {"context_records": 6, "max_tool_rounds": 5}}, indent=2))
    artifacts.append(Artifact(name="release-config", kind="config", path=cfg.name,
                              sha256=sha(cfg), bytes=cfg.stat().st_size))

    manifest = Manifest(channel=args.channel, version=args.version, released_at=now_ms(),
                        artifacts=artifacts, min_client=args.min_client,
                        notes=args.notes or f"release {args.version}",
                        rollout={"percent": 100})
    sign_manifest(manifest, keys["private"], keys["alg"], keys["key_id"])
    write_private(out / "manifest.json", manifest.to_json().encode())

    print(f"release {args.version} written to {out}")
    print(f"  manifest   : {out/'manifest.json'}  sha256={sha(out/'manifest.json')[:16]}…")
    for a in artifacts:
        print(f"  artifact   : {a.name:14s} {a.kind:12s} {a.bytes:>9d} B  sha256={a.sha256[:16]}…")
    print(f"  signing    : alg={keys['alg']} key_id={keys['key_id']}")
    print(f"  keys file  : {keys_path}  (chmod 600 - never ship this)")
    print("\nclient-side:")
    print(f"  brain update-channel-add {args.channel} file://{out}/manifest.json \\")
    print(f"      --alg {keys['alg']} --public {keys['public'][:24]}… --key-id {keys['key_id']} --mode strict")
    print("  brain update-check && brain update-apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
