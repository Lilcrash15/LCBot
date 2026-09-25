# Changelog

All notable changes to LCBot are logged here. Versions follow
`vMAJOR.MINOR.PATCH`.

## [Unreleased]

## [1.0.1] - 2026-09-25

### Changed
- **The built exe is now `LCBot.exe`, renamed from `TwitchChatBotV2.exe`.**
  Ryan noticed the exe said "V2" while the actual release tag was
  `v1.0.0` and asked to make those match. The "V2" was never a version
  number -- it was an internal build name from when this exe first
  replaced an even older `TwitchChatBot.exe` build, kept stable back
  then purely so a rebuild's output filename wouldn't collide with a
  copy still running/locked from a previous test. That collision risk
  is gone now (different filename entirely), and keeping "V2" around
  was just confusing next to a real `v1.0.0` release tag, so the exe
  now simply matches the app's real name and version everywhere: the
  title bar, the About dialog, and the Releases page all say "LCBot".
  Existing `config.json`/`chatbot.db` files aren't affected -- LCBot
  finds its own data next to wherever it's actually running from,
  regardless of what the exe is named.

## [1.0.0] - 2026-09-25

### Changed
- **Window title and About dialog renamed from the generic "Twitch
  Chat Bot" to "LCBot"** -- matching the name already used everywhere
  else (the GitHub repo, README, CHANGELOG). The exe itself is still
  `TwitchChatBotV2.exe` on purpose (kept stable so rebuilds never
  collide with a locked, still-running old exe) -- this only changes
  what's displayed once it's running.
- **Discord went-live announcements now correctly fire even when the
  bot is opened after you're already live**, not just on an offline ->
  live transition it happens to catch. Ryan: streaming means opening a
  pile of programs (OBS, the game, Discord, LCBot, ...) and the bot
  isn't always first, so the old "only announce on an actual
  transition" rule (meant to avoid a false announcement on every
  reconnect) was actively wrong for how he actually uses it. Now
  identifies each broadcast by Twitch's own start time and remembers
  (persisted, so it survives a reconnect or even fully closing and
  reopening the app) whether that specific broadcast has already been
  announced -- so it announces exactly once per real stream, whether
  the bot catches the moment it goes live, connects after it already
  started, or reconnects mid-stream for any reason. The manual "Send
  now (backup)" button also marks the broadcast as announced when
  used, so it and the automatic check never both post the same one.

### Fixed
- **The bot disconnecting during long streams and needing a manual
  "Connect" click to come back.** Root-caused via a real `lcbot.log`
  pull off Ryan's PC (no errors of any kind around the drop -- the app
  just silently thought it was still connected) plus Twitch's own IRC
  docs: Twitch can send a `RECONNECT` command for server-side
  maintenance at any time ("an indeterminate" amount of time before it
  actually disconnects), and separately, a connection can go "zombie"
  -- silently dead on the wire (a router/NAT dropping an idle
  connection after hours of streaming, a sleeping network adapter) --
  in a way a plain receive-with-timeout loop never detects on its own,
  since it just keeps timing out with no actual error to catch. LCBot
  now watches for both: it acts on `RECONNECT` immediately instead of
  waiting Twitch out, and treats total silence (not even a keepalive
  ping, which Twitch sends roughly every 5 minutes) for longer than 6
  minutes as a dead connection. Either way, it now automatically
  reconnects on its own (a few quick attempts with a short backoff)
  entirely in the background -- no popup, no manual click, nothing to
  notice -- and only falls back to the existing "you got disconnected"
  alert if it genuinely can't get back online after several tries.
- **Discord "Send test message" / went-live announcements failing with
  "Discord webhook rejected the message: 403"**: Discord sits behind
  Cloudflare, which blocks the default User-Agent Python's `urllib`
  sends when none is set (Cloudflare error 1010, a generic-scripted-
  client block) -- not Discord's own webhook logic rejecting anything
  about the message. LCBot's Discord webhook requests now send a real
  `User-Agent` header (same pattern already used for Twitch's CDN in
  `emote_cache.py`), confirmed via multiple independent real-world
  reports of the exact same Cloudflare block, not a guess.

### Added
- **A "Send now (backup)" button next to the Discord went-live message**
  in Settings -> Discord Announcements. Posts the real went-live
  message immediately, with live title/game filled in from Twitch when
  available -- a manual way to get the announcement out if the
  automatic one doesn't fire for any reason, without waiting on or
  debugging the automatic check. Different from "Send test message"
  above it, which just posts a fixed sentence to prove the webhook
  itself works.
- **Discord went-live checks now actually log what they're doing** in
  `lcbot.log` -- previously totally silent unless something threw an
  exception, so "why didn't it announce" had no way to be diagnosed
  from the log. Now logs (once per connection, not spammed every tick)
  whether announcements are off and why, whether the channel was
  already live when the bot connected (by design, no announcement
  fires for that session -- it'll fire on the next real offline ->
  live transition), and when an announcement actually sends.

### Added
- **Console chat log now has a hanging indent on wrapped messages**,
  like real Twitch/Discord/Streamlabs chat -- a long message that wraps
  to a second line now indents under the message text instead of
  wrapping all the way back to the left edge under the timestamp.
- **A popup now appears (from any tab) if the bot gets disconnected**
  -- whether from a dropped connection or a manual disconnect that
  wasn't confirmed -- with a Reconnect button right there. It stays on
  screen until you dismiss it (not a toast that fades on its own) and
  plays a system alert sound. Fixes a real miss: a disconnect used to
  only show up as one line scrolling by in the Console tab, easy to
  miss entirely if you weren't staring at that tab while live.

### Changed
- **Moved the Connect/Disconnect button off the always-visible top bar
  and into Settings -> Connection.** It was too easy to click by
  accident while switching tabs mid-stream -- exactly what happened
  and went unnoticed until real chat messages had already been missed.
  Disconnecting now also asks for confirmation first. The connection
  status label stays visible from every tab (just not clickable).

### Fixed
- **Some characters in chat (WizeBot's own status messages, and
  potentially other bots/viewers) were showing as blank "tofu" boxes**
  instead of the actual symbol/emoji -- e.g. the ❗ and ❌ in a WizeBot
  stream-status message. Real Twitch badges/emotes (fetched as images)
  were never the problem; this was specifically plain-text Unicode
  symbol/emoji characters that Windows' Segoe UI font has no glyph for,
  combined with Tk's own font-fallback on Windows being unreliable for
  this case. Those characters now get explicitly routed to Segoe UI
  Emoji instead of whatever Tk would otherwise (inconsistently) fall
  back to.
- **Taskbar/title bar icon, round 7 -- forced it via the Win32 API
  directly**: round 6's fix (a proper multi-resolution `icon.ico`)
  didn't fully solve it -- confirmed the fixed file really was on disk
  and being read (via `lcbot.log` timestamps) at the exact launch that
  still showed Tk's stock feather icon in the taskbar. That rules out
  both "the call is failing" (round 5) and "the file is missing a
  size Windows needs" (round 6). What's left is a separately-
  documented Tk-on-Windows split: `iconbitmap()` only changes what a
  window itself reports as its icon (title bar, Alt+Tab); the taskbar
  button can fall back to the *window class*'s icon instead, and every
  Tk window in a process shares one registered class with Tk's own
  icon baked in as that class's icon -- `iconbitmap()` never touches
  that. LCBot now also calls the Win32 API directly (`LoadImageW` +
  `WM_SETICON` + `SetClassLongPtrW`) right after the existing
  `iconbitmap()` calls, forcing the icon onto both the window *and*
  its window class. This needs an actual rebuild (it's a code change,
  not just a swapped-out icon file) -- run `build_exe.bat` and relaunch.
- **Taskbar/title bar icon, round 6 -- the actual root cause**: with
  every icon-related call confirmed succeeding in `lcbot.log` (see the
  taskbar icon entries below) and a full Windows icon-cache wipe not
  fixing it either, the real cause turned out to be `assets\icon.ico`
  itself: it only contained a single 256x256 image. Windows' taskbar
  needs a small icon (16x16/32x32), and apparently can't always
  reliably generate a usable one on the fly from a single giant source
  the way Tkinter loads icons -- so the call "succeeded" (no error) but
  never actually produced a small icon, leaving Windows showing its
  own generic fallback. `icon.ico` is now a proper multi-resolution
  icon (16/24/32/48/64/128/256, real distinct artwork at each size, not
  Windows stretching one image) built from the same source image. If
  you're not rebuilding right away, just relaunching the exe should
  already show it correctly, since the icon is loaded from
  `assets\icon.ico` at runtime -- rebuild when convenient to also bake
  the fixed icon into the exe's own file icon.

### Added
- **The Dashboard now remembers the last title/game it saw**, and
  pulls the real, current title/game from Twitch automatically the
  moment you connect -- no more having to click the refresh button by
  hand every time you open the app or reconnect.
- **Chat alert for title/game changes** (Settings -> Chat Alerts,
  editable message, on by default): announces in chat when the title
  changes, the game changes, or both at once, while live. Works no
  matter how the change was made (LCBot's own Dashboard, Twitch's own
  dashboard, or the mobile app), and won't fire a false "changed"
  alert the moment you connect.

### Fixed
- **Bot/Streamer switch (bottom of the Console tab) getting stuck
  looking highlighted** after picking one -- a well-known Tkinter
  quirk where a readonly dropdown keeps showing its just-picked value
  with a solid highlight fill until something else takes the
  selection away, and nothing ever did. Fixed everywhere in the app
  this could happen, not just that one dropdown -- picking anything
  from a dropdown now also lands focus in the chat box, since you're
  usually about to type a message right after switching identities.
- **Chat badge icons (e.g. next to a bot account like WizeBot)
  sometimes missing and staying missing for the rest of the session**:
  found two real bugs investigating this. First, if the very first
  badge fetch after connecting hit a network hiccup, it got marked as
  "done" anyway, so every badge for the rest of that session silently
  came back empty with no retry. Second, a badge that got asked for
  *before* that first fetch finished (or during a failed one) got
  permanently remembered as "doesn't exist" even after a later fetch
  actually found it. Both are fixed, and every reconnect now also
  retries badges fresh, so a bad first attempt or new/changed scopes
  can't leave badges broken for the rest of the run. If this doesn't
  fully explain what showed up in your screenshot, an updated
  `lcbot.log` will now say exactly why a badge lookup failed --
  including a plain-English note if it's just that the broadcaster
  account isn't authorized yet (Settings -> Log in with Twitch
  (streamer account)), which badges also need.

### Changed
- **Buttons, text boxes, and dropdowns app-wide look more modern**:
  flatter, borderless buttons with roomier padding instead of the
  thin-bordered boxy look every button used to have; entries and
  dropdowns got the same roomier treatment plus a border that actually
  brightens on focus. The Dashboard's small refresh/update icon
  buttons in particular got a bigger, bolder glyph instead of a tiny
  symbol lost in a small box. Applies consistently across all 5 built-
  in themes and any custom color scheme, since it's all driven from
  the same central theme system -- still the same AnkhBot-inspired
  layout throughout, just less dated-looking.

### Fixed
- **Taskbar icon logging blind spot**: a report of the taskbar reverting
  to the default feather icon came back with lcbot.log showing
  *nothing* about it either way, which meant there was no way to tell
  whether the icon-setting code had failed silently or whether
  everything on our end had actually worked and something outside the
  app was overriding it. Root cause: the Windows taskbar-identity call
  (`SetCurrentProcessExplicitAppUserModelID`) returns a success/failure
  code that was never being checked, and the icon-setting code only
  ever logged failures, never successes. Both now log their outcome
  either way, so the next report will say for certain which case it
  is. Also confirmed via research (see pythonguis.com's PyInstaller
  icon guide) that Windows' own shell icon cache is documented to be
  aggressive about exactly this symptom -- if the log comes back
  showing everything succeeded and the icon is still wrong, that's the
  most likely explanation, and running `ie4uinit.exe -show` (or
  restarting Explorer) is the standard, no-code fix for it.

### Added
- **`!permit username`** (mod-only): temporarily exempts that user's
  very next message from every moderation filter (links, caps,
  symbols, banned phrases, repeated messages) -- for a raid host
  dropping a link, or letting someone through after a false-positive
  filter hit. Covers exactly one message and expires on its own after
  a configurable window (Moderation tab -> Thresholds, 60s by
  default) even if they never send one, so it can't leave a standing
  hole in moderation if you forget about it.

### Fixed
- **Moderation (timeout/ban/delete-message/unban) stopped working**,
  both the automatic filters and the click-a-username menu, with
  Twitch replying "Unrecognized command: /ban" (and the same for
  /timeout, /delete, /unban) -- Twitch quietly retired all four as
  plain IRC chat commands. Moderation now goes through Twitch's real
  Moderation API instead, using the bot account's own login (since
  Twitch requires the moderator on these calls to be the token's own
  owner, and it's the bot -- not the streamer -- that's actually
  modded in the channel). **This needs the bot account to log in
  again**: click "Log in with Twitch (bot account)" in Settings once
  after updating, so it picks up the two new permissions this needs
  (Twitch will show them on the authorize page). Nothing else about
  login changes, and the streamer-account login is unaffected.

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
