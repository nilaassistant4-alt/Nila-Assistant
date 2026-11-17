"""Model router: decides WHERE each message is answered, and says so.

Three lanes, cheapest and most private first:

  local   - Ollama on this PC. Free, private, works offline.
  online  - a cloud model (Gemini by default). Only for messages that contain no
            private data, only within the daily budget, and only after the user
            explicitly selects mode "online". Auto is always local/offline.
  offline - the built-in rule engine. Always available, never leaves the device.

FAIL CLOSED: anything uncertain (private data in the message, budget exhausted,
provider cooling down, no key) keeps the message on this device and the reply
says why. Nothing here ever sends data online silently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from ..config import Config
from ..security.redact import scan
from .ledger import UsageLedger
from .local_brain import LocalBrain
from .providers import LLMProvider, build_provider, is_loopback_url

MODES = ("auto", "local", "online")


@dataclass
class RoutePlan:
    provider: LLMProvider
    where: str                  # "local" | "online" | "offline"
    label: str                  # human label shown in the UI
    ledger_key: str = ""        # non-empty only for online calls (budgeted)
    notice: str = ""            # why the route differs from what was asked
    blocked: bool = False       # an online request that was refused

    def to_dict(self) -> Dict[str, Any]:
        return {"where": self.where, "label": self.label, "notice": self.notice,
                "blocked": self.blocked}


class Router:
    def __init__(self, config: Config, ledger: UsageLedger,
                 online_provider: Optional[LLMProvider] = None,
                 connector_settings: Any = None):
        self.config = config
        self.ledger = ledger
        #: Optional hook onto the connectors screen (#3). Used ONLY to honour an
        #: explicit OFF: an owner who never opened that screen keeps the old
        #: behaviour, and being "not configured" is not the same as "forbidden".
        self.connector_settings = connector_settings
        # Optional provider injection is mainly for deterministic tests. It is
        # still behind the same explicit-mode, scanner, and budget gates.
        self.online_provider = online_provider
        self.offline = LocalBrain()

    # ------------------------------------------------------------------ #
    def lane_config(self, role: str) -> Dict[str, Any]:
        a = self.config.agent
        t = float(a.get("temperature", 0.2))
        if role == "local":
            # Default small brain: Qwen 2.5 3B, which is the best Tamil/English
            # balance at 2 GB. An owner who pulled only llama3.2 can still set
            # `local_model` and every route keeps working.
            return {"type": "ollama", "model": a.get("local_model", "qwen2.5:3b"),
                    "base_url": a.get("ollama_url") or None, "temperature": t,
                    "timeout": float(a.get("local_timeout", 120))}
        legacy_type = str(a.get("provider", "local")).lower()
        legacy_remote = legacy_type not in ("", "local", "ollama")
        online_type = a.get("online_provider") or (legacy_type if legacy_remote else "gemini")
        online_model = a.get("online_model") or (a.get("model") if legacy_remote else None)
        online_key_env = a.get("online_key_env") or (a.get("api_key_env") if legacy_remote else None)
        online_base_url = a.get("online_base_url") or (a.get("base_url") if legacy_remote else None)
        return {"type": online_type, "model": online_model or None,
                "api_key_env": online_key_env or None,
                "base_url": online_base_url or None, "temperature": t,
                "timeout": float(a.get("online_timeout", a.get("timeout", 30))),
                "daily_requests": a.get("online_daily_requests", 0),
                "rpm": a.get("online_rpm", 0), "stop_at_pct": a.get("online_stop_pct", 80)}

    # ------------------------------------------------------------------ #
    # the model pack: small jobs on the small brain, big work on the big one
    # ------------------------------------------------------------------ #
    def job_class(self, text: str) -> str:
        """Is this a small question or a big job?

        Deliberately simple and explainable, because the cost of a wrong guess is
        asymmetric: sending a small question to a big model wastes money, while
        sending a big question to a small model produces a bad answer. So the
        threshold leans towards "big" the moment the request shows any of the
        signals below, and everything else is "small".
        """
        low = " ".join(str(text or "").lower().split())
        if len(low) > 400:
            return "big"
        big_words = ("plan", "design", "explain in detail", "compare", "step by step",
                     "write a", "draft", "summarise", "summarize", "analyse", "analyze",
                     "code", "debug", "translate this", "strategy", "business plan",
                     "திட்டம்", "விளக்கு", "எழுது", "ஒப்பிடு", "வடிவமை", "கணக்கிடு")
        if any(w in low for w in big_words):
            return "big"
        # Several sentences, or a list of asks, is work rather than a lookup.
        if low.count("?") + low.count("\n") >= 2:
            return "big"
        return "small"

    def pack_plan(self, text: str, allow_cloud: bool = True) -> Dict[str, Any]:
        """Which brain a job would use, from the pack, and where it lives.

        This is the "connect them in the background, ask only when needed" part:
        the registry says what exists, `available()` says what is really present
        on this PC right now, and a model that is not present is never named.
        """
        from . import models as pack

        job = self.job_class(text)
        local = self.lane_config("local")
        names = pack.ollama_models(str(local.get("base_url") or "http://127.0.0.1:11434"))
        present = ["builtin"]
        if names:
            for model_id, spec in pack.PACK.items():
                if spec["runtime"] == "ollama" and pack._tag_present(spec["tag"], names):
                    present.append(model_id)
        cloud_ok = allow_cloud and bool(self.online_status(text).get("ok"))
        if cloud_ok:
            cid = str(self.lane_config("online").get("type") or "gemini").lower()
            present.append({"gemini": "gemini-flash", "anthropic": "claude",
                            "openai": "openai-compatible"}.get(cid, "gemini-flash"))
        choice = pack.choose(job, present, allow_cloud=cloud_ok)
        return {"job": job, "choice": choice, "present": present,
                "ollama_running": names is not None,
                "small_brain": pack.choose("small", present, allow_cloud=cloud_ok),
                "big_brain": pack.choose("big", present, allow_cloud=cloud_ok)}

    def default_mode(self) -> str:
        # A persisted legacy default must never silently opt a user into the
        # network. Online is accepted only as a per-turn, explicit selection.
        m = str(self.config.agent.get("route_default", "auto")).lower()
        return m if m in ("auto", "local") else "auto"

    # ------------------------------------------------------------------ #
    @property
    def builtin(self) -> LocalBrain:
        """The built-in engine the Brain also holds.

        One object, not two: the offline reply is built from
        ``self.instructions`` (the owner's standing rules), so a second copy
        would answer without them - which is exactly what the #2 acceptance
        run saw when Ollama was not running.
        """
        engine = getattr(self, "_shared_offline", None)
        return engine if engine is not None else self.offline

    def share_offline_engine(self, engine: LocalBrain) -> None:
        self._shared_offline = engine

    def _offline_plan(self, notice: str = "", blocked: bool = False) -> RoutePlan:
        return RoutePlan(self.builtin, "offline", "built-in engine (this device)",
                         notice=notice, blocked=blocked)

    def online_status(self, text: str = "") -> Dict[str, Any]:
        """Can an online call be made right now, and if not, why not?"""
        cfg = self.lane_config("online")
        cfg = self._with_vault_key(cfg)
        prov = self.online_provider or build_provider(cfg)
        if text:
            klass = scan(text).max_class
            if klass in ("sensitive", "restricted"):
                return {"ok": False, "provider": prov, "cfg": cfg,
                        "reason": "your message looks like it contains private data "
                                  f"({klass}), so it stays on this device"}
        if self._switched_off(prov) and not self._explicitly_on(prov):
            # (an owner's later ON wins over an older OFF - see `_explicitly_on`)
            # Before the key check: "you switched it off" is the true reason,
            # and telling the owner "no key" would send them hunting for one.
            return {"ok": False, "provider": prov, "cfg": cfg,
                    "reason": f"{prov.name} is switched off in the connectors screen"}
        if getattr(prov, "needs_network", True) is False or not prov.available():
            env = cfg.get("api_key_env") or "GEMINI_API_KEY"
            return {"ok": False, "provider": prov, "cfg": cfg,
                    "reason": (f"no online key is set (looked in the vault and in {env}) - "
                               f"add one with `brain connectors key "
                               f"{str(cfg.get('type') or 'gemini').lower()}`")}
        ok, why = self.ledger.check(prov.name, cfg)
        if not ok:
            return {"ok": False, "provider": prov, "cfg": cfg, "reason": why}
        return {"ok": True, "provider": prov, "cfg": cfg, "reason": ""}

    def _with_vault_key(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Let a key entered in the connectors screen reach the provider.

        Without this, a key added through the screen sat in the vault while the
        provider was built from the environment alone, and the owner was told
        "no online key set" about a key they had just entered.
        """
        settings = self.connector_settings
        registry = getattr(settings, "registry", None) if settings is not None else None
        if registry is None:
            return cfg
        try:
            return registry.router_credentials(cfg)
        except Exception:
            return cfg          # a locked vault must never break routing

    def _switched_off(self, prov: Any) -> bool:
        settings = self.connector_settings
        if settings is None:
            return False
        cid = str(getattr(prov, "name", "") or "").lower()
        if cid not in ("gemini", "openai", "anthropic", "ollama"):
            return False
        try:
            return bool(settings.disabled(cid))
        except Exception:
            return False

    def _explicitly_on(self, prov: Any) -> bool:
        """Did the owner switch this connector ON in the connectors screen?

        `_switched_off` only knows about an explicit OFF, which is enough to keep
        the old behaviour for someone who never opened the screen - but not enough
        to honour ON -> OFF -> ON, where the second ON leaves no OFF to unset.
        The durable mark is written by ``ConnectorRegistry.set_enabled``.
        """
        check = getattr(self.connector_settings, "explicitly_on", None)
        cid = str(getattr(prov, "name", "") or "").lower()
        try:
            return bool(check(cid)) if callable(check) else False
        except Exception:
            return False

    def plan(self, text: str, mode: Optional[str] = None) -> RoutePlan:
        mode = (mode or self.default_mode()).lower()
        if mode not in MODES:
            mode = "auto"
        lcfg = self.lane_config("local")
        local_url_bad = bool(lcfg.get("base_url")) and not is_loopback_url(lcfg["base_url"])
        local = build_provider(lcfg)
        # "this PC" must mean this PC: a non-loopback Ollama address is refused
        local_ok = local.available() and not local_url_bad

        if mode == "online":
            st = self.online_status(text)
            if st["ok"]:
                prov = st["provider"]
                model = getattr(prov, "model", "configured model")
                return RoutePlan(prov, "online", f"online - {prov.name} ({model})",
                                 ledger_key=prov.name)
            note = f"Online was not used: {st['reason']}. "
            if local_ok:
                return RoutePlan(local, "local", f"this PC - {local.model}",
                                 notice=note + "Answered locally instead.", blocked=True)
            return self._offline_plan(note + "Answered by the built-in engine instead.", True)

        if local_ok:
            return RoutePlan(local, "local", f"this PC - {local.model}")

        # No local model running: Auto still stays on-device. A cloud lane is
        # reachable only through the explicit `mode="online"` path above.
        if local_url_bad:
            return self._offline_plan("agent.ollama_url points away from this PC, so it was not "
                                      "used. Local AI must run on 127.0.0.1.")
        return self._offline_plan("Your local model (Ollama) isn't running, so the basic "
                                  "built-in engine answered. Start Ollama for better answers.")
