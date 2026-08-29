"""The command engine: dispatches !commands to built-in handlers or to
user-defined text templates, enforcing permission and cooldowns for both
the same way (so a streamer can rename/disable/re-permission a builtin
just like a custom one, matching how AnkhBot's command editor worked).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

from chatbot.core.irc_client import ChatMessage

logger = logging.getLogger("chatbot.commands")

PERMISSION_LEVELS = {
    "everyone": 0,
    "subscriber": 1,
    "vip": 2,
    "moderator": 3,
    "broadcaster": 4,
}

# $(name) or $(name.arg) style variables
_VAR_RE = re.compile(r"\$\((?P<name>[a-zA-Z0-9_]+)(?:\.(?P<arg>[^)]*))?\)")

CommandHandler = Callable[["CommandContext"], Optional[str]]


@dataclass
class CommandContext:
    """Everything a command handler needs: the triggering message, parsed
    args, and a back-reference to the running bot for cross-module access
    (currency, song queue, stream info, etc.)."""

    message: ChatMessage
    args: list[str]
    bot: "object"  # chatbot.core.bot.Bot, typed loosely to avoid a circular import

    @property
    def user(self) -> str:
        return self.message.display_name

    @property
    def username(self) -> str:
        return self.message.username

    def arg(self, index: int, default: str = "") -> str:
        return self.args[index] if index < len(self.args) else default

    def target_username(self) -> str:
        """First arg with an optional leading @, lowercased -- used by
        !give, !addquote-style commands that take 'a user' as an argument."""
        if not self.args:
            return ""
        return self.args[0].lstrip("@").lower()


@dataclass
class BuiltinCommand:
    name: str
    handler: CommandHandler
    default_permission: str = "everyone"
    default_cooldown_seconds: int = 5
    default_user_cooldown_seconds: int = 0
    description: str = ""


class CommandEngine:
    def __init__(self, db, resolve_variables: Callable[[str, CommandContext], str]):
        self.db = db
        self._resolve_variables = resolve_variables
        self._builtins: dict[str, BuiltinCommand] = {}
        self._last_global_use: dict[str, float] = {}

    def register_builtin(self, cmd: BuiltinCommand) -> None:
        self._builtins[cmd.name] = cmd
        self.db.upsert_command(
            cmd.name,
            response=cmd.description,
            permission=cmd.default_permission,
            cooldown_seconds=cmd.default_cooldown_seconds,
            user_cooldown_seconds=cmd.default_user_cooldown_seconds,
            enabled=True,
            builtin=True,
        )

    def add_custom_command(
        self,
        name: str,
        response: str,
        permission: str = "everyone",
        cooldown_seconds: int = 5,
        user_cooldown_seconds: int = 0,
    ) -> None:
        name = name.lower().lstrip("!")
        self.db.upsert_command(
            name,
            response=response,
            permission=permission,
            cooldown_seconds=cooldown_seconds,
            user_cooldown_seconds=user_cooldown_seconds,
            enabled=True,
            builtin=False,
        )

    def parse(self, text: str) -> Optional[tuple[str, list[str]]]:
        text = text.strip()
        if not text.startswith("!") or len(text) < 2:
            return None
        parts = text[1:].split()
        if not parts:
            return None
        return parts[0].lower(), parts[1:]

    def handle(self, message: ChatMessage, bot: "object") -> Optional[str]:
        parsed = self.parse(message.text)
        if not parsed:
            return None
        name, args = parsed

        row = self.db.get_command(name)
        if row is None and name not in self._builtins:
            return None
        if row is not None and not row["enabled"]:
            return None

        permission = row["permission"] if row else "everyone"
        cooldown = row["cooldown_seconds"] if row else 5
        user_cooldown = row["user_cooldown_seconds"] if row else 0

        required_rank = PERMISSION_LEVELS.get(permission, 0)
        if message.permission_rank() < required_rank:
            return None

        now = time.time()
        last_global = row["last_used"] if row else 0
        if now - (last_global or 0) < cooldown and message.permission_rank() < PERMISSION_LEVELS["moderator"]:
            return None

        if user_cooldown:
            last_user = self.db.get_user_cooldown(name, message.username)
            if now - last_user < user_cooldown and message.permission_rank() < PERMISSION_LEVELS["moderator"]:
                return None

        ctx = CommandContext(message=message, args=args, bot=bot)

        if name in self._builtins:
            try:
                result = self._builtins[name].handler(ctx)
            except Exception:
                logger.exception("builtin command %s raised", name)
                result = None
        else:
            result = self._resolve_variables(row["response"], ctx)

        self.db.mark_command_used(name)
        if user_cooldown:
            self.db.set_user_cooldown(name, message.username)
        return result


def default_variable_resolver(template: str, ctx: CommandContext) -> str:
    """Resolves $(var) / $(var.arg) tokens using whatever the bot's
    modules expose via bot.get_variable(name, arg, ctx). Falls back to
    leaving the token as-is if nothing recognizes it, so a typo is
    visible in chat instead of silently vanishing."""
    bot = ctx.bot

    def _sub(m: re.Match) -> str:
        name = m.group("name").lower()
        arg = m.group("arg")
        try:
            value = bot.get_variable(name, arg, ctx)  # type: ignore[attr-defined]
        except Exception:
            logger.exception("variable $(%s) raised", name)
            value = None
        return str(value) if value is not None else m.group(0)

    return _VAR_RE.sub(_sub, template)
