"""Loyalty points (the currency system) and the gambling minigames that
spend them -- !gamble, !slots, !roulette -- probably the single most
iconic AnkhBot feature set. Currency name/earn rate/odds are all
configurable via the settings table (edited from the GUI Settings tab).
"""
from __future__ import annotations

import logging
import random
import time
from typing import Optional

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine

logger = logging.getLogger("chatbot.currency")


class CurrencyModule:
    def __init__(self, db):
        self.db = db
        self._last_payout = time.time()

    # -- settings helpers ------------------------------------------------
    def currency_name(self) -> str:
        return self.db.get_setting("currency_name", "points")

    def earn_amount(self) -> int:
        return self.db.get_setting_int("currency_earn_amount", 10)

    def earn_interval_seconds(self) -> float:
        return self.db.get_setting_int("currency_earn_interval_minutes", 5) * 60

    # -- passive accrual, called from the bot's periodic tick ------------
    def maybe_pay_active_users(self) -> None:
        now = time.time()
        interval = self.earn_interval_seconds()
        if interval <= 0 or now - self._last_payout < interval:
            return
        self._last_payout = now
        active = self.db.all_active_usernames(since_seconds=interval)
        amount = self.earn_amount()
        if amount <= 0:
            return
        for username in active:
            self.db.add_points(username, amount)
        if active:
            logger.info("paid %d %s to %d active chatter(s)", amount, self.currency_name(), len(active))

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="points",
            handler=self._cmd_points,
            default_permission="everyone",
            default_cooldown_seconds=2,
            description="Show your (or another user's) point balance.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="give",
            handler=self._cmd_give,
            default_permission="everyone",
            default_cooldown_seconds=2,
            description="!give <user> <amount> -- transfer points to another viewer.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="giveall",
            handler=self._cmd_giveall,
            default_permission="moderator",
            default_cooldown_seconds=5,
            description="!giveall <amount> -- award points to every known viewer.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="top",
            handler=self._cmd_top,
            default_permission="everyone",
            default_cooldown_seconds=10,
            description="Show the point leaderboard.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="gamble",
            handler=self._cmd_gamble,
            default_permission="everyone",
            default_cooldown_seconds=5,
            default_user_cooldown_seconds=10,
            description="!gamble <amount|all> -- coin-flip odds to double or lose your bet.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="slots",
            handler=self._cmd_slots,
            default_permission="everyone",
            default_cooldown_seconds=5,
            default_user_cooldown_seconds=10,
            description="!slots <amount> -- spin three reels for a payout multiplier.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="roulette",
            handler=self._cmd_roulette,
            default_permission="everyone",
            default_cooldown_seconds=5,
            default_user_cooldown_seconds=10,
            description="!roulette <amount> <red|black|even|odd|high|low|0-36> -- classic roulette bet.",
        ))

    # -- points --------------------------------------------------------
    def _cmd_points(self, ctx: CommandContext) -> str:
        target = ctx.target_username() or ctx.username
        row = self.db.get_user(target)
        pts = row["points"] if row else 0
        who = "You have" if target == ctx.username else f"{target} has"
        return f"@{ctx.user} {who} {pts} {self.currency_name()}."

    def _cmd_give(self, ctx: CommandContext) -> str:
        if len(ctx.args) < 2:
            return f"@{ctx.user} usage: !give <user> <amount>"
        target = ctx.target_username()
        if target == ctx.username:
            return f"@{ctx.user} you can't give points to yourself."
        try:
            amount = int(ctx.args[1])
        except ValueError:
            return f"@{ctx.user} amount has to be a whole number."
        if amount <= 0:
            return f"@{ctx.user} amount has to be positive."
        sender_row = self.db.get_user(ctx.username)
        balance = sender_row["points"] if sender_row else 0
        if balance < amount:
            return f"@{ctx.user} you only have {balance} {self.currency_name()}."
        self.db.add_points(ctx.username, -amount)
        self.db.add_points(target, amount)
        return f"@{ctx.user} gave {amount} {self.currency_name()} to {target}."

    def _cmd_giveall(self, ctx: CommandContext) -> str:
        if not ctx.args:
            return f"@{ctx.user} usage: !giveall <amount>"
        try:
            amount = int(ctx.args[0])
        except ValueError:
            return f"@{ctx.user} amount has to be a whole number."
        users = self.db.all_active_usernames(since_seconds=3600)
        for username in users:
            self.db.add_points(username, amount)
        return f"Gave {amount} {self.currency_name()} to {len(users)} viewer(s) active in the last hour."

    def _cmd_top(self, ctx: CommandContext) -> str:
        n = 5
        if ctx.args:
            try:
                n = max(1, min(10, int(ctx.args[0])))
            except ValueError:
                pass
        rows = self.db.top_users(limit=n)
        if not rows:
            return "No one has any points yet."
        parts = [f"{i+1}. {r['username']} ({r['points']})" for i, r in enumerate(rows)]
        return f"Top {self.currency_name()}: " + " | ".join(parts)

    # -- minigames -------------------------------------------------------
    def _resolve_bet(self, ctx: CommandContext, arg_index: int = 0) -> Optional[tuple[int, str]]:
        """Returns (amount, error_message_or_empty). arg 'all' bets the full balance."""
        row = self.db.get_user(ctx.username)
        balance = row["points"] if row else 0
        raw = ctx.arg(arg_index)
        if not raw:
            return None
        if raw.lower() == "all":
            amount = balance
        else:
            try:
                amount = int(raw)
            except ValueError:
                return None
        min_bet = self.db.get_setting_int("gamble_min_bet", 10)
        if amount < min_bet:
            return None
        if amount > balance:
            return None
        return amount, ""

    def _cmd_gamble(self, ctx: CommandContext) -> str:
        min_bet = self.db.get_setting_int("gamble_min_bet", 10)
        bet = self._resolve_bet(ctx, 0)
        if bet is None:
            row = self.db.get_user(ctx.username)
            balance = row["points"] if row else 0
            return (f"@{ctx.user} usage: !gamble <amount|all> (min {min_bet}, you have {balance} "
                    f"{self.currency_name()}).")
        amount, _ = bet
        win_chance = self.db.get_setting_int("gamble_win_chance_pct", 45) / 100.0
        won = random.random() < win_chance
        if won:
            self.db.add_points(ctx.username, amount)
            new_balance = self.db.get_user(ctx.username)["points"]
            return f"@{ctx.user} won {amount} {self.currency_name()}! New balance: {new_balance}."
        self.db.add_points(ctx.username, -amount)
        new_balance = self.db.get_user(ctx.username)["points"]
        return f"@{ctx.user} lost {amount} {self.currency_name()}. New balance: {new_balance}."

    _SLOT_SYMBOLS = ["7", "BAR", "*", "$", "@"]
    _SLOT_PAYOUTS = {3: 10.0, 2: 2.0}  # matches -> multiplier of bet

    def _cmd_slots(self, ctx: CommandContext) -> str:
        min_bet = self.db.get_setting_int("slots_min_bet", 10)
        bet = self._resolve_bet(ctx, 0)
        if bet is None:
            row = self.db.get_user(ctx.username)
            balance = row["points"] if row else 0
            return (f"@{ctx.user} usage: !slots <amount|all> (min {min_bet}, you have {balance} "
                    f"{self.currency_name()}).")
        amount, _ = bet
        reels = [random.choice(self._SLOT_SYMBOLS) for _ in range(3)]
        display = " ".join(reels)
        unique = len(set(reels))
        matches = 3 if unique == 1 else (2 if unique == 2 else 0)
        multiplier = self._SLOT_PAYOUTS.get(matches, 0.0)
        if multiplier > 0:
            payout = int(amount * multiplier)
            self.db.add_points(ctx.username, payout - amount)
            new_balance = self.db.get_user(ctx.username)["points"]
            return f"@{ctx.user} [ {display} ] Jackpot-ish! +{payout - amount} {self.currency_name()}. Balance: {new_balance}."
        self.db.add_points(ctx.username, -amount)
        new_balance = self.db.get_user(ctx.username)["points"]
        return f"@{ctx.user} [ {display} ] No match. -{amount} {self.currency_name()}. Balance: {new_balance}."

    _RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}

    def _cmd_roulette(self, ctx: CommandContext) -> str:
        min_bet = self.db.get_setting_int("gamble_min_bet", 10)
        if len(ctx.args) < 2:
            return f"@{ctx.user} usage: !roulette <amount> <red|black|even|odd|high|low|0-36> (min {min_bet})."
        bet = self._resolve_bet(ctx, 0)
        if bet is None:
            row = self.db.get_user(ctx.username)
            balance = row["points"] if row else 0
            return f"@{ctx.user} bad amount (min {min_bet}, you have {balance} {self.currency_name()})."
        amount, _ = bet
        choice = ctx.args[1].lower()

        result = random.randint(0, 36)
        is_red = result in self._RED_NUMBERS
        color = "green" if result == 0 else ("red" if is_red else "black")

        won = False
        multiplier = 0.0
        if choice.isdigit() and 0 <= int(choice) <= 36:
            won = int(choice) == result
            multiplier = 35.0
        elif choice in ("red", "black"):
            won = choice == color
            multiplier = 2.0
        elif choice in ("even", "odd") and result != 0:
            won = (result % 2 == 0) == (choice == "even")
            multiplier = 2.0
        elif choice in ("high", "low") and result != 0:
            won = (result >= 19) == (choice == "high")
            multiplier = 2.0
        else:
            return f"@{ctx.user} bet has to be red, black, even, odd, high, low, or a number 0-36."

        if won:
            payout = int(amount * multiplier)
            self.db.add_points(ctx.username, payout - amount)
            new_balance = self.db.get_user(ctx.username)["points"]
            return (f"@{ctx.user} the ball landed on {result} ({color}). You won! "
                    f"+{payout - amount} {self.currency_name()}. Balance: {new_balance}.")
        self.db.add_points(ctx.username, -amount)
        new_balance = self.db.get_user(ctx.username)["points"]
        return (f"@{ctx.user} the ball landed on {result} ({color}). You lost {amount} "
                f"{self.currency_name()}. Balance: {new_balance}.")
