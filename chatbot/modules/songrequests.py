"""Song request queue -- !sr / !skip / !wrongsong / !queue / !song.

Playback happens in an OBS/Streamlabs OBS browser source
(overlay/song_overlay.html), not in this process. This module just
keeps the queue and writes out the current "now playing" state to a
JSON file that the overlay polls; advancing the queue is time-based
(tick() is called every few seconds by the bot's scheduler) since the
overlay never talks back to Python. Zero extra dependencies, stays
inside YouTube's Terms of Service (their own IFrame Player does the
actual playback).

Note for future reference (2026-09-04): a local-playback mode used to
exist here -- downloading each song's audio via yt-dlp and playing it
back in-process via the Windows MCI API, so OBS could capture the
bot's own application audio instead of needing a Browser Source. It
shipped, but real-world testing on Ryan's PC never got MCI to actually
play a downloaded file (every attempt failed with a generic MCI
"internal error", both with an explicit device type and with
auto-detect, despite the download itself completing correctly and the
file being a normal size) -- diagnosing MCI failures further wasn't
worth it against a Browser Source approach that already works
reliably and stays inside YouTube's ToS, so Ryan asked to remove it
and go back to Browser-Source-only. Removed rather than left disabled
behind a setting: chatbot/core/audio_player.py and
chatbot/core/youtube_audio.py are both gone, and the yt-dlp dependency
is gone with them, so the project is back to zero third-party
dependencies. If local playback is ever revisited, the removed files
are in git history (search this project's CHANGELOG for "local song
playback" to find the commit range) -- but the honest lesson from this
round is that MCI's own error reporting is too generic to have
diagnosed the real cause from log output alone.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from chatbot.core.paths import app_dir
from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine
from chatbot.modules.youtube_api import YouTubeAPI, YouTubeAPIError

logger = logging.getLogger("chatbot.songrequests")


class SongRequestsModule:
    def __init__(
        self,
        db,
        api: Optional[YouTubeAPI],
        state_path: Optional[str] = None,
    ):
        self.db = db
        self.api = api
        # Resolved here (not as a literal default-argument value) so
        # it's relative to the exe's own folder (app_dir(), see
        # paths.py) rather than whatever the OS set as the working
        # directory -- matters for the compiled exe, not source runs.
        self.state_path = state_path or os.path.join(app_dir(), "overlay", "song_overlay_state.json")
        self.now_playing: Optional[dict] = None
        self._write_state()

    def set_api(self, api: Optional[YouTubeAPI]) -> None:
        self.api = api

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="sr", handler=self._cmd_sr, default_permission="everyone",
            default_cooldown_seconds=3, default_user_cooldown_seconds=10,
            description="!sr <youtube url or search> -- queue a song.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="skip", handler=self._cmd_skip, default_permission="moderator",
            default_cooldown_seconds=1, description="Skip the currently playing song.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="wrongsong", handler=self._cmd_wrongsong, default_permission="everyone",
            default_cooldown_seconds=2, description="Remove your own most recent still-queued request.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="queue", handler=self._cmd_queue, default_permission="everyone",
            default_cooldown_seconds=10, description="Show the upcoming song queue.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="song", handler=self._cmd_song, default_permission="everyone",
            default_cooldown_seconds=5, description="Show what's currently playing.",
        ))

    def _cmd_sr(self, ctx: CommandContext) -> str:
        if not self.db.get_setting_bool("songrequests_enabled", True):
            return f"@{ctx.user} song requests are turned off right now."
        if self.api is None:
            return f"@{ctx.user} song requests need a YouTube API key set in Settings first."
        query = " ".join(ctx.args).strip()
        if not query:
            return f"@{ctx.user} usage: !sr <youtube link or search terms>"

        max_per_user = self.db.get_setting_int("songrequests_max_per_user_queued", 2)
        if self.db.count_queued_for_user(ctx.username) >= max_per_user:
            return f"@{ctx.user} you already have {max_per_user} songs queued -- wait for one to play."

        try:
            video = self.api.resolve(query)
        except YouTubeAPIError as exc:
            logger.warning("song lookup failed: %s", exc)
            return f"@{ctx.user} couldn't reach YouTube just now."
        if video is None:
            return f"@{ctx.user} couldn't find that song."

        max_duration = self.db.get_setting_int("songrequests_max_duration_seconds", 600)
        if video.duration_seconds > max_duration:
            return f"@{ctx.user} that's too long ({video.duration_seconds // 60}m, max {max_duration // 60}m)."

        self.db.enqueue_song(video.video_id, video.title, video.duration_seconds, ctx.username)
        position = len(self.db.queued_songs())
        self.tick()
        return f"@{ctx.user} queued \"{video.title}\" (position {position})."

    def _cmd_skip(self, ctx: CommandContext) -> str:
        title = self.skip_current()
        if title is None:
            return f"@{ctx.user} nothing is playing."
        return f"Skipped \"{title}\"."

    def skip_current(self) -> Optional[str]:
        """Stops whatever's currently playing and clears it, returning
        its title (or None if nothing was playing) -- shared by !skip
        and the Song Req tab's "Skip Current" button. Previously this
        button called tick() directly, which never actually cleared
        now_playing, so clicking it silently did nothing until the
        song finished naturally on its own -- both now go through this
        instead."""
        if self.now_playing is None:
            return None
        title = self.now_playing.get("title", "current song")
        self.now_playing = None
        self.tick()
        return title

    def _cmd_wrongsong(self, ctx: CommandContext) -> str:
        row = self.db.remove_last_request_by(ctx.username)
        if row is None:
            return f"@{ctx.user} you don't have anything queued."
        return f"@{ctx.user} removed \"{row['title']}\" from the queue."

    def _cmd_queue(self, ctx: CommandContext) -> str:
        rows = self.db.queued_songs()[:5]
        if not rows:
            return "The song queue is empty."
        parts = [f"{i+1}. {r['title']} ({r['requested_by']})" for i, r in enumerate(rows)]
        return "Up next: " + " | ".join(parts)

    def _cmd_song(self, ctx: CommandContext) -> str:
        if self.now_playing is None:
            return "Nothing is playing right now."
        return f"Now playing: \"{self.now_playing['title']}\" requested by {self.now_playing['requested_by']}."

    # -- queue advancement -------------------------------------------
    def tick(self) -> list:
        """Called every few seconds by the bot's scheduler. Always
        returns an empty list -- overlay mode never announces anything
        in chat, the browser overlay is the "what's playing" surface.
        Kept as a list return (rather than reverting to None) since
        bot.py's scheduler loop already iterates it and other modules
        (heist/boss_battle/timers) share that same convention."""
        if not self.db.get_setting_bool("songrequests_enabled", True):
            return []
        self._tick_overlay()
        return []

    def _tick_overlay(self) -> None:
        now = time.time()
        if self.now_playing is not None:
            elapsed = now - self.now_playing["started_at"]
            if elapsed < self.now_playing["duration_seconds"]:
                return  # still playing
            self.now_playing = None

        row = self.db.pop_next_song()
        if row is not None:
            self.now_playing = {
                "video_id": row["video_id"],
                "title": row["title"],
                "duration_seconds": row["duration_seconds"] or 180,
                "requested_by": row["requested_by"],
                "started_at": now,
            }
        self._write_state()

    def _write_state(self) -> None:
        upcoming = [
            {"title": r["title"], "requested_by": r["requested_by"]}
            for r in self.db.queued_songs()[:10]
        ]
        state = {"now_playing": self.now_playing, "queue": upcoming, "updated_at": time.time()}
        try:
            state_dir = os.path.dirname(self.state_path)
            if state_dir:
                os.makedirs(state_dir, exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.replace(tmp, self.state_path)
        except OSError:
            logger.exception("failed to write overlay state file")
