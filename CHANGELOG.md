# Changelog

All notable changes to LCBot are logged here. Versions follow
`vMAJOR.MINOR.PATCH`.

## [Unreleased]

## [0.1.1] - 2026-08-29

No functional changes -- first version actually published as a
GitHub release (v0.1.0 was tagged but never published).

## [0.1.0] - 2026-08-29

First tagged build.

### Added
- Core chat bot: connects to Twitch IRC, custom commands (`!addcom`/
  `!editcom`/`!delcom`/`!commands`) with variables (`$(user)`,
  `$(touser)`, `$(count)`, `$(1)`-`$(9)`, `$(points)`, `$(uptime)`,
  `$(game)`, `$(title)`, `$(followers)`, `$(viewers)`,
  `$(random.1-100)`), per-command permissions, and cooldowns.
- Currency/points system with passive earning, `!points`, `!give`,
  `!giveall`, `!top`.
- Mini games: `!gamble`, `!slots`, `!roulette`, plus the original
  AnkhBot's `!heist` and `!boss`/`!attack` group minigames (chat-only,
  no visuals).
- Moderation: link/caps/symbol-spam/banned-phrase/repeated-message
  filters with escalating strikes, plus a click-a-username menu in the
  Console tab for manual timeout/ban/unban.
- Timers, song requests (`!sr`/`!skip`/`!queue`/`!song`, YouTube-backed,
  with an OBS browser-source overlay), and quotes.
- Stream info commands (`!uptime`, `!title`, `!game`, `!followers`,
  `!followage`, `!viewers`, `!so`) via the Twitch Helix API.
- Dashboard tab with live stats and a "Basic" box (title/category
  editor, matching the original AnkhBot's own layout) that searches
  Twitch's real category list so the category set always matches
  Twitch's catalog exactly.
- Discord "went live" webhook announcements.
- Give Away, SFX, Event System (on-join/on-speak), and a separate
  co-op signup Queue.
- Dark/orange `ttk` theme modeled on the original AnkhBot R2 UI, a
  custom app icon, and a Windows dark-mode native title bar.
- Packaged as a standalone Windows executable via PyInstaller
  (`build_exe.bat` -> `dist/TwitchChatBotV2.exe`) -- no Python install
  required to run it.
- Plain-English error messages for common failures (expired/missing
  tokens, unreachable Twitch/Discord, bad settings) instead of raw
  exceptions or HTTP status codes.
