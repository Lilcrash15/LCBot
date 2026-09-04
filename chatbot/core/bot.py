"""The Bot class: wires the IRC client, database, and every feature
module together, and runs the background scheduler (currency payouts,
timers, song queue advancement). This is the one object the GUI talks
to -- it never touches the database or IRC client directly.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Callable, Optional

from chatbot.core.config import ConfigStore
from chatbot.core.database import Database
from chatbot.core.irc_client import ChatMessage, TwitchIRCClient
from chatbot.core.oauth import effective_client_id
from chatbot.modules.alerts import AlertsModule
from chatbot.modules.boss_battle import BossBattleModule
from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine, default_variable_resolver
from chatbot.modules.currency import CurrencyModule
from chatbot.modules.discord_notify import DiscordNotifier
from chatbot.modules.event_system import EventSystemModule
from chatbot.modules.game_queue import GameQueueModule
from chatbot.modules.giveaway import GiveawayModule
from chatbot.modules.heist import HeistModule
from chatbot.modules.moderation import ModerationModule
from chatbot.modules.quotes import QuotesModule
from chatbot.modules.sfx import SFXModule
from chatbot.modules.songrequests import SongRequestsModule
from chatbot.modules.streaminfo import StreamInfoModule
from chatbot.modules.timers import TimersModule
from chatbot.modules.twitch_api import StreamInfo, TwitchAPI, TwitchAPIError
from chatbot.modules.youtube_api import YouTubeAPI

logger = logging.getLogger("chatbot.bot")

SCHEDULER_INTERVAL_SECONDS = 10


class Bot:
    def __init__(
        self,
        config: ConfigStore,
        db: Database,
        on_chat: Optional[Callable[[ChatMessage], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
        on_outgoing: Optional[Callable[[str, str], None]] = None,
    ):
        self.config = config
        self.db = db
        self.on_chat = on_chat or (lambda msg: None)
        self.on_status = on_status or (lambda text: None)
        # Only ever called for messages sent as the bot -- identity is
        # the bot's own configured username (config.data.bot_username),
        # so the Console tab's echo reads like "MyBotName: ..." instead
        # of a generic "Bot: ...". See the docstring on
        # send_chat_as_broadcaster for why a "Streamer" send doesn't
        # call this at all (it lets the normal incoming-chat path
        # render it instead, exactly once).
        self.on_outgoing = on_outgoing or (lambda text, identity: None)

        self.twitch_api: Optional[TwitchAPI] = None
        self.youtube_api: Optional[YouTubeAPI] = None
        # Latest live/viewer-count snapshot, refreshed on the scheduler
        # thread every tick (see _scheduler_loop) so the Dashboard tab
        # can just read this attribute on the GUI thread instead of
        # making its own blocking Helix call on every 5s refresh.
        self.last_stream_info: Optional[StreamInfo] = None

        self.irc = TwitchIRCClient(
            on_message=self._on_message, on_status=self.on_status, on_join=self._on_join,
            on_usernotice=self._on_usernotice,
        )
        self.currency = CurrencyModule(db)
        self.moderation = ModerationModule(db)
        self.timers = TimersModule(db)
        self.quotes = QuotesModule(db)
        self.streaminfo = StreamInfoModule(db, channel=config.data.channel or "channel", api=None)
        self.songrequests = SongRequestsModule(db, api=None)
        self.sfx = SFXModule(db)
        self.giveaway = GiveawayModule(db)
        self.game_queue = GameQueueModule(db)
        self.heist = HeistModule(db)
        self.boss_battle = BossBattleModule(db)
        self.event_system = EventSystemModule(db, sfx_module=self.sfx)
        self.discord = DiscordNotifier()
        self.alerts = AlertsModule(db)
        self.engine = CommandEngine(db, resolve_variables=default_variable_resolver)

        self._register_modules()

        self._stop_event = threading.Event()
        self._scheduler_thread: Optional[threading.Thread] = None

    # -- lifecycle -------------------------------------------------------
    def refresh_apis(self) -> None:
        cfg = self.config.data
        # Falls back to LCBot's own shared Twitch app (see oauth.py's
        # LCBOT_CLIENT_ID) when the user hasn't entered their own
        # Client ID -- has to match whatever Client ID the "Log in
        # with Twitch (streamer account)" button actually authorized
        # with, or Twitch will reject every Helix call with the
        # helix_access_token it issued.
        client_id = effective_client_id(cfg.client_id)
        if client_id and cfg.helix_access_token:
            self.twitch_api = TwitchAPI(client_id, cfg.helix_access_token)
        else:
            self.twitch_api = None
        self.streaminfo.channel = (cfg.channel or self.streaminfo.channel).lower()
        self.streaminfo.set_api(self.twitch_api)

        if cfg.youtube_api_key:
            self.youtube_api = YouTubeAPI(cfg.youtube_api_key)
        else:
            self.youtube_api = None
        self.songrequests.set_api(self.youtube_api)

    def connect(self) -> None:
        self.refresh_apis()
        cfg = self.config.data
        self.event_system.reset_session()
        self.discord.reset()
        self.alerts.reset_session()
        self.last_stream_info = None
        self.irc.connect(cfg.bot_username, cfg.oauth_token, cfg.channel)
        self._start_scheduler()

    def _on_join(self, username: str) -> None:
        response = self.event_system.on_user_join(username, self)
        if response:
            self.send_chat(response)

    def _on_usernotice(self, tags: dict) -> None:
        response = self.alerts.handle_usernotice(tags)
        if response:
            self.send_chat(response)

    def disconnect(self) -> None:
        self._stop_event.set()
        self.irc.disconnect()

    @property
    def connected(self) -> bool:
        return self.irc.connected

    def send_chat(self, text: str) -> None:
        self.irc.send_message(text)
        self.on_outgoing(text, self.config.data.bot_username or "Bot")

    def send_chat_as_broadcaster(self, text: str) -> None:
        """Posts via the Helix Chat API using the broadcaster's own
        token, so the message shows up in chat as the streamer instead
        of the bot account -- mirrors the identity dropdown the
        original AnkhBot had next to its message box. Raises on
        failure (unauthorized, network error) so the GUI can surface
        the problem instead of silently swallowing it.

        Deliberately does NOT call on_outgoing here, unlike send_chat.
        A "Bot" message goes out over the bot's own IRC connection, and
        irc_client.py filters that exact echo back out (it compares the
        sender's login to the bot's own nick) -- so the manual outgoing
        callback is the only thing that ever displays it. A "Streamer"
        message is posted under Ryan's own Twitch login, a different
        account than the bot's, so that same self-echo filter doesn't
        (and shouldn't) catch it: Twitch relays it back through the
        bot's IRC connection exactly like any other viewer's message,
        badges and all, and the Console tab already renders that nicely
        via the normal incoming-chat path. Also calling on_outgoing here
        used to show that same message a second time as a plain
        "Streamer: ..." line."""
        if not self.twitch_api:
            raise RuntimeError("Broadcaster isn't authorized yet -- use \"Authorize (broadcaster)\" in Settings.")
        channel = (self.config.data.broadcaster_username or self.config.data.channel or "").strip().lower()
        if not channel:
            raise RuntimeError("Set \"Twitch channel to join\" in Settings first.")
        broadcaster_id = self.twitch_api.get_user_id(channel)
        if not broadcaster_id:
            raise RuntimeError(f"Couldn't resolve a Twitch user id for '{channel}'.")
        self.twitch_api.send_chat_message(broadcaster_id, broadcaster_id, text)

    def get_channel_info(self) -> dict:
        """Current title/game, for the pencil-icon dialog to pre-fill
        with -- works whether or not the stream is currently live (see
        TwitchAPI.get_channel_info). Returns {} if there's no API or
        channel configured yet rather than raising, since this is only
        ever used to pre-populate a form."""
        if not self.twitch_api:
            return {}
        channel = (self.config.data.broadcaster_username or self.config.data.channel or "").strip().lower()
        if not channel:
            return {}
        try:
            return self.twitch_api.get_channel_info(channel)
        except TwitchAPIError:
            return {}

    def update_stream_info(self, title: Optional[str] = None, game_id: Optional[str] = None) -> None:
        """Updates the live title/category via Twitch's Modify Channel
        Information endpoint, using the broadcaster's own token (needs
        channel:manage:broadcast -- if Ryan authorized before that scope
        was added, he'll need to re-click "Authorize (broadcaster)" to
        pick it up, same as the earlier user:write:chat addition).
        Raises on failure so the GUI dialog can show the real error."""
        if not self.twitch_api:
            raise RuntimeError("Broadcaster isn't authorized yet -- use \"Authorize (broadcaster)\" in Settings.")
        channel = (self.config.data.broadcaster_username or self.config.data.channel or "").strip().lower()
        if not channel:
            raise RuntimeError("Set \"Twitch channel to join\" in Settings first.")
        broadcaster_id = self.twitch_api.get_user_id(channel)
        if not broadcaster_id:
            raise RuntimeError(f"Couldn't resolve a Twitch user id for '{channel}'.")
        self.twitch_api.modify_channel_info(broadcaster_id, title=title, game_id=game_id)

    def _start_scheduler(self) -> None:
        self._stop_event.clear()
        self._scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="bot-scheduler")
        self._scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.currency.maybe_pay_active_users()
                for message in self.timers.tick():
                    self.send_chat(message)
                for message in self.songrequests.tick():
                    self.send_chat(message)
                heist_result = self.heist.tick()
                if heist_result:
                    self.send_chat(heist_result)
                boss_result = self.boss_battle.tick()
                if boss_result:
                    self.send_chat(boss_result)
                cfg = self.config.data
                self.discord.tick(
                    self.twitch_api, cfg.channel, cfg.discord_webhook_url,
                    cfg.discord_announce_enabled, cfg.discord_went_live_message,
                )
                for alert_message in self.alerts.tick(self.twitch_api, cfg.channel):
                    self.send_chat(alert_message)
                self._refresh_stream_info()
            except Exception:
                logger.exception("scheduler tick failed")
            time.sleep(SCHEDULER_INTERVAL_SECONDS)

    def _refresh_stream_info(self) -> None:
        """Refreshes self.last_stream_info (live status + viewer count)
        for the Dashboard tab to read -- pulled out of _scheduler_loop
        as its own method so it's directly testable without waiting on
        the real scheduler thread's sleep loop. On a failed Twitch call
        the previous snapshot is left in place rather than blanking the
        Dashboard out over one missed request."""
        cfg = self.config.data
        if not self.twitch_api or not cfg.channel:
            return
        try:
            self.last_stream_info = self.twitch_api.get_stream_info(cfg.channel)
        except TwitchAPIError:
            pass

    # -- message pipeline --------------------------------------------
    def _on_message(self, message: ChatMessage) -> None:
        self.db.touch_user(message.username, message.display_name)
        self.db.log_chat_message(message.username, message.display_name, message.text)
        self.timers.on_chat_message()
        self.on_chat(message)

        action = self.moderation.check_message(message)
        if action is not None:
            self._apply_moderation(message, action)
            if action.timeout_seconds or action.ban:
                return

        if self.giveaway.check_entry(message, self):
            return  # message was a raffle keyword entry, not a command

        speak_event_response = self.event_system.on_user_speak(message, self)
        if speak_event_response:
            self.send_chat(speak_event_response)

        response = self.engine.handle(message, self)
        if response:
            self.send_chat(response)

    def _apply_moderation(self, message: ChatMessage, action) -> None:
        if action.delete and message.message_id:
            self.send_chat(f"/delete {message.message_id}")
        if action.ban:
            self.send_chat(f"/ban {message.username} {action.reason}")
        elif action.timeout_seconds:
            self.send_chat(f"/timeout {message.username} {action.timeout_seconds} {action.reason}")
        if action.warn_message:
            self.send_chat(action.warn_message)

    # -- module registration ----------------------------------------
    def _register_modules(self) -> None:
        self.currency.register(self.engine)
        self.quotes.register(self.engine)
        self.streaminfo.register(self.engine)
        self.songrequests.register(self.engine)
        self.sfx.register(self.engine)
        self.giveaway.register(self.engine)
        self.game_queue.register(self.engine)
        self.heist.register(self.engine)
        self.boss_battle.register(self.engine)
        self._register_meta_commands()

    def _register_meta_commands(self) -> None:
        self.engine.register_builtin(BuiltinCommand(
            name="commands", handler=self._cmd_commands, default_cooldown_seconds=10,
            description="List enabled command names.",
        ))
        self.engine.register_builtin(BuiltinCommand(
            name="addcom", handler=self._cmd_addcom, default_permission="moderator",
            default_cooldown_seconds=1, description="!addcom !name response text -- add a custom command.",
        ))
        self.engine.register_builtin(BuiltinCommand(
            name="editcom", handler=self._cmd_editcom, default_permission="moderator",
            default_cooldown_seconds=1, description="!editcom !name new response text.",
        ))
        self.engine.register_builtin(BuiltinCommand(
            name="delcom", handler=self._cmd_delcom, default_permission="moderator",
            default_cooldown_seconds=1, description="!delcom !name -- remove a custom command.",
        ))

    def _cmd_commands(self, ctx: CommandContext) -> str:
        rows = [r["name"] for r in self.db.all_commands() if r["enabled"]]
        return "Commands: " + ", ".join(f"!{n}" for n in sorted(rows))

    def _cmd_addcom(self, ctx: CommandContext) -> str:
        if len(ctx.args) < 2:
            return f"@{ctx.user} usage: !addcom !name response text"
        name = ctx.args[0].lstrip("!").lower()
        if name in self.engine._builtins:  # noqa: SLF001 - internal but same module family
            return f"@{ctx.user} !{name} is a built-in command; use !editcom to change it."
        if self.db.get_command(name) is not None:
            return f"@{ctx.user} !{name} already exists -- use !editcom to change it."
        response = " ".join(ctx.args[1:])
        self.engine.add_custom_command(name, response)
        return f"Added !{name}."

    def _cmd_editcom(self, ctx: CommandContext) -> str:
        if len(ctx.args) < 2:
            return f"@{ctx.user} usage: !editcom !name new response text"
        name = ctx.args[0].lstrip("!").lower()
        row = self.db.get_command(name)
        if row is None:
            return f"@{ctx.user} !{name} doesn't exist."
        response = " ".join(ctx.args[1:])
        self.engine.add_custom_command(
            name, response, permission=row["permission"],
            cooldown_seconds=row["cooldown_seconds"], user_cooldown_seconds=row["user_cooldown_seconds"],
        )
        return f"Updated !{name}."

    def _cmd_delcom(self, ctx: CommandContext) -> str:
        if not ctx.args:
            return f"@{ctx.user} usage: !delcom !name"
        name = ctx.args[0].lstrip("!").lower()
        row = self.db.get_command(name)
        if row is None or row["builtin"]:
            return f"@{ctx.user} !{name} can't be deleted."
        self.db.delete_command(name)
        return f"Deleted !{name}."

    # -- $(...) variable provider --------------------------------------
    def get_variable(self, name: str, arg: Optional[str], ctx: CommandContext) -> Optional[str]:
        if name == "user":
            return ctx.user
        if name in ("touser", "target"):
            return ctx.target_username() or ctx.user
        if name.isdigit() and 1 <= int(name) <= 9:
            return ctx.arg(int(name) - 1, "")
        if name == "count":
            cmd_name = ctx.message.text.strip()[1:].split()[0].lower()
            row = self.db.get_command(cmd_name)
            return str((row["uses"] if row else 0) + 1)
        if name == "points":
            row = self.db.get_user(ctx.username)
            return str(row["points"] if row else 0)
        if name == "random":
            try:
                lo, hi = (arg or "1-100").split("-", 1)
                return str(random.randint(int(lo), int(hi)))
            except ValueError:
                return str(random.randint(1, 100))
        if name == "sender":
            return ctx.username

        value = self.streaminfo.get_variable(name, arg, ctx)
        if value is not None:
            return value
        return None
