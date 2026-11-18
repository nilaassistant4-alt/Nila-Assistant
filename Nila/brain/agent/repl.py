"""Text-first interactive shell (the MVP's primary interface)."""
from __future__ import annotations

import sys
from typing import Optional

from .agent import Brain

BANNER = r"""
  _   _ _ _
 | \ | (_) | __ _     your private, local-first
 |  \| | | |/ _` |    encrypted personal AI agent
 | |\  | | | (_| |
 |_| \_|_|_|\__,_|    voice + passphrase unlock - offline capable
                      --------------------------------------------
 zero mandatory dependencies - type `help`, or `quit` to leave
"""


def _fmt_turn(turn, verbose: bool = False) -> str:
    if not turn.ok:
        return f"\033[31merror:\033[0m {turn.error}"
    out = []
    # Reminders that came due are printed ABOVE the answer, on their own lines,
    # so an alert can never be mistaken for something the assistant concluded.
    for alert in getattr(turn, "alerts", None) or []:
        late = alert.get("late_minutes") or 0
        when = f" ({late} min late)" if late >= 1 else ""
        out.append(f"\033[33m⏰ REMINDER{when}:\033[0m {alert.get('title', '')}")
    out.append(turn.answer)
    if verbose and turn.tool_calls:
        calls = ", ".join(c["name"] + ("✓" if c["ok"] else "✗") for c in turn.tool_calls)
        out.append(f"\033[90m  [{turn.provider} · {turn.ms} ms · tools: {calls}]\033[0m")
    return "\n".join(out)


def run_repl(brain: Brain, verbose: bool = False, banner: bool = True) -> int:
    if banner:
        print(BANNER)
    sess = brain.vault.session
    if sess:
        flag = "  \033[33m(DEGRADED - no voice factor)\033[0m" if sess.degraded else ""
        print(f"  user: {sess.user or 'owner'}   mode: {sess.mode}{flag}")
        print(f"  provider: {brain.provider.name if brain.provider.available() else 'local (offline)'}")
        idle_left = sess.seconds_remaining()
        absolute_left = sess.seconds_remaining(absolute=True)
        cap = f"   absolute maximum in {absolute_left}s" if absolute_left is not None else ""
        print(f"  memories: {brain.memory.stats()['records']}   idle expiry in {idle_left}s{cap}")
    print("  type /help for commands, /quit to exit\n")

    warned = False
    while True:
        sess = brain.vault.session
        if sess is None:
            print("\n\033[33msession locked (timeout or /lock). run `brain unlock` again.\033[0m")
            return 0
        try:
            line = input("\033[36mbrain›\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in ("/quit", "/exit", "quit", "exit"):
            break
        if line.startswith("!"):
            print(_fmt_turn(brain.handle(line[1:].strip(), use_tools=True), verbose=True))
            continue
        turn = brain.handle(line)
        print(_fmt_turn(turn, verbose=verbose))
        # 30-minute rule, in the terminal too: say it once, and let the next
        # thing the owner types refresh the window (a message IS an owner action).
        idle = brain.vault.idle_seconds() or 0
        warn_after = brain.config.warn_after
        if idle >= warn_after and not warned:
            left = max(0, int(brain.config.session_timeout) - idle)
            print(f"\033[33m  No activity for {idle // 60} min. Nila locks in about "
                  f"{left // 60} min unless you type something.\033[0m")
            warned = True
        elif idle < warn_after:
            warned = False
    brain.vault.lock(reason="repl-exit")
    print("locked. goodbye.")
    return 0
