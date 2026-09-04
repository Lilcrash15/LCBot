"""SQLite persistence layer.

One connection, one lock, WAL mode so the GUI thread and the bot's
network thread can both hit the DB without stepping on each other.
Everything the GUI edits lives here: users/points, commands, quotes,
timers, banned words, and the song queue. Connection secrets live in
core/config.py instead.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional, Sequence

from chatbot.core.paths import app_dir

DEFAULT_DB_PATH = os.path.join(app_dir(), "chatbot.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    display_name TEXT,
    points INTEGER NOT NULL DEFAULT 0,
    watch_minutes INTEGER NOT NULL DEFAULT 0,
    rank TEXT NOT NULL DEFAULT 'viewer',
    first_seen REAL,
    last_seen REAL,
    last_active_minute REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS commands (
    name TEXT PRIMARY KEY,
    response TEXT NOT NULL DEFAULT '',
    permission TEXT NOT NULL DEFAULT 'everyone',
    cooldown_seconds INTEGER NOT NULL DEFAULT 5,
    user_cooldown_seconds INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    uses INTEGER NOT NULL DEFAULT 0,
    builtin INTEGER NOT NULL DEFAULT 0,
    last_used REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS command_user_cooldowns (
    command_name TEXT NOT NULL,
    username TEXT NOT NULL,
    last_used REAL NOT NULL,
    PRIMARY KEY (command_name, username)
);

CREATE TABLE IF NOT EXISTS quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    author TEXT,
    game TEXT,
    added_by TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS timers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    message TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL DEFAULT 15,
    min_messages_since_last INTEGER NOT NULL DEFAULT 5,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_fired REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS banned_phrases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL,
    is_regex INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS link_whitelist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS song_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    title TEXT,
    duration_seconds INTEGER DEFAULT 0,
    requested_by TEXT,
    requested_at REAL,
    played INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mod_strikes (
    username TEXT PRIMARY KEY,
    count INTEGER NOT NULL DEFAULT 0,
    last_strike REAL
);

CREATE TABLE IF NOT EXISTS sfx_files (
    name TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    volume INTEGER NOT NULL DEFAULT 100,
    permission TEXT NOT NULL DEFAULT 'everyone',
    cooldown_seconds INTEGER NOT NULL DEFAULT 5,
    last_used REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger_type TEXT NOT NULL,
    user_group TEXT NOT NULL DEFAULT 'everyone',
    username TEXT,
    message TEXT NOT NULL DEFAULT '',
    sfx_name TEXT,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS game_queue (
    username TEXT PRIMARY KEY,
    note TEXT,
    joined_at REAL
);

CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    text TEXT NOT NULL,
    logged_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_log_username ON chat_log (username, id);
"""

# Keeps chat_log from growing forever -- this is meant for the Console
# tab's "view this person's recent messages" action, not a permanent
# archive, so a rolling window of the most recent messages (across all
# users combined) is plenty.
CHAT_LOG_MAX_ROWS = 5000

DEFAULT_SETTINGS = {
    "currency_name": "points",
    "currency_earn_amount": "10",
    "currency_earn_interval_minutes": "5",
    "currency_active_bonus": "0",           # extra points/interval for users who chatted recently
    "gamble_min_bet": "10",
    "gamble_win_chance_pct": "45",
    "slots_min_bet": "10",
    "moderation_enabled": "1",
    "moderation_links_enabled": "1",
    "moderation_caps_enabled": "1",
    "moderation_caps_threshold_pct": "70",
    "moderation_caps_min_len": "10",
    "moderation_symbols_enabled": "1",
    "moderation_symbols_threshold_pct": "50",
    "moderation_repetition_enabled": "1",
    "moderation_banned_words_enabled": "1",
    "moderation_timeout_seconds": "600",
    "moderation_strikes_before_timeout": "2",
    "timers_global_enabled": "1",
    "songrequests_enabled": "1",
    "songrequests_max_duration_seconds": "600",
    "songrequests_max_per_user_queued": "2",
    "global_command_cooldown_seconds": "1",
    "queue_open": "0",
    "queue_game": "",
    "queue_cost": "0",
    "queue_sub_only": "0",
    "heist_min_wager": "10",
    "heist_join_window_seconds": "60",
    "boss_default_hp": "1000",
    "boss_default_seconds": "180",
    "boss_min_damage": "10",
    "boss_max_damage": "75",
    "boss_victory_reward": "50",
    "boss_mvp_bonus": "150",
    "alerts_enabled": "1",
    "alerts_follow_enabled": "1",
    "alerts_sub_enabled": "1",
    "alerts_raid_enabled": "1",
    "alerts_follow_message": "Thanks for the follow, {user}! \U0001F49C",
    "alerts_sub_message": "{user} just subscribed! Thanks for the support! \U0001F389",
    "alerts_resub_message": "{user} resubscribed for {months} months! \U0001F389",
    "alerts_subgift_message": "{user} gifted a sub to {recipient}! \U0001F381",
    "alerts_raid_message": "{user} is raiding with {viewers} viewers! Welcome raiders! \U0001F680",
    "hide_support_popup": "0",
    # Themes tab (chatbot/gui/theme.py) -- theme_name is one of
    # theme.THEME_ORDER ("classic"/"dark"/"light"/"synthwave"/"forest"/
    # "custom"); theme_custom_colors only matters when theme_name is
    # "custom" and holds the 3 user-picked colors as
    # "bg|fg|accent" (see theme.serialize_custom_colors/parse_custom_colors).
    "theme_name": "classic",
    "theme_custom_colors": "",
    # Up to 3 saved custom color schemes ("Profile 1"/"Profile 2"/
    # "Profile 3" in the Themes tab), same "bg|fg|accent" format as
    # theme_custom_colors above -- blank means that slot hasn't been
    # saved to yet. Separate from theme_custom_colors, which is always
    # whatever's currently applied/in the picker; saving a profile
    # copies that into one of these slots so it can be switched back
    # to later without re-entering the colors by hand.
    "theme_profile_1": "",
    "theme_profile_2": "",
    "theme_profile_3": "",
}


class Database:
    def __init__(self, path: str = DEFAULT_DB_PATH):
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA)
            for key, value in DEFAULT_SETTINGS.items():
                self._conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value)
                )
            self._conn.commit()

    # -- low level -------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- settings ----------------------------------------------------
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row is not None else default

    def get_setting_int(self, key: str, default: int = 0) -> int:
        val = self.get_setting(key)
        try:
            return int(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    def get_setting_float(self, key: str, default: float = 0.0) -> float:
        val = self.get_setting(key)
        try:
            return float(val) if val is not None else default
        except (TypeError, ValueError):
            return default

    def get_setting_bool(self, key: str, default: bool = False) -> bool:
        val = self.get_setting(key)
        if val is None:
            return default
        return val.strip() not in ("0", "false", "False", "")

    def set_setting(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def all_settings(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.query("SELECT key, value FROM settings")}

    # -- users ---------------------------------------------------------
    def touch_user(self, username: str, display_name: str) -> sqlite3.Row:
        now = time.time()
        row = self.query_one("SELECT * FROM users WHERE username = ?", (username,))
        if row is None:
            self.execute(
                "INSERT INTO users (username, display_name, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?)",
                (username, display_name, now, now),
            )
            return self.query_one("SELECT * FROM users WHERE username = ?", (username,))
        self.execute(
            "UPDATE users SET display_name = ?, last_seen = ? WHERE username = ?",
            (display_name, now, username),
        )
        return self.query_one("SELECT * FROM users WHERE username = ?", (username,))

    def get_user(self, username: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM users WHERE username = ?", (username.lower(),))

    # -- chat log (Console tab's "view this person's messages") ------
    def log_chat_message(self, username: str, display_name: str, text: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_log (username, display_name, text, logged_at) VALUES (?, ?, ?, ?)",
                (username.lower(), display_name, text, time.time()),
            )
            # Cheap rolling-window prune: a no-op once the table is
            # under the cap, since the WHERE clause is never true then.
            self._conn.execute(
                "DELETE FROM chat_log WHERE id <= (SELECT MAX(id) FROM chat_log) - ?",
                (CHAT_LOG_MAX_ROWS,),
            )
            self._conn.commit()

    def get_recent_messages(self, username: str, limit: int = 50) -> list[sqlite3.Row]:
        """Most recent messages first."""
        return self.query(
            "SELECT * FROM chat_log WHERE username = ? ORDER BY id DESC LIMIT ?",
            (username.lower(), limit),
        )

    def add_points(self, username: str, amount: int) -> int:
        with self._lock:
            self._conn.execute(
                "INSERT INTO users (username, display_name, points, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(username) DO UPDATE SET points = MAX(0, points + excluded.points)",
                (username, username, max(amount, 0), time.time(), time.time()),
            )
            if amount < 0:
                self._conn.execute(
                    "UPDATE users SET points = MAX(0, points + ?) WHERE username = ?",
                    (amount, username),
                )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT points FROM users WHERE username = ?", (username,)
            ).fetchone()
            return row["points"] if row else 0

    def set_points(self, username: str, amount: int) -> None:
        self.execute(
            "INSERT INTO users (username, display_name, points, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(username) DO UPDATE SET points = excluded.points",
            (username, username, max(amount, 0), time.time(), time.time()),
        )

    def top_users(self, limit: int = 5) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM users ORDER BY points DESC, watch_minutes DESC LIMIT ?", (limit,)
        )

    def all_active_usernames(self, since_seconds: float) -> list[str]:
        cutoff = time.time() - since_seconds
        rows = self.query("SELECT username FROM users WHERE last_seen >= ?", (cutoff,))
        return [r["username"] for r in rows]

    def add_watch_minutes(self, username: str, minutes: int) -> None:
        self.execute(
            "UPDATE users SET watch_minutes = watch_minutes + ? WHERE username = ?",
            (minutes, username),
        )

    def set_rank(self, username: str, rank: str) -> None:
        self.execute("UPDATE users SET rank = ? WHERE username = ?", (rank, username))

    # -- commands --------------------------------------------------
    def upsert_command(
        self,
        name: str,
        response: str = "",
        permission: str = "everyone",
        cooldown_seconds: int = 5,
        user_cooldown_seconds: int = 0,
        enabled: bool = True,
        builtin: bool = False,
    ) -> None:
        existing = self.query_one("SELECT name FROM commands WHERE name = ?", (name,))
        if existing:
            if builtin:
                # Don't clobber user-edited permission/cooldown on every app start;
                # only fill in the response text for builtins (used as a doc string).
                self.execute("UPDATE commands SET response = ? WHERE name = ?", (response, name))
            else:
                self.execute(
                    "UPDATE commands SET response=?, permission=?, cooldown_seconds=?, "
                    "user_cooldown_seconds=?, enabled=? WHERE name=?",
                    (response, permission, cooldown_seconds, user_cooldown_seconds, int(enabled), name),
                )
            return
        self.execute(
            "INSERT INTO commands (name, response, permission, cooldown_seconds, "
            "user_cooldown_seconds, enabled, builtin) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, response, permission, cooldown_seconds, user_cooldown_seconds, int(enabled), int(builtin)),
        )

    def get_command(self, name: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM commands WHERE name = ?", (name.lower(),))

    def all_commands(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM commands ORDER BY builtin DESC, name ASC")

    def delete_command(self, name: str) -> None:
        self.execute("DELETE FROM commands WHERE name = ? AND builtin = 0", (name,))

    def mark_command_used(self, name: str) -> None:
        self.execute(
            "UPDATE commands SET uses = uses + 1, last_used = ? WHERE name = ?",
            (time.time(), name),
        )

    def get_user_cooldown(self, command_name: str, username: str) -> float:
        row = self.query_one(
            "SELECT last_used FROM command_user_cooldowns WHERE command_name = ? AND username = ?",
            (command_name, username),
        )
        return row["last_used"] if row else 0.0

    def set_user_cooldown(self, command_name: str, username: str) -> None:
        self.execute(
            "INSERT INTO command_user_cooldowns (command_name, username, last_used) VALUES (?, ?, ?) "
            "ON CONFLICT(command_name, username) DO UPDATE SET last_used = excluded.last_used",
            (command_name, username, time.time()),
        )

    # -- quotes ------------------------------------------------------
    def add_quote(self, text: str, author: str, game: str, added_by: str) -> int:
        cur = self.execute(
            "INSERT INTO quotes (text, author, game, added_by, created_at) VALUES (?, ?, ?, ?, ?)",
            (text, author, game, added_by, time.time()),
        )
        return cur.lastrowid

    def get_quote(self, quote_id: int) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM quotes WHERE id = ?", (quote_id,))

    def random_quote(self) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM quotes ORDER BY RANDOM() LIMIT 1")

    def delete_quote(self, quote_id: int) -> bool:
        cur = self.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
        return cur.rowcount > 0

    def all_quotes(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM quotes ORDER BY id ASC")

    # -- timers --------------------------------------------------------
    def add_timer(self, name: str, message: str, interval_minutes: int, min_messages: int = 5) -> int:
        cur = self.execute(
            "INSERT INTO timers (name, message, interval_minutes, min_messages_since_last) "
            "VALUES (?, ?, ?, ?)",
            (name, message, interval_minutes, min_messages),
        )
        return cur.lastrowid

    def all_timers(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM timers ORDER BY id ASC")

    def update_timer(self, timer_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE timers SET {cols} WHERE id = ?", (*fields.values(), timer_id))

    def delete_timer(self, timer_id: int) -> None:
        self.execute("DELETE FROM timers WHERE id = ?", (timer_id,))

    def mark_timer_fired(self, timer_id: int) -> None:
        self.execute("UPDATE timers SET last_fired = ? WHERE id = ?", (time.time(), timer_id))

    # -- moderation ------------------------------------------------
    def add_banned_phrase(self, phrase: str, is_regex: bool = False) -> int:
        cur = self.execute(
            "INSERT INTO banned_phrases (phrase, is_regex) VALUES (?, ?)", (phrase, int(is_regex))
        )
        return cur.lastrowid

    def all_banned_phrases(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM banned_phrases ORDER BY id ASC")

    def delete_banned_phrase(self, phrase_id: int) -> None:
        self.execute("DELETE FROM banned_phrases WHERE id = ?", (phrase_id,))

    def add_link_whitelist(self, domain: str) -> None:
        self.execute("INSERT INTO link_whitelist (domain) VALUES (?)", (domain.lower(),))

    def all_link_whitelist(self) -> list[str]:
        return [r["domain"] for r in self.query("SELECT domain FROM link_whitelist")]

    def get_strikes(self, username: str) -> int:
        row = self.query_one("SELECT count FROM mod_strikes WHERE username = ?", (username,))
        return row["count"] if row else 0

    def add_strike(self, username: str) -> int:
        self.execute(
            "INSERT INTO mod_strikes (username, count, last_strike) VALUES (?, 1, ?) "
            "ON CONFLICT(username) DO UPDATE SET count = count + 1, last_strike = excluded.last_strike",
            (username, time.time()),
        )
        return self.get_strikes(username)

    def reset_strikes(self, username: str) -> None:
        self.execute("DELETE FROM mod_strikes WHERE username = ?", (username,))

    # -- song queue --------------------------------------------------
    def enqueue_song(self, video_id: str, title: str, duration_seconds: int, requested_by: str) -> int:
        row = self.query_one("SELECT COALESCE(MAX(position), 0) AS m FROM song_queue WHERE played = 0")
        next_pos = (row["m"] if row else 0) + 1
        cur = self.execute(
            "INSERT INTO song_queue (video_id, title, duration_seconds, requested_by, "
            "requested_at, position) VALUES (?, ?, ?, ?, ?, ?)",
            (video_id, title, duration_seconds, requested_by, time.time(), next_pos),
        )
        return cur.lastrowid

    def queued_songs(self) -> list[sqlite3.Row]:
        return self.query(
            "SELECT * FROM song_queue WHERE played = 0 ORDER BY position ASC"
        )

    def count_queued_for_user(self, username: str) -> int:
        row = self.query_one(
            "SELECT COUNT(*) AS c FROM song_queue WHERE played = 0 AND requested_by = ?",
            (username,),
        )
        return row["c"] if row else 0

    def pop_next_song(self) -> Optional[sqlite3.Row]:
        row = self.query_one(
            "SELECT * FROM song_queue WHERE played = 0 ORDER BY position ASC LIMIT 1"
        )
        if row:
            self.execute("UPDATE song_queue SET played = 1 WHERE id = ?", (row["id"],))
        return row

    def remove_last_request_by(self, username: str) -> Optional[sqlite3.Row]:
        row = self.query_one(
            "SELECT * FROM song_queue WHERE played = 0 AND requested_by = ? "
            "ORDER BY position DESC LIMIT 1",
            (username,),
        )
        if row:
            self.execute("DELETE FROM song_queue WHERE id = ?", (row["id"],))
        return row

    def clear_queue(self) -> None:
        self.execute("DELETE FROM song_queue WHERE played = 0")

    # -- SFX -----------------------------------------------------------
    def add_sfx(self, name: str, file_path: str, volume: int = 100,
                permission: str = "everyone", cooldown_seconds: int = 5) -> None:
        self.execute(
            "INSERT INTO sfx_files (name, file_path, volume, permission, cooldown_seconds) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
            "file_path=excluded.file_path, volume=excluded.volume, "
            "permission=excluded.permission, cooldown_seconds=excluded.cooldown_seconds",
            (name.lower(), file_path, volume, permission, cooldown_seconds),
        )

    def get_sfx(self, name: str) -> Optional[sqlite3.Row]:
        return self.query_one("SELECT * FROM sfx_files WHERE name = ?", (name.lower(),))

    def all_sfx(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM sfx_files ORDER BY name ASC")

    def delete_sfx(self, name: str) -> None:
        self.execute("DELETE FROM sfx_files WHERE name = ?", (name.lower(),))

    def mark_sfx_used(self, name: str) -> None:
        self.execute("UPDATE sfx_files SET last_used = ? WHERE name = ?", (time.time(), name.lower()))

    # -- event system (on-join / on-speak) ------------------------------
    def add_event(self, trigger_type: str, user_group: str, username: Optional[str],
                  message: str, sfx_name: Optional[str]) -> int:
        cur = self.execute(
            "INSERT INTO events (trigger_type, user_group, username, message, sfx_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (trigger_type, user_group, (username or "").lower() or None, message, sfx_name),
        )
        return cur.lastrowid

    def all_events(self, trigger_type: Optional[str] = None) -> list[sqlite3.Row]:
        if trigger_type:
            return self.query(
                "SELECT * FROM events WHERE trigger_type = ? ORDER BY id ASC", (trigger_type,)
            )
        return self.query("SELECT * FROM events ORDER BY trigger_type ASC, id ASC")

    def delete_event(self, event_id: int) -> None:
        self.execute("DELETE FROM events WHERE id = ?", (event_id,))

    def update_event(self, event_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self.execute(f"UPDATE events SET {cols} WHERE id = ?", (*fields.values(), event_id))

    # -- game queue (Queue tab -- separate from the song queue) --------
    def queue_join(self, username: str, note: str = "") -> bool:
        if self.query_one("SELECT username FROM game_queue WHERE username = ?", (username,)):
            return False
        self.execute(
            "INSERT INTO game_queue (username, note, joined_at) VALUES (?, ?, ?)",
            (username, note, time.time()),
        )
        return True

    def queue_leave(self, username: str) -> bool:
        cur = self.execute("DELETE FROM game_queue WHERE username = ?", (username,))
        return cur.rowcount > 0

    def queue_all(self) -> list[sqlite3.Row]:
        return self.query("SELECT * FROM game_queue ORDER BY joined_at ASC")

    def queue_clear(self) -> None:
        self.execute("DELETE FROM game_queue")
