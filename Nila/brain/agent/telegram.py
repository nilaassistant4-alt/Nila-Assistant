"""Telegram: Nila writes to your phone, and to nobody else.

The owner's request was plain - "whatever it is, an update or a problem, I want it
on Telegram" - and it hides three decisions that matter more than the code:

**One chat, and it is sealed.** A bot token alone can only message a chat that has
already spoken to the bot, so the chat id is fixed once, in the sealed vault, and
every send goes to that id. There is no "send to whoever asked", no group, and no
broadcast list. This module is the *outbound* half; `brain.agent.phone` is the
inbound half, and it also answers only that same chat id.

**The token is treated like the passphrase.** It lives in the vault, never in
config.json, never in a log and never in an error message: `masked()` is the only
way it leaves this module, and every request goes to `api.telegram.org` - a token
posted to any other host is a token given away.

**A message that cannot be sent is not lost silently.** Phone off, no network,
Telegram down: the message is queued in the sealed vault (encrypted, capped, oldest
first) and `flush()` resends it when a connection exists. A failed notification
that vanishes is worse than a late one.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

HOST = "api.telegram.org"
BASE = f"https://{HOST}/bot"
#: Telegram itself rejects messages over 4096 characters; splitting is done here so
#: a long report arrives in order rather than as "Bad Request: message is too long".
MAX_CHARS = 3800
#: Bounded queue: a phone that is off for a week must not turn the vault into an
#: unbounded archive of old notifications.
MAX_QUEUED = 50
META_TOKEN = "telegram_token"
META_CHAT = "telegram_chat_id"
META_QUEUE = "telegram_outbox"
META_NOTIFY = "telegram_notify"
#: What Nila is allowed to push to the phone. All on by default once a chat is
#: set, because the owner asked for "any update, any problem"; each one can be
#: turned off without losing the others.
NOTIFY_KINDS = {
    "reminders": "a reminder that came due while Nila was closed",
    "errors": "something Nila could not do (a failed action, a missing key)",
    "updates": "a short note after Nila starts, and after a new version is unpacked",
    "reports": "an issue report, when one is written",
}


class TelegramError(RuntimeError):
    """A send failed, with a sentence the owner can act on."""


def masked(token: str) -> str:
    """The only shape a token may take in output, logs or audit entries."""
    text = str(token or "")
    if len(text) < 10:
        return "not set" if not text else "…"
    return f"{text[:6]}…{text[-4:]} ({len(text)} chars)"


def split_text(text: str, limit: int = MAX_CHARS) -> List[str]:
    """Break a long message on a line, then on a word, never mid-character."""
    clean = str(text or "").strip()
    if not clean:
        return []
    if len(clean) <= limit:
        return [clean]
    parts: List[str] = []
    rest = clean
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return parts


class Telegram:
    """One bot, one chat, one direction - with a queue for when the phone is off."""

    def __init__(self, vault: Any = None, timeout: float = 15.0,
                 opener: Optional[Any] = None):
        self.vault = vault
        self.timeout = timeout
        #: injected in tests; the real one is urllib's, wrapped in `open_once`
        self._opener = opener
        self.token = self._meta(META_TOKEN, "")
        self.chat_id = str(self._meta(META_CHAT, "") or "")

    # -- the sealed settings -------------------------------------------- #
    def _meta(self, key: str, default: Any = None) -> Any:
        if self.vault is None:
            return default
        try:
            return self.vault.meta_get(key, default)
        except Exception:
            return default

    def _set_meta(self, key: str, value: Any) -> None:
        if self.vault is None:
            raise TelegramError("the token must be stored in the vault - unlock it first")
        self.vault.meta_set(key, value)

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def status(self) -> Dict[str, Any]:
        #: The reader is a separate process (`brain telegram listen`); the status
        #: says whether the bridge is *set up* for it, and how far it has read.
        offset = 0
        try:
            offset = int(self._meta("telegram_offset", 0) or 0)
        except (TypeError, ValueError):
            offset = 0
        return {"ok": True, "configured": self.configured,
                "token": masked(self.token), "chat_id": self.chat_id or "not set",
                "queued": len(self.pending()),
                "host": HOST,
                "one_way": False,
                "reads_replies": True,
                "last_update_id": offset,
                "reader": ("nila-phone.bat" if offset else "not started yet"),
                "note": (\
                    "Nila sends to the one chat you chose, and reads only that chat's "
                    "messages while `brain telegram listen` is running. Anything from "
                    "another chat is dropped, counted and audited - never answered."),
                "how": _how_text()}

    def set_token(self, token: str) -> Dict[str, Any]:
        clean = str(token or "").strip()
        if not clean:
            raise TelegramError("paste the token BotFather gave you")
        if ":" not in clean or not clean.split(":")[0].isdigit():
            raise TelegramError("that does not look like a bot token "
                                "(BotFather gives you 123456789:ABC-DEF…)")
        self._set_meta(META_TOKEN, clean)
        self.token = clean
        return {"ok": True, "token": masked(clean), "configured": self.configured}

    def set_chat(self, chat_id: str | int) -> Dict[str, Any]:
        clean = str(chat_id or "").strip()
        if not clean:
            raise TelegramError("which chat? send /start to your bot, then copy the "
                                "chat id from `brain telegram whoami`")
        if not (clean.lstrip("-").isdigit()):
            raise TelegramError(f"'{clean}' is not a chat id - it is a number")
        self._set_meta(META_CHAT, clean)
        self.chat_id = clean
        return {"ok": True, "chat_id": clean, "configured": self.configured}

    def clear(self) -> Dict[str, Any]:
        self._set_meta(META_TOKEN, "")
        self._set_meta(META_CHAT, "")
        self.token, self.chat_id = "", ""
        return {"ok": True, "configured": False}

    # -- talking to Telegram -------------------------------------------- #
    def _url(self, method: str) -> str:
        return f"{BASE}{self.token}/{method}"

    def _check_host(self) -> None:
        # A token in a URL is only safe while the host is the one it belongs to.
        parsed = urllib.parse.urlparse(self._url("getMe"))
        if parsed.scheme != "https" or parsed.hostname != HOST:
            raise TelegramError(f"refusing to send the bot token anywhere but {HOST}")

    def _post(self, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.token:
            raise TelegramError("no bot token yet - `brain telegram token <token>`")
        self._check_host()
        data = urllib.parse.urlencode(payload).encode()
        request = urllib.request.Request(self._url(method), data=data, method="POST")
        opener = self._opener
        try:
            if opener is not None:
                # a test opener returns the JSON body directly
                raw = opener(request, self.timeout)
                return json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
            from ..util.http import open_once

            with open_once(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            body = ""
            try:
                body = exc.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            # Telegram puts the reason in the body; the URL (and the token in it)
            # is deliberately not part of this message.
            raise TelegramError(f"Telegram refused the message: HTTP {exc.code} "
                                f"{body}".strip()) from exc
        except urllib.error.URLError as exc:
            raise TelegramError(f"cannot reach {HOST} right now: {exc.reason}") from exc
        except TimeoutError as exc:
            raise TelegramError(f"{HOST} did not answer in {self.timeout:.0f}s") from exc

    def whoami(self) -> Dict[str, Any]:
        """Which chats have spoken to this bot? That is how the id is found.

        Telegram will not let a bot message first, so the owner presses Start and
        this reads the update the bot received. The *last* chat is the one offered,
        and nothing is stored until `set_chat` is called.
        """
        raw = self._post("getUpdates", {"limit": 20, "timeout": 0})
        chats: List[Dict[str, Any]] = []
        for update in (raw or {}).get("result") or []:
            message = update.get("message") or update.get("edited_message") or {}
            chat = message.get("chat") or {}
            if not chat.get("id"):
                continue
            row = {"chat_id": str(chat.get("id")),
                   "name": " ".join(x for x in (chat.get("first_name"),
                                                chat.get("last_name")) if x),
                   "username": chat.get("username", ""),
                   "type": chat.get("type", ""),
                   "text": str(message.get("text") or "")[:40]}
            if row not in chats:
                chats.append(row)
        return {"ok": True, "chats": chats,
                "hint": (f"then: brain telegram chat {chats[-1]['chat_id']}"
                         if chats else
                         "no message yet - open your bot in Telegram and press Start, "
                         "then run this again")}

    def me(self) -> Dict[str, Any]:
        """Which bot is this token? Never returns the token itself."""
        if not self.token:
            raise TelegramError("no bot token yet - `brain telegram token <token>`")
        raw = self._post("getMe", {})
        bot = (raw or {}).get("result") or {}
        return {"ok": bool((raw or {}).get("ok")), "username": bot.get("username", ""),
                "name": bot.get("first_name", ""), "id": bot.get("id", ""),
                "token": masked(self.token)}

    # -- what may be pushed, and the one call the rest of the code uses --- #
    def notify_settings(self) -> Dict[str, bool]:
        raw = self._meta(META_NOTIFY, "{}")
        try:
            rows = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            rows = {}
        return {kind: bool(rows.get(kind, True)) for kind in NOTIFY_KINDS}

    def set_notify(self, kind: str, on: bool) -> Dict[str, Any]:
        if kind not in NOTIFY_KINDS:
            raise TelegramError(f"unknown kind '{kind}'. Known: "
                                f"{', '.join(sorted(NOTIFY_KINDS))}")
        rows = self.notify_settings()
        rows[kind] = bool(on)
        self._set_meta(META_NOTIFY, json.dumps(rows))
        return {"ok": True, "kind": kind, "on": bool(on), "settings": rows}

    def notify(self, text: str, kind: str = "updates",
               urgent: bool = False) -> Dict[str, Any]:
        """The single entry point every other part of Nila uses.

        It never raises: a notification is a courtesy, and a courtesy must not
        break the action it is reporting. The result says what happened, so a
        caller that wants to show it can.
        """
        try:
            if not self.configured:
                return {"ok": False, "skipped": "telegram is not set up"}
            if not self.notify_settings().get(kind, True):
                return {"ok": False, "skipped": f"'{kind}' notifications are off"}
            prefix = "Nila (urgent)" if urgent else "Nila"
            result = self.send(f"{prefix}: {text}", kind=kind)
            result["kind"] = kind
            return result
        except Exception as exc:                  # never break the caller
            return {"ok": False, "error": str(exc)[:200]}

    # -- sending -------------------------------------------------------- #
    def send(self, text: str, kind: str = "message",
             extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send now; if it cannot be sent, queue it and say that plainly."""
        chunks = split_text(text)
        if not chunks:
            raise TelegramError("there is nothing to send")
        if not self.chat_id:
            raise TelegramError("no chat chosen yet - `brain telegram chat <id>` "
                                "(send /start to the bot first)")
        sent, errors = 0, []
        for chunk in chunks:
            payload = {"chat_id": self.chat_id, "text": chunk,
                       "disable_web_page_preview": "true"}
            try:
                raw = self._post("sendMessage", payload)
                if not (raw or {}).get("ok"):
                    errors.append(str((raw or {}).get("description") or "not ok"))
                else:
                    sent += 1
            except TelegramError as exc:
                errors.append(str(exc))
                break                       # one network failure, not five
        if errors:
            queued = self.enqueue(text, kind=kind, extra=extra)
            return {"ok": False, "sent": sent, "queued": queued,
                    "error": errors[0],
                    "note": ("Telegram is unreachable, so the message is queued in "
                             "the vault and will go out on `brain telegram flush`")}
        drained = 0
        if self.pending():
            # A working connection is the moment to deliver what was queued.
            try:
                drained = int(self.flush().get("sent", 0))
            except TelegramError:
                drained = 0
        return {"ok": True, "sent": sent, "parts": len(chunks),
                "chat_id": self.chat_id, "kind": kind, "drained": drained}

    # -- the queue ------------------------------------------------------ #
    def pending(self) -> List[Dict[str, Any]]:
        raw = self._meta(META_QUEUE, "[]")
        try:
            rows = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except (TypeError, ValueError):
            rows = []
        return [r for r in rows if isinstance(r, dict)]

    def enqueue(self, text: str, kind: str = "message",
                extra: Optional[Dict[str, Any]] = None) -> int:
        rows = self.pending()
        rows.append({"text": str(text)[:8000], "kind": kind,
                     "at": int(time.time()), "extra": extra or {}})
        rows = rows[-MAX_QUEUED:]
        self._set_meta(META_QUEUE, json.dumps(rows))
        return len(rows)

    def flush(self, limit: int = 10) -> Dict[str, Any]:
        """Try to deliver what is queued. Keeps whatever still cannot be sent."""
        rows = self.pending()
        if not rows:
            return {"ok": True, "sent": 0, "left": 0}
        if not self.chat_id:
            raise TelegramError("no chat chosen yet - the queue stays sealed")
        sent, left = 0, []
        for row in rows[:limit]:
            try:
                raw = self._post("sendMessage", {"chat_id": self.chat_id,
                                                 "text": split_text(row["text"])[0],
                                                 "disable_web_page_preview": "true"})
                if (raw or {}).get("ok"):
                    sent += 1
                else:
                    left.append(row)
            except TelegramError:
                left.append(row)
        left.extend(rows[limit:])
        self._set_meta(META_QUEUE, json.dumps(left[-MAX_QUEUED:]))
        return {"ok": True, "sent": sent, "left": len(left)}

    def drop_queue(self) -> Dict[str, Any]:
        self._set_meta(META_QUEUE, "[]")
        return {"ok": True, "queued": 0}


def _how_text() -> str:
    return (
        "How to set it up (5 minutes, free):\n"
        "  1. In Telegram, search @BotFather, send /newbot, pick a name. It answers\n"
        "     with a token like 123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ.\n"
        "     `brain telegram token 123456789:ABC…`\n"
        "  2. Open your new bot's chat and press Start (send /start). A bot cannot\n"
        "     message first - that is Telegram's rule, not Nila's.\n"
        "  3. `brain telegram whoami` prints your chat id (a number, like\n"
        "     987654321 or -100…). Then `brain telegram chat 987654321`.\n"
        "  4. `brain telegram test` - a real message should arrive on your phone.\n"
        "  5. To answer from your phone, leave the phone window open: double-click\n"
        "     `nila-phone.bat`, or run `brain telegram listen`. Then send /help to\n"
        "     the bot and it replies with what it can do.\n"
        "The number question: a Telegram account is tied to a phone number, but the\n"
        "bot needs only the *chat id*, and that is what Nila stores. Nila never\n"
        "sees, asks for, or stores your phone number, and never your login."
    )
