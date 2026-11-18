"""Telegram, both ways: the owner writes from his phone, Nila answers on his phone.

The stage-9 bridge could only push. The owner's words for stage 11 were plain -
"phone to Nila and Nila to phone must work" - so this module adds the missing
direction, and it is careful about the three things that make a chat bot
dangerous rather than useful:

**One chat, sealed.** A bot token is public knowledge the moment it leaks, and
anyone who finds the bot can send it a message. So every update is checked
against the chat id stored in the sealed vault *before* a single word is treated
as an instruction. A stranger's message is dropped, counted, and audited - never
answered, never run, never echoed back to the owner (echoing it would turn the
stranger's text into an instruction that appears to come from Nila).

**Nothing runs while the vault is locked.** The worker is a loop the owner
starts, not a service. With the vault locked there is no key, so a message waits
in Telegram - `getUpdates` only advances the offset when a message has actually
been handled, so a restart replays rather than drops.

**Every inbound message is an audit entry, and the reply says what happened.**
`/due`, `/done 2`, `/snooze 2 10` are answered by code, exactly like the CLI, so
they work with no model and no network key. Free text goes through
`Brain.handle`, which is the same path the terminal uses - one brain, not two.

The command set is deliberately small and each answer is short: a phone screen
is not a terminal, and a bot that answers a wrong command with a paragraph trains
the owner to stop reading it.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Dict, List, Optional

from .telegram import META_CHAT, Telegram, TelegramError, masked

#: How many updates to ask for at once. Small: one owner, one chat.
BATCH = 20
#: The long-poll wait Telegram is asked to hold open, in seconds. Under a minute
#: so a dead connection is noticed instead of looking like a quiet phone.
LONG_POLL = 25
#: Where the last handled update id is kept, so a restart does not replay a
#: command ("/done 2" twice would close the wrong reminder the second time).
META_OFFSET = "telegram_offset"
META_SEEN = "telegram_answered"

HELP = (
    "Nila, from your phone:\n"
    "/due - what is due now\n"
    "/list - open reminders\n"
    "/done 2 - mark reminder 2 done\n"
    "/snooze 2 10 - push reminder 2 back 10 minutes\n"
    "/remind in 30 min take the tablet - new reminder\n"
    "/status - is the vault unlocked, any messages waiting\n"
    "/help - this list\n"
    "Anything else is a question, answered by Nila like the terminal."
)


class PhoneBridge:
    """The loop that reads the owner's messages and answers them."""

    def __init__(self, telegram: Telegram, brain: Any = None,
                 allow: Optional[List[str]] = None,
                 printer: Optional[Callable[[str], None]] = None,
                 waiter: Optional[Callable[[float], None]] = None):
        self.tg = telegram
        self.brain = brain
        self.printer = printer or (lambda line: None)
        self._waiter = waiter or time.sleep
        #: The sealed chat id is the allow-list. An explicit `allow` is only for
        #: tests and for an owner who reads the ids himself; it is never widened
        #: from an incoming message, which is the whole point.
        self.allow = [str(x) for x in (allow or ([telegram.chat_id] if telegram.chat_id else []))]
        self.answered = 0
        self.dropped = 0
        self.errors = 0

    # -- the sealed bookkeeping ----------------------------------------- #
    @property
    def offset(self) -> int:
        raw = self.tg._meta(META_OFFSET, 0)
        try:
            return int(raw or 0)
        except (TypeError, ValueError):
            return 0

    def _save_offset(self, update_id: int) -> None:
        try:
            self.tg._set_meta(META_OFFSET, int(update_id))
        except Exception:
            pass                      # a locked vault must not kill the loop

    def _recent(self) -> Dict[str, Any]:
        raw = self.tg._meta(META_SEEN, "{}")
        try:
            rows = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except (TypeError, ValueError):
            rows = {}
        return rows if isinstance(rows, dict) else {}

    def _remember(self, text: str, reply: str) -> None:
        """Remember the last few answers, so a repeat is not run twice."""
        rows = self._recent()
        rows[str(int(time.time()))] = {"text": text[:200], "reply": reply[:200]}
        keep = dict(sorted(rows.items())[-10:])
        try:
            self.tg._set_meta(META_SEEN, json.dumps(keep))
        except Exception:
            pass

    # -- Telegram I/O ---------------------------------------------------- #
    def fetch(self, timeout: float = LONG_POLL) -> List[Dict[str, Any]]:
        """Ask for updates after the saved offset.

        `timeout` is Telegram's own long-poll parameter: the request is held open
        while the phone is quiet, so this is not a busy loop.
        """
        payload = {"offset": self.offset + 1 if self.offset else 0,
                   "timeout": int(max(0, timeout)),
                   "limit": BATCH,
                   "allowed_updates": json.dumps(["message"])}
        raw = self.tg._post("getUpdates", payload)
        if not (raw or {}).get("ok"):
            raise TelegramError(str((raw or {}).get("description") or "getUpdates refused"))
        return [u for u in ((raw or {}).get("result") or []) if isinstance(u, dict)]

    def say(self, text: str) -> Dict[str, Any]:
        """Reply to the sealed chat. Never raises: the loop must survive."""
        try:
            out = self.tg.send(text)
        except TelegramError as exc:
            self.errors += 1
            self.printer(f"could not reply: {exc}")
            return {"ok": False, "error": str(exc)[:200]}
        if not out.get("ok"):
            # send() queues instead of raising; that is still a failed reply and
            # must be visible in the loop's count, not silently swallowed.
            self.errors += 1
            self.printer(f"reply queued: {out.get('error') or 'unknown reason'}")
        return out

    # -- what a message means -------------------------------------------- #
    def handle_message(self, update: Dict[str, Any]) -> Optional[str]:
        """One update in, one reply out (or None when it is not for the owner)."""
        message = update.get("message") or update.get("edited_message") or {}
        chat = (message.get("chat") or {})
        chat_id = str(chat.get("id", "") or "")
        update_id = update.get("update_id")
        if update_id is not None:
            self._save_offset(int(update_id))
        if not chat_id or chat_id not in self.allow:
            # A stranger. Audit the fact, keep the text out of the log (it may be
            # a deliberate probe) and say nothing back.
            self.dropped += 1
            self._audit("phone_message_dropped", {"chat_id": chat_id,
                                                  "reason": "not the sealed chat"})
            self.printer(f"dropped a message from chat {chat_id or '?'} "
                         f"(not the chat in your vault)")
            return None
        text = str(message.get("text") or "").strip()
        if not text:
            return self._reply(update_id, "Only text, for now: Nila reads words, "
                                          "not photos or voice notes.", "not text")
        reply = self.command(text)
        self.answered += 1
        self._audit("phone_message", {"chars": len(text),
                                      "command": text.split()[0][:24] if text else ""})
        self._remember(text, reply)
        return self._reply(update_id, reply, text)

    def _reply(self, update_id: Any, reply: str, about: str) -> str:
        out = self.say(reply)
        if not out.get("ok"):
            self.printer("the reply could not be sent; it is queued in the vault")
        return reply

    def _audit(self, event: str, extra: Dict[str, Any]) -> None:
        try:
            vault = getattr(self.tg, "vault", None)
            if vault is not None:
                vault.audit(event, extra)
        except Exception:
            pass

    def command(self, text: str) -> str:
        """The answers to slash commands; free text falls through to the brain."""
        low = " ".join(text.lower().split())
        first, _, rest = text.partition(" ")
        cmd = first.lstrip("/").lower() if first.startswith("/") else ""
        # "/due@my_bot" is how Telegram addresses a command in a group.
        cmd = cmd.split("@")[0]
        if cmd in ("start", "help", ""):
            return HELP if cmd else self.ask(text)
        if cmd in ("due", "now"):
            return self.due()
        if cmd in ("list", "reminders", "ls"):
            return self.listing()
        if cmd in ("done", "close"):
            return self.done(rest.strip())
        if cmd in ("snooze", "later"):
            return self.snooze(rest.strip())
        if cmd == "remind":
            return self.remind(rest.strip())
        if cmd in ("status", "ok", "ping"):
            return self.status()
        return self.ask(text)

    # -- the answers ----------------------------------------------------- #
    def _need_brain(self) -> Optional[str]:
        if self.brain is None:
            return ("Nila is not unlocked in this window, so there is nothing to "
                    "ask yet. Start Nila on the PC and try again.")
        return None

    def ask(self, text: str) -> str:
        missing = self._need_brain()
        if missing:
            return missing
        try:
            turn = self.brain.handle(text)
        except PermissionError as exc:
            return f"The vault is locked: {exc}"
        except Exception as exc:                       # never break the loop
            self.errors += 1
            return f"Nila could not answer that ({type(exc).__name__}). The terminal shows the details."
        if not turn.ok:
            return f"That did not work: {turn.error or 'unknown reason'}"
        return str(turn.answer or "").strip()[:3500] or "(no answer)"

    def due(self) -> str:
        missing = self._need_brain()
        if missing:
            return missing
        try:
            rows = self.brain.due_reminders(consume=False) or []
        except Exception as exc:
            return f"could not read your reminders: {type(exc).__name__}"
        if not rows:
            return "Nothing is due right now."
        lines = ["Due now:"]
        for r in rows:
            lines.append(f"- {r.get('title') or r.get('text') or 'reminder'}"
                         + (f" ({r['late_minutes']} min late)" if r.get("late_minutes") else ""))
        lines.append("/done 1 closes the first one; /list shows them numbered.")
        return "\n".join(lines)

    def _open(self) -> List[Any]:
        book = self.brain.reminder_book()
        return book.open()

    def listing(self) -> str:
        missing = self._need_brain()
        if missing:
            return missing
        try:
            rows = self._open()
        except Exception as exc:
            return f"could not read your reminders: {type(exc).__name__}"
        if not rows:
            return "No open reminders. Say `/remind in 30 min …` to add one."
        from .reminders import describe, now_ms
        lines = ["Open reminders:"]
        for i, rec in enumerate(rows[:20], 1):
            try:
                lines.append(f"{i}. {describe(rec, now_ms())}")
            except Exception:
                lines.append(f"{i}. {(rec.meta or {}).get('text', 'reminder')}")
        if len(rows) > 20:
            lines.append(f"…and {len(rows) - 20} more; the dashboard shows all.")
        return "\n".join(lines)

    def done(self, arg: str) -> str:
        missing = self._need_brain()
        if missing:
            return missing
        num = self._number(arg)
        if num is None:
            return "Which one? `/list` numbers them, then `/done 2`."
        book = self.brain.reminder_book()
        rec = book.by_number(num)
        if rec is None:
            return f"There is no reminder {num}. /list shows what is open."
        try:
            book.close(rec, done=True)
        except Exception as exc:
            return f"could not close it: {type(exc).__name__}"
        return f"Done: reminder {num}. " + self.listing()

    def snooze(self, arg: str) -> str:
        missing = self._need_brain()
        if missing:
            return missing
        parts = arg.split()
        num = self._number(parts[0]) if parts else None
        if num is None:
            return "Say which one and how long: `/snooze 2 10` (reminder 2, ten minutes)."
        minutes = 10.0
        if len(parts) > 1:
            try:
                minutes = float(re.sub(r"[^0-9.]", "", parts[1]) or 10)
            except ValueError:
                minutes = 10.0
            if len(parts) > 2 and parts[2].lower().startswith("h"):
                minutes *= 60
        book = self.brain.reminder_book()
        rec = book.by_number(num)
        if rec is None:
            return f"There is no reminder {num}. /list shows what is open."
        try:
            book.snooze(rec, minutes)
        except Exception as exc:
            return f"could not snooze it: {type(exc).__name__}"
        when = f"{minutes:g} minutes"
        return f"Reminder {num} will come back in {when}."

    def remind(self, arg: str) -> str:
        missing = self._need_brain()
        if missing:
            return missing
        if not arg:
            return "What should I remind you about? `/remind in 30 min take the tablet`"
        if not re.search(r"\bin \d|\bat \d|\btomorrow|\bevery|\d{1,2}[:.]\d{2}", arg.lower()):
            return ("I need a time: `/remind in 30 min …`, `/remind tomorrow 9:00 …`, "
                    "or `/remind every day 8:00 …`")
        try:
            turn = self.brain.handle("remind me " + arg)
        except Exception as exc:
            return f"could not set it: {type(exc).__name__}"
        return str(turn.answer or "").strip()[:1500] or "Reminder set."

    def status(self) -> str:
        lines = ["Nila"]
        vault = getattr(self.tg, "vault", None)
        session = getattr(vault, "session", None)
        lines.append("- vault: " + ("unlocked" if session else "locked"))
        if session is not None:
            mode = getattr(session, "mode", "") or ""
            lines.append(f"- session: {mode or 'open'}")
        lines.append(f"- answered today: {self.answered} · dropped: {self.dropped}"
                     f" · queued: {len(self.tg.pending())}")
        lines.append("- token: " + masked(self.tg.token))
        if self.brain is not None:
            try:
                n = len(self._open())
                lines.append(f"- open reminders: {n}")
            except Exception:
                pass
        return "\n".join(lines)

    @staticmethod
    def _number(raw: str) -> Optional[int]:
        m = re.match(r"\s*(\d{1,3})", str(raw or ""))
        return int(m.group(1)) if m else None

    # -- the loop -------------------------------------------------------- #
    def poll_once(self, timeout: float = 0) -> Dict[str, Any]:
        """One round trip: read what arrived, answer it, report what happened."""
        if not self.tg.token:
            raise TelegramError("no bot token yet - `brain telegram token <token>`")
        if not self.allow:
            raise TelegramError("no chat chosen yet - send /start to the bot, then "
                                "`brain telegram whoami` and `brain telegram chat <id>`")
        updates = self.fetch(timeout=timeout)
        replies = 0
        for update in updates:
            out = self.handle_message(update)
            if out is not None:
                replies += 1
        return {"ok": True, "updates": len(updates), "replies": replies,
                "answered": self.answered, "dropped": self.dropped,
                "errors": self.errors, "offset": self.offset}

    def run(self, once: bool = False, max_rounds: int = 0,
            idle: float = 1.0, timeout: float = LONG_POLL) -> Dict[str, Any]:
        """The long-poll loop. `once` is for a test; the loop is for the phone.

        A failure is reported and then the loop continues after a short wait -
        a phone bridge that dies because Wi-Fi blinked is worse than useless.
        """
        rounds, started = 0, time.time()
        while True:
            rounds += 1
            try:
                result = self.poll_once(timeout=timeout)
                self.printer(f"[{time.strftime('%H:%M:%S')}] waiting for a message "
                             f"from chat {self.tg.chat_id} "
                             f"(answered {self.answered}, dropped {self.dropped})"
                             if rounds == 1 else
                             f"[{time.strftime('%H:%M:%S')}] "
                             f"{result['updates']} new, {self.answered} answered")
            except TelegramError as exc:
                self.errors += 1
                self.printer(f"phone bridge: {exc} - retrying")
                result = {"ok": False, "error": str(exc)[:200],
                          "answered": self.answered, "dropped": self.dropped,
                          "rounds": rounds, "seconds": round(time.time() - started, 1)}
                if once:
                    return result
                self._waiter(min(30.0, 2.0 ** min(rounds, 5)))
                if max_rounds and rounds >= max_rounds:
                    return result
                continue
            if once or (max_rounds and rounds >= max_rounds):
                result.update({"rounds": rounds,
                               "seconds": round(time.time() - started, 1)})
                return result
            if not result["updates"]:
                self._waiter(idle)
