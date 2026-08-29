"""Give Away / Raffle: mods start a raffle with a keyword, viewers type
that keyword in chat to enter (optionally spending currency per
entry), and a winner is drawn at random. State is in-memory/session
only -- a raffle isn't something you'd want surviving a restart mid-way.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from chatbot.core.irc_client import ChatMessage
from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine, PERMISSION_LEVELS


@dataclass
class GiveawayState:
    keyword: str
    prize: str
    entry_cost: int = 0
    max_entries_per_user: int = 1
    open: bool = True
    entries: dict = field(default_factory=dict)  # username -> ticket count
    started_at: float = field(default_factory=time.time)


class GiveawayModule:
    def __init__(self, db):
        self.db = db
        self.state: Optional[GiveawayState] = None

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="giveaway", handler=self._cmd_giveaway,
            default_permission="moderator", default_cooldown_seconds=1,
            description="!giveaway start <keyword> [prize] [cost] | close | winner | status",
        ))

    def _cmd_giveaway(self, ctx: CommandContext) -> str:
        if not ctx.args:
            return f"@{ctx.user} usage: !giveaway start <keyword> [prize] [cost] | close | winner | status"
        sub = ctx.args[0].lower()

        if sub == "start":
            if len(ctx.args) < 2:
                return f"@{ctx.user} usage: !giveaway start <keyword> [prize] [cost]"
            keyword = ctx.args[1]
            prize = ctx.args[2] if len(ctx.args) > 2 else "a prize"
            cost = 0
            if len(ctx.args) > 3 and ctx.args[3].isdigit():
                cost = int(ctx.args[3])
            self.state = GiveawayState(keyword=keyword, prize=prize, entry_cost=cost)
            cost_note = f" (costs {cost} {ctx.bot.currency.currency_name()} to enter)" if cost else ""
            return f"🎉 Giveaway started! Type \"{keyword}\" in chat to win {prize}{cost_note}!"

        if sub == "close":
            if not self.state or not self.state.open:
                return f"@{ctx.user} there's no open giveaway."
            self.state.open = False
            return f"Giveaway entries are closed -- {len(self.state.entries)} entered. Draw with !giveaway winner."

        if sub == "winner":
            if not self.state or not self.state.entries:
                return f"@{ctx.user} no one has entered a giveaway yet."
            winner = random.choice(
                [u for u, tickets in self.state.entries.items() for _ in range(tickets)]
            )
            return f"🎉 The winner of {self.state.prize} is @{winner}! Congratulations!"

        if sub == "status":
            if not self.state:
                return f"@{ctx.user} no giveaway has been started."
            status = "open" if self.state.open else "closed"
            return (f"Giveaway for {self.state.prize} (\"{self.state.keyword}\") is {status} -- "
                    f"{len(self.state.entries)} entered.")

        return f"@{ctx.user} unknown giveaway command."

    def check_entry(self, message: ChatMessage, bot: "object") -> bool:
        """Called from the bot's message pipeline for every non-command
        message. Returns True if this message was consumed as a giveaway
        entry (so callers can decide whether to also announce it)."""
        if self.state is None or not self.state.open:
            return False
        if message.text.strip().lower() != self.state.keyword.lower():
            return False
        if self.state.entries.get(message.username, 0) >= self.state.max_entries_per_user:
            return False
        if self.state.entry_cost:
            row = self.db.get_user(message.username)
            balance = row["points"] if row else 0
            if balance < self.state.entry_cost:
                return False
            self.db.add_points(message.username, -self.state.entry_cost)
        self.state.entries[message.username] = self.state.entries.get(message.username, 0) + 1
        return True
