"""Connectors: the AI services and outside integrations Nila may reach, and what
each state actually means.

This module is the *one* place that answers "is this connector on, does it have a
key, and may it be used right now?".  The dashboard, ``brain connectors`` and the
router all read the same answer, so a screen can never say "ready" while the
router refuses, or the reverse.

Three rules are structural, not cosmetic:

**A secret is never readable.** ``ConnectorVault`` stores connector secrets
sealed under a key derived from the master key, inside the encrypted part of
``identity.json``. Listing connectors reports ``has_key`` (a bool), ``key_hint``
(the last four characters) and ``fingerprint`` (a hash prefix) - never the
secret, never its length. The vault is also *locked* to the session tier, so a
degraded session cannot release a key at all.

**A key must not travel through a browser form.** ``set_key`` is reachable from
the CLI (hidden prompt, no echo, no argument, no environment variable) and from
the dashboard only with a higher authorisation level plus a one-time, single-use
re-authentication. Passing a secret as a process argument would expose it in the
process list, so that route is refused outright.

**Turning something ON costs a fresh login.** A standing network connection is
never created by clicking; each ON or OFF calls
``IdentityCeremony.authorise_action``, which re-runs the whole ceremony, burns
the approval, audits both the request and the consumption, and fails closed.

Nothing here knows about payment, banking or card connectors. Those are not
implemented on purpose: the owner's brief named ChatGPT / Claude / Gemini, which
are AI services, and the connector list in ``CATALOGUE`` is the complete set of
services this build understands.
"""
from __future__ import annotations

import binascii
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from ..config import Config
from ..crypto.envelope import CryptoError, open_sealed, seal
from ..util.misc import b64d, b64e, now_ms, sha256_hex

# --------------------------------------------------------------------------- #
# the catalogue: every connector this build knows, and what it is
# --------------------------------------------------------------------------- #
AI = "ai"
INTEGRATION = "integration"

#: ``gated``        - needs network egress, so it needs the net.host capability
#: ``local_only``   - never leaves this PC (loopback transport)
CATALOGUE: Tuple[Dict[str, Any], ...] = (
    {"id": "local", "kind": AI, "label": "Built-in engine",
     "detail": "Always available, never uses the network. Basic answers.",
     "lane": "offline", "egress": "local_only", "needs_key": False,
     "hosts": [], "optional": False, "default_on": True},
    {"id": "ollama", "kind": AI, "label": "Ollama (this PC)",
     "detail": "A model running on this computer. Free, private, works offline.",
     "lane": "local", "egress": "local_only", "needs_key": False,
     "hosts": ["127.0.0.1"], "optional": False, "default_on": True},
    {"id": "gemini", "kind": AI, "label": "Google Gemini",
     "detail": "Cloud AI. Only for messages without private data, within the daily budget.",
     "lane": "online", "egress": "gated", "needs_key": True,
     "hosts": ["generativelanguage.googleapis.com"], "optional": True, "default_on": False},
    {"id": "openai", "kind": AI, "label": "OpenAI-compatible",
     "detail": "Any /chat/completions endpoint: OpenAI, OpenRouter, Groq, LM Studio, vLLM.",
     "lane": "online", "egress": "gated", "needs_key": True,
     "hosts": [], "optional": True, "default_on": False},
    {"id": "anthropic", "kind": AI, "label": "Anthropic Claude",
     "detail": "Cloud AI (paid). Optional; off unless you turn it on and add a key.",
     "lane": "online", "egress": "gated", "needs_key": True,
     "hosts": ["api.anthropic.com"], "optional": True, "default_on": False},
)

CATALOGUE_BY_ID = {c["id"]: c for c in CATALOGUE}

#: A plausible key: anything longer than a password, no whitespace inside.
MIN_KEY_CHARS = 12
MAX_KEY_CHARS = 4096

MAX_TEST_PROMPT_CHARS = 200


class ConnectorError(RuntimeError):
    """A refusal the owner can act on. Callers turn this into a message."""


class StepUpRequired(ConnectorError):
    """The action is legal but needs a fresh re-authentication first."""

    def __init__(self, message: str, actions: List[str], action: str = "",
                 needs: str = "approval"):
        super().__init__(message)
        self.actions = list(actions)
        self.action = action
        self.needs = needs          # "approval" | "authenticator" | "unlock"


class AuthLevel:
    """How strongly the caller is authenticated right now.

    ``NONE``     - nothing; no connector secret is ever readable.
    ``SESSION``  - an ordinary unlocked session (CLI after the ceremony, or the
                   bearer-token dashboard).
    ``TOTP``     - a session whose SECOND factor was a knowledge key (TOTP or a
                   recovery code). This is the bar for *editing* a key, because
                   the secret is re-enterable from the authenticator, while a
                   voiceprint can be replayed less usefully and is not a
                   secret the owner can re-issue.
    """
    NONE = "none"
    SESSION = "session"
    TOTP = "totp"

    ORDER = {NONE: 0, SESSION: 1, TOTP: 2}

    @classmethod
    def of(cls, session: Any, vault: Any = None) -> str:
        if session is None:
            return cls.NONE
        if getattr(session, "degraded", True):
            # A degraded session skipped the stronger factor: it may look at
            # metadata, never at a secret.
            return cls.SESSION
        mode = str(getattr(session, "mode", "") or "").lower()
        has_totp = False
        try:
            has_totp = bool(vault is not None and vault.has_totp())
        except Exception:
            has_totp = False
        if "totp" in mode or "recovery" in mode or (has_totp and "voice" not in mode):
            return cls.TOTP
        return cls.SESSION

    @classmethod
    def at_least(cls, have: str, need: str) -> bool:
        return cls.ORDER.get(have, 0) >= cls.ORDER.get(need, 0)


# --------------------------------------------------------------------------- #
# sealed secret storage
# --------------------------------------------------------------------------- #
class ConnectorVault:
    """Connector secrets, sealed under a key derived from the master key.

    Stored as ``{"v": 1, "secrets": {"gemini": "<base64 blob>"}}`` *inside* the
    encrypted identity blob, and each secret is additionally sealed with the
    ``connector-secrets`` domain key, so it is readable only by an unlocked
    session whose tier allowed the identity blob to be opened at all.

    Why not the memory table? A credential there is a ``credential_ref``
    (restricted class) and would become recallable text. A connector key must be
    usable by a program and never readable by the model, so it lives here and
    there is no API that returns it.
    """

    DOMAIN = "pbx/connector-secrets/v1"
    KEY = "connector_secrets"

    def __init__(self, vault: Any):
        self.vault = vault

    # -- internals ------------------------------------------------------- #
    def _root(self) -> bytes:
        try:
            return self.vault.subkey("connector-secrets", 32)
        except Exception as exc:      # locked, or a provisional session
            raise ConnectorError("the vault must be unlocked with a full session "
                                 "before a connector key can be handled") from exc

    def _load(self) -> Dict[str, str]:
        raw = self.vault.meta_get(self.KEY, None)
        if not raw:
            return {}
        if not isinstance(raw, dict) or int(raw.get("v", 0)) != 1:
            raise ConnectorError("the connector secret store is unreadable; refusing to continue")
        secrets = raw.get("secrets")
        if not isinstance(secrets, dict):
            raise ConnectorError("the connector secret store is corrupt; refusing to continue")
        return {str(k): str(v) for k, v in secrets.items()}

    def _save(self, secrets: Dict[str, str]) -> None:
        self.vault.meta_set(self.KEY, {"v": 1, "secrets": dict(secrets), "updated": now_ms()})

    # -- public ---------------------------------------------------------- #
    def names(self) -> List[str]:
        return sorted(self._load())

    def has(self, cid: str) -> bool:
        return cid in self._load()

    def get(self, cid: str) -> str:
        """The plaintext secret. Only credential *use* calls this, never a route."""
        sealed = self._load().get(cid)
        if sealed is None:
            raise ConnectorError(f"no key is stored for {cid}")
        try:
            blob = b64d(sealed)
            plain = open_sealed(blob, self._root(), self.DOMAIN, cid)
            return plain.decode("utf-8")
        except (CryptoError, ValueError, binascii.Error) as exc:
            raise ConnectorError(f"the stored key for {cid} cannot be opened in this "
                                 f"session; re-enter it") from exc

    def set(self, cid: str, secret: str) -> Dict[str, Any]:
        secret = (secret or "").strip()
        if not secret:
            raise ConnectorError("the key is empty")
        if len(secret) < MIN_KEY_CHARS:
            raise ConnectorError(f"that looks too short to be an API key "
                                 f"(at least {MIN_KEY_CHARS} characters)")
        if len(secret) > MAX_KEY_CHARS:
            raise ConnectorError("that is longer than any API key; refusing")
        if any(ch.isspace() for ch in secret):
            raise ConnectorError("a key cannot contain spaces or line breaks")
        if "PRIVATE KEY" in secret:
            raise ConnectorError("that is a private key file, not an API key")
        blob = seal(secret.encode("utf-8"), self._root(), self.DOMAIN, cid)
        secrets = self._load()
        secrets[cid] = b64e(blob)
        self._save(secrets)
        return {"id": cid, "has_key": True, "key_hint": hint(secret),
                "fingerprint": fingerprint(secret)}

    def remove(self, cid: str) -> bool:
        secrets = self._load()
        if cid not in secrets:
            return False
        secrets.pop(cid)
        self._save(secrets)
        return True


def hint(secret: str) -> str:
    """The only part of a key that may ever be displayed."""
    s = (secret or "").strip()
    if not s:
        return ""
    return s[-4:] if len(s) >= 8 else "···"


def fingerprint(secret: str) -> str:
    """A stable, non-reversible tag so the owner can tell two keys apart."""
    return sha256_hex((secret or "").encode("utf-8"))[:10]


# --------------------------------------------------------------------------- #
# settings + usage
# --------------------------------------------------------------------------- #
#: The public endpoint each cloud provider uses when nothing else is configured.
#: `set_on` may clear one of these; it never clears a URL it did not write.
DEFAULT_ONLINE_URLS = {"gemini": "https://generativelanguage.googleapis.com/v1beta",
                       "openai": "https://api.openai.com/v1",
                       "anthropic": "https://api.anthropic.com/v1"}


def _is_default_online_url(url: str) -> bool:
    host = url.split("//", 1)[-1].split("/", 1)[0].split(":", 1)[0].lower()
    return host in {u.split("//", 1)[-1].split("/", 1)[0] for u in DEFAULT_ONLINE_URLS.values()}


class ConnectorSettings:
    """Which connectors are ON, kept in the *encrypted* half of the identity blob.

    It used to be tempting to keep this in ``config.json`` beside the provider
    name. That file is plaintext, and "the owner has a Gemini key" is itself
    useful to an attacker reading the disk, so both the switch and the key are
    sealed. A locked vault therefore reports nothing at all, which is the
    honest answer.
    """

    KEY = "connector_settings"

    #: set by the registry that owns this object, so the router can ask "which
    #: key applies to this lane?" without importing the registry itself.
    registry: Any = None

    def __init__(self, vault: Any):
        self.vault = vault

    def _load(self) -> Dict[str, Any]:
        raw = self.vault.meta_get(self.KEY, None)
        if not isinstance(raw, dict):
            return {}
        out = raw.get("on")
        return {str(k): bool(v) for k, v in out.items()} if isinstance(out, dict) else {}

    def explicit(self, cid: str) -> Optional[bool]:
        """The owner's decision, or None when they have never touched this row."""
        stored = self._load()
        return bool(stored[cid]) if cid in stored else None

    #: Mark of a connector the owner switched ON at least once. `_load()` keeps
    #: only the LATEST state, so ON -> OFF -> ON left nothing behind and the
    #: router could not tell an owner's ON from "never touched" - the acceptance
    #: run caught the third turn staying offline after switching back on.
    ON_KEY = "connector_explicit_on"

    def explicitly_on(self, cid: str) -> bool:
        try:
            raw = self.vault.meta_get(self.ON_KEY, {}) or {}
            return isinstance(raw, dict) and cid in raw
        except Exception:
            return False

    def mark_explicit_on(self, cid: str, on: bool) -> None:
        try:
            raw = dict(self.vault.meta_get(self.ON_KEY, {}) or {})
        except Exception:
            raw = {}
        if on:
            raw[str(cid)] = int(time.time() * 1000)
        else:
            raw.pop(str(cid), None)
        self.vault.meta_set(self.ON_KEY, raw)

    def is_on(self, cid: str) -> bool:
        """What the screen shows: a cloud connector is OFF until asked for.

        The router does NOT use this - it asks :meth:`disabled`, so an owner who
        never opened this screen keeps exactly the behaviour they had. The
        difference matters: ``is_on`` is a statement about the switch,
        ``disabled`` is the gate.
        """
        spec = CATALOGUE_BY_ID.get(cid) or {}
        explicit = self.explicit(cid)
        if explicit is not None:
            return explicit
        return bool(spec.get("default_on", False))

    def disabled(self, cid: str) -> bool:
        """True only when the owner explicitly switched this connector OFF."""
        return self.explicit(cid) is False

    def set_on(self, cid: str, on: bool) -> None:
        settings = self._load()
        settings[cid] = bool(on)
        self.vault.meta_set(self.KEY, {"v": 1, "on": settings, "updated": now_ms()})

    def all(self) -> Dict[str, bool]:
        return {c["id"]: self.is_on(c["id"]) for c in CATALOGUE}


class UsageToday:
    """Today's usage per connector, read from the ledger the router already writes.

    No second counter: a counter that disagrees with the budget is worse than no
    counter at all. Local connectors have no ledger entry and are reported as
    ``budgeted: false`` with the request count the ledger holds (usually zero).
    """

    def __init__(self, ledger: Any, config: Config):
        self.ledger = ledger
        self.config = config

    def for_ai(self, cid: str) -> Dict[str, Any]:
        cfg = self._lane_config(cid)
        if self.ledger is None:
            return {"budgeted": False, "requests": 0, "failures": 0, "tokens": 0}
        try:
            snap = self.ledger.snapshot(cid, cfg)
        except Exception:
            return {"budgeted": False, "requests": 0, "failures": 0, "tokens": 0}
        return {"budgeted": True, "requests": snap["used"], "failures": snap["failures"],
                "tokens": snap["tokens"], "allowed": snap["allowed"],
                "remaining": snap["remaining"], "pct": snap["pct"],
                "cooldown_s": snap["cooldown_s"],
                "cap_is_default": snap["cap_is_default"],
                "resets": "tomorrow (local midnight)"}

    def _lane_config(self, cid: str) -> Dict[str, Any]:
        """The same limits the router would apply, so the numbers match."""
        a = self.config.agent
        if cid == "ollama":
            return {"type": "ollama", "daily_requests": 0, "rpm": 0}
        if cid == "gemini":
            typ = a.get("online_provider") or "gemini"
            if str(typ).lower() != "gemini":
                return {"type": "gemini", "daily_requests": a.get("online_daily_requests", 0),
                        "rpm": a.get("online_rpm", 0)}
        return {"type": cid, "daily_requests": a.get("online_daily_requests", 0),
                "rpm": a.get("online_rpm", 0),
                "stop_at_pct": a.get("online_stop_pct", 80)}


# --------------------------------------------------------------------------- #
# test calls
# --------------------------------------------------------------------------- #
#: One small, fixed prompt. Fixed on purpose: a "Test" button that accepted
#: arbitrary text would be a chat box that bypasses the router's private-data
#: scanner and the turn's budget accounting.
TEST_PROMPT = ("Reply with exactly: OK. Then, in one short sentence, say which "
               "model answered. Do not add anything else.")
TEST_MAX_TOKENS = 60


def _test_prompt_ok(text: str) -> str:
    text = (text or "").strip()
    if text:
        raise ConnectorError("the test prompt is fixed so the cost stays predictable; "
                             "leave it empty")
    if len(TEST_PROMPT) > MAX_TEST_PROMPT_CHARS:      # pragma: no cover - guard
        raise ConnectorError("test prompt too long")
    return TEST_PROMPT


# --------------------------------------------------------------------------- #
# the registry
# --------------------------------------------------------------------------- #
class ConnectorRegistry:
    """Everything the connectors screen needs, in one place.

    ``status()`` is read-only and cheap; it never makes a network call (a
    dashboard that pings four cloud endpoints every refresh would leak the fact
    that the app is open, and would burn quota).
    """

    def __init__(self, config: Config, vault: Any, brain: Any = None,
                 memory: Any = None):
        self.config = config
        self.vault = vault
        self.brain = brain
        self.memory = memory
        self.secrets = ConnectorVault(vault)
        self.settings = ConnectorSettings(vault)
        self.settings.registry = self
        self.usage = UsageToday(getattr(brain, "ledger", None), config)

    # -- reads ----------------------------------------------------------- #
    def _auth(self) -> str:
        return AuthLevel.of(getattr(self.vault, "session", None), self.vault)

    def _gateway(self) -> Any:
        return getattr(self.brain, "integrations", None)

    def _ai_connectors(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        on_map = self.settings.all()
        names = set(self.secrets.names())
        for spec in CATALOGUE:
            cid = spec["id"]
            row: Dict[str, Any] = {
                "id": cid, "kind": spec["kind"], "label": spec["label"],
                "detail": spec["detail"], "optional": spec["optional"],
                "on": on_map.get(cid, False), "lane": spec["lane"],
                "allowed_hosts": list(spec["hosts"]),
                "needs_key": spec["needs_key"],
                "has_key": cid in names,
                "key_hint": "", "fingerprint": "", "key_source": "",
                "egress": spec["egress"],
                "usage": self.usage.for_ai(cid) if spec["lane"] != "offline"
                         else {"budgeted": False, "requests": 0, "failures": 0, "tokens": 0},
            }
            # The hint and fingerprint are derived from the key, so they are only
            # produced for a caller allowed to know a key exists. Nothing here
            # ever contains the key itself.
            if cid in names:
                try:
                    secret = self.secrets.get(cid)
                    row["key_hint"] = hint(secret)
                    row["fingerprint"] = fingerprint(secret)
                    row["key_source"] = "vault"
                except ConnectorError:
                    row["has_key"] = True
                    row["key_hint"] = ""
                    row["key_source"] = "vault (locked to this session)"
            else:
                material = self.key_material(cid)
                if material["source"] == "environment":
                    row["has_key"] = True
                    row["key_source"] = f"environment ({material['env']})"
                    row["key_hint"] = hint(material["key"])
                    row["fingerprint"] = fingerprint(material["key"])
            row["ready"] = self._ready(cid, row)
            row["why_not"] = self._why_not(cid, row)
            out.append(row)
        return out

    def key_material(self, cid: str) -> Dict[str, str]:
        """The key a call may use, and where it came from.

        Returns ``{"key": ..., "source": "vault"|"environment"|"", "env": name}``.
        This is the ONE function that decides; before it existed the router read
        the environment, the connectors screen read the vault, and the two could
        disagree - a key entered through the screen was invisible to the router,
        so an owner could add a Gemini key and still be told "no online key set".
        """
        env_name = self._env_name(cid)
        try:
            if self.secrets.has(cid):
                return {"key": self.secrets.get(cid), "source": "vault", "env": env_name}
        except ConnectorError:
            pass                      # a degraded/locked session: fall through
        value = os.environ.get(env_name, "") if env_name else ""
        return {"key": value, "source": "environment" if value else "", "env": env_name}

    def _env_name(self, cid: str) -> str:
        a = self.config.agent
        return str({"gemini": a.get("online_key_env") or a.get("api_key_env") or "GEMINI_API_KEY",
                    "openai": a.get("online_key_env") or a.get("api_key_env") or "OPENAI_API_KEY",
                    "anthropic": a.get("online_key_env") or a.get("api_key_env") or "ANTHROPIC_API_KEY"}
                   .get(cid, ""))

    def _key_present(self, row: Dict[str, Any]) -> bool:
        return bool(row.get("has_key"))

    def _ready(self, cid: str, row: Dict[str, Any]) -> bool:
        if not row["on"]:
            return False
        spec = CATALOGUE_BY_ID[cid]
        if spec["needs_key"] and not self._key_present(row):
            return False
        return True

    def _why_not(self, cid: str, row: Dict[str, Any]) -> str:
        spec = CATALOGUE_BY_ID[cid]
        if row["on"] and row["ready"]:
            return ""
        if not row["on"]:
            return ("off - turn it on, which asks you to log in again"
                    if spec["needs_key"] else "off")
        if spec["needs_key"] and not self._key_present(row):
            return "on, but no key yet - add one with `brain connectors key <name>`"
        return ""

    def integrations(self) -> List[Dict[str, Any]]:
        """Configured outside services: what is on, which hosts it may reach, what it used.

        Read through the same gateway the agent calls, so a service that is
        disabled there cannot appear enabled here.
        """
        gw = self._gateway()
        raw = getattr(gw, "integrations", {}) if gw is not None else {}
        out: List[Dict[str, Any]] = []
        for name, integ in sorted(raw.items()):
            auth = getattr(integ, "auth", {}) or {}
            source = self._auth_source(auth)
            out.append({
                "id": name, "kind": INTEGRATION, "label": name,
                "detail": getattr(integ, "description", "") or "",
                "on": bool(getattr(integ, "enabled", False)),
                "optional": True, "lane": "integration", "egress": "gated",
                "needs_key": str(auth.get("type", "none")).lower() != "none",
                "has_key": source["present"], "key_hint": source["hint"],
                "fingerprint": source["fingerprint"], "key_source": source["source"],
                "allowed_hosts": list(getattr(integ, "allow_hosts", []) or []),
                "operations": sorted(getattr(integ, "operations", {}) or {}),
                "rate_limit_per_minute": getattr(integ, "rate_limit_per_minute", 0),
                "usage": self._integration_usage(name),
                "ready": bool(getattr(integ, "enabled", False)) and source["present"]
                         if source["required"] else bool(getattr(integ, "enabled", False)),
                "why_not": ("" if getattr(integ, "enabled", False) else
                            "off - plugins are disabled unless the config says \"enabled\": true"),
            })
        out.append(self._custom_row())
        return out

    def _auth_source(self, auth: Dict[str, Any]) -> Dict[str, Any]:
        kind = str(auth.get("type", "none")).lower()
        required = kind != "none"
        if not required:
            return {"present": False, "hint": "", "fingerprint": "",
                    "source": "no key needed", "required": False}
        if "$env" in auth:
            env = str(auth["$env"])
            value = os.environ.get(env, "")
            return {"present": bool(value), "hint": hint(value),
                    "fingerprint": fingerprint(value) if value else "",
                    "source": f"environment ({env})", "required": True}
        if "$vault" in auth:
            key = str(auth["$vault"])
            found = False
            try:
                rows = self.memory.search(f"secret {key}", limit=1, kind="credential_ref") \
                    if self.memory is not None else []
                found = bool(rows)
            except Exception:
                found = False
            return {"present": found, "hint": "", "fingerprint": "",
                    "source": f"vault reference ({key})", "required": True}
        return {"present": False, "hint": "", "fingerprint": "",
                "source": "literal secret refused", "required": True}

    def _integration_usage(self, name: str) -> Dict[str, Any]:
        if self.brain is None or getattr(self.brain, "ledger", None) is None:
            return {"budgeted": False, "requests": 0, "failures": 0, "tokens": 0}
        try:
            snap = self.brain.ledger.snapshot(name, {"daily_requests": 0, "rpm": 0})
        except Exception:
            return {"budgeted": False, "requests": 0, "failures": 0, "tokens": 0}
        return {"budgeted": True, "requests": snap["used"], "failures": snap["failures"],
                "tokens": snap["tokens"]}

    def _custom_row(self) -> Dict[str, Any]:
        """The "add your own" placeholder is a row in the UI, not a connector."""
        return {"id": "+custom", "kind": INTEGRATION, "label": "Add a service",
                "detail": "Declared in integrations.json with allow_hosts, operations "
                          "and a $env or $vault secret reference. The model can never "
                          "invent a URL.",
                "on": False, "optional": True, "lane": "integration", "egress": "gated",
                "needs_key": True, "has_key": False, "key_hint": "", "fingerprint": "",
                "key_source": "", "allowed_hosts": [], "operations": [],
                "usage": {"budgeted": False, "requests": 0, "failures": 0, "tokens": 0},
                "ready": False, "why_not": "edit $BRAIN_HOME/integrations.json",
                "placeholder": True}

    def router_credentials(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """The key the router's online lane should use, as a provider config.

        Only the online lane may call this, and only when the connector it names
        is not switched off. The key is returned into the provider object and
        nowhere else - it is not stored on the config, not logged, and not
        reachable from any route.
        """
        cid = str(cfg.get("type") or "gemini").lower()
        if cid not in CATALOGUE_BY_ID or self.settings.disabled(cid):
            return cfg
        material = self.key_material(cid)
        if not material["source"]:
            return cfg
        return {**cfg, "api_key": material["key"], "key_source": material["source"]}

    def status(self) -> Dict[str, Any]:
        auth = self._auth()
        return {
            "ok": True,
            "auth_level": auth,
            "can_edit_keys": auth == AuthLevel.TOTP,
            "ai": self._ai_connectors(),
            "integrations": self.integrations(),
            # same facts, flat: the dashboard reads the detailed rows above, the
            # acceptance contract (and a script) reads this list
            "connectors": self.flat_connectors(),
            "step_up_required_for": ["on", "off", "add key", "remove key", "test"],
            "auth_actions": self.pending_actions(),
            "notes": [
                "A key is never shown. Only its last four characters and a fingerprint.",
                "Turning a connector on or off asks you to log in again first.",
                "No payment, banking or card connectors exist in this build.",
            ],
        }

    # -- the flat shape the acceptance contract asks for ------------------- #
    def _effective_on(self, cid: str, spec: Dict[str, Any]) -> bool:
        """Is this connector usable right now?

        For an AI provider the router's own rule applies: it is usable unless the
        owner switched it off. For an OUTSIDE SERVICE the rule is the opposite:
        enabled in integrations.json is only a *registration*, so the owner's
        explicit switch is the only thing that turns it on. (A fresh vault used to
        report `calendar: enabled true` because of that confusion.)
        """
        if spec["kind"] == INTEGRATION:
            return self.settings.explicit(cid) is True
        return not self.settings.disabled(cid)

    def _state_for(self, spec: Dict[str, Any], on: bool, configured: bool,
                   has_key: bool) -> Tuple[str, str]:
        """`ready` | `needs_setup` | `off`, with a reason a person can act on."""
        if not on:
            return "off", "switched off in this screen"
        if spec.get("placeholder"):
            return "needs_setup", ("a route shape only - add a real entry to "
                                   "integrations.json to use it")
        if spec["needs_key"] and not has_key:
            return "needs_setup", f"no key yet - run: brain connectors key {spec['id']}"
        if not configured:
            return "needs_setup", "registered but not enabled in integrations.json"
        if spec.get("egress") == "gated":
            return "ready", ("ready - every call still needs a fresh login and "
                             "approval for its host")
        return "ready", "ready - runs on this PC only"

    def _env_key_for(self, spec: Dict[str, Any]) -> str:
        """A key the owner exported instead of storing it (never printed)."""
        names = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
                 "openai": ("OPENAI_API_KEY",),
                 "anthropic": ("ANTHROPIC_API_KEY",)}
        for var in names.get(spec["id"], ()):
            if os.environ.get(var):
                return os.environ[var]
        return ""

    def _connector_row(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        cid = spec["id"]
        has_key = cid in set(self.secrets.names()) or bool(self._env_key_for(spec))
        configured = (not spec["needs_key"]) or has_key
        on = self._effective_on(cid, spec)
        state, reason = self._state_for(spec, on, configured, has_key)
        usage = None
        if spec.get("lane") == "online" and hasattr(self.usage, "for_ai"):
            row = self.usage.for_ai(cid)
            usage = {"used": int(row.get("requests", 0)),
                     "allowed": int(row.get("allowed", 0))}
        return {"id": cid, "name": spec["label"], "kind": spec["kind"],
                "enabled": bool(on), "configured": bool(configured),
                "state": state, "reason": reason,
                "hosts": list(spec.get("hosts") or []), "usage": usage}

    def _spec_for(self, cid: str) -> Optional[Dict[str, Any]]:
        spec = CATALOGUE_BY_ID.get(cid)
        if spec is not None:
            return spec
        row = next((r for r in self.integrations() if r["id"] == cid), None)
        if row is None:
            return None
        return {"id": cid, "kind": INTEGRATION, "label": row["label"],
                "detail": row.get("detail", ""), "optional": True,
                "lane": "integration", "egress": "gated",
                "hosts": row.get("allowed_hosts") or [], "needs_key": False,
                "default_on": False, "placeholder": row.get("placeholder", False)}

    def flat_connectors(self) -> List[Dict[str, Any]]:
        """Every connector in the acceptance contract's flat shape.

        Deliberately carries no secret: `configured` is a boolean and `reason` is
        a sentence. The key hint and fingerprint stay in the detailed rows.
        """
        out = [self._connector_row(spec) for spec in CATALOGUE]
        for row in self.integrations():
            spec = {"id": row["id"], "kind": INTEGRATION, "label": row["label"],
                    "detail": row.get("detail", ""), "optional": True,
                    "lane": "integration", "egress": "gated",
                    "hosts": row.get("allowed_hosts") or [], "needs_key": False,
                    "default_on": False, "placeholder": row.get("placeholder", False)}
            if row["id"] not in CATALOGUE_BY_ID:
                out.append(self._connector_row(spec))
        return out

    def set_enabled(self, cid: str, enabled: bool) -> Dict[str, Any]:
        """`/enable` and `/disable`: a switch that needs a FRESH step-up.

        `session.step_up_ok` is the gate and it is consumed here, so one
        re-authentication authorises exactly one toggle. Only the flat row comes
        back - never a key.
        """
        spec = self._spec_for(cid)
        if spec is None:
            raise ConnectorError(f"unknown connector '{cid}'")
        sess = self.vault.session
        if sess is None:
            raise ConnectorError("vault is locked")
        if not getattr(sess, "step_up_ok", False):
            raise StepUpRequired(
                "switching a connector on or off needs a fresh login check",
                ["approve the change in Connectors (POST /v1/connectors/authorise), "
                 "or run: brain connectors on|off " + cid],
                action=f"connector:{cid}:{'on' if enabled else 'off'}",
                needs="approval")
        sess.step_up_ok = False                       # single use, always
        self.settings.set_on(cid, enabled)
        try:
            self.settings.mark_explicit_on(cid, enabled)
        except Exception:
            pass
        applied = True
        if enabled and spec.get("lane") == "online":
            try:
                self._apply_lane(cid, spec)
            except Exception:
                applied = False
        row = self._connector_row(spec)
        if spec["kind"] == INTEGRATION and enabled and not self._integration_registered(cid):
            # Honest: switching on a service that is not registered cannot make
            # it usable, so the row says so instead of implying success.
            row["state"] = "needs_setup"
            row["reason"] = ("switched on, but the service is not registered in "
                             "integrations.json - add it there to use it")
        self.vault.audit("connector_switched",
                         {"connector": cid, "on": bool(enabled), "kind": spec["kind"],
                          "via": "api-enable", "applied_to_router": applied,
                          "auth_level": self._auth()})
        return row

    def _integration_registered(self, cid: str) -> bool:
        try:
            return cid in (getattr(self._gateway(), "integrations", {}) or {})
        except Exception:
            return False

    # -- fresh-login approval -------------------------------------------- #
    def _ceremony(self) -> Any:
        from ..identity.ceremony import IdentityCeremony
        return IdentityCeremony(self.config, self.vault)

    def pending_actions(self) -> List[Dict[str, Any]]:
        try:
            return self._ceremony().pending_actions()
        except Exception:
            return []

    def authorise(self, action: str, passphrase: str = "",
                  totp_code: str = "", uses: int = 1,
                  require_reauth: bool = True) -> Dict[str, Any]:
        """Create a one-time approval for ``action`` (the "is it really you?" step).

        The dashboard uses this after its own small login form; the CLI folds it
        into the command it is already running. The approval is bound to this
        session and this action, and it is spent by the next write.
        """
        if not passphrase:
            ceremony = self._ceremony()
            grant = (ceremony.grant_from_fresh_session(action, max_uses=uses)
                     if require_reauth else {"ok": False})
            if grant.get("ok"):
                self.vault.audit("connector_action_approved",
                                 {"action": action, "mode": "totp-session",
                                  "uses_left": grant.get("uses_left", uses)})
                return {"ok": True, "action": action, "mode": "totp-session",
                        "expires_at": grant.get("expires_at", 0),
                        "expires_in_s": max(0, int(grant.get("expires_at", 0) - now_ms() + 999) // 1000),
                        "degraded": False}
            raise StepUpRequired("this needs a fresh login check",
                                 [f"approve '{action}' with your passphrase and "
                                  f"authenticator code"], action=action,
                                 needs="approval")
        result = self._ceremony().authorise_action(action, passphrase,
                                                   totp_code=totp_code, max_uses=uses)
        if not result.ok:
            raise ConnectorError("; ".join(result.reasons) or "re-authentication failed")
        detail = dict(result.detail or {})
        grant = detail.get("grant", {})
        self.vault.audit("connector_action_approved", {"action": action,
                                                       "mode": grant.get("mode", ""),
                                                       "uses_left": grant.get("uses_left", 1)})
        return {"ok": True, "action": action, "mode": grant.get("mode", ""),
                "expires_at": grant.get("expires_at", 0),
                "expires_in_s": max(0, int(grant.get("expires_at", 0) - now_ms() + 999) // 1000),
                "degraded": bool(grant.get("degraded", False))}

    #: One approval authorises exactly one action. ON and OFF are different
    #: actions, so flipping the switch both ways asks for the login twice.
    SWITCH_USES = 1

    def _consume(self, action: str) -> None:
        ok, why = self._ceremony().consume_action(action)
        if not ok:
            raise StepUpRequired(
                f"{why} - changing a connector needs a fresh login check",
                [f"approve '{action}' first: brain connectors approve {action} "
                 f"(or the Approve button), then repeat the action"],
                action=action, needs="approval")

    def _approve(self, action: str, passphrase: str, totp_code: str,
                 already_approved: bool, uses: int = 1,
                 require_reauth: bool = True) -> None:
        """One fresh login for the actions it covers, then spend one use.

        ``uses=1`` is for a secret write (the approval dies with the write).
        ``uses=SWITCH_USES`` covers a switch, because ON then OFF inside the
        grant window is the same login, and both uses are audited separately.
        """
        if already_approved:
            self._consume(action)
            return
        shortcut = not passphrase and require_reauth
        out = self.authorise(action, passphrase, totp_code, uses=uses,
                             require_reauth=require_reauth)
        self._consume(action)
        if shortcut and out.get("mode") == "totp-session":
            # One login, one owner action: a second, different action is asked
            # to log in again instead of riding the same 2-minute window.
            self.vault.forget_fresh_login()

    # -- writes ----------------------------------------------------------- #
    def set_on(self, cid: str, on: bool, passphrase: str = "",
               totp_code: str = "", approved: bool = False,
               require_reauth: bool = True) -> Dict[str, Any]:
        spec = CATALOGUE_BY_ID.get(cid)
        if spec is None:
            raise ConnectorError(f"unknown connector '{cid}'. Known: "
                                 f"{', '.join(CATEGORY_IDS)}")
        action = f"connector:{cid}:{'on' if on else 'off'}"
        self._approve(action, passphrase, totp_code, approved, uses=self.SWITCH_USES,
                      require_reauth=require_reauth)
        self.settings.set_on(cid, on)
        if on and spec["lane"] == "online":
            self._apply_lane(cid, spec)
        self.vault.audit("connector_switched",
                         {"connector": cid, "on": bool(on), "kind": spec["kind"],
                          "egress": spec["egress"], "auth_level": self._auth()})
        return {"ok": True, "id": cid, "on": bool(on), "detail": self._describe(cid)}

    def _apply_lane(self, cid: str, spec: Dict[str, Any]) -> None:
        """Point the router's *online* lane at the connector the owner switched on.

        Only the online lane: Auto and "This PC only" never reach a cloud model,
        and a persisted default is ignored by the router anyway.
        """
        agent = dict(self.config.agent or {})
        agent["online_provider"] = cid
        if cid == "openai":
            agent.setdefault("online_base_url", DEFAULT_ONLINE_URLS["openai"])
        else:
            # Clear a *default* cloud endpoint left over from another provider,
            # because it belongs to that provider. A URL the owner chose - a
            # proxy, a regional host, a test server on 127.0.0.1 - is not ours to
            # throw away: doing that sent the next call to the public host and the
            # acceptance run's third turn fell back to offline.
            left = str(agent.get("online_base_url") or "")
            if left and _is_default_online_url(left):
                agent.pop("online_base_url", None)
        agent.pop("online_key_env", None)      # the env fallback keeps its default
        self.config.agent = agent
        self.config.save()

    def _describe(self, cid: str) -> str:
        spec = CATALOGUE_BY_ID.get(cid) or {}
        if not self.settings.is_on(cid):
            return "off"
        if spec.get("needs_key") and not self.key_material(cid)["source"]:
            return "on, but no key yet"
        return "on"

    def set_key(self, cid: str, secret: str, auth: Optional[str] = None,
                passphrase: str = "", totp_code: str = "",
                approved: bool = False) -> Dict[str, Any]:
        if cid not in CATALOGUE_BY_ID:
            raise ConnectorError(f"unknown connector '{cid}'")
        auth = auth or self._auth()
        if not AuthLevel.at_least(auth, AuthLevel.TOTP):
            how = (["unlock again with --totp <code>, then set the key"]
                   if self.vault.has_totp() else
                   ["no authenticator is enrolled - run `brain totp` once, then "
                    "unlock with that code",
                    "or unlock with your recovery code, then set the key"])
            raise StepUpRequired(
                "editing a key needs a session whose second factor was your "
                "authenticator or a recovery code. A voice-only or degraded session "
                "can switch connectors and test them, but not write a secret.",
                how,
                action=f"connector:{cid}:key:set", needs="authenticator")
        self._approve(f"connector:{cid}:key:set", passphrase, totp_code, approved)
        out = self.secrets.set(cid, secret)
        self.vault.audit("connector_key_set",
                         {"connector": cid, "hint": out["key_hint"],
                          "fingerprint": out["fingerprint"]})
        return {"ok": True, "id": cid, "has_key": True, "key_hint": out["key_hint"],
                "fingerprint": out["fingerprint"], "detail": self._describe(cid)}

    def remove_key(self, cid: str, passphrase: str = "", totp_code: str = "",
                   approved: bool = False) -> Dict[str, Any]:
        if cid not in CATALOGUE_BY_ID:
            raise ConnectorError(f"unknown connector '{cid}'")
        if not AuthLevel.at_least(self._auth(), AuthLevel.TOTP):
            raise StepUpRequired("removing a key needs an authenticator-verified session",
                                 (["unlock again with --totp <code>, then remove the key"]
                                  if self.vault.has_totp() else
                                  ["no authenticator is enrolled - run `brain totp` once, "
                                   "then unlock with that code"]),
                                 action=f"connector:{cid}:key:remove",
                                 needs="authenticator")
        self._approve(f"connector:{cid}:key:remove", passphrase, totp_code, approved)
        removed = self.secrets.remove(cid)
        self.vault.audit("connector_key_removed", {"connector": cid, "existed": removed})
        return {"ok": True, "id": cid, "removed": removed, "has_key": False,
                "detail": self._describe(cid)}

    # -- test ------------------------------------------------------------- #
    def test(self, cid: str, prompt: str = "", passphrase: str = "",
             totp_code: str = "", approved: bool = False) -> Dict[str, Any]:
        """One small, fixed-prompt call, counted against the daily budget.

        This is the only place a connector is exercised from a button. It is
        deliberately NOT a chat route: the router's private-data scanner, the
        turn's tool loop and the memory context are all bypassed, so the only
        thing that can leave the machine is the fixed prompt itself.
        """
        spec = CATALOGUE_BY_ID.get(cid)
        if spec is None:
            raise ConnectorError(f"unknown connector '{cid}'")
        text = _test_prompt_ok(prompt)
        if not self.settings.is_on(cid) and cid != "local":
            raise ConnectorError(f"{spec['label']} is off; turn it on first")
        self._approve(f"connector:{cid}:test", passphrase, totp_code, approved)
        return self._run_test(cid, spec, text)

    def _run_test(self, cid: str, spec: Dict[str, Any], text: str) -> Dict[str, Any]:
        from ..agent.ledger import UsageLedger
        from ..agent.providers import ProviderError

        if cid == "local":
            return {"ok": True, "id": cid, "answer": "the built-in engine is always "
                                                     "available and makes no call",
                    "counted": False}
        ledger = getattr(self.brain, "ledger", None) or UsageLedger(
            self.config.home / "usage.json")
        lane = self.usage._lane_config(cid)
        provider = self._build_for_test(cid, spec)
        if provider is None:
            raise ConnectorError(f"{spec['label']} is not configured in this build")
        if not provider.available():
            raise ConnectorError(f"{spec['label']} is not reachable or has no key "
                                 f"in this session")
        ok, why = ledger.check(provider.name, lane)
        if not ok:
            self.vault.audit("connector_test_blocked",
                             {"connector": cid, "reason": why})
            raise ConnectorError(f"not sent: {why}")
        started = time.time()
        used = 0
        try:
            completion = provider.complete("You are a connectivity test.", [
                {"role": "user", "content": text}], tools=None)
            answer = (completion.text or "").strip()
            used = int((completion.usage or {}).get("total_tokens", 0) or 0)
            ok = True
            error = ""
        except ProviderError as exc:
            answer, error = "", str(exc)
            ok = False
            if getattr(exc, "retry_after", None):
                ledger.set_cooldown(provider.name, int(exc.retry_after))
        ledger.record(provider.name, ok, used)      # failures count too
        self.vault.audit("connector_tested",
                         {"connector": cid, "ok": ok, "tokens": used,
                          "seconds": round(time.time() - started, 3),
                          "error_kind": error.split("->")[0][:80] if error else ""})
        if not ok:
            raise ConnectorError(f"the call failed: {error}")
        return {"ok": True, "id": cid, "answer": answer[:500], "tokens": used,
                "counted": True, "usage": self.usage.for_ai(cid)}

    def _build_for_test(self, cid: str, spec: Dict[str, Any]) -> Any:
        from ..agent.providers import (AnthropicProvider, GeminiProvider,
                                       OllamaProvider, OpenAICompatibleProvider)

        a = self.config.agent
        if cid == "ollama":
            return OllamaProvider(model=a.get("local_model", "llama3.2:3b"),
                                  base_url=a.get("ollama_url") or "http://127.0.0.1:11434")
        key = self.key_material(cid)["key"]
        if cid == "gemini":
            return GeminiProvider(api_key=key, model=a.get("online_model") or "gemini-flash-latest")
        if cid == "anthropic":
            return AnthropicProvider(api_key=key,
                                     model=a.get("online_model") or "claude-sonnet-4-5")
        if cid == "openai":
            return OpenAICompatibleProvider(
                api_key=key, model=a.get("online_model") or "gpt-4o-mini",
                base_url=a.get("online_base_url") or "https://api.openai.com/v1")
        return None


CATEGORY_IDS = [c["id"] for c in CATALOGUE]
