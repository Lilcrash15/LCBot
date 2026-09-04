"""Fast, no-network smoke tests for the core logic (DB, command engine
variable substitution, permission/cooldown gating, currency math,
moderation filters, and IRC tag parsing). Run with:

    python -m unittest discover -s tests -v

Nothing here touches Twitch or YouTube -- it's here to catch regressions
in the plumbing, not to validate live behavior.
"""
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chatbot.core import backup, oauth, overlay_server, paths, update_check
from chatbot.core.bot import Bot
from chatbot.core.config import ConfigStore
from chatbot.core.database import Database
from chatbot.core.friendly_errors import friendly_error_text
from chatbot.core.irc_client import ChatMessage, TwitchIRCClient, _parse_tags
from chatbot.gui import theme
from chatbot.modules.alerts import AlertsModule
from chatbot.modules.boss_battle import BossBattleModule, BossState
from chatbot.modules.commands import CommandContext, CommandEngine, default_variable_resolver
from chatbot.modules.currency import CurrencyModule
from chatbot.modules.discord_notify import DiscordNotifier
from chatbot.modules.event_system import EventSystemModule
from chatbot.modules.game_queue import GameQueueModule
from chatbot.modules.giveaway import GiveawayModule, GiveawayState
from chatbot.modules.heist import HeistModule, HeistState
from chatbot.modules.moderation import ModerationModule
from chatbot.modules.songrequests import SongRequestsModule
from chatbot.modules.streaminfo import StreamInfoModule
from chatbot.modules.twitch_api import StreamInfo, TwitchAPIError
from chatbot.modules.youtube_api import extract_video_id, parse_iso8601_duration


class FakeCurrency:
    def currency_name(self):
        return "points"


class FakeBot:
    """Just enough of Bot's surface for the command engine to run."""

    def __init__(self, db):
        self.db = db
        self.currency = FakeCurrency()

    def get_variable(self, name, arg, ctx):
        if name == "user":
            return ctx.user
        if name == "count":
            return "3"
        return None


def make_message(username="viewer1", text="!hello", mod=False, broadcaster=False) -> ChatMessage:
    return ChatMessage(
        raw="", username=username, display_name=username.capitalize(), channel="testchan",
        text=text, is_mod=mod, is_broadcaster=broadcaster,
    )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_default_settings_present(self):
        self.assertEqual(self.db.get_setting("currency_name"), "points")
        self.assertEqual(self.db.get_setting_int("currency_earn_amount"), 10)

    def test_theme_profile_slots_default_empty(self):
        # The 3 Saved Custom Profiles slots (Themes tab) start blank --
        # theme.parse_custom_colors("") returns None, which is what
        # MainWindow._refresh_theme_profile_ui treats as "(empty)" /
        # Load disabled, so a fresh install shows no profiles as saved.
        for slot in (1, 2, 3):
            self.assertEqual(self.db.get_setting(f"theme_profile_{slot}"), "")

    def test_points_never_go_negative(self):
        self.db.add_points("alice", 50)
        self.db.add_points("alice", -1000)
        row = self.db.get_user("alice")
        self.assertEqual(row["points"], 0)

    def test_touch_user_creates_then_updates(self):
        self.db.touch_user("bob", "Bob")
        row1 = self.db.get_user("bob")
        self.assertIsNotNone(row1)
        self.db.touch_user("bob", "BobNewName")
        row2 = self.db.get_user("bob")
        self.assertEqual(row2["display_name"], "BobNewName")

    def test_chat_log_records_and_retrieves_most_recent_first(self):
        self.db.log_chat_message("bob", "Bob", "hello")
        self.db.log_chat_message("bob", "Bob", "second message")
        self.db.log_chat_message("alice", "Alice", "unrelated")
        rows = self.db.get_recent_messages("bob")
        self.assertEqual([r["text"] for r in rows], ["second message", "hello"])

    def test_chat_log_is_case_insensitive_on_username(self):
        self.db.log_chat_message("Bob", "Bob", "hello")
        rows = self.db.get_recent_messages("BOB")
        self.assertEqual(len(rows), 1)

    def test_chat_log_prunes_beyond_max_rows(self):
        from chatbot.core.database import CHAT_LOG_MAX_ROWS
        for i in range(CHAT_LOG_MAX_ROWS + 25):
            self.db.log_chat_message("bob", "Bob", f"message {i}")
        total = self.db.query_one("SELECT COUNT(*) AS c FROM chat_log")["c"]
        self.assertLessEqual(total, CHAT_LOG_MAX_ROWS)
        # and it kept the newest ones, not the oldest
        newest = self.db.get_recent_messages("bob", limit=1)
        self.assertEqual(newest[0]["text"], f"message {CHAT_LOG_MAX_ROWS + 24}")

    def test_command_upsert_and_delete(self):
        self.db.upsert_command("hello", response="Hi there!", builtin=False)
        row = self.db.get_command("hello")
        self.assertEqual(row["response"], "Hi there!")
        self.db.delete_command("hello")
        self.assertIsNone(self.db.get_command("hello"))

    def test_song_queue_order(self):
        self.db.enqueue_song("vid1", "Song One", 120, "alice")
        self.db.enqueue_song("vid2", "Song Two", 180, "bob")
        first = self.db.pop_next_song()
        self.assertEqual(first["video_id"], "vid1")
        second = self.db.pop_next_song()
        self.assertEqual(second["video_id"], "vid2")
        self.assertIsNone(self.db.pop_next_song())


class CommandEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.engine = CommandEngine(self.db, resolve_variables=default_variable_resolver)
        self.bot = FakeBot(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_custom_command_variable_substitution(self):
        self.engine.add_custom_command("hello", "Hi $(user), this has been used $(count) times.")
        msg = make_message(text="!hello")
        result = self.engine.handle(msg, self.bot)
        self.assertEqual(result, "Hi Viewer1, this has been used 3 times.")

    def test_permission_gating_blocks_lower_rank(self):
        self.engine.add_custom_command("modonly", "secret", permission="moderator")
        msg = make_message(text="!modonly", mod=False)
        self.assertIsNone(self.engine.handle(msg, self.bot))
        mod_msg = make_message(text="!modonly", mod=True)
        self.assertEqual(self.engine.handle(mod_msg, self.bot), "secret")

    def test_cooldown_blocks_rapid_reuse(self):
        self.engine.add_custom_command("spam", "pong", cooldown_seconds=60)
        msg = make_message(text="!spam")
        first = self.engine.handle(msg, self.bot)
        self.assertEqual(first, "pong")
        second = self.engine.handle(make_message(text="!spam", username="other"), self.bot)
        self.assertIsNone(second)  # global cooldown still active

    def test_unknown_command_returns_none(self):
        self.assertIsNone(self.engine.handle(make_message(text="!nope"), self.bot))

    def test_non_command_text_ignored(self):
        self.assertIsNone(self.engine.handle(make_message(text="just chatting"), self.bot))


class CurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.currency = CurrencyModule(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_gamble_never_exceeds_balance(self):
        self.db.set_points("alice", 100)
        random.seed(1)
        ctx = CommandContext(message=make_message(username="alice", text="!gamble 100"), args=["100"], bot=None)
        for _ in range(25):
            self.currency._cmd_gamble(ctx)
            balance = self.db.get_user("alice")["points"]
            self.assertGreaterEqual(balance, 0)
            if balance == 0:
                break

    def test_give_transfers_points(self):
        self.db.set_points("alice", 100)
        self.db.set_points("bob", 0)
        ctx = CommandContext(message=make_message(username="alice", text="!give bob 30"), args=["bob", "30"], bot=None)
        self.currency._cmd_give(ctx)
        self.assertEqual(self.db.get_user("alice")["points"], 70)
        self.assertEqual(self.db.get_user("bob")["points"], 30)

    def test_give_rejects_insufficient_balance(self):
        self.db.set_points("alice", 10)
        ctx = CommandContext(message=make_message(username="alice", text="!give bob 30"), args=["bob", "30"], bot=None)
        result = self.currency._cmd_give(ctx)
        self.assertIn("only have", result)
        self.assertEqual(self.db.get_user("bob"), None)


class ModerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.mod = ModerationModule(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_mods_are_exempt(self):
        msg = make_message(text="CHECK OUT MY SITE HTTP://SPAM.COM NOW!!!!", mod=True)
        self.assertIsNone(self.mod.check_message(msg))

    def test_link_from_regular_user_flagged(self):
        msg = make_message(text="check this out example.com/free-stuff")
        action = self.mod.check_message(msg)
        self.assertIsNotNone(action)
        self.assertIn("link", action.reason)

    def test_whitelisted_domain_not_flagged(self):
        self.db.add_link_whitelist("twitch.tv")
        msg = make_message(text="watch me at twitch.tv/testchan")
        self.assertIsNone(self.mod.check_message(msg))

    def test_excessive_caps_flagged(self):
        msg = make_message(text="THIS IS ALL CAPS AND SHOULD BE FLAGGED")
        action = self.mod.check_message(msg)
        self.assertIsNotNone(action)
        self.assertIn("caps", action.reason)

    def test_strikes_escalate_to_timeout(self):
        self.db.set_setting("moderation_strikes_before_timeout", 2)
        msg1 = make_message(username="baduser", text="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
        first = self.mod.check_message(msg1)
        self.assertEqual(first.timeout_seconds, 0)
        msg2 = make_message(username="baduser", text="BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB")
        second = self.mod.check_message(msg2)
        self.assertGreater(second.timeout_seconds, 0)


class IRCParsingTests(unittest.TestCase):
    def test_parse_tags_unescapes_values(self):
        tags = _parse_tags(r"display-name=Some\sUser;badges=moderator/1;mod=1")
        self.assertEqual(tags["display-name"], "Some User")
        self.assertEqual(tags["mod"], "1")

    def test_permission_rank_ordering(self):
        everyone = ChatMessage(raw="", username="a", display_name="A", channel="c", text="")
        vip = ChatMessage(raw="", username="a", display_name="A", channel="c", text="", is_vip=True)
        mod = ChatMessage(raw="", username="a", display_name="A", channel="c", text="", is_mod=True)
        broadcaster = ChatMessage(raw="", username="a", display_name="A", channel="c", text="", is_broadcaster=True)
        ranks = [m.permission_rank() for m in (everyone, vip, mod, broadcaster)]
        self.assertEqual(ranks, sorted(ranks))
        self.assertTrue(ranks[0] < ranks[1] < ranks[2] < ranks[3])


class YouTubeHelpersTests(unittest.TestCase):
    def test_extract_video_id_from_various_url_forms(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertIsNone(extract_video_id("just a search query"))

    def test_parse_iso8601_duration(self):
        self.assertEqual(parse_iso8601_duration("PT3M33S"), 213)
        self.assertEqual(parse_iso8601_duration("PT1H2M3S"), 3723)
        self.assertEqual(parse_iso8601_duration("PT45S"), 45)


class GiveawayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.giveaway = GiveawayModule(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_keyword_entry_is_consumed_and_costs_points(self):
        self.db.set_points("alice", 100)
        self.giveaway.state = GiveawayState(keyword="raffle", prize="a mug", entry_cost=10)
        msg = make_message(username="alice", text="raffle")
        consumed = self.giveaway.check_entry(msg, bot=None)
        self.assertTrue(consumed)
        self.assertEqual(self.giveaway.state.entries["alice"], 1)
        self.assertEqual(self.db.get_user("alice")["points"], 90)

    def test_non_keyword_message_not_consumed(self):
        self.giveaway.state = GiveawayState(keyword="raffle", prize="a mug")
        msg = make_message(username="alice", text="hello everyone")
        self.assertFalse(self.giveaway.check_entry(msg, bot=None))

    def test_insufficient_points_blocks_entry(self):
        self.db.set_points("alice", 5)
        self.giveaway.state = GiveawayState(keyword="raffle", prize="a mug", entry_cost=10)
        msg = make_message(username="alice", text="raffle")
        self.assertFalse(self.giveaway.check_entry(msg, bot=None))
        self.assertEqual(self.db.get_user("alice")["points"], 5)


class GameQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.gq = GameQueueModule(self.db)
        self.bot = FakeBot(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_join_requires_open_queue(self):
        ctx = CommandContext(message=make_message(username="alice", text="!join"), args=[], bot=self.bot)
        result = self.gq._cmd_join(ctx)
        self.assertIn("isn't open", result)

    def test_join_and_duplicate_join(self):
        self.db.set_setting("queue_open", "1")
        ctx = CommandContext(message=make_message(username="alice", text="!join"), args=[], bot=self.bot)
        first = self.gq._cmd_join(ctx)
        self.assertIn("you're in the queue", first)
        second = self.gq._cmd_join(ctx)
        self.assertIn("already in the queue", second)
        self.assertEqual(len(self.db.queue_all()), 1)


class EventSystemTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.events = EventSystemModule(self.db)
        self.bot = FakeBot(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def test_join_event_fires_once_per_session(self):
        self.db.add_event("join", "everyone", None, "Welcome $(user)!", None)
        first = self.events.on_user_join("alice", self.bot)
        self.assertEqual(first, "Welcome Alice!")
        second = self.events.on_user_join("alice", self.bot)
        self.assertIsNone(second)

    def test_user_specific_event_only_matches_that_user(self):
        self.db.add_event("join", "user_specific", "bob", "Hey boss!", None)
        self.assertIsNone(self.events.on_user_join("alice", self.bot))
        self.assertEqual(self.events.on_user_join("bob", self.bot), "Hey boss!")

    def test_reset_session_allows_events_to_refire(self):
        self.db.add_event("speak", "everyone", None, "hi $(user)", None)
        msg = make_message(username="alice", text="hello")
        self.assertEqual(self.events.on_user_speak(msg, self.bot), "hi Alice")
        self.events.reset_session()
        self.assertEqual(self.events.on_user_speak(msg, self.bot), "hi Alice")


class IRCClientBehaviorTests(unittest.TestCase):
    """Regression coverage for two real bugs Ryan hit live: the bot's own
    sent messages coming back through the PRIVMSG stream and getting
    logged twice, and "Joined #channel" being announced twice because
    Twitch sends both numeric 001 and 376 on every connect."""

    def setUp(self):
        self.messages = []
        self.statuses = []
        self.client = TwitchIRCClient(
            on_message=lambda m: self.messages.append(m),
            on_status=lambda s: self.statuses.append(s),
        )
        self.client._our_nick = "mybot"
        self.client._channel = "testchan"

    def test_own_sent_message_echoed_back_is_not_redelivered(self):
        self.client._handle_line(":mybot!mybot@mybot.tmi.twitch.tv PRIVMSG #testchan :hello")
        self.assertEqual(self.messages, [])

    def test_other_users_message_still_delivered(self):
        self.client._handle_line(":viewer1!viewer1@viewer1.tmi.twitch.tv PRIVMSG #testchan :hello")
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0].username, "viewer1")

    def test_welcome_and_motd_only_announce_joined_once(self):
        self.client._handle_line(":tmi.twitch.tv 001 mybot :Welcome, GLHF!")
        self.client._handle_line(":tmi.twitch.tv 376 mybot :>")
        joined_statuses = [s for s in self.statuses if s.startswith("Joined")]
        self.assertEqual(len(joined_statuses), 1)


class FakeTwitchAPI:
    """Stands in for TwitchAPI in Bot tests -- records what would have
    been sent via Helix instead of making a real network call."""

    def __init__(self, stream_info=None, channel_info=None, user_id="12345"):
        self.sent = []
        self.stream_info = stream_info or StreamInfo(live=False)
        self.channel_info = channel_info if channel_info is not None else {
            "title": "Existing Title", "game_id": "509658", "game_name": "Just Chatting",
        }
        self.user_id = user_id
        self.modify_calls = []

    def get_user_id(self, login):
        return self.user_id

    def send_chat_message(self, broadcaster_id, sender_id, message):
        self.sent.append((broadcaster_id, sender_id, message))

    def get_stream_info(self, channel_login):
        return self.stream_info

    def get_channel_info(self, broadcaster_login):
        return self.channel_info

    def modify_channel_info(self, broadcaster_id, title=None, game_id=None):
        self.modify_calls.append((broadcaster_id, title, game_id))


class BotOutgoingIdentityTests(unittest.TestCase):
    """Regression coverage for two things about the Console tab's
    "Bot"/"Streamer" identity dropdown: (1) a real bug Ryan hit live --
    sending as "Streamer" showed up twice, once as a manual "Streamer:
    ..." echo and again as a normal incoming chat line, because Twitch
    relays a broadcaster-sent Helix message back through the bot's IRC
    connection just like any other viewer's message (it's a different
    login than the bot's own, so irc_client.py's self-echo filter
    doesn't catch it) -- a "Bot" send, by contrast, IS caught by that
    filter, so it still needs its manual echo to show up at all; and
    (2) that echo is labeled with the bot's own configured username
    (e.g. "MyBotName: ...") instead of the generic word "Bot", falling
    back to "Bot" only if no bot username has been configured yet."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        config = ConfigStore(os.path.join(self.tmpdir, "config.json"))
        config.data.channel = "testchan"
        config.data.broadcaster_username = "testchan"
        config.data.bot_username = "mybotname"
        db = Database(os.path.join(self.tmpdir, "chatbot.db"))
        self.outgoing = []
        self.bot = Bot(config, db, on_outgoing=lambda text, identity: self.outgoing.append((text, identity)))
        self.fake_api = FakeTwitchAPI()
        self.bot.twitch_api = self.fake_api

    def test_send_chat_as_bot_fires_outgoing_callback_with_bot_username(self):
        self.bot.irc.send_message = lambda text: None  # skip the real IRC send queue
        self.bot.send_chat("hello")
        self.assertEqual(self.outgoing, [("hello", "mybotname")])

    def test_send_chat_falls_back_to_generic_label_when_bot_username_unset(self):
        self.bot.config.data.bot_username = ""
        self.bot.irc.send_message = lambda text: None
        self.bot.send_chat("hello")
        self.assertEqual(self.outgoing, [("hello", "Bot")])

    def test_send_chat_as_broadcaster_does_not_fire_outgoing_callback(self):
        self.bot.send_chat_as_broadcaster("!cc")
        self.assertEqual(self.fake_api.sent, [("12345", "12345", "!cc")])
        self.assertEqual(self.outgoing, [])


class BotStreamInfoTests(unittest.TestCase):
    """Regression coverage for the Dashboard tab's live-viewer-count
    stat tile: Bot._refresh_stream_info() (pulled out of the scheduler
    loop so it's directly callable in a test, without waiting on the
    real thread's 10s sleep) should populate last_stream_info from
    Twitch, leave the previous snapshot alone on a failed lookup rather
    than blanking it, and no-op quietly when there's no API or channel
    configured yet -- so the GUI's read of self.bot.last_stream_info
    never needs its own try/except or its own network call."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ConfigStore(os.path.join(self.tmpdir, "config.json"))
        self.config.data.channel = "testchan"
        db = Database(os.path.join(self.tmpdir, "chatbot.db"))
        self.bot = Bot(self.config, db)

    def test_refresh_stream_info_populates_from_api(self):
        self.bot.twitch_api = FakeTwitchAPI(StreamInfo(live=True, viewer_count=99))
        self.bot._refresh_stream_info()
        self.assertEqual(self.bot.last_stream_info.viewer_count, 99)
        self.assertTrue(self.bot.last_stream_info.live)

    def test_refresh_stream_info_noop_without_api(self):
        self.bot.twitch_api = None
        self.bot._refresh_stream_info()
        self.assertIsNone(self.bot.last_stream_info)

    def test_refresh_stream_info_noop_without_channel(self):
        self.bot.twitch_api = FakeTwitchAPI(StreamInfo(live=True, viewer_count=5))
        self.bot.config.data.channel = ""
        self.bot._refresh_stream_info()
        self.assertIsNone(self.bot.last_stream_info)

    def test_refresh_stream_info_keeps_previous_snapshot_on_error(self):
        class FailingAPI:
            def get_stream_info(self, channel_login):
                raise TwitchAPIError("Twitch is down")

        self.bot.twitch_api = FakeTwitchAPI(StreamInfo(live=True, viewer_count=12))
        self.bot._refresh_stream_info()
        self.assertEqual(self.bot.last_stream_info.viewer_count, 12)

        self.bot.twitch_api = FailingAPI()
        self.bot._refresh_stream_info()
        self.assertEqual(self.bot.last_stream_info.viewer_count, 12)


class BotUpdateStreamInfoTests(unittest.TestCase):
    """Regression coverage for the Console tab's pencil-icon "update
    title/game" dialog: Bot.get_channel_info() pre-fills the dialog
    (works whether or not the stream is live, and never raises -- it's
    only ever used to populate a form), and Bot.update_stream_info()
    resolves the broadcaster's user id and forwards to Twitch's Modify
    Channel Information endpoint, raising friendly errors for the same
    not-authorized/no-channel cases send_chat_as_broadcaster already
    covers."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.config = ConfigStore(os.path.join(self.tmpdir, "config.json"))
        self.config.data.channel = "testchan"
        self.config.data.broadcaster_username = "testchan"
        db = Database(os.path.join(self.tmpdir, "chatbot.db"))
        self.bot = Bot(self.config, db)

    def test_get_channel_info_returns_current_title_and_game(self):
        self.bot.twitch_api = FakeTwitchAPI(channel_info={
            "title": "Speedrunning!", "game_id": "509658", "game_name": "Just Chatting",
        })
        info = self.bot.get_channel_info()
        self.assertEqual(info["title"], "Speedrunning!")
        self.assertEqual(info["game_name"], "Just Chatting")

    def test_get_channel_info_returns_empty_without_api(self):
        self.bot.twitch_api = None
        self.assertEqual(self.bot.get_channel_info(), {})

    def test_get_channel_info_returns_empty_without_channel(self):
        self.bot.twitch_api = FakeTwitchAPI()
        self.bot.config.data.channel = ""
        self.bot.config.data.broadcaster_username = ""
        self.assertEqual(self.bot.get_channel_info(), {})

    def test_get_channel_info_swallows_api_errors(self):
        class FailingAPI:
            def get_channel_info(self, broadcaster_login):
                raise TwitchAPIError("Twitch is down")

        self.bot.twitch_api = FailingAPI()
        self.assertEqual(self.bot.get_channel_info(), {})

    def test_update_stream_info_forwards_title_and_game_id(self):
        fake_api = FakeTwitchAPI(user_id="98765")
        self.bot.twitch_api = fake_api
        self.bot.update_stream_info(title="New Title", game_id="509658")
        self.assertEqual(fake_api.modify_calls, [("98765", "New Title", "509658")])

    def test_update_stream_info_title_only_leaves_game_id_none(self):
        fake_api = FakeTwitchAPI(user_id="98765")
        self.bot.twitch_api = fake_api
        self.bot.update_stream_info(title="New Title")
        self.assertEqual(fake_api.modify_calls, [("98765", "New Title", None)])

    def test_update_stream_info_raises_without_api(self):
        self.bot.twitch_api = None
        with self.assertRaises(RuntimeError):
            self.bot.update_stream_info(title="x")

    def test_update_stream_info_raises_without_channel(self):
        self.bot.twitch_api = FakeTwitchAPI()
        self.bot.config.data.channel = ""
        self.bot.config.data.broadcaster_username = ""
        with self.assertRaises(RuntimeError):
            self.bot.update_stream_info(title="x")

    def test_update_stream_info_raises_when_user_id_unresolved(self):
        class UnresolvableAPI(FakeTwitchAPI):
            def get_user_id(self, login):
                return None

        self.bot.twitch_api = UnresolvableAPI()
        with self.assertRaises(RuntimeError):
            self.bot.update_stream_info(title="x")


class FriendlyErrorTests(unittest.TestCase):
    """friendly_error_text() is what turns a raw TwitchAPIError/
    Discord-webhook RuntimeError/network exception into the plain-
    English message the GUI actually shows -- Ryan specifically asked
    for "please check X setting" style errors instead of a stack trace
    or a bare HTTP status code, for connecting (bot/broadcaster) and
    for the Dashboard title/game save."""

    def test_twitch_401_suggests_reauthorizing_broadcaster(self):
        msg = friendly_error_text("Helix modify channel info failed: 401 {\"error\":\"Unauthorized\"}")
        self.assertIn("Authorize (broadcaster)", msg)
        self.assertIn("Settings", msg)

    def test_twitch_403_also_suggests_reauthorizing_broadcaster(self):
        msg = friendly_error_text("Helix /channels failed: 403 {\"error\":\"Forbidden\"}")
        self.assertIn("Authorize (broadcaster)", msg)

    def test_twitch_404_mentions_channel_setting(self):
        msg = friendly_error_text("Helix /channels failed: 404 {\"error\":\"Not Found\"}")
        self.assertIn("channel", msg.lower())
        self.assertIn("Settings", msg)

    def test_twitch_unreachable_mentions_internet_connection(self):
        msg = friendly_error_text("Helix /channels unreachable: [Errno -2] Name or service not resolved")
        self.assertIn("internet connection", msg)
        self.assertNotIn("Errno", msg)

    def test_twitch_other_status_code_still_readable(self):
        msg = friendly_error_text("Helix /channels failed: 500 {\"error\":\"Internal Server Error\"}")
        self.assertIn("500", msg)
        self.assertNotIn("Internal Server Error", msg)

    def test_discord_401_mentions_webhook_url_setting(self):
        msg = friendly_error_text("Discord webhook rejected the message: 401 Unauthorized")
        self.assertIn("Webhook URL", msg)
        self.assertIn("Settings", msg)

    def test_discord_unreachable_mentions_internet_connection(self):
        msg = friendly_error_text("Couldn't reach Discord: [Errno -2] Name or service not resolved")
        self.assertIn("internet connection", msg)
        self.assertNotIn("Errno", msg)

    def test_bot_own_runtime_errors_pass_through_unchanged(self):
        # These are already plain English (written by Bot itself), so
        # they shouldn't get mangled by the Twitch/Discord matching.
        text = "Set \"Twitch channel to join\" in Settings first."
        self.assertEqual(friendly_error_text(RuntimeError(text)), text)

    def test_generic_os_error_mentions_internet_connection(self):
        msg = friendly_error_text(ConnectionRefusedError("Connection refused"))
        self.assertIn("internet connection", msg)

    def test_accepts_exception_object_or_already_stringified_text(self):
        exc = RuntimeError("Helix /channels failed: 401 {}")
        self.assertEqual(friendly_error_text(exc), friendly_error_text(str(exc)))


class FakeStreamInfoTwitchAPI:
    """Stands in for TwitchAPI in DiscordNotifier tests -- just hands
    back whatever StreamInfo the test queues up next, instead of
    hitting Twitch's real /streams endpoint."""

    def __init__(self, info):
        self.info = info

    def get_stream_info(self, channel_login):
        return self.info


class DiscordNotifierTests(unittest.TestCase):
    """Regression coverage for the Discord "went live" webhook: it
    should announce only on an actual offline -> live transition, never
    on the first check after a (re)connect (that just establishes a
    baseline -- otherwise reopening the bot while already live would
    fire a false announcement), and it should respect the
    enabled/webhook_url/channel guards and the check interval."""

    def setUp(self):
        self.sent = []
        self.notifier = DiscordNotifier(sender=lambda url, text: self.sent.append((url, text)))

    def test_first_tick_establishes_baseline_without_announcing(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=True, title="Hello", game_name="Just Chatting"))
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1000.0)
        self.assertEqual(self.sent, [])

    def test_offline_to_live_transition_announces(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=False))
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1000.0)
        api.info = StreamInfo(live=True, title="Hello", game_name="Just Chatting")
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1061.0)
        self.assertEqual(self.sent, [("https://discord.example/webhook", "testchan is live")])

    def test_stays_live_does_not_reannounce(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=True, title="Hello", game_name="Just Chatting"))
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1000.0)
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1061.0)
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1122.0)
        self.assertEqual(self.sent, [])

    def test_respects_check_interval(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=False))
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1000.0)
        api.info = StreamInfo(live=True, title="Hello", game_name="Just Chatting")
        # Only 10s later -- inside the 60s interval, so this tick shouldn't even check Twitch yet.
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1010.0)
        self.assertEqual(self.sent, [])

    def test_disabled_never_announces(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=False))
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", False, "{channel} is live", now=1000.0)
        api.info = StreamInfo(live=True)
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", False, "{channel} is live", now=1061.0)
        self.assertEqual(self.sent, [])

    def test_missing_webhook_url_never_announces(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=False))
        self.notifier.tick(api, "testchan", "", True, "{channel} is live", now=1000.0)
        api.info = StreamInfo(live=True)
        self.notifier.tick(api, "testchan", "", True, "{channel} is live", now=1061.0)
        self.assertEqual(self.sent, [])

    def test_reset_clears_baseline_so_next_tick_rebaselines(self):
        api = FakeStreamInfoTwitchAPI(StreamInfo(live=True, title="Hello", game_name="Just Chatting"))
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1000.0)
        self.notifier.reset()
        # Still live after reset -- this should re-establish baseline, not announce, same as a fresh launch.
        self.notifier.tick(api, "testchan", "https://discord.example/webhook", True, "{channel} is live", now=1061.0)
        self.assertEqual(self.sent, [])

    def test_render_fills_placeholders(self):
        info = StreamInfo(live=True, title="My Stream", game_name="Just Chatting")
        text = DiscordNotifier._render("{channel} -- {title} -- {game}", "testchan", info)
        self.assertEqual(text, "testchan -- My Stream -- Just Chatting")

    def test_render_falls_back_to_raw_template_on_bad_placeholder(self):
        info = StreamInfo(live=True, title="My Stream", game_name="Just Chatting")
        text = DiscordNotifier._render("{channel} went live at {nope}", "testchan", info)
        self.assertEqual(text, "{channel} went live at {nope}")

    def test_send_calls_injected_sender(self):
        self.notifier.send("https://discord.example/webhook", "hi")
        self.assertEqual(self.sent, [("https://discord.example/webhook", "hi")])


class StreamInfoModuleTests(unittest.TestCase):
    """Regression coverage for !viewers and $(viewers) -- the module
    that already backed !uptime/!title/!game/!followers, extended with
    a live viewer count. Covers the offline/not-configured messages the
    other commands already establish the pattern for, plus the
    singular/plural wording for exactly 1 viewer."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.module = StreamInfoModule(self.db, channel="testchan", api=None)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _ctx(self):
        return CommandContext(message=make_message(username="viewer1", text="!viewers"), args=[], bot=None)

    def test_viewers_command_reports_count_when_live(self):
        self.module.set_api(FakeStreamInfoTwitchAPI(StreamInfo(live=True, viewer_count=42)))
        self.assertEqual(
            self.module._cmd_viewers(self._ctx()), "testchan has 42 viewers watching right now."
        )

    def test_viewers_command_uses_singular_for_exactly_one(self):
        self.module.set_api(FakeStreamInfoTwitchAPI(StreamInfo(live=True, viewer_count=1)))
        self.assertEqual(
            self.module._cmd_viewers(self._ctx()), "testchan has 1 viewer watching right now."
        )

    def test_viewers_command_when_offline(self):
        self.module.set_api(FakeStreamInfoTwitchAPI(StreamInfo(live=False)))
        self.assertEqual(self.module._cmd_viewers(self._ctx()), "testchan is offline right now.")

    def test_viewers_command_without_api_configured(self):
        self.assertIn("isn't set up yet", self.module._cmd_viewers(self._ctx()))

    def test_viewers_variable_returns_count_when_live(self):
        self.module.set_api(FakeStreamInfoTwitchAPI(StreamInfo(live=True, viewer_count=7)))
        self.assertEqual(self.module.get_variable("viewers", None, self._ctx()), "7")

    def test_viewers_variable_offline(self):
        self.module.set_api(FakeStreamInfoTwitchAPI(StreamInfo(live=False)))
        self.assertEqual(self.module.get_variable("viewers", None, self._ctx()), "offline")

    def test_viewers_variable_without_api_returns_none(self):
        self.assertIsNone(self.module.get_variable("viewers", None, self._ctx()))


class HeistTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.heist = HeistModule(self.db)
        self.bot = FakeBot(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _ctx(self, username, text):
        parts = text.split()
        return CommandContext(message=make_message(username=username, text=text), args=parts[1:], bot=self.bot)

    def test_starting_deducts_wager_and_opens_window(self):
        self.db.set_points("alice", 100)
        result = self.heist._cmd_heist(self._ctx("alice", "!heist 50"))
        self.assertIn("crew", result.lower())
        self.assertEqual(self.db.get_user("alice")["points"], 50)
        self.assertEqual(self.heist.state.wagers["alice"], 50)

    def test_insufficient_balance_rejected(self):
        self.db.set_points("alice", 5)
        result = self.heist._cmd_heist(self._ctx("alice", "!heist 50"))
        self.assertIn("only have", result)
        self.assertIsNone(self.heist.state)

    def test_below_minimum_wager_rejected(self):
        self.db.set_points("alice", 100)
        result = self.heist._cmd_heist(self._ctx("alice", "!heist 1"))
        self.assertIn("minimum", result)

    def test_second_user_joins_existing_heist(self):
        self.db.set_points("alice", 100)
        self.db.set_points("bob", 100)
        self.heist._cmd_heist(self._ctx("alice", "!heist 20"))
        result = self.heist._cmd_heist(self._ctx("bob", "!heist 30"))
        self.assertIn("in for 30", result)
        self.assertEqual(len(self.heist.state.wagers), 2)

    def test_cancel_refunds_everyone(self):
        self.db.set_points("alice", 100)
        self.heist._cmd_heist(self._ctx("alice", "!heist 40"))
        result = self.heist._cmd_heist(CommandContext(
            message=make_message(username="mod1", text="!heist cancel", mod=True), args=["cancel"], bot=self.bot,
        ))
        self.assertIn("refunded", result)
        self.assertEqual(self.db.get_user("alice")["points"], 100)
        self.assertIsNone(self.heist.state)

    def test_non_mod_cannot_cancel(self):
        self.db.set_points("alice", 100)
        self.heist._cmd_heist(self._ctx("alice", "!heist 40"))
        result = self.heist._cmd_heist(self._ctx("alice", "!heist cancel"))
        self.assertIn("only mods", result)
        self.assertIsNotNone(self.heist.state)

    def test_tick_before_window_closes_does_nothing(self):
        self.heist.state = HeistState(started_at=time.time(), join_window_seconds=60, wagers={"alice": 10})
        self.assertIsNone(self.heist.tick())

    def test_tick_resolves_and_clears_state_after_window(self):
        self.db.set_points("alice", 100)
        self.db.set_points("bob", 100)
        self.heist.state = HeistState(
            started_at=time.time() - 61, join_window_seconds=60, wagers={"alice": 20, "bob": 20},
        )
        result = self.heist.tick()
        self.assertIsNotNone(result)
        self.assertIsNone(self.heist.state)
        # whatever the outcome, no wager can just vanish or duplicate --
        # each participant's balance is either back to 100 (lost) or a
        # multiple of their wager above 100 (won), never below 100.
        self.assertGreaterEqual(self.db.get_user("alice")["points"], 100)
        self.assertGreaterEqual(self.db.get_user("bob")["points"], 100)

    def test_tick_with_no_participants_returns_none(self):
        self.heist.state = HeistState(started_at=time.time() - 61, join_window_seconds=60, wagers={})
        self.assertIsNone(self.heist.tick())


class BossBattleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        self.boss = BossBattleModule(self.db)
        self.bot = FakeBot(self.db)

    def tearDown(self):
        self.db.close()
        os.unlink(self.tmp.name)

    def _ctx(self, username, text, mod=False):
        parts = text.split()
        return CommandContext(message=make_message(username=username, text=text, mod=mod), args=parts[1:], bot=self.bot)

    def test_non_mod_cannot_start(self):
        result = self.boss._cmd_boss(self._ctx("alice", "!boss start"))
        self.assertIn("only mods", result)
        self.assertIsNone(self.boss.state)

    def test_mod_start_sets_hp_and_time(self):
        result = self.boss._cmd_boss(self._ctx("mod1", "!boss start 500 60", mod=True))
        self.assertIn("500", result)
        self.assertEqual(self.boss.state.max_hp, 500)
        self.assertEqual(self.boss.state.time_limit_seconds, 60)

    def test_attack_with_no_boss_stays_silent(self):
        self.assertIsNone(self.boss._cmd_attack(self._ctx("alice", "!attack")))

    def test_attack_reduces_hp_and_tracks_damage(self):
        self.boss.state = BossState(max_hp=10_000, hp=10_000, started_at=time.time(), time_limit_seconds=60)
        result = self.boss._cmd_attack(self._ctx("alice", "!attack"))
        self.assertIn("hits the boss", result)
        self.assertLess(self.boss.state.hp, 10_000)
        self.assertGreater(self.boss.state.damage_dealt["alice"], 0)

    def test_killing_blow_pays_out_and_clears_state(self):
        self.db.set_setting("boss_min_damage", "50")
        self.db.set_setting("boss_max_damage", "50")
        self.boss.state = BossState(max_hp=30, hp=30, started_at=time.time(), time_limit_seconds=60)
        result = self.boss._cmd_attack(self._ctx("alice", "!attack"))
        self.assertIn("defeated", result)
        self.assertIsNone(self.boss.state)
        self.assertEqual(self.db.get_user("alice")["points"], 200)  # 50 reward + 150 MVP bonus (defaults)

    def test_tick_before_time_limit_does_nothing(self):
        self.boss.state = BossState(max_hp=100, hp=100, started_at=time.time(), time_limit_seconds=60)
        self.assertIsNone(self.boss.tick())

    def test_tick_after_time_limit_clears_with_no_rewards(self):
        self.boss.state = BossState(
            max_hp=100, hp=40, started_at=time.time() - 61, time_limit_seconds=60,
            damage_dealt={"alice": 60},
        )
        result = self.boss.tick()
        self.assertIn("escapes", result)
        self.assertIsNone(self.boss.state)
        row = self.db.get_user("alice")
        self.assertEqual(row["points"] if row else 0, 0)  # no kill -> no reward


class OverlayServerTests(unittest.TestCase):
    """The whole point of overlay_server.py is that fetch()-ing the state
    file works over http:// where it can't over file://; assert the
    server actually serves both the html and a sibling json file, and
    that a second start() on the same port fails soft (None) instead of
    crashing the app."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        with open(os.path.join(self.tmpdir, "song_overlay.html"), "w") as fh:
            fh.write("<html>overlay</html>")
        with open(os.path.join(self.tmpdir, "song_overlay_state.json"), "w") as fh:
            fh.write('{"now_playing": null}')
        # Use a high, unlikely-to-collide port rather than the real
        # default so this test can run alongside a real running bot.
        self.port = 47_563
        self.server = overlay_server.start(self.tmpdir, port=self.port)

    def tearDown(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    def test_serves_html_and_json_over_http(self):
        self.assertIsNotNone(self.server)
        with urllib.request.urlopen(f"http://localhost:{self.port}/song_overlay.html", timeout=5) as resp:
            self.assertIn(b"overlay", resp.read())
        with urllib.request.urlopen(f"http://localhost:{self.port}/song_overlay_state.json", timeout=5) as resp:
            self.assertIn(b"now_playing", resp.read())

    def test_second_start_on_same_port_returns_none_instead_of_raising(self):
        second = overlay_server.start(self.tmpdir, port=self.port)
        self.assertIsNone(second)


class FakeFollowersAPI:
    """Stands in for TwitchAPI.get_recent_followers in AlertsModule
    tests -- returns whatever list `self.followers` currently holds
    (most-recent-first, like the real Helix endpoint), and records how
    many times it was actually called so throttling can be asserted."""

    def __init__(self, followers=None):
        self.followers = followers or []
        self.calls = 0

    def get_recent_followers(self, broadcaster_login, first=10):
        self.calls += 1
        return self.followers[:first]


class AlertsUsernoticeTests(unittest.TestCase):
    """Sub/resub/subgift/raid alerts come from Twitch IRC's USERNOTICE
    tags in real time -- see irc_client.py's on_usernotice callback."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmpdir, "chatbot.db"))
        self.alerts = AlertsModule(self.db)

    def test_sub_uses_default_template(self):
        msg = self.alerts.handle_usernotice({"msg-id": "sub", "display-name": "Foo"})
        self.assertIn("Foo", msg)
        self.assertIn("subscribed", msg)

    def test_resub_includes_cumulative_months(self):
        msg = self.alerts.handle_usernotice(
            {"msg-id": "resub", "display-name": "Foo", "msg-param-cumulative-months": "6"}
        )
        self.assertIn("Foo", msg)
        self.assertIn("6", msg)

    def test_subgift_includes_recipient(self):
        msg = self.alerts.handle_usernotice({
            "msg-id": "subgift", "display-name": "Gifter",
            "msg-param-recipient-display-name": "LuckyViewer",
        })
        self.assertIn("Gifter", msg)
        self.assertIn("LuckyViewer", msg)

    def test_raid_includes_viewer_count(self):
        msg = self.alerts.handle_usernotice(
            {"msg-id": "raid", "display-name": "BigStreamer", "msg-param-viewerCount": "42"}
        )
        self.assertIn("BigStreamer", msg)
        self.assertIn("42", msg)

    def test_unrecognized_msg_id_returns_none(self):
        self.assertIsNone(self.alerts.handle_usernotice({"msg-id": "ritual", "display-name": "Foo"}))

    def test_disabled_globally_suppresses_everything(self):
        self.db.set_setting("alerts_enabled", "0")
        self.assertIsNone(self.alerts.handle_usernotice({"msg-id": "sub", "display-name": "Foo"}))

    def test_sub_type_disabled_suppresses_sub_but_not_raid(self):
        self.db.set_setting("alerts_sub_enabled", "0")
        self.assertIsNone(self.alerts.handle_usernotice({"msg-id": "sub", "display-name": "Foo"}))
        msg = self.alerts.handle_usernotice(
            {"msg-id": "raid", "display-name": "Foo", "msg-param-viewerCount": "5"}
        )
        self.assertIsNotNone(msg)

    def test_custom_template_is_used(self):
        self.db.set_setting("alerts_sub_message", "WOW {user} subbed!!")
        msg = self.alerts.handle_usernotice({"msg-id": "sub", "display-name": "Foo"})
        self.assertEqual(msg, "WOW Foo subbed!!")


class AlertsFollowerPollingTests(unittest.TestCase):
    """New followers have no live IRC event any more -- detected by
    polling and diffing against what's already been seen, same pattern
    DiscordNotifierTests uses for went-live detection."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = Database(os.path.join(self.tmpdir, "chatbot.db"))
        self.alerts = AlertsModule(self.db)

    def test_first_check_establishes_baseline_without_announcing(self):
        api = FakeFollowersAPI([{"user_id": "1", "user_name": "Existing"}])
        messages = self.alerts.tick(api, "chan", now=1000.0)
        self.assertEqual(messages, [])

    def test_new_follower_after_baseline_is_announced(self):
        api = FakeFollowersAPI([{"user_id": "1", "user_name": "Existing"}])
        self.alerts.tick(api, "chan", now=1000.0)  # baseline
        api.followers = [
            {"user_id": "2", "user_name": "NewFollower"},
            {"user_id": "1", "user_name": "Existing"},
        ]
        messages = self.alerts.tick(api, "chan", now=1070.0)  # past the 60s throttle
        self.assertEqual(len(messages), 1)
        self.assertIn("NewFollower", messages[0])

    def test_throttled_before_interval_elapses(self):
        api = FakeFollowersAPI([{"user_id": "1", "user_name": "Existing"}])
        self.alerts.tick(api, "chan", now=1000.0)
        self.alerts.tick(api, "chan", now=1010.0)  # well under 60s later
        self.assertEqual(api.calls, 1)

    def test_reconnect_resets_baseline_so_no_false_flood(self):
        api = FakeFollowersAPI([{"user_id": "1", "user_name": "Existing"}])
        self.alerts.tick(api, "chan", now=1000.0)
        self.alerts.reset_session()
        messages = self.alerts.tick(api, "chan", now=2000.0)
        self.assertEqual(messages, [])

    def test_disabled_follow_alert_returns_nothing(self):
        self.db.set_setting("alerts_follow_enabled", "0")
        api = FakeFollowersAPI([{"user_id": "1", "user_name": "Existing"}])
        messages = self.alerts.tick(api, "chan", now=1000.0)
        self.assertEqual(messages, [])
        self.assertEqual(api.calls, 0)


class IRCUsernoticeDispatchTests(unittest.TestCase):
    """Confirms irc_client.py actually parses USERNOTICE lines and
    invokes on_usernotice with the tag dict, mirroring
    IRCClientBehaviorTests' style for the other callbacks."""

    def setUp(self):
        self.usernotices = []
        self.client = TwitchIRCClient(
            on_message=lambda m: None,
            on_usernotice=lambda tags: self.usernotices.append(tags),
        )
        self.client._our_nick = "mybot"
        self.client._channel = "testchan"

    def test_usernotice_tags_are_parsed_and_dispatched(self):
        self.client._handle_line(
            "@msg-id=raid;display-name=Foo;msg-param-viewerCount=10 "
            ":tmi.twitch.tv USERNOTICE #testchan"
        )
        self.assertEqual(len(self.usernotices), 1)
        self.assertEqual(self.usernotices[0]["msg-id"], "raid")
        self.assertEqual(self.usernotices[0]["display-name"], "Foo")


class BackupRestoreTests(unittest.TestCase):
    """create_backup/restore_backup round-trip through a real sqlite
    file (using sqlite3's own backup API, not a raw file copy -- see
    backup.py's docstring for why), plus the guardrails: a backup file
    is rejected outright unless it carries LCBot's manifest, and
    restoring saves the previous database aside rather than silently
    discarding it."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "chatbot.db")
        self.db = Database(self.db_path)
        self.db.upsert_command("hello", response="hi there!")
        self.db.add_quote("test quote", "someone", "Just Chatting", "mod")
        self.db.set_points("vieweruser", 500)

    def test_backup_then_restore_round_trips_data(self):
        backup_path = os.path.join(self.tmpdir, "mybackup.lcbotbak")
        backup.create_backup(self.db_path, backup_path, "0.1.1")
        self.assertTrue(os.path.exists(backup_path))

        # Simulate data changing after the backup was taken.
        self.db.delete_command("hello")
        self.db.set_points("vieweruser", 0)
        self.db.close()

        manifest = backup.restore_backup(backup_path, self.db_path)
        self.assertEqual(manifest["magic"], backup.BACKUP_MAGIC)

        restored = Database(self.db_path)
        self.assertIsNotNone(restored.get_command("hello"))
        self.assertEqual(restored.get_user("vieweruser")["points"], 500)
        restored.close()

    def test_restore_saves_previous_database_as_safety_backup(self):
        backup_path = os.path.join(self.tmpdir, "mybackup.lcbotbak")
        backup.create_backup(self.db_path, backup_path, "0.1.1")
        self.db.close()

        backup.restore_backup(backup_path, self.db_path)
        safety_copies = [
            f for f in os.listdir(self.tmpdir) if f.startswith("chatbot.db.pre-restore-")
        ]
        self.assertEqual(len(safety_copies), 1)

    def test_garbage_file_is_rejected_not_silently_accepted(self):
        fake_path = os.path.join(self.tmpdir, "not-a-backup.lcbotbak")
        with open(fake_path, "w") as fh:
            fh.write("this is not a zip file at all")
        with self.assertRaises(backup.InvalidBackupError):
            backup.read_manifest(fake_path)

    def test_valid_zip_without_lcbot_manifest_is_rejected(self):
        import zipfile
        fake_path = os.path.join(self.tmpdir, "wrong-format.lcbotbak")
        with zipfile.ZipFile(fake_path, "w") as zf:
            zf.writestr("readme.txt", "just some other zip file")
        with self.assertRaises(backup.InvalidBackupError):
            backup.read_manifest(fake_path)

    def test_export_portable_json_is_plain_and_readable(self):
        import json
        export_path = os.path.join(self.tmpdir, "export.json")
        backup.export_portable_json(self.db, export_path)
        with open(export_path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertIn("commands", data)
        self.assertIn("quotes", data)
        self.assertTrue(any(c["name"] == "hello" for c in data["commands"]))


class UpdateCheckTests(unittest.TestCase):
    """update_check.py is a courtesy, best-effort check -- every failure
    mode (network down, bad JSON, no releases yet) should come back as
    None rather than raising, so a broken check never blocks startup."""

    def _mock_response(self, payload_bytes):
        cm = mock.MagicMock()
        cm.__enter__.return_value.read.return_value = payload_bytes
        cm.__exit__.return_value = False
        return cm

    def test_newer_release_available(self):
        body = b'{"tag_name": "v9.9.9", "html_url": "https://example.com/releases/tag/v9.9.9"}'
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(body)):
            result = update_check.check_for_update("0.1.1")
        self.assertEqual(result["version"], "v9.9.9")
        self.assertEqual(result["url"], "https://example.com/releases/tag/v9.9.9")

    def test_same_or_older_release_returns_none(self):
        body = b'{"tag_name": "v0.1.0", "html_url": "https://example.com"}'
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(body)):
            result = update_check.check_for_update("0.1.1")
        self.assertIsNone(result)

    def test_network_failure_returns_none_not_raises(self):
        with mock.patch("urllib.request.urlopen", side_effect=OSError("no network")):
            result = update_check.check_for_update("0.1.1")
        self.assertIsNone(result)

    def test_malformed_json_returns_none_not_raises(self):
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(b"not json")):
            result = update_check.check_for_update("0.1.1")
        self.assertIsNone(result)

    def test_four_part_version_tags_compare_correctly(self):
        body = b'{"tag_name": "v0.1.1.2", "html_url": "https://example.com"}'
        with mock.patch("urllib.request.urlopen", return_value=self._mock_response(body)):
            result = update_check.check_for_update("0.1.1")
        self.assertEqual(result["version"], "v0.1.1.2")


class OAuthClientIdFallbackTests(unittest.TestCase):
    """The "Log in with Twitch" buttons and every Helix call need the
    same Client ID -- oauth.effective_client_id() is the one place
    that decides which one that is (the user's own, if they entered
    one in Settings' optional/advanced Client ID field, else LCBot's
    shared built-in app -- see oauth.LCBOT_CLIENT_ID). Covers both the
    pure function and Bot.refresh_apis actually using it when building
    the TwitchAPI client."""

    def test_uses_configured_client_id_when_present(self):
        self.assertEqual(oauth.effective_client_id("my-own-id"), "my-own-id")

    def test_strips_whitespace_from_configured_client_id(self):
        self.assertEqual(oauth.effective_client_id("  my-own-id  "), "my-own-id")

    def test_falls_back_to_shared_client_id_when_blank(self):
        with mock.patch.object(oauth, "LCBOT_CLIENT_ID", "lcbot-shared-id"):
            self.assertEqual(oauth.effective_client_id(""), "lcbot-shared-id")
            self.assertEqual(oauth.effective_client_id(None), "lcbot-shared-id")
            self.assertEqual(oauth.effective_client_id("   "), "lcbot-shared-id")

    def test_configured_client_id_overrides_shared_one(self):
        with mock.patch.object(oauth, "LCBOT_CLIENT_ID", "lcbot-shared-id"):
            self.assertEqual(oauth.effective_client_id("my-own-id"), "my-own-id")

    def test_returns_blank_when_neither_is_set(self):
        # Today's real state (LCBOT_CLIENT_ID is still "" until Ryan
        # registers the shared app) -- callers need to keep handling
        # this case (see MainWindow._show_missing_client_id_error)
        # until it's wired in.
        with mock.patch.object(oauth, "LCBOT_CLIENT_ID", ""):
            self.assertEqual(oauth.effective_client_id(""), "")

    def _make_bot(self):
        tmpdir = tempfile.mkdtemp()
        config = ConfigStore(os.path.join(tmpdir, "config.json"))
        config.data.helix_access_token = "some-token"
        db = Database(os.path.join(tmpdir, "chatbot.db"))
        return Bot(config, db)

    def test_refresh_apis_uses_shared_client_id_when_user_left_it_blank(self):
        bot = self._make_bot()
        bot.config.data.client_id = ""
        with mock.patch.object(oauth, "LCBOT_CLIENT_ID", "lcbot-shared-id"):
            bot.refresh_apis()
        self.assertIsNotNone(bot.twitch_api)
        self.assertEqual(bot.twitch_api.client_id, "lcbot-shared-id")

    def test_refresh_apis_prefers_users_own_client_id(self):
        bot = self._make_bot()
        bot.config.data.client_id = "my-own-id"
        with mock.patch.object(oauth, "LCBOT_CLIENT_ID", "lcbot-shared-id"):
            bot.refresh_apis()
        self.assertEqual(bot.twitch_api.client_id, "my-own-id")

    def test_refresh_apis_leaves_twitch_api_none_without_any_client_id_or_token(self):
        bot = self._make_bot()
        bot.config.data.client_id = ""
        bot.config.data.helix_access_token = ""
        with mock.patch.object(oauth, "LCBOT_CLIENT_ID", ""):
            bot.refresh_apis()
        self.assertIsNone(bot.twitch_api)


class ThemeTests(unittest.TestCase):
    """Only the pure color-math side of theme.py (the Themes tab's
    engine) -- no tk.Tk()/ttk.Style instantiation here, matching this
    file's no-tkinter policy. apply_theme()/apply_dark_theme() are
    exercised for real by the Xvfb+ImageMagick headless GUI check
    before each ship, not here."""

    def test_all_presets_have_the_same_keys(self):
        expected_keys = set(theme.PRESETS["classic"].keys())
        for name, colors in theme.PRESETS.items():
            self.assertEqual(set(colors.keys()), expected_keys, name)
            for key, value in colors.items():
                self.assertTrue(theme.is_valid_hex_color(value), f"{name}.{key} = {value!r}")

    def test_theme_order_and_labels_agree(self):
        self.assertEqual(set(theme.THEME_ORDER), set(theme.THEME_LABELS.keys()))
        # Every preset in PRESETS is reachable from the dropdown; "custom"
        # is the one label with no PRESETS entry (built on the fly).
        self.assertEqual(set(theme.THEME_ORDER) - {"custom"}, set(theme.PRESETS.keys()))

    def test_is_valid_hex_color(self):
        self.assertTrue(theme.is_valid_hex_color("#1a1a1a"))
        self.assertTrue(theme.is_valid_hex_color("#ABCDEF"))
        self.assertFalse(theme.is_valid_hex_color("1a1a1a"))       # missing '#'
        self.assertFalse(theme.is_valid_hex_color("#1a1a1"))       # too short
        self.assertFalse(theme.is_valid_hex_color("#gggggg"))      # not hex
        self.assertFalse(theme.is_valid_hex_color(""))
        self.assertFalse(theme.is_valid_hex_color(None))

    def test_build_custom_colors_produces_all_ten_keys(self):
        colors = theme.build_custom_colors("#101010", "#f0f0f0", "#00aaff")
        self.assertEqual(set(colors.keys()), set(theme.PRESETS["classic"].keys()))
        self.assertEqual(colors["BG"], "#101010")
        self.assertEqual(colors["FG"], "#f0f0f0")
        self.assertEqual(colors["ACCENT"], "#00aaff")
        for value in colors.values():
            self.assertTrue(theme.is_valid_hex_color(value))

    def test_build_custom_colors_falls_back_on_bad_input(self):
        # A blank/garbage color for any of the 3 inputs should fall back
        # to Classic's value for that slot rather than raising or
        # producing garbage output -- a bad Entry value can't be allowed
        # to crash theme application.
        colors = theme.build_custom_colors("not-a-color", "#ffffff", "#ff0000")
        self.assertEqual(colors["BG"], theme.PRESETS["classic"]["BG"])
        self.assertEqual(colors["FG"], "#ffffff")
        self.assertEqual(colors["ACCENT"], "#ff0000")

    def test_build_custom_colors_picks_readable_select_fg(self):
        # A bright accent should get dark selection text, a dark accent
        # should get light selection text -- either way, readable.
        bright = theme.build_custom_colors("#101010", "#f0f0f0", "#ffee00")
        dark = theme.build_custom_colors("#101010", "#f0f0f0", "#1a0030")
        self.assertEqual(bright["SELECT_FG"], "#141414")
        self.assertEqual(dark["SELECT_FG"], "#f5f5f5")

    def test_serialize_and_parse_custom_colors_round_trip(self):
        raw = theme.serialize_custom_colors("#101010", "#f0f0f0", "#00aaff")
        parsed = theme.parse_custom_colors(raw)
        self.assertEqual(parsed, ("#101010", "#f0f0f0", "#00aaff"))

    def test_parse_custom_colors_rejects_bad_input(self):
        self.assertIsNone(theme.parse_custom_colors(None))
        self.assertIsNone(theme.parse_custom_colors(""))
        self.assertIsNone(theme.parse_custom_colors("#101010|#f0f0f0"))          # only 2 parts
        self.assertIsNone(theme.parse_custom_colors("not-a-color|#f0f0f0|#00aaff"))

    def test_resolve_colors_known_preset(self):
        self.assertEqual(theme.resolve_colors("dark"), theme.PRESETS["dark"])

    def test_resolve_colors_unknown_name_falls_back_to_classic(self):
        self.assertEqual(theme.resolve_colors("not-a-real-theme"), theme.PRESETS["classic"])

    def test_resolve_colors_custom_with_valid_saved_colors(self):
        raw = theme.serialize_custom_colors("#101010", "#f0f0f0", "#00aaff")
        colors = theme.resolve_colors("custom", raw)
        self.assertEqual(colors, theme.build_custom_colors("#101010", "#f0f0f0", "#00aaff"))

    def test_resolve_colors_custom_with_missing_or_bad_saved_colors_falls_back(self):
        self.assertEqual(theme.resolve_colors("custom", ""), theme.PRESETS["classic"])
        self.assertEqual(theme.resolve_colors("custom", None), theme.PRESETS["classic"])
        self.assertEqual(theme.resolve_colors("custom", "garbage"), theme.PRESETS["classic"])

    def test_current_is_dark_follows_live_theme(self):
        # current_is_dark() drives the Windows titlebar mode for the
        # main window and every popup (_apply_windows_titlebar_mode) --
        # it must track whichever theme is actually live right now, via
        # the same module globals as everything else in this file, not
        # some cached value from whenever apply_theme() last ran.
        original_bg = theme.BG
        try:
            theme.BG = theme.PRESETS["classic"]["BG"]
            self.assertTrue(theme.current_is_dark())
            theme.BG = theme.PRESETS["light"]["BG"]
            self.assertFalse(theme.current_is_dark())
        finally:
            # Restore the live global so later tests in this process
            # (or a re-run) don't inherit a stale "light" BG.
            theme.BG = original_bg


class PathsTests(unittest.TestCase):
    """app_dir() -- built in response to Ryan's taskbar-icon report: a
    fresh rebuild still couldn't find assets/icon.ico, and resolving
    paths against the exe's own directory instead of os.getcwd() is
    the standard fix for a shortcut/launcher handing the process an
    unexpected working directory."""

    def test_app_dir_matches_cwd_when_not_frozen(self):
        self.assertFalse(getattr(sys, "frozen", False))
        self.assertEqual(paths.app_dir(), os.getcwd())

    def test_app_dir_uses_executable_directory_when_frozen(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", "/fake/dist/TwitchChatBotV2.exe"):
            self.assertEqual(paths.app_dir(), "/fake/dist")


if __name__ == "__main__":
    unittest.main()
