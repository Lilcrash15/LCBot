"""The quote database -- !quote, !addquote, !delquote."""
from __future__ import annotations

from chatbot.modules.commands import BuiltinCommand, CommandContext, CommandEngine


class QuotesModule:
    def __init__(self, db):
        self.db = db

    def register(self, engine: CommandEngine) -> None:
        engine.register_builtin(BuiltinCommand(
            name="quote",
            handler=self._cmd_quote,
            default_permission="everyone",
            default_cooldown_seconds=5,
            description="!quote [id] -- show a random quote, or a specific one by id.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="addquote",
            handler=self._cmd_addquote,
            default_permission="moderator",
            default_cooldown_seconds=1,
            description="!addquote <text> -- save a new quote.",
        ))
        engine.register_builtin(BuiltinCommand(
            name="delquote",
            handler=self._cmd_delquote,
            default_permission="moderator",
            default_cooldown_seconds=1,
            description="!delquote <id> -- remove a quote.",
        ))

    def _cmd_quote(self, ctx: CommandContext) -> str:
        if ctx.args and ctx.args[0].isdigit():
            row = self.db.get_quote(int(ctx.args[0]))
            if row is None:
                return f"@{ctx.user} no quote with that id."
        else:
            row = self.db.random_quote()
            if row is None:
                return f"@{ctx.user} there are no quotes yet -- mods can add one with !addquote."
        game = f" [{row['game']}]" if row["game"] else ""
        return f"Quote #{row['id']}: \"{row['text']}\" -{row['author'] or 'unknown'}{game}"

    def _cmd_addquote(self, ctx: CommandContext) -> str:
        text = " ".join(ctx.args).strip()
        if not text:
            return f"@{ctx.user} usage: !addquote <text> [- author]"
        author = ctx.message.channel
        if " - " in text:
            text, author = [p.strip() for p in text.rsplit(" - ", 1)]
        game = ctx.bot.get_variable("game", None, ctx) or ""
        quote_id = self.db.add_quote(text, author, game, ctx.username)
        return f"Added quote #{quote_id}."

    def _cmd_delquote(self, ctx: CommandContext) -> str:
        if not ctx.args or not ctx.args[0].isdigit():
            return f"@{ctx.user} usage: !delquote <id>"
        ok = self.db.delete_quote(int(ctx.args[0]))
        return f"Deleted quote #{ctx.args[0]}." if ok else f"@{ctx.user} no quote with that id."
