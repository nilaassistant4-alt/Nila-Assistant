"""Secure outbound integration gateway.

Design constraints (see docs/ARCHITECTURE.md §7):

* The model can never invent a URL. It calls a **named integration** and an
  **operation** that the owner pre-registered in ``config/integrations.json``.
* Secrets are *references*, never literals: ``{"$env": "NOTION_TOKEN"}`` or
  ``{"$vault": "notion_token"}`` (stored encrypted in the memory vault).
* Every call goes through a policy check: host allow-list, method allow-list,
  rate limit, timeout, response size cap.
* Every call is audited with a redacted parameter digest (never the secret).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..util.http import RedirectRefused, open_follow, open_once
from ..util.misc import mask, now_ms, sha256_hex

from ..security.permission import CAP_NET

PLACEHOLDER = re.compile(r"\{\{\s*([\w.\-]+)\s*\}\}")
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_TIMEOUT = 20.0


class IntegrationError(RuntimeError):
    pass


class PolicyViolation(IntegrationError):
    pass


@dataclass
class Operation:
    name: str
    method: str
    path: str
    params: Dict[str, Any] = field(default_factory=dict)
    required: List[str] = field(default_factory=list)
    description: str = ""

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "method": self.method, "path": self.path,
                "params": self.params, "required": self.required,
                "description": self.description}


@dataclass
class Integration:
    name: str
    base_url: str
    auth: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    operations: Dict[str, Operation] = field(default_factory=dict)
    allow_hosts: List[str] = field(default_factory=list)
    timeout: float = DEFAULT_TIMEOUT
    rate_limit_per_minute: int = 60
    #: Mandate 5: plugins are DISABLED by default. This was `True`, so an
    #: integration whose config omitted the `enabled` key was silently live -
    #: the example JSON says false, but the code disagreed with it, and the code
    #: is what runs. Fail closed: absence of an explicit "enabled": true is a
    #: refusal, not a permission.
    enabled: bool = False
    description: str = ""

    def describe(self) -> Dict[str, Any]:
        return {"name": self.name, "base_url": self.base_url, "enabled": self.enabled,
                "description": self.description,
                "auth_type": self.auth.get("type", "none"),
                "operations": [op.describe() for op in self.operations.values()]}


@dataclass
class RateBucket:
    calls: List[int] = field(default_factory=list)


class IntegrationGateway:
    def __init__(self, config, memory=None, vault=None, path: Optional[Path] = None):
        self.config = config
        self.memory = memory
        self.vault = vault
        self.path = Path(path) if path else (config.home / "integrations.json")
        self.integrations: Dict[str, Integration] = {}
        self._buckets: Dict[str, RateBucket] = {}
        self.load()

    # ------------------------------------------------------------------ #
    def load(self) -> None:
        candidates = [self.path]
        shipped = Path(__file__).resolve().parents[2] / "config" / "integrations.example.json"
        if shipped.exists():
            candidates.append(shipped)
        for cand in candidates:
            if cand.exists():
                try:
                    data = json.loads(cand.read_bytes())
                except Exception:
                    continue
                for raw in data.get("integrations", []):
                    integ = self._parse(raw)
                    self.integrations[integ.name] = integ
                if cand == self.path:
                    break

    def _parse(self, raw: Dict[str, Any]) -> Integration:
        ops: Dict[str, Operation] = {}
        for op_raw in raw.get("operations", []) or []:
            op = Operation(name=op_raw["name"], method=str(op_raw.get("method", "GET")).upper(),
                           path=op_raw.get("path", "/"), params=op_raw.get("params", {}) or {},
                           required=list(op_raw.get("required", []) or []),
                           description=op_raw.get("description", ""))
            ops[op.name] = op
        return Integration(
            name=raw["name"], base_url=str(raw.get("base_url", "")).rstrip("/"),
            auth=dict(raw.get("auth", {}) or {}), headers=dict(raw.get("headers", {}) or {}),
            operations=ops, allow_hosts=list(raw.get("allow_hosts", []) or []),
            timeout=float(raw.get("timeout", DEFAULT_TIMEOUT)),
            rate_limit_per_minute=int(raw.get("rate_limit_per_minute", 60)),
            enabled=bool(raw.get("enabled", False)), description=raw.get("description", ""))

    def describe(self) -> List[Dict[str, Any]]:
        return [i.describe() for i in self.integrations.values() if i.enabled]

    def names(self) -> List[str]:
        return [n for n, i in self.integrations.items() if i.enabled]

    # ------------------------------------------------------------------ #
    def resolve_secret(self, spec: Dict[str, Any]) -> str:
        if "$env" in spec:
            import os

            val = os.environ.get(str(spec["$env"]), "")
            if not val:
                raise PolicyViolation(f"environment variable not set: {spec['$env']}")
            return val
        if "$vault" in spec and self.memory is not None:
            key = str(spec["$vault"])
            rows = self.memory.search(f"secret {key}", limit=1, kind="credential_ref")
            if not rows:
                raise PolicyViolation(f"no vault secret named {key}")
            return rows[0].body.strip()
        if "$literal" in spec:
            raise PolicyViolation("literal secrets in integration config are not allowed; "
                                  "use $env or $vault")
        # A literal token written straight into the file ({"type": "bearer",
        # "token": "AIza..."}) used to fall through to `return ""`. The call then
        # went out with an EMPTY Authorization header - no secret leaked, but the
        # owner saw "401" and went hunting for a broken key instead of reading
        # "you put the secret in the wrong place". Caught by probing this path.
        for key in ("token", "api_key", "secret", "value", "key"):
            raw = spec.get(key)
            if isinstance(raw, str) and raw.strip():
                raise PolicyViolation(
                    f"a literal secret was found in integrations.json (auth.{key}). "
                    f"Secrets never belong in a plaintext config: store it in the "
                    f"vault and reference it as {{\"$vault\": \"<name>\"}}, or use "
                    f"{{\"$env\": \"VAR\"}}")
        return ""

    def _headers(self, integ: Integration) -> Dict[str, str]:
        headers = {k: self._sub(integ, v) for k, v in integ.headers.items()}
        auth = integ.auth or {}
        kind = str(auth.get("type", "none")).lower()
        if kind == "bearer":
            headers["Authorization"] = f"Bearer {self.resolve_secret(auth)}"
        elif kind == "api_key_header":
            headers[str(auth.get("header", "X-Api-Key"))] = self.resolve_secret(auth)
        elif kind == "basic":
            import base64

            user = self._sub(integ, str(auth.get("username", "")))
            pwd = self.resolve_secret(auth)
            token = base64.b64encode(f"{user}:{pwd}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        headers.setdefault("User-Agent", "personal-brain/0.1 (+local-first)")
        headers.setdefault("Accept", "application/json")
        return headers

    def _sub(self, integ: Integration, value: str) -> str:
        def repl(m):
            token = m.group(1)
            if token.startswith("secret."):
                return self.resolve_secret({"$env": token.split(".", 1)[1]})
            return token
        return PLACEHOLDER.sub(repl, str(value))

    # ------------------------------------------------------------------ #
    def _check_egress_permission(self, ctx: Any, url: str, name: str,
                                 operation: str, dry_run: bool) -> Optional[Dict[str, Any]]:
        """Return a refusal dict if the user has not affirmed reaching this host.

        Fails closed: no broker in the session means no egress, mirroring the
        central tool gate. A dry run makes no request and so needs no grant.
        """
        if dry_run:
            return None
        broker = getattr(ctx, "permissions", None) if ctx is not None else None
        if broker is None:
            raise IntegrationError(
                f"integration '{name}.{operation}' needs the '{CAP_NET}' permission, "
                f"but no permission broker is available in this session")
        host = urllib.parse.urlparse(url).hostname or name
        # CONSUME, do not peek. `peek()` answers "would this be allowed" without
        # spending anything, so an affirmed one-shot survived every call that
        # used it: one approval authorised unlimited integrations calls (audit
        # B10, reproduced with a live loopback server). `consume()` burns the
        # approval at the gate, which is the only place that can do it safely.
        decision = broker.consume(CAP_NET, host,
                                  purpose=f"integration '{name}.{operation}'")
        if decision.allowed:
            return None
        # Nothing usable was consumed, so raise the prompt that the human can
        # affirm later. `request()` must not be able to hand back a one-shot
        # that was already spent: `consume()` above has burned it and returned.
        decision = broker.request(CAP_NET, host,
                                  purpose=f"integration '{name}.{operation}'",
                                  ask=False, tool=f"{name}.{operation}")
        if ctx is not None and getattr(ctx, "audit", None):
            ctx.audit("integration_egress_blocked",
                      {"integration": name, "operation": operation, "host": host,
                       "capability": CAP_NET, "reason": decision.reason})
        return {"ok": False, "error": "permission required",
                "capability": CAP_NET, "label": "reach a host", "scope": host,
                "reason": decision.reason, "prompt_id": decision.prompt_id,
                "needs_affirmation": True,
                "how_to_affirm": (f"brain permission affirm {decision.prompt_id}"
                                  if decision.prompt_id else ""),
                "how_to_grant": (f"brain permission grant {CAP_NET} --scope {host} "
                                 f"--purpose '{name}.{operation}'")}

    def _check_policy(self, integ: Integration, url: str, method: str) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("https", "http"):
            raise PolicyViolation(f"unsupported scheme: {parsed.scheme}")
        if parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"):
            raise PolicyViolation("plain http is only allowed for loopback integrations")
        host = parsed.hostname or ""
        if integ.allow_hosts:
            if not any(host == h or host.endswith("." + h) for h in integ.allow_hosts):
                raise PolicyViolation(f"host not allow-listed: {host}")
        if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            raise PolicyViolation(f"method not allowed: {method}")

    def _rate_limit(self, integ: Integration) -> None:
        bucket = self._buckets.setdefault(integ.name, RateBucket())
        cutoff = now_ms() - 60_000
        bucket.calls = [t for t in bucket.calls if t > cutoff]
        if len(bucket.calls) >= integ.rate_limit_per_minute:
            raise PolicyViolation(
                f"rate limit reached for {integ.name} ({integ.rate_limit_per_minute}/min)")
        bucket.calls.append(now_ms())

    # ------------------------------------------------------------------ #
    def call(self, name: str, operation: str, params: Optional[Dict[str, Any]] = None,
             ctx: Any = None, dry_run: bool = False) -> Dict[str, Any]:
        params = dict(params or {})
        integ = self.integrations.get(name)
        if integ is None:
            raise IntegrationError(
                f"integration '{name}' is not registered. Available: {self.names() or 'none'}")
        if not integ.enabled:
            raise IntegrationError(f"integration '{name}' is disabled")
        op = integ.operations.get(operation)
        if op is None:
            raise IntegrationError(
                f"operation '{operation}' is not registered for '{name}'. "
                f"Available: {sorted(integ.operations)}")
        for req in op.required:
            if req not in params or params[req] in (None, ""):
                raise IntegrationError(f"missing required parameter '{req}' for {name}.{operation}")

        # template the path/query/body from the *declared* operation spec only
        path = self._render(integ, op.path, params)
        query = {k: self._render(integ, str(v), params)
                 for k, v in (op.params.get("query") or {}).items() if k in params}
        body_tpl = op.params.get("json") or {}
        body = self._render_json(integ, body_tpl, params) if body_tpl else None
        url = integ.base_url + path
        if query:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)

        self._check_policy(integ, url, op.method)

        # Affirmative permission for network egress.
        #
        # `_check_policy` above says the call is *allowed by configuration*.
        # This asks whether Nila may actually reach this host right now. Without
        # it the `net.host` capability was declared but gated nothing: the model
        # supplies `params`, so a prompt-injected model could pass recalled
        # memory content out to any allow-listed host with no prompt at all.
        #
        # Enforced here rather than by `capability=CAP_NET` on the tool spec
        # because the central gate would scope the check to the integration
        # *name* while the real destination is a *host* - two scopes that could
        # never be satisfied by the same grant. The resolved host is the only
        # honest scope, and only the gateway knows it.
        blocked = self._check_egress_permission(ctx, url, name, operation, dry_run)
        if blocked is not None:
            return blocked

        self._rate_limit(integ)

        audit = {"integration": name, "operation": operation, "method": op.method,
                 "host": urllib.parse.urlparse(url).hostname,
                 "param_keys": sorted(params), "dry_run": dry_run}
        if ctx is not None and getattr(ctx, "audit", None):
            ctx.audit("integration_call", audit)

        if dry_run:
            return {"ok": True, "dry_run": True, "would_call": {"method": op.method, "url": url,
                                                                 "body_keys": sorted(body or {})}}

        headers = self._headers(integ)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=op.method)
        for k, v in headers.items():
            req.add_header(k, v)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        started = time.time()
        try:
            # A redirect must never carry an integration secret to a host that
            # was not allow-listed. Redirects are followed only when the call
            # has no credential attached, and then the destination is put
            # through the SAME policy check as the original URL.
            if headers:
                with open_once(req, timeout=integ.timeout) as resp:
                    raw = resp.read(MAX_RESPONSE_BYTES + 1)
                    status = resp.status
                    ctype = resp.headers.get("Content-Type", "")
            else:
                with open_follow(req, timeout=integ.timeout) as resp:
                    self._redirect_guard(integ, resp, url)
                    raw = resp.read(MAX_RESPONSE_BYTES + 1)
                    status = resp.status
                    ctype = resp.headers.get("Content-Type", "")
        except RedirectRefused as exc:
            if ctx is not None and getattr(ctx, "audit", None):
                ctx.audit("integration_redirect_refused",
                          {**audit, "status": exc.code, "location": str(exc.location)[:200]})
            raise IntegrationError(f"{name}.{operation}: {exc}") from exc
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode(errors="replace")[:400]
            except Exception:
                pass
            if ctx is not None and getattr(ctx, "audit", None):
                ctx.audit("integration_error", {**audit, "status": exc.code})
            raise IntegrationError(f"{name}.{operation} -> HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if ctx is not None and getattr(ctx, "audit", None):
                ctx.audit("integration_error", {**audit, "error": str(exc)})
            raise IntegrationError(f"{name}.{operation} unreachable: {exc}") from exc

        if len(raw) > MAX_RESPONSE_BYTES:
            raise IntegrationError(f"response larger than {MAX_RESPONSE_BYTES} bytes; refused")
        elapsed = round(time.time() - started, 3)
        if ctx is not None and getattr(ctx, "audit", None):
            ctx.audit("integration_ok", {**audit, "status": status, "seconds": elapsed,
                                         "bytes": len(raw),
                                         "digest": sha256_hex(raw)[:16]})
        if "json" in ctype:
            try:
                return {"ok": True, "status": status, "seconds": elapsed, "json": json.loads(raw)}
            except json.JSONDecodeError:
                pass
        return {"ok": True, "status": status, "seconds": elapsed,
                "text": raw.decode(errors="replace")[:20_000]}

    def _redirect_guard(self, integ: Integration, resp: Any, original: str) -> None:
        """Re-check policy on the URL a credential-free call actually landed on."""
        final = getattr(resp, "url", "") or getattr(resp, "geturl", lambda: "")()
        if not final or final == original:
            return
        self._check_policy(integ, final, "GET")
        host = urllib.parse.urlparse(final).hostname or ""
        if integ.allow_hosts and not any(host == h or host.endswith("." + h)
                                         for h in integ.allow_hosts):
            raise IntegrationError(f"redirected to a host that is not allow-listed: {host}")

    # ------------------------------------------------------------------ #
    def _render(self, integ: Integration, template: str, params: Dict[str, Any]) -> str:
        def repl(m):
            key = m.group(1)
            if key.startswith("param."):
                key = key.split(".", 1)[1]
            if key not in params:
                raise IntegrationError(f"template needs parameter '{key}' which was not supplied")
            return urllib.parse.quote(str(params[key]), safe="")
        out = PLACEHOLDER.sub(repl, str(template))
        return self._sub(integ, out)

    def _render_json(self, integ: Integration, template: Any, params: Dict[str, Any]) -> Any:
        if isinstance(template, dict):
            return {k: self._render_json(integ, v, params) for k, v in template.items()}
        if isinstance(template, list):
            return [self._render_json(integ, v, params) for v in template]
        if isinstance(template, str):
            if template.startswith("$param."):
                key = template.split(".", 1)[1]
                if key not in params:
                    raise IntegrationError(f"body needs parameter '{key}'")
                return params[key]
            return self._render(integ, template, params)
        return template
