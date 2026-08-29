"""The Queue tab: sign-ups for co-op/multiplayer sessions -- distinct
from the song request queue. A mod opens the queue for a game, viewers
!join (optionally with a note like their in-game name), and the mod
!queue pick's or !queue random's some number of them.
"""
from __future__ import annotations

import random

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine


class GameQueueModule:
    def __init__(self, db):
        self.db = db

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="queue", handler=self._cmd_queue,
            default_permission="moderator", default_cooldown_seconds=1,
            description="!queue open <game> [cost] | close | clear | pick <n> | random <n>",
        ))
        engine.register_builtin(BuiltinCommand(
            name="join", handler=self._cmd_join,
            default_permission="everyone", default_cooldown_seconds=2,
            description="!join [note] -- enter the open queue.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="leave", handler=self._cmd_leave,
            default_permission="everyone", default_cooldown_seconds=2,
            description="Leave the queue.",
        ))

    def _cmd_queue(self, ctx: CommandContext) -> str:
        if not ctx.args:
            return f"@{ctx.user} usage: !queue open <game> [cost] | close | clear | pick <n> | random <n>"
        sub = ctx.args[0].lower()

        if sub == "open":
            game = ctx.args[1] if len(ctx.args) > 1 else "the game"
            cost = int(ctx.args[2]) if len(ctx.args) > 2 and ctx.args[2].isdigit() else 0
            self.db.set_setting("queue_open", "1")
            self.db.set_setting("queue_game", game)
            self.db.set_setting("queue_cost", cost)
            self.db.queue_clear()
            cost_note = f" ({cost} {ctx.bot.currency.currency_name()} to !join)" if cost else ""
            return f"Queue is open for {game}{cost_note} -- type !join to sign up!"

        if sub == "close":
            self.db.set_setting("queue_open", "0")
            return "Queue is closed -- no new entries."

        if sub == "clear":
            self.db.queue_clear()
            return "Queue cleared."

        if sub in ("pick", "random") and len(ctx.args) > 1 and ctx.args[1].isdigit():
            n = int(ctx.args[1])
            entries = list(self.db.queue_all())
            if not entries:
                return "The queue is empty."
            chosen = entries[:n] if sub == "pick" else random.sample(entries, min(n, len(entries)))
            names = ", ".join(r["username"] for r in chosen)
            return f"Picked: {names}"

        return f"@{ctx.user} usage: !queue open <game> [cost] | close | clear | pick <n> | random <n>"

    def _cmd_join(self, ctx: CommandContext) -> str:
        if not self.db.get_setting_bool("queue_open", False):
            return f"@{ctx.user} the queue isn't open right now."
        cost = self.db.get_setting_int("queue_cost", 0)
        if cost:
            row = self.db.get_user(ctx.username)
            balance = row["points"] if row else 0
            if balance < cost:
                return f"@{ctx.user} joining costs {cost} {ctx.bot.currency.currency_name()}, you have {balance}."
            self.db.add_points(ctx.username, -cost)
        note = " ".join(ctx.args)
        added = self.db.queue_join(ctx.username, note)
        if not added:
            return f"@{ctx.user} you're already in the queue."
        return f"@{ctx.user} you're in the queue{' (' + note + ')' if note else ''}."

    def _cmd_leave(self, ctx: CommandContext) -> str:
        removed = self.db.queue_leave(ctx.username)
        return f"@{ctx.user} left the queue." if removed else f"@{ctx.user} you weren't in the queue."
