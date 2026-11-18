"""The update manager: check -> verify -> stage -> install -> (optionally) hot-load.

Hot-load policy (important for a personal brain)
------------------------------------------------
Only *data-like* artifacts are applied to the running brain without a restart:

    voice_model  -> replaces the calibration model used for speaker scoring
    prompt       -> replaces the system preamble
    config       -> merged into runtime config (never overwrites keys you set)
    integration  -> replaces the integration registry

``package`` artifacts (actual code) are installed into ``updates/installed/<v>``
and ``updates/current`` is atomically repointed, but the *running* process keeps
executing the old code until you restart. Self-modifying code at runtime is a
persistence mechanism we do not want in a security product; the release notes
and ``requires_restart`` flag make the state explicit.
"""
from __future__ import annotations

import json
import shutil
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..updates.channels import Channel, ChannelStore
from ..updates.installer import InstallError, Installer
from ..updates.manifest import (
    Manifest, ManifestError, generate_signing_key, sign_manifest, verify_signature,
    version_ge,
)
from ..util.misc import ensure_dir, now_ms, sha256_hex, write_private

HOT_ARTIFACTS = ("voice_model", "prompt", "config", "integration")
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
CURRENT_FILE = "current_manifest.json"


class UpdateError(RuntimeError):
    pass


class UpdateManager:
    def __init__(self, config: Config):
        self.config = config
        self.root = ensure_dir(config.update_dir)
        self.channels = ChannelStore(self.root)
        self.installer = Installer(self.root)
        self.client_version = str(config.updates.get("client_version") or
                                  __import__("brain").__version__)

    # ------------------------------------------------------------------ #
    # channel management
    # ------------------------------------------------------------------ #
    def add_channel(self, name: str, url: str, alg: str = "ed25519", public: str = "",
                    key_id: str = "", mode: str = "tofu", auto_apply: bool = False) -> Dict[str, Any]:
        if mode not in ("strict", "tofu", "insecure"):
            raise UpdateError("mode must be strict | tofu | insecure")
        if mode == "strict" and not public:
            raise UpdateError("strict mode requires a pinned public key")
        ch = Channel(name=name, url=url, alg=alg, public=public, key_id=key_id,
                     mode=mode, auto_apply=auto_apply)
        self.channels.add(ch)
        warning = ""
        if mode == "tofu":
            warning = ("trust-on-first-use: the FIRST manifest you fetch pins the key. "
                       "Verify its fingerprint out-of-band before trusting it.")
        elif mode == "insecure":
            warning = ("INSECURE: signatures are not verified. Only for a channel you "
                       "control end-to-end (e.g. your own LAN).")
        return {"ok": True, "channel": ch.to_dict(), "warning": warning}

    def list_channels(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self.channels.channels.values()]

    # ------------------------------------------------------------------ #
    # fetch
    # ------------------------------------------------------------------ #
    def _fetch(self, url: str, timeout: float = 30.0) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme in ("http", "https"):
            req = urllib.request.Request(url, method="GET")
            req.add_header("User-Agent", f"personal-brain/{self.client_version}")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.read(MAX_ARTIFACT_BYTES + 1)
            except urllib.error.HTTPError as exc:
                raise UpdateError(f"HTTP {exc.code} fetching {url}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                raise UpdateError(f"cannot reach {url}: {exc}") from exc
        if parsed.scheme == "file" or not parsed.scheme:
            path = Path(parsed.path if parsed.scheme == "file" else url).expanduser()
            if not path.exists():
                raise UpdateError(f"no such file: {path}")
            return path.read_bytes()[: MAX_ARTIFACT_BYTES + 1]
        raise UpdateError(f"unsupported channel scheme: {parsed.scheme}")

    def _base_url(self, channel: Channel) -> str:
        return channel.url.rsplit("/", 1)[0] if "/" in channel.url.replace("://", "") else channel.url

    # ------------------------------------------------------------------ #
    def check(self, channel_name: Optional[str] = None) -> Dict[str, Any]:
        channel = (self.channels.get(channel_name) if channel_name
                   else self.channels.default())
        if channel is None:
            return {"ok": False, "error": "no update channel configured",
                    "hint": "brain update-channel-add stable https://your.host/manifest.json"}
        try:
            raw = self._fetch(channel.url)
        except UpdateError as exc:
            return {"ok": False, "channel": channel.name, "error": str(exc)}

        digest = sha256_hex(raw)
        try:
            manifest = Manifest.parse(raw)
        except (ManifestError, KeyError, ValueError, json.JSONDecodeError) as exc:
            return {"ok": False, "channel": channel.name, "error": f"bad manifest: {exc}"}

        if manifest.channel != channel.name:
            return {"ok": False, "channel": channel.name,
                    "error": f"manifest declares channel '{manifest.channel}' but was fetched "
                             f"from the '{channel.name}' channel"}

        sig = verify_signature(manifest, channel.pinned())
        if not sig["ok"]:
            return {"ok": False, "channel": channel.name, "manifest_sha256": digest,
                    "error": f"signature rejected: {sig['reason']}", "signature": sig}

        # TOFU: pin the key material on first successful check
        tofu_pinned = False
        pin = sig.get("pin") or {}
        if channel.mode == "tofu" and not channel.key_id and pin:
            channel.alg = pin.get("alg", channel.alg)
            channel.key_id = pin.get("key_id", "")
            if pin.get("alg") == "hmac-sha256":
                # the channel key is symmetric: the publisher must hand it to you
                # out of band, otherwise TOFU cannot authenticate anything.
                channel.public = channel.public or ""
                channel.extra["tofu_warning"] = (
                    "hmac-sha256 TOFU pinned only the key_id. Set the shared channel key "
                    "with --public/--key for real authenticity.")
            if pin.get("alg") == "hmac-sha256" and not (channel.public or "").strip():
                # Signing a manifest is pointless if the verification key is
                # itself unauthenticated: trusting an attacker-supplied manifest
                # hands them the key that "verifies" their next manifest, so a
                # forged manifest is accepted forever (audit B11, reproduced).
                # Pin only the id they must match, and refuse to authenticate.
                channel.extra["tofu_warning"] = (
                    "the first manifest carried its own hmac-sha256 key_id, which authenticates "
                    "nothing. Supply the shared key out of band: "
                    f"brain update-channel-add {channel.name} {channel.url} --alg hmac-sha256 "
                    "--key <base64 key> --key-id " + str(pin.get("key_id") or "") + " --mode strict")
                channel.extra["tofu_unverified"] = True
                self.channels.save()
                return {"ok": False, "channel": channel.name, "manifest_sha256": digest,
                        "error": ("this channel pins a symmetric (hmac-sha256) key, and trusting "
                                  "the manifest to supply it would authenticate nothing. Add the "
                                  "shared key out of band, then retry."),
                        "signature": sig, "next_step": channel.extra["tofu_warning"]}
            channel.extra["tofu_pinned_at"] = now_ms()
            channel.extra["tofu_manifest_sha256"] = digest
            tofu_pinned = True

        if not version_ge(self.client_version, manifest.min_client):
            return {"ok": False, "channel": channel.name,
                    "error": f"this release requires client >= {manifest.min_client}; "
                             f"you are on {self.client_version}"}

        channel.last_checked = now_ms()
        channel.last_manifest_sha = digest
        self.channels.save()
        write_private(self.root / f"manifest-{channel.name}.json", raw)

        current = self.installer.current_version()
        return {
            "ok": True, "channel": channel.name, "version": manifest.version,
            "released_at": manifest.released_at, "notes": manifest.notes,
            "artifacts": [a.to_dict() for a in manifest.artifacts],
            "installed_version": current,
            "update_available": bool(manifest.version != current),
            "newer": bool(current is None or version_ge(manifest.version, current)),
            "signature": sig, "tofu_pinned_now": tofu_pinned,
            "manifest_sha256": digest,
            "warning": ("TOFU pinned the signing key on this first check - verify key_id "
                        f"{channel.key_id} out of band" if tofu_pinned else ""),
        }

    # ------------------------------------------------------------------ #
    def download(self, channel_name: Optional[str] = None,
                 version: Optional[str] = None) -> Tuple[Manifest, Dict[str, bytes]]:
        channel = (self.channels.get(channel_name) if channel_name else self.channels.default())
        if channel is None:
            raise UpdateError("no update channel configured")
        path = self.root / f"manifest-{channel.name}.json"
        if version and (self.root / f"manifest-{channel.name}-{version}.json").exists():
            path = self.root / f"manifest-{channel.name}-{version}.json"
        if not path.exists():
            raise UpdateError("run `brain update-check` first")
        manifest = Manifest.parse(path.read_bytes())
        sig = verify_signature(manifest, channel.pinned())
        if not sig["ok"]:
            raise UpdateError(f"signature rejected: {sig['reason']}")

        base = self._base_url(channel)
        blobs: Dict[str, bytes] = {}
        for art in manifest.artifacts:
            url = art.path if art.path.startswith(("http://", "https://", "file:", "/")) \
                else f"{base}/{art.path.lstrip('./')}"
            data = self._fetch(url)
            if len(data) > MAX_ARTIFACT_BYTES:
                raise UpdateError(f"artifact {art.name} exceeds {MAX_ARTIFACT_BYTES} bytes")
            blobs[art.name] = data
        return manifest, blobs

    # ------------------------------------------------------------------ #
    def apply(self, channel_name: Optional[str] = None, force: bool = False,
              hot_load: bool = True) -> Dict[str, Any]:
        channel = (self.channels.get(channel_name) if channel_name else self.channels.default())
        if channel is None:
            return {"ok": False, "error": "no update channel configured"}
        if channel.auto_apply is False and not force:
            pass  # apply() is always an explicit user action; auto_apply governs daemons
        try:
            manifest, blobs = self.download(channel_name)
        except (UpdateError, ManifestError) as exc:
            return {"ok": False, "error": str(exc)}

        current = self.installer.current_version()
        if current == manifest.version and not force:
            return {"ok": True, "already_installed": True, "version": current}

        # Was this manifest authenticated against a key we pinned? ``insecure``
        # mode never is; a ``tofu`` channel is only authenticated once a key has
        # been pinned. The installer uses this to decide whether it may *import*
        # (i.e. execute) the package before the pointer swap.
        authenticated = self._authenticated(channel, manifest)
        try:
            report = self.installer.install(manifest, blobs, import_test=authenticated)
        except InstallError as exc:
            return {"ok": False, "error": str(exc), "rolled_back": True,
                    "current": self.installer.current_version()}

        applied: List[str] = []
        if hot_load:
            for art in manifest.artifacts:
                if art.kind not in HOT_ARTIFACTS:
                    continue
                try:
                    self._hot_apply(art.kind, art.name, blobs[art.name])
                    applied.append(f"{art.kind}:{art.name}")
                except Exception as exc:
                    applied.append(f"{art.kind}:{art.name} FAILED ({type(exc).__name__}: {exc})")

        channel.last_version = manifest.version
        self.channels.save()
        write_private(self.root / CURRENT_FILE,
                      json.dumps({"version": manifest.version, "channel": channel.name,
                                  "applied_at": now_ms(), "artifacts": [a.to_dict()
                                                                        for a in manifest.artifacts],
                                  "hot_applied": applied}, indent=2).encode())
        requires_restart = any(a.kind == "package" for a in manifest.artifacts)
        self.installer.prune(keep=3)
        return {"ok": True, "version": manifest.version, "installed_to": report.installed_to,
                "staged": report.staged, "hot_applied": applied, "notes": report.notes,
                "requires_restart": requires_restart,
                "rollback": "brain update-rollback" if requires_restart else ""}

    @staticmethod
    def _authenticated(channel: Channel, manifest: Manifest) -> bool:
        """True only when the manifest carried a signature we can verify.

        Deliberately strict about the two "trust me" modes:

        * ``insecure`` verifies nothing, so nothing it ships may be imported;
        * ``tofu`` before a key is pinned has verified nothing either - the
          first fetch is trusted by definition. ``tofu`` *after* a pin does
          verify, and is treated as authenticated.
        """
        if str(channel.mode).lower() == "insecure":
            return False
        if not (manifest.signature or {}).get("value"):
            return False
        return bool(channel.key_id)

    def _hot_apply(self, kind: str, name: str, data: bytes) -> None:
        if kind == "voice_model":
            payload = json.loads(data)
            if payload.get("backend") != "mfcc-cal-v1" or "mean" not in payload:
                raise UpdateError("voice model payload does not look like a calibration model")
            dest = self.config.home / "voice_model.override.json"
            write_private(dest, data)
            self.config.updates["voice_model_override"] = str(dest)
            self.config.save()
        elif kind == "prompt":
            write_private(self.config.home / "system_prompt.override.txt", data)
            self.config.updates["prompt_override"] = str(self.config.home / "system_prompt.override.txt")
            self.config.save()
        elif kind == "config":
            incoming = json.loads(data)
            merged = 0
            for section, values in incoming.items():
                if section.startswith("_") or not isinstance(values, dict):
                    continue
                store = {"sync": self.config.sync, "updates": self.config.updates,
                         "agent": self.config.agent, "identity": self.config.identity}.get(section)
                if store is None:
                    continue
                for k, v in values.items():
                    if k not in store:        # never clobber a user-set value
                        store[k] = v
                        merged += 1
            self.config.save()
            if not merged:
                raise UpdateError("config artifact changed nothing (all keys already set)")
        elif kind == "integration":
            payload = json.loads(data)
            dest = self.config.home / "integrations.json"
            write_private(dest, json.dumps(payload, indent=2).encode())
        else:
            raise UpdateError(f"artifact kind '{kind}' cannot be hot-applied")

    # ------------------------------------------------------------------ #
    def rollback(self) -> Dict[str, Any]:
        result = self.installer.rollback()
        if result.get("ok"):
            write_private(self.root / CURRENT_FILE,
                          json.dumps({"version": result["version"], "rolled_back_at": now_ms()},
                                     indent=2).encode())
        return result

    def status(self) -> Dict[str, Any]:
        cur = self.root / CURRENT_FILE
        info: Dict[str, Any] = {}
        if cur.exists():
            try:
                info = json.loads(cur.read_bytes())
            except Exception:
                info = {}
        return {"installed_version": self.installer.current_version(),
                "channels": self.list_channels(),
                "last_applied": info,
                "client_version": self.client_version,
                "hot_loadable": list(HOT_ARTIFACTS),
                "history": self.installer.history.exists()}
