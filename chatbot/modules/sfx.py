"""Sound Files (SFX): attach a local audio clip to a chat command, so
!hydrate or !airhorn actually plays something on the streamer's PC.
Playback uses winsound (stdlib, Windows-only) so there's no extra
dependency -- WAV files only for now; MP3 support would need a
third-party decoder. On non-Windows platforms (e.g. running the tests)
playback is a no-op so the rest of the app still works.
"""
from __future__ import annotations

import logging
import platform
import threading
from typing import Optional

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine

logger = logging.getLogger("chatbot.sfx")

_IS_WINDOWS = platform.system() == "Windows"
if _IS_WINDOWS:
    import winsound
else:
    winsound = None  # type: ignore[assignment]


class SFXModule:
    def __init__(self, db):
        self.db = db

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="sfx", handler=self._cmd_sfx,
            default_permission="everyone", default_cooldown_seconds=3,
            description="!sfx <name> -- play a sound file added in the SFX tab.",
        ))

    def _cmd_sfx(self, ctx: CommandContext) -> Optional[str]:
        if not ctx.args:
            names = ", ".join(r["name"] for r in self.db.all_sfx())
            return f"@{ctx.user} usage: !sfx <name>. Available: {names or '(none added yet)'}"
        name = ctx.args[0].lower()
        row = self.db.get_sfx(name)
        if row is None:
            return f"@{ctx.user} no sound named \"{name}\"."
        required = row["permission"]
        from chatbot.modules.commands import PERMISSION_LEVELS
        if ctx.message.permission_rank() < PERMISSION_LEVELS.get(required, 0):
            return None
        self.play(row["file_path"], row["volume"])
        self.db.mark_sfx_used(name)
        return None

    def play(self, file_path: str, volume: int = 100) -> None:
        """Fire-and-forget playback on a worker thread so it never blocks chat."""
        if not _IS_WINDOWS:
            logger.info("(sfx playback skipped, not on Windows): %s", file_path)
            return

        def _play():
            try:
                # winsound has no volume control; volume is stored for the
                # overlay/GUI display and for a future non-winsound backend.
                winsound.PlaySound(file_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
            except RuntimeError:
                logger.exception("failed to play sfx: %s", file_path)

        threading.Thread(target=_play, daemon=True).start()
