# LCBot

A self-hosted Twitch chat bot in the spirit of the original AnkhBot,
before Streamlabs bought it and folded it into "Streamlabs Chatbot."
Runs locally on your PC as a desktop app, no subscription, no cloud
account, all your data (points, commands, quotes) in a SQLite file
next to the app.

> **Status**: private, pre-release (`v0.1.0`). This repository isn't
> open source -- see [`LICENSE`](LICENSE). Compiled Windows builds
> are published on the [Releases](../../releases) page.

## Features

- **Custom commands** with variables (`$(user)`, `$(touser)`, `$(count)`,
  `$(1)`-`$(9)` for args, `$(points)`, `$(uptime)`, `$(game)`, `$(title)`,
  `$(followers)`, `$(viewers)`, `$(random.1-100)`), per-command
  permissions (everyone/subscriber/vip/moderator/broadcaster) and
  cooldowns.
  Manage them from chat too: `!addcom`, `!editcom`, `!delcom`, `!commands`.
- **Currency/points system**: passive earning for active chatters,
  `!points`, `!give`, `!giveall`, `!top` leaderboard.
- **Minigames**: `!gamble`, `!slots`, `!roulette` -- all odds/payouts
  configurable in the Mini Games tab. Plus two group minigames from the
  original AnkhBot: `!heist <amount>` (anyone can start or join a heist
  with their own wager; when the join window closes, some fraction of
  the crew "makes it out" with a multiplier on their own bet, the rest
  lose theirs) and `!boss start [hp] [seconds]` / `!attack` (mods spawn
  a boss with a shared HP pool, everyone chips in damage with
  `!attack`, and if it's defeated before time runs out every attacker
  gets a reward plus a bonus for whoever dealt the most damage). Both
  are purely chat/text -- no graphics, no health bar widget.
- **Moderation**: link filter (with a whitelist), excessive-caps filter,
  symbol-spam filter, banned-phrase filter, repeated-message filter,
  with escalating strikes -> timeout. Mods and the broadcaster are
  always exempt.
- **Timers**: scheduled chat announcements that only fire once chat's
  actually had activity since the last one.
- **Song requests**: `!sr`, `!skip`, `!wrongsong`, `!queue`, `!song`,
  backed by the YouTube Data API, playable on stream via a bundled OBS
  browser-source overlay (`overlay/song_overlay.html`).
- **Quotes**: `!quote`, `!addquote`, `!delquote`.
- **Stream info**: `!uptime`, `!title`, `!game`, `!followers`, `!followage`, `!viewers`
  (current live viewer count), `!so`. The Dashboard tab also shows a
  live "Live Viewers" tile, refreshed automatically every few seconds
  while connected. A "Basic" box at the top of the Dashboard (same
  spot the original AnkhBot put it) lets you update your title and
  category without leaving the app: click the refresh (↻) button to
  pull in the current title/game, edit the Title field, type a few
  letters into the Game field to search Twitch's own category list
  and pick the exact match from the dropdown (so it matches Twitch's
  category exactly, not just a typed name), then click the up-arrow
  (↑) button to push the change to Twitch. Needs the
  `channel:manage:broadcast` scope on the broadcaster token -- if you
  authorized before that was added, click "Authorize (broadcaster)"
  again in Settings to pick it up.
- **Console**: the live chat log renders Twitch chat badges (mod/VIP/
  sub/broadcaster/etc.) and Twitch emotes inline, not as raw text --
  images are fetched from Twitch's CDN on first use and cached to disk
  after that. Plain Unicode emoji in messages render however Windows'
  own font fallback handles them (Segoe UI Emoji), same as any other
  Windows app. A dropdown next to the send box lets you send a typed
  message either as the bot account (over the existing chat
  connection) or as yourself the streamer (via the Twitch API, using
  the broadcaster token from step 4) -- the same choice the original
  AnkhBot offered. Click any username in the log to view their recent
  messages or timeout/ban/unban them, same as clicking a name in
  Twitch's own chat -- moderation actions send the same `/timeout`,
  `/ban`, `/unban` chat commands the auto-moderation system uses, so
  the bot account needs to actually be a moderator in your channel for
  them to take effect.
- **App icon**: drop a `.ico` file at `assets/icon.ico` and both the
  running app's window/taskbar icon and the compiled exe's file icon
  will pick it up automatically on the next `build_exe.bat` run -- no
  code changes needed.
- **Discord "went live" announcements**: paste a Discord webhook URL
  into Settings -> Discord Announcements and check "Announce in
  Discord when I go live on Twitch." The bot checks Twitch every
  minute (piggybacking on the same stream-info call `!uptime`/`!title`
  use) and posts your message the moment you go from offline to live
  -- it won't re-announce while you're still live, and reopening the
  bot while a stream is already live won't fire a false announcement
  either. The message supports `{channel}`, `{title}`, and `{game}`
  placeholders. Use "Send test message" to confirm the webhook works
  before you rely on it.

Everything above is editable live from the GUI (Commands, Currency,
Moderation, Timers, Song Requests, Quotes, Users tabs) -- nothing
requires editing files by hand.

## Requirements

- Python 3.10 or newer (tested on 3.11/3.12). Get it from
  [python.org](https://www.python.org/downloads/) -- on Windows, check
  "Add python.exe to PATH" during install, and tkinter (the GUI
  toolkit) is included automatically.
- No third-party packages -- see `requirements.txt`.

## Setup

### 1. Run it

```
python run_bot.py
```

This creates `config.json` (connection settings) and `chatbot.db`
(everything else) next to the script the first time you run it.

### 2. Create a bot account (optional but recommended)

You *can* run the bot from your own Twitch account, but a separate
account (e.g. `yourname_bot`) keeps bot messages visually distinct in
chat and is what AnkhBot always recommended. Log into that account in
your browser before doing step 4.

### 3. Register a Twitch Dev Console app

1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
   and log in with **your streamer account** (not the bot account).
2. Click "Register Your Application."
   - Name: anything unique, e.g. `yourname-chatbot`
   - OAuth Redirect URLs: `http://localhost:17563/`
   - Category: Chat Bot
3. Save it, then copy the **Client ID** into the app's Settings tab.
   You generally don't need the Client Secret for this app (the bot
   uses the browser-based implicit flow), but it's stored if you have
   another use for it.

### 4. Authorize

In the Settings tab:
- **Bot chat OAuth token**: click "Get chat token via browser" while
  logged into your **bot account** in your browser. This opens Twitch's
  sign-in/authorize page and fills the token in for you.
- **Broadcaster access token**: click "Authorize (broadcaster)" while
  logged into **your own (streamer) account**. This powers `!uptime`,
  `!followers`, `!followage`, etc., lets you send messages as yourself
  from the Console tab's identity dropdown (`user:write:chat` scope),
  and lets you update your title/category from the pencil icon
  (`channel:manage:broadcast` scope) -- if you authorized before either
  of those scopes was added, just click "Authorize (broadcaster)"
  again to pick them up.

Fill in **Twitch channel to join** and **Bot account username**, then
click **Save Settings**.

### 5. YouTube API key (only needed for song requests)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/),
   create a project (or use an existing one).
2. Enable the **YouTube Data API v3**.
3. Create an API key under Credentials, and optionally restrict it to
   the YouTube Data API v3.
4. Paste it into Settings -> YouTube Data API key.

### 6. Discord webhook (only needed for went-live announcements)

1. In Discord, go to your server -> Server Settings -> Integrations ->
   Webhooks -> New Webhook, pick the channel you want announcements
   posted to, then click **Copy Webhook URL**.
2. Paste it into Settings -> Discord Announcements -> Webhook URL,
   check "Announce in Discord when I go live on Twitch," and click
   **Send test message** to make sure it's wired up correctly.

### 7. Connect

Click **Connect** at the top of the window. You should see "Joined
#yourchannel" in the Dashboard log. Type `!commands` in your own chat
to see the full list.

### 8. (Optional) Song request overlay

While the bot app is open it serves the `overlay/` folder over HTTP at
`http://localhost:17564/`. In OBS or Streamlabs Desktop, add a **Browser
Source**, leave **"Local file" unchecked**, and set the URL to:

```
http://localhost:17564/song_overlay.html
```

Set the size to at least 480x320. It polls the bot's live now-playing
state every few seconds and plays whatever's queued via the YouTube
IFrame API.

Don't use "Local file" / browse-to-the-.html-file mode for this one --
Chromium blocks a `file://` page from fetching a neighboring file (the
overlay's own now-playing state), so it'll load but sit stuck on
"Waiting for a song request..." forever with no audio. Serving it over
`http://localhost` instead sidesteps that restriction entirely.

## Where your data lives

- `config.json` -- channel, bot username, tokens, API keys. **Keep this
  private** -- it's your login. Don't upload it anywhere, don't put it
  in git without a `.gitignore`.
- `chatbot.db` -- SQLite database with commands, points, quotes, timers,
  moderation lists, and the song queue. Back it up if you care about
  your points economy.
- `overlay/song_overlay_state.json` -- transient, regenerated constantly
  next to `song_overlay.html` (so the overlay's relative fetch finds
  it); safe to delete any time the bot isn't running.

## Testing

Fast, no-network unit tests for the core logic (command engine,
currency math, moderation filters, IRC tag parsing, DB):

```
python -m unittest discover -s tests -v
```

## Architecture, if you want to extend it

```
chatbot/
  core/
    config.py           connection/secrets (JSON)
    database.py         SQLite wrapper -- everything the GUI edits
    irc_client.py        raw Twitch IRC over TLS, threaded
    oauth.py             browser-based Twitch OAuth (implicit flow)
    overlay_server.py    serves overlay/ over HTTP for the song overlay
    friendly_errors.py   plain-English Twitch/Discord/network error text
    bot.py               wires it all together, runs the scheduler
  modules/
    commands.py     the !command engine + $(variable) substitution
    currency.py     points, !gamble/!slots/!roulette
    heist.py        !heist group gamble
    boss_battle.py  !boss / !attack community damage race
    giveaway.py     !giveaway raffles
    moderation.py   chat filters -> delete/timeout decisions
    timers.py       scheduled announcements
    songrequests.py YouTube-backed request queue
    quotes.py       quote database
    streaminfo.py   Twitch Helix-backed !uptime/!followers/etc.
    twitch_api.py   Helix REST wrapper (also sends chat as the streamer,
                     updates title/category)
    youtube_api.py  YouTube Data API wrapper
    discord_notify.py Discord "went live" webhook announcements
    sfx.py          local sound-file playback (!sfx)
    event_system.py on-join / on-speak triggered messages
    game_queue.py   co-op/multiplayer signup queue (!join/!leave)
  gui/
    main_window.py  the whole Tkinter UI
    emote_cache.py  fetches/caches Twitch emote + badge images for Console
    theme.py        dark/orange ttk theme
overlay/
  song_overlay.html OBS browser-source player for song requests
assets/
  icon.ico          app/window/exe icon
tests/
  test_smoke.py     unit tests, no network or tkinter required
run_bot.py           entry point
build_exe.bat        PyInstaller build script -> dist\TwitchChatBotV2.exe
setup_github.bat      one-time: creates the private GitHub repo and pushes
release.bat           tags a version and publishes a GitHub release
LICENSE               proprietary, all rights reserved (see above)
CHANGELOG.md          version history
```

Every module that adds chat commands follows the same pattern: a
`register(engine)` method that calls `engine.register_builtin(...)`
for each command, and handler methods that take a `CommandContext` and
return the text to send (or `None` to say nothing). Custom commands
added via `!addcom` or the Commands tab go through the exact same
permission/cooldown pipeline as built-ins, stored in the `commands`
table -- so nothing built-in is special-cased in a way you can't also
get from a plain text command.

## Roadmap ideas (not built yet)

- Duel and Free-for-All minigames (Heist and Boss Battle are done)
- Per-rank cooldown overrides and a `!permit` command to bypass the
  link filter once

## Releases

Tagged versions and their change notes live in
[`CHANGELOG.md`](CHANGELOG.md). Each tagged release on the
[Releases](../../releases) page has the matching `TwitchChatBotV2.exe`
attached, built the same way `build_exe.bat` builds it locally --
grab that if you just want to run the bot without installing Python
or building it yourself.

To cut a new release: bump `__version__` in `chatbot/__init__.py`,
add an entry to `CHANGELOG.md`, then run `release.bat`, which tags the
current commit, pushes the tag, and (if the GitHub CLI is installed
and signed in) creates the GitHub release and attaches the compiled
exe automatically.
