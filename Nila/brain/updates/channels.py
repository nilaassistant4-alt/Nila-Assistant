"""Pinned update channels.

A channel is the *only* source of updates the agent will consider. It lives in
``$BRAIN_HOME/updates/channels.json`` and pins:

* ``url``          where the manifest is fetched from
* ``alg``/``key_id``/``public``  the signing key we trust
* ``mode``         ``strict`` (signature required), ``tofu`` (pin on first use,
                   then strict), ``insecure`` (hash-only; loudly warned about)
* ``auto_apply``   never default true - the MVP requires an explicit apply

``tofu`` exists because a distributed ZIP has no baked-in vendor key: the first
manifest you fetch pins the key, and every later one must match. That is weaker
than a pre-pinned key (the first fetch is trusted) so the CLI prints a warning
and asks for confirmation.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.misc import ensure_dir, now_ms, write_private


@dataclass
class Channel:
    name: str
    url: str
    alg: str = "ed25519"
    key_id: str = ""
    public: str = ""
    mode: str = "tofu"          # strict | tofu | insecure
    auto_apply: bool = False
    last_version: str = ""
    last_checked: int = 0
    last_manifest_sha: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def pinned(self) -> Dict[str, Any]:
        return {"alg": self.alg, "key_id": self.key_id, "public": self.public,
                "key": self.public, "mode": self.mode}

    def to_dict(self) -> Dict[str, Any]:
        out = {"name": self.name, "url": self.url, "alg": self.alg, "key_id": self.key_id,
               "public": self.public, "mode": self.mode, "auto_apply": self.auto_apply,
               "last_version": self.last_version, "last_checked": self.last_checked,
               "last_manifest_sha": self.last_manifest_sha}
        out.update(self.extra)
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Channel":
        known = {"name", "url", "alg", "key_id", "public", "mode", "auto_apply",
                 "last_version", "last_checked", "last_manifest_sha"}
        return cls(name=str(raw["name"]), url=str(raw["url"]), alg=str(raw.get("alg", "ed25519")),
                   key_id=str(raw.get("key_id", "")), public=str(raw.get("public", "")),
                   mode=str(raw.get("mode", "tofu")), auto_apply=bool(raw.get("auto_apply", False)),
                   last_version=str(raw.get("last_version", "")),
                   last_checked=int(raw.get("last_checked", 0)),
                   last_manifest_sha=str(raw.get("last_manifest_sha", "")),
                   extra={k: v for k, v in raw.items() if k not in known})


class ChannelStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        ensure_dir(self.root)
        self.path = self.root / "channels.json"
        self.channels: Dict[str, Channel] = {}
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_bytes())
                for raw in data.get("channels", []):
                    ch = Channel.from_dict(raw)
                    self.channels[ch.name] = ch
            except Exception:
                self.channels = {}

    def save(self) -> None:
        payload = {"version": 1, "updated_at": now_ms(),
                   "channels": [c.to_dict() for c in self.channels.values()]}
        write_private(self.path, json.dumps(payload, indent=2).encode())

    def add(self, channel: Channel) -> None:
        self.channels[channel.name] = channel
        self.save()

    def get(self, name: str) -> Optional[Channel]:
        return self.channels.get(name)

    def remove(self, name: str) -> bool:
        if name in self.channels:
            self.channels.pop(name)
            self.save()
            return True
        return False

    def names(self) -> List[str]:
        return sorted(self.channels)

    def default(self) -> Optional[Channel]:
        for pref in ("stable", "default", "main"):
            if pref in self.channels:
                return self.channels[pref]
        first = sorted(self.channels.values(), key=lambda c: c.name)
        return first[0] if first else None
