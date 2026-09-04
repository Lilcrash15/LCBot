"""Local audio playback via the Windows MCI API (winmm.dll), reached
through ctypes -- no extra Python package needed for this part. MCI is
Windows' own decades-old multimedia control interface; opening a file
through it hands the actual decoding off to whatever codec Windows has
installed (AAC/M4A decode has shipped in Windows Media Foundation since
Windows 7, which is why song downloads are requested as .m4a -- see
chatbot/core/youtube_audio.py). This keeps *this* module inside the
project's zero-third-party-dependency rule -- SFX (sfx.py) already
plays local files the same "just use what Windows ships with" way, via
winsound instead of MCI, since winsound only understands WAV.

Why this exists at all: with song audio actually playing inside this
process (rather than in a browser embedding YouTube's own player), OBS
can capture it directly with an "Application Audio Capture" source
pointed at the bot's exe, instead of needing a Browser Source -- that
was the whole point Ryan asked for this. See youtube_audio.py's
docstring for the real tradeoff that made local playback possible in
the first place (needing to fetch audio outside YouTube's official
player, which is genuinely outside their Terms of Service).

Caveat that can only be confirmed on a real Windows machine, not this
dev sandbox: MCI's format support depends on which Windows codecs are
actually installed. A normal, non-"N"/"KN" edition Windows 10/11 with
default media features should play .m4a fine; Windows N/KN editions
(media features stripped out, common in some regions/enterprise
images) may need the optional "Media Feature Pack" installed first.
play() returns False rather than raising or crashing if MCI can't open
a file for any reason, so the caller (SongRequestsModule) can skip to
the next song and tell chat what happened instead of getting stuck.

Confirmed live on Ryan's PC (2026-09-04): every song failed with
"Windows couldn't open that audio file" even though the download
itself was succeeding. Root cause was MCI's own device-type
auto-detection, not a missing codec -- `open "path" alias X` (no
explicit `type`) makes MCI guess which device to use from a fixed
registry list of file extensions (HKLM\SOFTWARE\Microsoft\Windows
NT\CurrentVersion\MCI Extensions), and that list does not include
.m4a/.mp4 on a stock Windows install even though Windows Media
Foundation can decode AAC fine. Fixed in play() by explicitly
requesting the "MPEGVideo" device (the Quartz/DirectShow MCI driver)
instead of relying on that guesswork, with a fallback to the old
auto-detect open if that ever fails too.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading

logger = logging.getLogger("chatbot.audio_player")

_ALIAS = "lcbot_song"


class AudioPlayer:
    """One reusable MCI device alias -- opened fresh for each song and
    closed either when it finishes or when something else takes over
    (stop()/play() again), since only one song plays at a time. Safe to
    construct and call on any platform: everything is a no-op (and
    logged once) when winmm.dll isn't available, e.g. this Linux dev
    sandbox or a future non-Windows build."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._winmm = None
        if sys.platform.startswith("win"):
            try:
                self._winmm = ctypes.windll.winmm  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                logger.exception("winmm.dll not available -- local audio playback disabled")
        else:
            logger.info("not on Windows -- local audio playback is a no-op here")

    def _send(self, command: str, buf_size: int = 260) -> tuple[int, str]:
        """Sends one MCI command string. Returns (error_code, response)
        -- error_code is 0 on success, matching mciSendStringW's own
        return value, so callers can tell "opened/played fine" apart
        from "Windows rejected this" without parsing text."""
        buf = ctypes.create_unicode_buffer(buf_size)
        err = self._winmm.mciSendStringW(command, buf, buf_size, 0)
        return err, buf.value

    def play(self, path: str) -> bool:
        """Stops whatever's currently playing and starts `path` fresh.
        Returns False (and logs a warning, not an exception) if MCI
        couldn't open or play the file at all -- an unsupported codec,
        a corrupt/incomplete download, or no winmm on this platform."""
        if self._winmm is None:
            return False
        with self._lock:
            self._send(f"close {_ALIAS}")
            try:
                size = os.path.getsize(path)
            except OSError:
                size = -1  # file genuinely missing -- MCI's own error below will say so too
            # Quoted so a temp path with spaces in it (song titles end
            # up in some filenames) doesn't get split into extra MCI
            # command tokens.
            #
            # Plain `open "path" alias X` (no explicit type) makes MCI
            # guess the device type from the file's extension via a
            # fixed registry list (HKLM\...\MCI Extensions) -- and
            # confirmed live, that list does NOT include .m4a/.mp4 on
            # a stock Windows install, even though Windows Media
            # Foundation can decode AAC just fine (the first live test,
            # 2026-09-04, failed with "Windows couldn't open that audio
            # file" for every song despite the download itself
            # succeeding). Explicitly requesting the "MPEGVideo" device
            # -- the Quartz/DirectShow MCI driver that's shipped with
            # Windows since XP and has been AAC-capable since Windows 7
            # -- sidesteps that missing registry mapping entirely
            # instead of relying on MCI's extension guesswork. Falls
            # back to the old auto-detect open if that somehow fails
            # too (e.g. a different file type whose extension IS in
            # the registry list), so this can only add compatibility,
            # never remove it.
            #
            # Both attempts' individual error codes are logged
            # separately (not just whichever one happened last) -- a
            # second live round after the MPEGVideo fix above still
            # failed, and only having the *last* attempt's error code
            # (277 = MCIERR_INTERNAL, a generic catch-all) made it
            # impossible to tell which of the two opens actually ran
            # into that, or whether the file itself is the problem
            # (corrupt/incomplete download) rather than which device
            # type MCI used.
            err1, _ = self._send(f'open "{path}" type MPEGVideo alias {_ALIAS}')
            err2 = None
            err = err1
            if err:
                err2, _ = self._send(f'open "{path}" alias {_ALIAS}')
                err = err2
            if err:
                logger.warning(
                    "MCI couldn't open %s (%s bytes) -- type MPEGVideo: error %d, auto-detect open: %s",
                    path, size, err1, (f"error {err2}" if err2 else "not attempted"),
                )
                return False
            err, _ = self._send(f"play {_ALIAS}")
            if err:
                logger.warning("MCI couldn't play %s (error %d)", path, err)
                self._send(f"close {_ALIAS}")
                return False
            return True

    def stop(self) -> None:
        if self._winmm is None:
            return
        with self._lock:
            self._send(f"stop {_ALIAS}")
            self._send(f"close {_ALIAS}")

    def is_playing(self) -> bool:
        """False once the song has actually finished (or was never
        successfully started) -- SongRequestsModule.tick() polls this
        instead of trusting YouTube's reported duration, since the real
        local file can run a little short or long, and playback only
        starts after the download finishes."""
        if self._winmm is None:
            return False
        with self._lock:
            err, mode = self._send(f"status {_ALIAS} mode")
            if err:
                return False
            return mode.strip().lower() == "playing"
