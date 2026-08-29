"""Song request queue -- !sr / !skip / !wrongsong / !queue / !song.

Playback itself happens in an OBS/Streamlabs OBS browser source
(overlay/song_overlay.html), not in this process. This module just
keeps the queue and writes out the current "now playing" state to a
JSON file that the overlay polls; advancing the queue is time-based
(tick() is called every few seconds by the bot's scheduler) so the
overlay never needs to talk back to Python.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine
from chatbot.modules.youtube_api import YouTubeAPI, YouTubeAPIError

logger = logging.getLogger("chatbot.songrequests")


class SongRequestsModule:
    def __init__(self, db, api: Optional[YouTubeAPI], state_path: str = os.path.join("overlay", "song_overlay_state.json")):
        self.db = db
        self.api = api
        self.state_path = state_path
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
        if self.now_playing is None:
            return f"@{ctx.user} nothing is playing."
        title = self.now_playing.get("title", "current song")
        self.now_playing = None
        self.tick()
        return f"Skipped \"{title}\"."

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

    # -- queue advancement / overlay state -------------------------------
    def tick(self) -> None:
        if not self.db.get_setting_bool("songrequests_enabled", True):
            return
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
