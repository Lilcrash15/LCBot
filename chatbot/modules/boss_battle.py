"""!boss / !attack -- a community damage race against a shared HP pool,
modeled on the original AnkhBot's Boss Battle. Deliberately kept
text-only per Ryan's direction -- no health bar widget, no graphics,
just numbers announced in chat.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine, PERMISSION_LEVELS


@dataclass
class BossState:
    max_hp: int
    hp: int
    started_at: float
    time_limit_seconds: float
    damage_dealt: dict = field(default_factory=dict)  # username -> total damage


class BossBattleModule:
    def __init__(self, db):
        self.db = db
        self.state: Optional[BossState] = None

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="boss", handler=self._cmd_boss,
            default_permission="everyone", default_cooldown_seconds=1,
            description="!boss start [hp] [seconds] (mod) | cancel (mod) | status",
        ))
        engine.register_builtin(BuiltinCommand(
            name="attack", handler=self._cmd_attack,
            default_permission="everyone", default_cooldown_seconds=1,
            default_user_cooldown_seconds=10,
            description="Attack the active boss battle for random damage.",
        ))

    def _cmd_boss(self, ctx: CommandContext) -> str:
        sub = ctx.arg(0).lower()
        if sub == "cancel":
            return self._cancel(ctx)
        if sub == "start":
            return self._start(ctx)
        return self._status(ctx)

    def _start(self, ctx: CommandContext) -> str:
        if ctx.message.permission_rank() < PERMISSION_LEVELS["moderator"]:
            return f"@{ctx.user} only mods can start a boss battle."
        if self.state is not None:
            return f"@{ctx.user} a boss battle is already underway -- !boss status"
        hp = self.db.get_setting_int("boss_default_hp", 1000)
        seconds = self.db.get_setting_int("boss_default_seconds", 180)
        if len(ctx.args) > 1 and ctx.args[1].isdigit():
            hp = int(ctx.args[1])
        if len(ctx.args) > 2 and ctx.args[2].isdigit():
            seconds = int(ctx.args[2])
        hp = max(hp, 1)
        self.state = BossState(max_hp=hp, hp=hp, started_at=time.time(), time_limit_seconds=seconds)
        return f"⚔️ A boss with {hp} HP has appeared! Type !attack to fight it -- {seconds}s on the clock!"

    def _cancel(self, ctx: CommandContext) -> str:
        if ctx.message.permission_rank() < PERMISSION_LEVELS["moderator"]:
            return f"@{ctx.user} only mods can cancel a boss battle."
        if self.state is None:
            return f"@{ctx.user} no boss battle is running."
        self.state = None
        return "Boss battle cancelled -- no rewards given."

    def _status(self, ctx: CommandContext) -> str:
        state = self.state
        if state is None:
            return f"@{ctx.user} no boss battle is running -- mods can start one with !boss start."
        remaining = max(0, int(state.time_limit_seconds - (time.time() - state.started_at)))
        return f"Boss HP: {max(state.hp, 0)}/{state.max_hp} -- {remaining}s left. Type !attack!"

    def _cmd_attack(self, ctx: CommandContext) -> Optional[str]:
        state = self.state
        if state is None:
            return None  # nothing to attack -- stay quiet rather than spam chat
        if time.time() - state.started_at > state.time_limit_seconds:
            return None  # tick() will close it out shortly

        low = self.db.get_setting_int("boss_min_damage", 10)
        high = self.db.get_setting_int("boss_max_damage", 75)
        damage = random.randint(min(low, high), max(low, high))
        state.hp -= damage
        state.damage_dealt[ctx.username] = state.damage_dealt.get(ctx.username, 0) + damage

        if state.hp > 0:
            return f"@{ctx.user} hits the boss for {damage}! ({max(state.hp, 0)}/{state.max_hp} HP left)"
        return self._resolve_victory()

    def _resolve_victory(self) -> str:
        state = self.state
        self.state = None
        currency = self.db.get_setting("currency_name", "points")
        reward = self.db.get_setting_int("boss_victory_reward", 50)
        mvp_bonus = self.db.get_setting_int("boss_mvp_bonus", 150)

        mvp = max(state.damage_dealt, key=state.damage_dealt.get) if state.damage_dealt else None
        for username in state.damage_dealt:
            self.db.add_points(username, reward)
        if mvp:
            self.db.add_points(mvp, mvp_bonus)

        participants = len(state.damage_dealt)
        mvp_text = f" MVP: @{mvp} ({state.damage_dealt[mvp]} dmg, +{mvp_bonus} bonus)." if mvp else ""
        return f"🏆 The boss is defeated! {participants} fighter(s) earned {reward} {currency} each.{mvp_text}"

    # -- called every scheduler tick from Bot ---------------------------
    def tick(self) -> Optional[str]:
        """Closes out a boss battle that ran out the clock undefeated.
        Returns a chat message to announce, or None if there's nothing
        to do (no battle running, or it's still within its time limit --
        a kill inside the window is announced directly by !attack)."""
        state = self.state
        if state is None:
            return None
        if time.time() - state.started_at < state.time_limit_seconds:
            return None
        self.state = None
        if not state.damage_dealt:
            return "The boss battle timer ran out with nobody landing a hit. It wandered off."
        return f"The boss battle timer ran out! It escapes with {max(state.hp, 0)} HP left -- no rewards this time."
