# Changelog

All notable changes to LCBot are logged here. Versions follow
`vMAJOR.MINOR.PATCH`.

## [Unreleased]

## [0.2.0] - 2026-09-04

### Removed
- **Local song audio playback** ("Play song audio directly from the
  bot"): removed after real-world testing confirmed Windows' MCI API
  never reliably played a downloaded song, even with better per-attempt
  error logging in place -- the file downloaded correctly (right size,
  no error) and MCI's `open` call still failed both with and without an
  explicit device type, with the same generic "internal error" either
  way. Rather than keep chasing an unreliable Windows multimedia API,
  song requests are back to always using the original Browser Source
  overlay, which has worked reliably from the start and stays inside
  YouTube's Terms of Service. This also removes LCBot's one third-party
  dependency (**yt-dlp**) -- the app is back to zero non-standard-library
  dependencies.

### Fixed
- **Taskbar icon showing correctly on one launch, then reverting to a
  generic icon on a later one** with no code change in between: the
  app never gave Windows a stable identity to hang the taskbar icon
  off, so Windows derived one on its own -- inconsistently, especially
  since the exe is built with PyInstaller's `--onefile` mode, which
  re-extracts to a brand new temp folder on every single launch. LCBot
  now explicitly sets a fixed Application User Model ID on startup
  (before any window opens), which is the standard fix for this exact
  symptom on Windows/PyInstaller/Tkinter apps.

### Added
- Chat alerts for new followers, subs/resubs/gift subs, and raids --
  each independently toggleable with an editable message template
  (Settings -> Chat Alerts). Subs/resubs/gift subs/raids fire in real
  time off Twitch chat; new followers are detected by polling every
  minute.
- Backup & Restore (Settings -> Backup & Restore): "Backup Now" saves
  a `.lcbotbak` snapshot of your commands/points/quotes/timers/
  settings; "Restore from Backup" loads one back in (rejecting
  anything that isn't a genuine LCBot backup, and always saving your
  current database aside first); "Export My Data (JSON)" writes a
  plain, non-proprietary JSON export for anyone who wants their data
  outside LCBot entirely.
- Update check: on launch and via Help -> Check for updates, checks
  GitHub for a newer release and shows a clickable "Update available"
  link in the top bar if one exists.
- A small one-time "Support LCBot" popup on launch linking to a
  donation page, with its own "don't show again" opt-out (also
  reachable any time from Help -> Support / buy me a coffee).
- A **Themes tab**: pick from 5 built-in looks (Classic -- the
  original AnkhBot black/orange, Dark Mode, Light Mode, Synthwave,
  Forest) or build your own from 3 colors (Background, Text, Accent)
  with the panel/muted-text/tab/selection colors worked out
  automatically. Applying a theme updates the whole app immediately,
  no restart needed.
- "Log in with Twitch" buttons for both the bot account and the
  streamer account -- no more separately registering your own app at
  dev.twitch.tv first. LCBot now uses its own registered Twitch app
  behind the scenes (same model as Nightbot/StreamElements/etc.), so
  logging in with your own Twitch account(s) is all that's needed.
  Registering your own Twitch app is still there as an optional,
  advanced fallback (Settings -> Client ID) for anyone who'd rather
  not share LCBot's app.

### Added
- Settings and Themes tabs now scroll -- no more resizing the window
  just to reach "Save Settings" or the bottom of a long section.
- **Saved Custom Profiles** (Themes tab): save up to 3 of your own
  custom Background/Text/Accent color schemes as Profile 1/2/3, and
  switch between them with one click without re-entering colors by
  hand each time.

### Fixed
- Popup windows (Support LCBot, Add Timer, Add Quote, the per-command
  editor, recent-messages, etc.) now match the main window's chrome:
  the native title bar follows the current theme's dark/light mode
  instead of always showing Windows' plain white titlebar, and they
  pick up LCBot's app icon instead of Tk's default feather icon.
  Switching themes live now also updates the main window's titlebar
  immediately, so a Light-preset window doesn't keep a dark titlebar.

### Added
- **Play song audio directly from the bot** (Settings -> Song Requests,
  off by default): instead of relying on a Browser Source pointed at
  the song-request overlay, LCBot can download and play each song's
  audio itself, so OBS/Streamlabs Desktop can pick it up with an
  "Application Audio Capture" source on TwitchChatBotV2.exe -- no
  Browser Source needed, and no separate video window either (audio
  only, by design). This needs **yt-dlp**, LCBot's one and only
  third-party dependency (everything else is still pure standard
  library) -- see requirements.txt and Settings -> Song Requests for
  the full explanation, including the Terms-of-Service tradeoff that
  comes with fetching audio outside YouTube's own player. Leave the
  setting off to keep using the original ToS-clean Browser Source
  overlay; nothing else about song requests changes either way. Also
  fixed: the "Skip Current" button in the Song Req tab wasn't actually
  skipping the current song (it called `tick()` directly instead of a
  real skip) -- now shared with `!skip` via a proper `skip_current()`.

### Fixed
- **Local song playback, round 2**: after the `.m4a`/`.mp4` device-type
  fix below, a live retest still failed the same way ("Windows
  couldn't open that audio file"), but the log only ever showed the
  *last* attempt's MCI error code (277, a generic catch-all), with no
  way to tell whether the new fix itself had actually failed or
  something else (e.g. a corrupt/incomplete download) was the real
  cause. LCBot now logs both attempts' error codes and the file's
  size separately, so the next `lcbot.log` will say exactly which
  attempt failed and with what code, instead of leaving that
  ambiguous. If it's still failing after updating, please also try
  playing the downloaded file (`song_cache\<id>.m4a`) directly in
  Windows Media Player or VLC -- that tells us whether this is a
  playback-method problem or a bad download.
- **Local song playback**: every song failed with "Windows couldn't
  open that audio file" even though the download itself succeeded.
  Windows' own device-type auto-detection doesn't recognize `.m4a`/
  `.mp4` files by default, even though Windows can decode AAC audio
  fine -- LCBot now explicitly tells Windows which player to use
  instead of leaving it to guess, which fixes this.
- **Taskbar/title bar icon, round 2**: the previous fix (below) didn't
  actually show up live -- the taskbar kept the stock feather icon
  even though the icon file was found and Tk raised no error. Root
  cause: Tk has a documented quirk where setting the icon only via
  `-default` can leave the taskbar entry itself on the stock icon.
  LCBot now sets the window's own icon directly as well as the
  `-default` (each independently, so one can't block the other), which
  is what actually reaches the taskbar.
- **Taskbar/title bar icon** showing Tk's default feather icon instead
  of LCBot's own: the app was looking for `assets\icon.ico` relative to
  whatever folder Windows happened to launch it from, which isn't
  always the same folder the exe itself lives in (e.g. a desktop
  shortcut without its own "Start in" folder). It now always looks
  next to the exe itself, regardless of how it was launched. If this
  still doesn't show the right icon after updating, check that an
  `assets` folder with `icon.ico` inside it actually sits right next
  to `TwitchChatBotV2.exe`.
- Added `lcbot.log` (next to the exe, auto-trimmed so it can't grow
  forever): since the app runs without a visible console window,
  there was previously no way to see a warning or error it logged --
  this is what caught the exact cause of the icon issue above, and
  should make any future "X isn't working" report faster to diagnose.

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
