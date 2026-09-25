"""Minimal Twitch IRC client over plain TCP+TLS.

Twitch chat is IRC with a handful of custom tags/capabilities layered
on (badges, display names, message ids for deletion, etc.). This
avoids pulling in a generic IRC library so tag parsing and rate
limiting can be tailored to Twitch specifically. Runs its read loop on
a background thread and hands parsed messages to a callback, so it
can sit behind a Tkinter GUI without blocking it.
"""
from __future__ import annotations

import logging
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("chatbot.irc")

TWITCH_HOST = "irc.chat.twitch.tv"
TWITCH_PORT = 6697

# Twitch's own IRC servers ping idle clients to keep the connection
# alive -- not formally documented with an exact interval, but
# consistently reported in practice as roughly every ~5 minutes (see
# Twitch's own developer forum: "Keepalive question for Serverless
# Chatbot", discuss.dev.twitch.com/t/49857). If literally nothing --
# not even a PING -- has come through in noticeably longer than that,
# the connection is almost certainly a "zombie": still open as far as
# this process's socket is concerned, but silently dead on the wire
# (a router/NAT dropping an idle mapping after Ryan's PC has been
# streaming for hours, a sleeping network adapter, etc.) -- something
# a plain recv()-with-timeout loop can't detect on its own, since it
# just keeps timing out forever with no actual OSError to catch. This
# was the real, confirmed root cause of "disconnects during a long
# stream and I have to click Connect again" (2026-09-25): Ryan's own
# lcbot.log showed the app just sitting there thinking it was still
# connected, with no error of any kind, until he noticed and manually
# reconnected.
IDLE_CONNECTION_TIMEOUT_SECONDS = 360  # 6 minutes -- ~1 minute of margin above Twitch's own ~5-minute cadence

# How long to wait between automatic reconnect attempts after an
# unexpected drop (the idle-timeout above, or Twitch's own RECONNECT
# command -- see _RECONNECT_RE below) before giving up and surfacing a
# real "Disconnected" to the GUI. Twitch's own docs describe RECONNECT
# as routine, sent "for maintenance reasons" with "an indeterminate"
# amount of time before the server actually closes the connection
# (dev.twitch.tv/docs/chat/irc/) -- exactly the kind of thing a long
# stream is more likely to run into, and exactly the kind of thing
# worth quietly retrying through instead of surfacing to Ryan as a
# disconnect he has to notice and fix by hand.
RECONNECT_BACKOFF_SECONDS = (2.0, 5.0, 10.0, 20.0, 30.0)

# @tag1=val1;tag2=val2 :nick!user@host PRIVMSG #channel :message text
_TAGS_RE = re.compile(r"^@(?P<tags>\S+) :(?P<prefix>\S+) (?P<command>\S+) (?P<params>.*)$")
_NO_TAGS_RE = re.compile(r"^:(?P<prefix>\S+) (?P<command>\S+) (?P<params>.*)$")
_PING_RE = re.compile(r"^PING(?: :(?P<payload>.*))?$")
# Sent as a bare ":tmi.twitch.tv RECONNECT" with no trailing params at
# all -- deliberately its own regex rather than routing through
# _NO_TAGS_RE, which requires a space *after* the command before its
# params group, and so never matches a command with zero params.
_RECONNECT_RE = re.compile(r"^:\S+ RECONNECT\s*$")


@dataclass
class ChatMessage:
    raw: str
    username: str            # lowercase login name
    display_name: str
    channel: str
    text: str
    tags: dict = field(default_factory=dict)
    is_mod: bool = False
    is_broadcaster: bool = False
    is_vip: bool = False
    is_subscriber: bool = False
    message_id: str = ""

    def permission_rank(self) -> int:
        """Higher = more privileged. Mirrors the classic everyone < sub < vip < mod < broadcaster ladder."""
        if self.is_broadcaster:
            return 4
        if self.is_mod:
            return 3
        if self.is_vip:
            return 2
        if self.is_subscriber:
            return 1
        return 0


def _parse_tags(raw_tags: str) -> dict:
    tags = {}
    for pair in raw_tags.split(";"):
        if "=" in pair:
            k, v = pair.split("=", 1)
        else:
            k, v = pair, ""
        # Twitch escapes \s \n \r \: \\ in tag values
        v = (
            v.replace("\\s", " ")
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\:", ";")
            .replace("\\\\", "\\")
        )
        tags[k] = v
    return tags


class TwitchIRCClient:
    """Connects to Twitch chat, parses PRIVMSGs, and sends outgoing chat
    with rate limiting. All network I/O happens on a dedicated thread."""

    RATE_LIMIT_MESSAGES = 18   # stay under Twitch's 20/30s for a normal (non-mod) bot account
    RATE_LIMIT_WINDOW = 30.0

    def __init__(
        self,
        on_message: Callable[[ChatMessage], None],
        on_status: Optional[Callable[[str], None]] = None,
        on_raw: Optional[Callable[[str], None]] = None,
        on_join: Optional[Callable[[str], None]] = None,
        on_usernotice: Optional[Callable[[dict], None]] = None,
    ):
        self.on_message = on_message
        self.on_status = on_status or (lambda msg: None)
        self.on_raw = on_raw or (lambda line: None)
        self.on_join = on_join or (lambda username: None)
        # Twitch sends sub/resub/gift-sub/raid/ritual events as
        # USERNOTICE, tagged with msg-id and a bunch of msg-param-*
        # fields -- see AlertsModule.handle_usernotice for what it does
        # with these tags. Requires the twitch.tv/commands capability,
        # already requested below alongside tags/membership.
        self.on_usernotice = on_usernotice or (lambda tags: None)

        self._sock: Optional[ssl.SSLSocket] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._sender_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._send_queue: "queue.Queue[str]" = queue.Queue()
        self._connected = False
        self._channel = ""
        self._our_nick = ""
        self._sent_timestamps: list[float] = []
        self._send_lock = threading.Lock()
        self._announced_joined = False
        # Remembered from connect() so an unexpected drop can silently
        # re-establish the same connection (see _attempt_auto_reconnect)
        # without needing Bot.connect() to run again -- refresh_apis(),
        # the per-module session resets, etc. all stay untouched, since
        # nothing about auth or config actually changed, just the
        # transport underneath it.
        self._last_oauth_token = ""
        self._last_activity = 0.0
        self._reconnect_requested = False

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self, bot_username: str, oauth_token: str, channel: str, timeout: float = 15.0) -> None:
        if self._connected:
            self.disconnect()

        self._stop_event.clear()
        self._channel = channel.lower().lstrip("#")
        self._our_nick = bot_username.lower()
        self._last_oauth_token = oauth_token
        self._announced_joined = False
        self._reconnect_requested = False

        self._establish_socket(bot_username, oauth_token, self._channel, timeout=timeout)

        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True, name="irc-reader")
        self._sender_thread = threading.Thread(target=self._send_loop, daemon=True, name="irc-sender")
        self._reader_thread.start()
        self._sender_thread.start()

    def _establish_socket(
        self, bot_username: str, oauth_token: str, channel: str, timeout: float = 15.0, reconnect: bool = False
    ) -> None:
        """The actual socket-level handshake (TCP+TLS connect, then
        CAP/PASS/NICK/JOIN) -- pulled out of connect() so
        _attempt_auto_reconnect() can redo just this part on an
        unexpected drop, without re-running connect()'s own
        bookkeeping (which would incorrectly reset _stop_event/etc. on
        every retry) or anything in Bot.connect() above this class
        entirely. Raises OSError on failure, same as a raw socket
        connect always would -- callers decide what that means."""
        raw_sock = socket.create_connection((TWITCH_HOST, TWITCH_PORT), timeout=timeout)
        context = ssl.create_default_context()
        self._sock = context.wrap_socket(raw_sock, server_hostname=TWITCH_HOST)
        self._sock.settimeout(1.0)

        token = oauth_token if oauth_token.startswith("oauth:") else f"oauth:{oauth_token}"
        self._raw_send(f"CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership")
        self._raw_send(f"PASS {token}")
        self._raw_send(f"NICK {bot_username.lower()}")
        self._raw_send(f"JOIN #{channel}")

        self._connected = True
        self._last_activity = time.time()
        if reconnect:
            self.on_status(f"Reconnected automatically, rejoining #{channel}...")
        else:
            self.on_status(f"Connected, joining #{channel}...")

    def disconnect(self) -> None:
        self._stop_event.set()
        self._connected = False
        try:
            if self._sock:
                self._sock.close()
        except OSError:
            pass
        self._sock = None
        self.on_status("Disconnected")

    def send_message(self, text: str) -> None:
        """Queue a chat message; the sender thread enforces rate limits."""
        if not text:
            return
        # Twitch IRC messages are line-based; strip newlines defensively.
        text = text.replace("\r", " ").replace("\n", " ").strip()
        if not text:
            return
        if len(text) > 500:
            text = text[:497] + "..."
        self._send_queue.put(text)

    # -- internals -----------------------------------------------------
    def _raw_send(self, line: str) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendall((line + "\r\n").encode("utf-8"))
        except OSError as exc:
            logger.warning("send failed: %s", exc)
            self.on_status(f"Connection error: {exc}")
            self._connected = False

    def _send_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                text = self._send_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            with self._send_lock:
                now = time.time()
                self._sent_timestamps = [t for t in self._sent_timestamps if now - t < self.RATE_LIMIT_WINDOW]
                if len(self._sent_timestamps) >= self.RATE_LIMIT_MESSAGES:
                    sleep_for = self.RATE_LIMIT_WINDOW - (now - self._sent_timestamps[0]) + 0.1
                    time.sleep(max(sleep_for, 0.1))
                self._raw_send(f"PRIVMSG #{self._channel} :{text}")
                self._sent_timestamps.append(time.time())
            time.sleep(0.35)  # small courtesy gap between individual sends

    def _read_loop(self) -> None:
        # Outer loop: one iteration per socket "generation" -- runs the
        # inner read loop against whatever socket is currently live,
        # and on an unexpected drop (not a deliberate disconnect()),
        # tries to silently reconnect and keep going on the SAME
        # thread rather than spawning a new one. Only exits (and tells
        # the GUI "Disconnected") on a deliberate stop, or once
        # auto-reconnect has genuinely exhausted its attempts.
        while not self._stop_event.is_set():
            self._reconnect_requested = False
            self._last_activity = time.time()
            buffer = ""
            while not self._stop_event.is_set():
                try:
                    chunk = self._sock.recv(4096)
                except socket.timeout:
                    if time.time() - self._last_activity > IDLE_CONNECTION_TIMEOUT_SECONDS:
                        logger.warning(
                            "No data from Twitch IRC in over %ds (not even a keepalive PING) -- "
                            "treating the connection as dead", IDLE_CONNECTION_TIMEOUT_SECONDS,
                        )
                        break
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                self._last_activity = time.time()
                buffer += chunk.decode("utf-8", errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line:
                        self._handle_line(line)
                if self._reconnect_requested:
                    break
            # Inner loop ended -- figure out why before deciding what to do.
            self._connected = False
            if self._stop_event.is_set():
                break  # a deliberate disconnect() already set this -- nothing to reconnect
            if self._attempt_auto_reconnect():
                continue  # back to the top with a fresh socket, still the same thread
            break  # every retry failed -- give up and tell the GUI for real
        # Defensive, for the rare race where a reconnect attempt lands
        # a fresh socket right as disconnect() is also tearing things
        # down on another thread -- make sure we end up in a genuinely
        # closed state no matter which side "won".
        self._connected = False
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self.on_status("Disconnected")

    def _attempt_auto_reconnect(self) -> bool:
        """Tries to silently re-establish the connection after an
        unexpected drop, before bothering the GUI with a real
        "Disconnected" status. Returns True once back online, False if
        every attempt in RECONNECT_BACKOFF_SECONDS failed (the caller
        then falls through to a real disconnect, exactly like before
        this existed)."""
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        channel, username, token = self._channel, self._our_nick, self._last_oauth_token
        for attempt, delay in enumerate(RECONNECT_BACKOFF_SECONDS, start=1):
            if self._stop_event.wait(delay):
                return False  # disconnect() ran while we were waiting -- stop trying
            logger.info("Auto-reconnect attempt %d/%d to Twitch IRC...", attempt, len(RECONNECT_BACKOFF_SECONDS))
            try:
                self._establish_socket(username, token, channel, reconnect=True)
            except OSError as exc:
                logger.warning("Auto-reconnect attempt %d/%d failed: %s", attempt, len(RECONNECT_BACKOFF_SECONDS), exc)
                continue
            logger.info("Auto-reconnected to Twitch IRC after %d attempt(s)", attempt)
            return True
        logger.warning(
            "Auto-reconnect gave up after %d attempts over about %ds -- surfacing a real disconnect",
            len(RECONNECT_BACKOFF_SECONDS), int(sum(RECONNECT_BACKOFF_SECONDS)),
        )
        return False

    def _handle_line(self, line: str) -> None:
        self.on_raw(line)

        ping_match = _PING_RE.match(line)
        if ping_match:
            payload = ping_match.group("payload") or ""
            self._raw_send(f"PONG :{payload}")
            return

        if _RECONNECT_RE.match(line):
            # Twitch's own signal that it's about to close this
            # connection for maintenance, with "an indeterminate"
            # amount of time before it actually does
            # (dev.twitch.tv/docs/chat/irc/) -- rather than wait it
            # out, flag it so _read_loop breaks out and reconnects
            # proactively on its own schedule instead of Twitch's.
            logger.info("Twitch sent RECONNECT (server-side maintenance) -- reconnecting proactively")
            self._reconnect_requested = True
            return

        tags: dict = {}
        rest = line
        m = _TAGS_RE.match(line)
        if m:
            tags = _parse_tags(m.group("tags"))
            rest = f":{m.group('prefix')} {m.group('command')} {m.group('params')}"

        m2 = _NO_TAGS_RE.match(rest)
        if not m2:
            return
        prefix, command, params = m2.group("prefix"), m2.group("command"), m2.group("params")

        if command == "376" or command == "001":
            # Twitch sends both 001 (welcome) and 376 (end of MOTD) on
            # every connect -- only announce once, not twice.
            if not self._announced_joined:
                self._announced_joined = True
                self.on_status(f"Joined #{self._channel}")
            return

        if command == "NOTICE":
            text = params.split(":", 1)[1] if ":" in params else params
            self.on_status(f"NOTICE: {text}")
            return

        if command == "JOIN":
            login = prefix.split("!", 1)[0] if "!" in prefix else prefix
            if login.lower() != self._our_nick:
                try:
                    self.on_join(login.lower())
                except Exception:
                    logger.exception("on_join handler raised")
            return

        if command == "USERNOTICE":
            try:
                self.on_usernotice(tags)
            except Exception:
                logger.exception("on_usernotice handler raised")
            return

        if command != "PRIVMSG":
            return

        # params looks like: "#channel :message text"
        if " :" not in params:
            return
        chan_part, text = params.split(" :", 1)
        channel = chan_part.lstrip("#")

        login = prefix.split("!", 1)[0] if "!" in prefix else prefix
        if login.lower() == self._our_nick:
            # Twitch relays a bot's own sent messages back through the
            # same PRIVMSG stream as everyone else's. We already show
            # what we sent via on_outgoing the instant it's queued, so
            # passing this echoed copy along would both duplicate it in
            # the log and risk the command engine treating the bot's
            # own output as a fresh incoming command.
            return
        display_name = tags.get("display-name") or login
        badges = tags.get("badges", "")
        mod_flag = tags.get("mod") == "1" or "moderator/" in badges
        broadcaster_flag = "broadcaster/" in badges
        vip_flag = "vip/" in badges
        sub_flag = tags.get("subscriber") == "1" or "subscriber/" in badges or "founder/" in badges

        msg = ChatMessage(
            raw=line,
            username=login.lower(),
            display_name=display_name,
            channel=channel,
            text=text,
            tags=tags,
            is_mod=mod_flag,
            is_broadcaster=broadcaster_flag,
            is_vip=vip_flag,
            is_subscriber=sub_flag,
            message_id=tags.get("id", ""),
        )
        try:
            self.on_message(msg)
        except Exception:
            logger.exception("on_message handler raised")
