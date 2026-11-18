"""LLM providers behind one interface.

The brain must work with no network and no API key, so ``LocalBrain`` is always
available as the zero-dependency fallback. Network providers are opt-in via
config/env, and the API key is fetched from the environment at call time (never
written to disk by this code).

Supported:
  * ``openai``   - any OpenAI-compatible /chat/completions endpoint
                   (OpenAI, OpenRouter, Groq, Together, vLLM, LM Studio, llama.cpp server)
  * ``anthropic``- /v1/messages
  * ``gemini``   - Google Gemini (generateContent REST API)
  * ``ollama``   - local model server (http://127.0.0.1:11434)
  * ``local``    - the offline rule engine (default)
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from ..util.http import RedirectRefused, open_follow, open_once
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DEFAULT_TIMEOUT = 30.0


class ProviderError(RuntimeError):
    """A provider call failed. ``status`` / ``retry_after`` let the router react
    to rate limits (429) instead of hammering a provider that said stop."""

    def __init__(self, message: str, status: Optional[int] = None,
                 retry_after: Optional[int] = None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


@dataclass
class Completion:
    text: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    provider: str = ""


def _post_json(url: str, payload: Dict[str, Any], headers: Dict[str, str],
               timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    req.add_header("Content-Type", "application/json")
    try:
        # open_once: this request carries an API key, so a 3xx is refused
        # instead of being followed to a host nobody approved.
        with open_once(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except RedirectRefused as exc:
        # A 3xx on a credentialed request: refuse loudly rather than treat it
        # as a provider error the caller might retry against the new host.
        raise ProviderError(str(exc), status=exc.code) from exc
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode()[:500]
        except Exception:
            pass
        retry = None
        try:
            retry = int(exc.headers.get("Retry-After", "")) if exc.headers else None
        except (TypeError, ValueError):
            retry = None
        # the URL is deliberately not echoed with query strings: keys never ride in URLs here,
        # but an error message must never be a place a credential could appear.
        raise ProviderError(f"{url.split('?')[0]} -> HTTP {exc.code}: {body}",
                            status=exc.code, retry_after=retry) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"cannot reach {url}: {exc.reason}") from exc


class LLMProvider:
    name = "abstract"
    needs_network = True

    def available(self) -> bool:  # pragma: no cover
        return False

    def complete(self, system: str, messages: List[Dict[str, str]],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tools_mode: str = "auto") -> Completion:  # pragma: no cover
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini",
                 base_url: str = "https://api.openai.com/v1", temperature: float = 0.2,
                 timeout: float = DEFAULT_TIMEOUT, name: str = "openai"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.name = name

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, messages: List[Dict[str, str]],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tools_mode: str = "auto") -> Completion:
        payload: Dict[str, Any] = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [{"role": "system", "content": system}] + messages,
        }
        native = tools and tools_mode in ("auto", "native")
        if native:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        elif tools and tools_mode == "prompt":
            payload["messages"][0]["content"] += "\n\n" + _tool_prompt(tools)
        raw = _post_json(f"{self.base_url}/chat/completions", payload,
                         {"Authorization": f"Bearer {self.api_key}"}, self.timeout)
        try:
            choice = raw["choices"][0]["message"]
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"unexpected response shape: {str(raw)[:300]}") from exc
        calls: List[Dict[str, Any]] = []
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append({"id": tc.get("id", ""), "name": fn.get("name", ""), "args": args})
        text = choice.get("content") or ""
        if not calls and text:
            calls = parse_tool_json(text)
        return Completion(text=text, tool_calls=calls, model=raw.get("model", self.model),
                          usage=raw.get("usage", {}), provider=self.name)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-5",
                 base_url: str = "https://api.anthropic.com", temperature: float = 0.2,
                 timeout: float = DEFAULT_TIMEOUT, version: str = "2023-06-01"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.version = version

    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, system: str, messages: List[Dict[str, str]],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tools_mode: str = "auto") -> Completion:
        payload: Dict[str, Any] = {
            "model": self.model, "max_tokens": 2048, "temperature": self.temperature,
            "system": system, "messages": messages,
        }
        if tools and tools_mode in ("auto", "native"):
            payload["tools"] = [{"name": t["function"]["name"],
                                 "description": t["function"]["description"],
                                 "input_schema": t["function"]["parameters"]} for t in tools]
        elif tools and tools_mode == "prompt":
            payload["system"] += "\n\n" + _tool_prompt(tools)
        raw = _post_json(f"{self.base_url}/v1/messages", payload,
                         {"x-api-key": self.api_key, "anthropic-version": self.version},
                         self.timeout)
        text = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
        calls = []
        for b in raw.get("content", []):
            if b.get("type") == "tool_use":
                calls.append({"id": b.get("id", ""), "name": b.get("name", ""),
                              "args": b.get("input", {}) or {}})
        if not calls and text:
            calls = parse_tool_json(text)
        return Completion(text=text, tool_calls=calls, model=raw.get("model", self.model),
                          usage=raw.get("usage", {}), provider=self.name)


def is_loopback_url(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url or "").hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


#: Hosts a credential may be sent to. A config file can be edited by any program
#: running as you, so the key must never follow a redirected ``base_url`` to an
#: arbitrary server. Loopback is allowed so tests and local gateways still work.
_GEMINI_HOSTS = ("generativelanguage.googleapis.com",)


class GeminiProvider(LLMProvider):
    """Google Gemini via the Generative Language REST API.

    The key travels in the ``x-goog-api-key`` header (never in the URL, so it
    cannot leak through logs or error messages). Tools are used in prompt mode
    (a JSON block), which behaves the same across Gemini model versions.
    """
    name = "gemini"

    def __init__(self, api_key: str, model: str = "gemini-flash-latest",
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 temperature: float = 0.2, timeout: float = DEFAULT_TIMEOUT,
                 max_output_tokens: int = 1024):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        #: Filled in by `pick_model`; empty means "not asked / could not ask".
        self.models_seen: List[str] = []
        self.models_guessed = False

    def base_url_ok(self) -> bool:
        from urllib.parse import urlparse
        u = urlparse(self.base_url)
        return (u.scheme == "https" and (u.hostname or "").lower() in _GEMINI_HOSTS) \
            or (u.scheme in ("http", "https") and is_loopback_url(self.base_url))

    def available(self) -> bool:
        return bool(self.api_key) and self.base_url_ok()

    def list_models(self, timeout: float = 8.0) -> List[str]:
        """The model names this key may use, asked of the API, not guessed.

        Returns bare names (``gemini-2.5-flash``); any ``models/`` prefix the API
        uses is stripped so a name can go straight into the request path. An
        unreachable API yields an empty list, and the caller then says so rather
        than guessing again.
        """
        if not self.base_url_ok() or not self.api_key:
            return []
        req = urllib.request.Request(f"{self.base_url}/models?pageSize=200",
                                     method="GET")
        req.add_header("x-goog-api-key", self.api_key)
        try:
            with open_once(req, timeout=timeout) as resp:
                raw = json.loads(resp.read().decode() or "{}")
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
            return []
        names: List[str] = []
        for row in raw.get("models") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").split("/")[-1].strip()
            if not name:
                continue
            methods = row.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue                      # embeddings-only, not a chat model
            if "embedding" in name.lower() or "aqa" in name.lower():
                continue                      # not a chat model by name either
            names.append(name)
        return names

    def pick_model(self, candidates: Optional[List[str]] = None) -> str:
        """The best model this key can actually use, or the configured one.

        Order: the configured model if the API lists it, then the preferred
        list, then the first listed chat model. If the API cannot be reached the
        configured name is returned unchanged - the honest fallback, and the
        reason the error message is not a guess.
        """
        listed = self.list_models()
        self.models_seen = listed
        if not listed:
            return self.model                     # cannot ask: keep the setting
        for want in [self.model, *(candidates or GEMINI_PREFERRED)]:
            if want and want in listed:
                return want
        for want in (candidates or GEMINI_PREFERRED):
            if want in listed:
                return want
        return listed[0]

    def complete(self, system: str, messages: List[Dict[str, str]],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tools_mode: str = "auto") -> Completion:
        if not self.base_url_ok():
            raise ProviderError("refusing to send the Gemini key to an unexpected address")
        # A 404 on the model name is a naming problem, not a request problem, so
        # it is handled here once: ask what exists, retry with a real name, and
        # if even that fails say which names were offered.
        try:
            return self._complete_once(system, messages, tools)
        except ProviderError as exc:
            if getattr(exc, "status", None) != 404:
                raise
            wanted = self.model
            better = self.pick_model()
            if not better or better == wanted:
                raise ProviderError(
                    "Gemini does not offer the model "
                    f"'{wanted}' for this key. Available: "
                    + (", ".join(self.models_seen[:8]) or "the list could not be read")
                ) from exc
            self.model = better
            return self._complete_once(system, messages, tools)

    def _complete_once(self, system: str, messages: List[Dict[str, str]],
                       tools: Optional[List[Dict[str, Any]]]) -> Completion:
        sys_text = system + ("\n\n" + _tool_prompt(tools) if tools else "")
        contents = [{"role": "user" if m.get("role") == "user" else "model",
                     "parts": [{"text": m.get("content", "")}]} for m in messages]
        payload = {"systemInstruction": {"parts": [{"text": sys_text}]},
                   "contents": contents,
                   "generationConfig": {"temperature": self.temperature,
                                        "maxOutputTokens": self.max_output_tokens}}
        raw = _post_json(f"{self.base_url}/models/{self.model}:generateContent", payload,
                         {"x-goog-api-key": self.api_key}, self.timeout)
        try:
            parts = raw["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            reason = ""
            try:
                reason = raw.get("promptFeedback", {}).get("blockReason", "")
            except Exception:
                pass
            raise ProviderError("Gemini returned no answer"
                                + (f" (blocked: {reason})" if reason else "")) from exc
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        calls = parse_tool_json(text) if tools else []
        meta = raw.get("usageMetadata", {}) or {}
        return Completion(text=text, tool_calls=calls, model=self.model,
                          usage={"total_tokens": meta.get("totalTokenCount", 0),
                                 "prompt_tokens": meta.get("promptTokenCount", 0)},
                          provider=self.name)


#: Gemini model names change between accounts and regions ("gemini-flash-latest"
#: works for some keys and 404s for others). Guessing a name was the bug; asking
#: the API which models the key can actually use is the fix. The list is fetched
#: once per provider instance and cached, so a normal turn pays nothing.
GEMINI_PREFERRED = (
    "gemini-flash-latest", "gemini-2.5-flash", "gemini-2.0-flash",
    "gemini-1.5-flash", "gemini-1.5-flash-latest",
)


class OllamaProvider(LLMProvider):
    name = "ollama"
    # The transport is HTTP, but the router enforces loopback-only for the
    # local lane. It is not cloud egress and must retain local context/tools.
    needs_network = False

    def __init__(self, model: str = "llama3.2", base_url: str = "http://127.0.0.1:11434",
                 temperature: float = 0.2, timeout: float = 120.0):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout = timeout
        self.api_key = ""

    def available(self) -> bool:
        try:
            with open_follow(urllib.request.Request(f"{self.base_url}/api/tags"), timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def complete(self, system: str, messages: List[Dict[str, str]],
                 tools: Optional[List[Dict[str, Any]]] = None,
                 tools_mode: str = "auto") -> Completion:
        payload: Dict[str, Any] = {
            "model": self.model, "stream": False,
            "options": {"temperature": self.temperature},
            "messages": [{"role": "system", "content": system}] + messages,
        }
        if tools and tools_mode in ("auto", "native"):
            payload["tools"] = tools
        elif tools and tools_mode == "prompt":
            payload["messages"][0]["content"] += "\n\n" + _tool_prompt(tools)
        raw = _post_json(f"{self.base_url}/api/chat", payload, {}, self.timeout)
        msg = raw.get("message", {}) or {}
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append({"id": "", "name": fn.get("name", ""), "args": fn.get("arguments", {}) or {}})
        text = msg.get("content", "") or ""
        if not calls and text:
            calls = parse_tool_json(text)
        return Completion(text=text, tool_calls=calls, model=raw.get("model", self.model),
                          usage={}, provider=self.name)


# --------------------------------------------------------------------------- #
def _tool_prompt(tools: List[Dict[str, Any]]) -> str:
    lines = ["You can call tools by replying with ONLY a JSON block like:",
             '```json', '{"tool": "name", "args": {...}}', '```', "", "Tools:"]
    for t in tools:
        fn = t.get("function", t)
        props = fn.get("parameters", {}).get("properties", {})
        req = set(fn.get("parameters", {}).get("required", []))
        sig = ", ".join(f"{k}{'*' if k in req else ''}" for k in props)
        lines.append(f"- {fn.get('name')}({sig}): {fn.get('description', '')}")
    lines.append("\nAnswer in prose when no tool is needed. Never invent tool names.")
    return "\n".join(lines)


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_tool_json(text: str) -> List[Dict[str, Any]]:
    """Extract ``{"tool": ..., "args": ...}`` calls from free-form model output."""
    if not text:
        return []
    candidates: List[str] = []
    candidates += _JSON_BLOCK.findall(text)
    stripped = text.strip()
    if stripped.startswith("{"):
        candidates.append(stripped)
    for m in re.finditer(r"\{[^{}]*\"tool\"[^{}]*\}", text, re.S):
        candidates.append(m.group(0))
    out: List[Dict[str, Any]] = []
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("tool"):
            args = obj.get("args") or obj.get("arguments") or obj.get("parameters") or {}
            if not isinstance(args, dict):
                args = {"_raw": args}
            out.append({"id": obj.get("id", ""), "name": str(obj["tool"]), "args": args})
    seen, deduped = set(), []
    for call in out:
        key = (call["name"], json.dumps(call["args"], sort_keys=True))
        if key not in seen:
            seen.add(key)
            deduped.append(call)
    return deduped


def build_provider(cfg: Dict[str, Any]) -> LLMProvider:
    """Factory from ``Config.provider_config()``. Falls back to LocalBrain."""
    kind = str(cfg.get("type") or "local").lower()
    # A key may arrive IN the config (the connectors screen resolves it from the
    # sealed vault); otherwise the environment is the fallback. This is the only
    # place a provider is constructed from a config, so both lanes agree.
    inline_key = str(cfg.get("api_key") or "")
    model = cfg.get("model")
    temp = float(cfg.get("temperature", 0.2))
    timeout = float(cfg.get("timeout", DEFAULT_TIMEOUT))
    base = cfg.get("base_url")

    if kind in ("openai", "openai-compatible", "custom"):
        key_env = cfg.get("api_key_env") or "OPENAI_API_KEY"
        return OpenAICompatibleProvider(
            api_key=inline_key or os.environ.get(key_env, ""), model=model or "gpt-4o-mini",
            base_url=base or "https://api.openai.com/v1", temperature=temp, timeout=timeout)
    if kind == "gemini":
        key_env = cfg.get("api_key_env") or "GEMINI_API_KEY"
        return GeminiProvider(
            api_key=inline_key or os.environ.get(key_env, ""), model=model or "gemini-flash-latest",
            base_url=base or "https://generativelanguage.googleapis.com/v1beta",
            temperature=temp, timeout=timeout)
    if kind == "anthropic":
        key_env = cfg.get("api_key_env") or "ANTHROPIC_API_KEY"
        return AnthropicProvider(
            api_key=inline_key or os.environ.get(key_env, ""), model=model or "claude-sonnet-4-5",
            base_url=base or "https://api.anthropic.com", temperature=temp, timeout=timeout)
    if kind == "ollama":
        return OllamaProvider(model=model or "llama3.2",
                              base_url=base or "http://127.0.0.1:11434",
                              temperature=temp, timeout=timeout)
    from .local_brain import LocalBrain

    return LocalBrain()
