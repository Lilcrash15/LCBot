"""Stream/follower info commands and the $(uptime)/$(game)/etc. variables,
backed by the Twitch Helix API. Works without an API key configured too
-- commands just say so instead of crashing, so the rest of the bot is
still usable while someone sets up their Twitch Dev app.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine
from chatbot.modules.twitch_api import TwitchAPI, TwitchAPIError

logger = logging.getLogger("chatbot.streaminfo")


def _format_duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class StreamInfoModule:
    def __init__(self, db, channel: str, api: Optional[TwitchAPI]):
        self.db = db
        self.channel = channel
        self.api = api

    def set_api(self, api: Optional[TwitchAPI]) -> None:
        self.api = api

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="uptime", handler=self._cmd_uptime, default_cooldown_seconds=10,
            description="How long the stream has been live.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="title", handler=self._cmd_title, default_cooldown_seconds=10,
            description="The current stream title.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="game", handler=self._cmd_game, default_cooldown_seconds=10,
            description="The current game/category.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="followers", handler=self._cmd_followers, default_cooldown_seconds=15,
            description="Total follower count.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="viewers", handler=self._cmd_viewers, default_cooldown_seconds=10,
            description="Current live viewer count.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="followage", handler=self._cmd_followage, default_cooldown_seconds=5,
            description="How long you've followed the channel.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="so", handler=self._cmd_shoutout, default_permission="moderator",
            default_cooldown_seconds=5, description="!so <user> -- shout out another streamer.",
        ))

    def _require_api(self, ctx: CommandContext) -> Optional[str]:
        if self.api is None:
            return f"@{ctx.user} stream info isn't set up yet -- add a Client ID/token in Settings."
        return None

    def _cmd_uptime(self, ctx: CommandContext) -> str:
        err = self._require_api(ctx)
        if err:
            return err
        try:
            info = self.api.get_stream_info(self.channel)
        except TwitchAPIError as exc:
            logger.warning("uptime lookup failed: %s", exc)
            return f"@{ctx.user} couldn't reach Twitch just now."
        if not info.live or info.started_at is None:
            return f"{self.channel} is offline right now."
        return f"{self.channel} has been live for {_format_duration(time.time() - info.started_at)}."

    def _cmd_title(self, ctx: CommandContext) -> str:
        err = self._require_api(ctx)
        if err:
            return err
        try:
            info = self.api.get_stream_info(self.channel)
        except TwitchAPIError:
            return f"@{ctx.user} couldn't reach Twitch just now."
        return f"Title: {info.title}" if info.live else f"{self.channel} is offline."

    def _cmd_game(self, ctx: CommandContext) -> str:
        err = self._require_api(ctx)
        if err:
            return err
        try:
            info = self.api.get_stream_info(self.channel)
        except TwitchAPIError:
            return f"@{ctx.user} couldn't reach Twitch just now."
        return f"Currently playing: {info.game_name}" if info.live else f"{self.channel} is offline."

    def _cmd_followers(self, ctx: CommandContext) -> str:
        err = self._require_api(ctx)
        if err:
            return err
        try:
            count = self.api.get_follower_count(self.channel)
        except TwitchAPIError:
            return f"@{ctx.user} couldn't reach Twitch just now."
        return f"{self.channel} has {count} followers."

    def _cmd_viewers(self, ctx: CommandContext) -> str:
        err = self._require_api(ctx)
        if err:
            return err
        try:
            info = self.api.get_stream_info(self.channel)
        except TwitchAPIError:
            return f"@{ctx.user} couldn't reach Twitch just now."
        if not info.live:
            return f"{self.channel} is offline right now."
        count = info.viewer_count
        return f"{self.channel} has {count} viewer{'s' if count != 1 else ''} watching right now."

    def _cmd_followage(self, ctx: CommandContext) -> str:
        err = self._require_api(ctx)
        if err:
            return err
        target = ctx.target_username() or ctx.username
        info = self.api.get_follow_info(self.channel, target)
        if info is None:
            return f"@{ctx.user} {'you are' if target == ctx.username else target + ' is'} not following."
        try:
            started = time.mktime(time.strptime(info["followed_at"], "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
        except (KeyError, ValueError):
            return f"@{ctx.user} couldn't read that follow date."
        return f"{target} has followed for {_format_duration(time.time() - started)}."

    def _cmd_shoutout(self, ctx: CommandContext) -> str:
        target = ctx.target_username()
        if not target:
            return f"@{ctx.user} usage: !so <user>"
        game_note = ""
        if self.api is not None:
            try:
                info = self.api.get_stream_info(target)
                if info.game_name:
                    game_note = f" -- they were last playing {info.game_name}"
            except TwitchAPIError:
                pass
        return f"Go check out {target} at https://twitch.tv/{target}{game_note}!"

    # -- variable provider for the $(...) template engine -----------------
    def get_variable(self, name: str, arg: Optional[str], ctx: CommandContext) -> Optional[str]:
        if name == "channel":
            return self.channel
        if self.api is None:
            return None
        try:
            if name == "uptime":
                info = self.api.get_stream_info(self.channel)
                return _format_duration(time.time() - info.started_at) if info.live and info.started_at else "offline"
            if name == "game":
                info = self.api.get_stream_info(self.channel)
                return info.game_name if info.live else "offline"
            if name == "title":
                info = self.api.get_stream_info(self.channel)
                return info.title if info.live else "offline"
            if name == "followers":
                return str(self.api.get_follower_count(self.channel))
            if name == "viewers":
                info = self.api.get_stream_info(self.channel)
                return str(info.viewer_count) if info.live else "offline"
        except TwitchAPIError:
            return None
        return None
