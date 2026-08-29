"""Fast, no-network smoke tests for the core logic (DB, command engine
variable substitution, permission/cooldown gating, currency math,
moderation filters, and IRC tag parsing). Run with:

    python -m unittest discover -s tests -v

Nothing here touches Twitch or YouTube -- it's here to catch regressions
in the plumbing, not to validate live behavior.
"""
import os
import random
import sys
import tempfile
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chatbot.core import overlay_server
from chatbot.core.bot import Bot
from chatbot.core.config import ConfigStore
from chatbot.core.database import Database
from chatbot.core.friendly_errors import friendly_error_text
from chatbot.core.irc_client import ChatMessage, TwitchIRCClient, _parse_tags
from chatbot.modules.boss_battle import BossBattleModule, BossState
from chatbot.modules.commands import CommandContext, CommandEngine, default_variable_resolver
from chatbot.modules.currency import CurrencyModule
from chatbot.modules.discord_notify import DiscordNotifier
from chatbot.modules.event_system import EventSystemModule
from chatbot.modules.game_queue import GameQueueModule
from chatbot.modules.giveaway import GiveawayModule, GiveawayState
from chatbot.modules.heist import HeistModule, HeistState
from chatbot.modules.moderation import ModerationModule
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


if __name__ == "__main__":
    unittest.main()
