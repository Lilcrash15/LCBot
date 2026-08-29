"""!heist -- a group gamble modeled on the original AnkhBot's Heist,
one of the most-requested classic minigames. Anyone can kick one off
with `!heist <amount>`, and anyone else can join in with their own
(independently sized) wager while the window is open. When the window
closes, one shared outcome is rolled for the whole crew -- some
fraction of participants "make it out" and get their own wager back
with a multiplier, the rest lose theirs. Purely text-based, no visuals.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine, PERMISSION_LEVELS

# (weight, announcement, fraction of the crew that makes it out, payout multiplier for them)
_OUTCOMES = [
    (35, "The vault alarm went off -- the whole crew got caught!", 0.0, 0.0),
    (35, "Half the crew made it out clean, the rest got left behind.", 0.5, 2.0),
    (20, "Clean job! The whole crew made it out with the loot.", 1.0, 2.0),
    (10, "JACKPOT! A vault door was left wide open -- everyone triples up!", 1.0, 3.0),
]


@dataclass
class HeistState:
    started_at: float
    join_window_seconds: float
    wagers: dict = field(default_factory=dict)  # username -> amount wagered
    resolved: bool = False


class HeistModule:
    def __init__(self, db):
        self.db = db
        self.state: Optional[HeistState] = None

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="heist", handler=self._cmd_heist,
            default_permission="everyone", default_cooldown_seconds=1,
            description="!heist <amount> -- start or join the heist. !heist cancel (mod) | status",
        ))

    def _min_wager(self) -> int:
        return self.db.get_setting_int("heist_min_wager", 10)

    def _cmd_heist(self, ctx: CommandContext) -> Optional[str]:
        sub = ctx.arg(0).lower()
        if sub == "cancel":
            return self._cancel(ctx)
        if sub == "status":
            return self._status(ctx)
        return self._join_or_start(ctx)

    def _join_or_start(self, ctx: CommandContext) -> str:
        currency = ctx.bot.currency.currency_name()
        min_wager = self._min_wager()
        try:
            amount = int(ctx.arg(0))
        except (TypeError, ValueError):
            return (f"@{ctx.user} usage: !heist <amount> (min {min_wager} {currency}) "
                    "-- starts one if none's running, or joins one already open.")
        if amount < min_wager:
            return f"@{ctx.user} the minimum heist wager is {min_wager} {currency}."

        row = self.db.get_user(ctx.username)
        balance = row["points"] if row else 0
        if amount > balance:
            return f"@{ctx.user} you only have {balance} {currency}."

        state = self.state
        starting = state is None or state.resolved
        if starting:
            window = self.db.get_setting_int("heist_join_window_seconds", 60)
            self.state = state = HeistState(started_at=time.time(), join_window_seconds=window)
        elif time.time() - state.started_at > state.join_window_seconds:
            return f"@{ctx.user} the heist crew already left -- results incoming."
        elif ctx.username in state.wagers:
            return f"@{ctx.user} you're already in on this heist."

        self.db.add_points(ctx.username, -amount)
        state.wagers[ctx.username] = amount

        if starting:
            return (f"🏦 @{ctx.user} is putting together a heist crew for {amount} {currency}! "
                     f"Type !heist <amount> to join within {int(state.join_window_seconds)}s "
                     f"(min {min_wager} {currency}).")
        return f"@{ctx.user} is in for {amount} {currency}! ({len(state.wagers)} in the crew)"

    def _cancel(self, ctx: CommandContext) -> str:
        if ctx.message.permission_rank() < PERMISSION_LEVELS["moderator"]:
            return f"@{ctx.user} only mods can cancel a heist."
        state = self.state
        if state is None or state.resolved:
            return f"@{ctx.user} no heist is running."
        for username, amount in state.wagers.items():
            self.db.add_points(username, amount)
        self.state = None
        return "Heist cancelled -- everyone's wager was refunded."

    def _status(self, ctx: CommandContext) -> str:
        state = self.state
        if state is None or state.resolved:
            return f"@{ctx.user} no heist is running -- start one with !heist <amount>."
        remaining = max(0, int(state.join_window_seconds - (time.time() - state.started_at)))
        return f"Heist crew: {len(state.wagers)} joined, {remaining}s left to join with !heist <amount>."

    # -- called every scheduler tick from Bot ---------------------------
    def tick(self) -> Optional[str]:
        """Resolves the heist once its join window has closed. Returns a
        chat message to announce, or None if there's nothing to do."""
        state = self.state
        if state is None or state.resolved:
            return None
        if time.time() - state.started_at < state.join_window_seconds:
            return None
        state.resolved = True
        currency = self.db.get_setting("currency_name", "points")

        participants = list(state.wagers.items())
        self.state = None
        if not participants:
            return None

        weights = [w for w, *_ in _OUTCOMES]
        _, label, win_fraction, multiplier = random.choices(_OUTCOMES, weights=weights, k=1)[0]

        usernames = [u for u, _ in participants]
        random.shuffle(usernames)
        winner_count = round(len(usernames) * win_fraction)
        winners = set(usernames[:winner_count])

        total_paid = 0
        for username, wager in participants:
            if username in winners:
                payout = int(wager * multiplier)
                self.db.add_points(username, payout)
                total_paid += payout

        if not winners:
            return f"💥 {label} All {len(participants)} crew member(s) lost their wager."
        names = ", ".join(sorted(winners)[:10])
        more = f" +{len(winners) - 10} more" if len(winners) > 10 else ""
        return (f"💰 {label} {len(winners)}/{len(participants)} of the crew made it out "
                f"with {total_paid} {currency} total: {names}{more}")
