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
  backed by the YouTube Data API, with a bundled OBS browser-source
  overlay (`overlay/song_overlay.html`) that stays entirely inside
  YouTube's own player -- see setup step 8 below.
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
  authorized before that was added, click "Log in with Twitch
  (streamer account)" again in Settings to pick it up.
- **Console**: the live chat log renders Twitch chat badges (mod/VIP/
  sub/broadcaster/etc.) and Twitch emotes inline, not as raw text --
  images are fetched from Twitch's CDN on first use and cached to disk
  after that. Plain Unicode emoji in messages render however Windows'
  own font fallback handles them (Segoe UI Emoji), same as any other
  Windows app. A dropdown next to the send box lets you send a typed
  message either as the bot account (over the existing chat
  connection) or as yourself the streamer (via the Twitch API, using
  the broadcaster token from step 3) -- the same choice the original
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
- **Chat alerts**: announces new followers, subs/resubs/gift subs, and
  raids right in chat, each independently toggleable with its own
  editable message template (Settings -> Chat Alerts). Subs, resubs,
  gift subs, and raids fire in real time straight off Twitch chat, the
  same way the original AnkhBot picked them up; new followers are
  detected by checking Twitch every minute (Twitch removed the old
  live "someone followed" chat event years ago, so this is the modern
  equivalent). Reconnecting or relaunching never floods chat thanking
  your whole existing follower list -- only follows *after* that point
  get announced.
- **Backup & Restore**: Settings -> Backup & Restore. "Backup Now"
  saves your commands, points, quotes, timers, and settings to a
  `.lcbotbak` file (a safe point-in-time snapshot, taken with SQLite's
  own backup mechanism so it's never caught mid-write); "Restore from
  Backup" loads one back in, refusing anything that isn't actually a
  genuine LCBot backup, and always saves your current database aside
  first just in case. `.lcbotbak` is LCBot's own file type only so the
  restore picker can't be pointed at the wrong file by accident --
  under the hood it's just your database in a labeled zip, nothing
  stops you opening it yourself. If you ever want your data in a
  format any other program can read, "Export My Data (JSON)" writes a
  plain, fully readable JSON file instead.
- **Update check**: on launch, and any time from Help -> Check for
  updates, LCBot checks GitHub for a newer release. If one exists, a
  small "Update available" link appears in the top bar -- click it to
  open the release page. Fails silently if there's no internet or
  GitHub's unreachable; it's a courtesy check, never something that
  blocks startup.
- **Support LCBot**: a small popup on launch (once per install, or
  disable it any time with its own "Don't show this again" checkbox --
  also reachable from Help -> Support / buy me a coffee) links to a
  donation page if LCBot's been useful for your stream. Doesn't touch
  anything else in the app and never nags beyond that first popup.
- **Themes**: a dedicated Themes tab to pick the app's look -- 5
  built-in options (Classic, the original AnkhBot black-and-orange
  look; Dark Mode; Light Mode; Synthwave; Forest) or your own custom
  scheme built from 3 colors you pick (Background, Text, Accent),
  with everything else (panels, muted text, tabs, selection highlight)
  worked out automatically to stay readable. Hit "Apply Theme" and the
  whole app updates immediately -- no restart needed.

Everything above is editable live from the GUI (Commands, Currency,
Moderation, Timers, Song Requests, Quotes, Users tabs) -- nothing
requires editing files by hand.

## Setup

### 1. Download and run

Grab `TwitchChatBotV2.exe` from the [Releases](../../releases) page
(newest version at the top) and run it -- no Python install needed,
nothing else to set up first. The first time it runs it creates a
blank `config.json` (connection settings) and `chatbot.db` (everything
else) next to itself. Every field starts empty; nothing is pre-filled,
so it's ready for anyone downloading it fresh.

(If you'd rather run it from source instead of the compiled exe, see
[Running from source](#running-from-source-optional) near the bottom.)

### 2. Create a bot account (optional but recommended)

You *can* run the bot from your own Twitch account, but a separate
account (e.g. `yourname_bot`) keeps bot messages visually distinct in
chat and is what AnkhBot always recommended. Log into that account in
your browser before doing step 3.

### 3. Log in with Twitch

No separate Twitch Dev Console signup needed -- that's handled by
LCBot's own registered app behind the scenes, the same way Nightbot,
StreamElements, and every other Twitch bot works: one app, and you
just authorize it for your own account(s). In the Settings tab:

- **Bot chat OAuth token**: click "Log in with Twitch (bot account)"
  while logged into your **bot account** in your browser. This opens
  Twitch's sign-in/authorize page and fills the token in for you.
- **Broadcaster access token**: click "Log in with Twitch (streamer
  account)" while logged into **your own (streamer) account**. This
  powers `!uptime`, `!followers`, `!followage`, etc., lets you send
  messages as yourself from the Console tab's identity dropdown
  (`user:write:chat` scope), and lets you update your title/category
  from the pencil icon (`channel:manage:broadcast` scope) -- if you
  authorized before either of those scopes was added, just click "Log
  in with Twitch (streamer account)" again to pick them up.

Fill in **Twitch channel to join** and **Bot account username**, then
click **Save Settings**.

<details>
<summary><strong>Advanced: use your own Twitch app instead</strong> (optional -- most people can skip this)</summary>

If you'd rather not rely on LCBot's shared app -- your own isolated
Twitch rate limits, or you just prefer it -- you can register your own
and LCBot will use it instead:

1. Go to [dev.twitch.tv/console/apps](https://dev.twitch.tv/console/apps)
   and log in with **your streamer account** (not the bot account).
2. Click "Register Your Application."
   - Name: anything unique, e.g. `yourname-chatbot`
   - OAuth Redirect URLs: `http://localhost:17563/`
   - Category: Chat Bot
3. Save it, then paste the **Client ID** into Settings -> "Client ID
   (optional, advanced)". You generally don't need the Client Secret
   for this app (the bot uses the browser-based implicit flow), but
   it's stored if you have another use for it. Once a Client ID is
   filled in there, both "Log in with Twitch" buttons above use your
   app instead of LCBot's shared one -- clear the field to switch back.

</details>

**Client IDs aren't secret** -- Twitch's own docs say they're "public
and can be embedded in a web page's source" -- so LCBot's shared one
being baked into this public repo isn't a security issue; it's the
same setup every hosted Twitch bot uses. The one thing that's never
shared, shipped, or asked for is a **Client Secret** or password.

### 4. YouTube API key (only needed for song requests)

1. Go to the [Google Cloud Console](https://console.cloud.google.com/),
   create a project (or use an existing one).
2. Enable the **YouTube Data API v3**.
3. Create an API key under Credentials, and optionally restrict it to
   the YouTube Data API v3.
4. Paste it into Settings -> YouTube Data API key.

### 5. Discord webhook (only needed for went-live announcements)

1. In Discord, go to your server -> Server Settings -> Integrations ->
   Webhooks -> New Webhook, pick the channel you want announcements
   posted to, then click **Copy Webhook URL**.
2. Paste it into Settings -> Discord Announcements -> Webhook URL,
   check "Announce in Discord when I go live on Twitch," and click
   **Send test message** to make sure it's wired up correctly.

### 6. Connect

Click **Connect** at the top of the window. You should see "Joined
#yourchannel" in the Dashboard log. Type `!commands` in your own chat
to see the full list.

### 8. Song request playback: the Browser Source overlay

While the bot app is open it serves the `overlay/` folder over HTTP at
`http://localhost:17564/`. In OBS or Streamlabs Desktop, add a
**Browser Source**, leave **"Local file" unchecked**, and set the URL to:

```
http://localhost:17564/song_overlay.html
```

Set the size to at least 480x320. It polls the bot's live now-playing
state every few seconds and plays whatever's queued via the YouTube
IFrame API -- this stays entirely inside YouTube's own player, so
there's nothing else to configure or any tradeoff to weigh.

Don't use "Local file" / browse-to-the-.html-file mode for this one --
Chromium blocks a `file://` page from fetching a neighboring file (the
overlay's own now-playing state), so it'll load but sit stuck on
"Waiting for a song request..." forever with no audio. Serving it over
`http://localhost` instead sidesteps that restriction entirely.

(An earlier version of LCBot briefly had an option to download and
play song audio directly from the bot itself, avoiding the Browser
Source. It's been removed -- Windows' MCI playback API never
reliably worked for it in practice -- so the Browser Source above is
the only, and only ever intended long-term, way to get song audio
on stream.)

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
- `lcbot.log` -- a plain text log of what the app's been doing,
  including anything that went wrong. The app runs without a visible
  console window, so this is the only place to actually see an error
  if something isn't working right (e.g. a wrong-looking taskbar icon,
  a song that won't play) -- worth checking here first, or attaching
  it if you're reporting a bug. Auto-trimmed so it can't grow forever;
  safe to delete any time the bot isn't running.

All of the above -- `config.json`, `chatbot.db`,
`lcbot.log`, etc. -- live right next to the exe itself, regardless of
which folder Windows happened to launch it from (a desktop shortcut,
Task Scheduler, etc.). If you ever see LCBot behaving as if it's
missing its settings/data (or its taskbar icon looks like Tk's stock
feather icon instead of LCBot's own), check that these files are
actually sitting in the same folder as `TwitchChatBotV2.exe` -- if
they're not, something is launching the exe in an unusual way.

## Testing

Fast, no-network unit tests for the core logic (command engine,
currency math, moderation filters, IRC tag parsing, DB):

```
python -m unittest discover -s tests -v
```

## Running from source (optional)

Only needed if you want to modify the code, or run it without the
compiled exe:

- Python 3.10 or newer (tested on 3.11/3.12). Get it from
  [python.org](https://www.python.org/downloads/) -- on Windows, check
  "Add python.exe to PATH" during install, and tkinter (the GUI
  toolkit) is included automatically.
- No third-party packages needed -- everything runs on the Python
  standard library.

```
python run_bot.py
```

This creates `config.json` and `chatbot.db` next to the script the
first time you run it, same as the compiled exe does next to itself.

## Architecture, if you want to extend it

```
chatbot/
  core/
    config.py           connection/secrets (JSON)
    paths.py             app_dir() -- resolves config.json/chatbot.db/
                         assets/overlay/lcbot.log next to the exe
                         itself, not whatever folder Windows happened
                         to launch it from
    database.py         SQLite wrapper -- everything the GUI edits
    irc_client.py        raw Twitch IRC over TLS, threaded
    oauth.py             browser-based Twitch OAuth (implicit flow)
    overlay_server.py    serves overlay/ over HTTP for the song overlay
    friendly_errors.py   plain-English Twitch/Discord/network error text
    backup.py            .lcbotbak backup/restore + portable JSON export
    update_check.py      checks GitHub for a newer release
    bot.py               wires it all together, runs the scheduler
  modules/
    alerts.py       follow/sub/raid chat alerts
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
    theme.py        ttk theming: the built-in theme presets (Classic/
                     Dark/Light/Synthwave/Forest), the custom-color-
                     scheme math behind the Themes tab, and applying
                     a theme (live, no restart) to the whole app
overlay/
  song_overlay.html OBS browser-source player for song requests
assets/
  icon.ico          app/window/exe icon
tests/
  test_smoke.py     unit tests, no network or display required (does
                     import tkinter itself -- fine on Windows' bundled
                     Python, which always has it -- just never opens a
                     window)
run_bot.py           entry point
build_exe.bat        PyInstaller build script -> dist\TwitchChatBotV2.exe
build_and_release.bat one-click: bump version, build, commit/push, tag, and publish a release
_ship_helpers.py      Python helper build_and_release.bat calls for version-number/CHANGELOG edits
setup_github.bat      one-time: creates the GitHub repo and pushes
release.bat           tags a version and publishes a GitHub release (no version bump/build -- see build_and_release.bat for the full one-click flow)
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
or building it yourself. Versions are always plain `MAJOR.MINOR.PATCH`
(e.g. `v1.2.0`) -- no partial or four-part versions.

To cut a new release, run **`build_and_release.bat`** -- a single
script that does the whole thing: asks whether this is a patch, minor,
or major version bump (or lets you type an exact one), bumps
`chatbot/__init__.py` and rolls `CHANGELOG.md`'s `[Unreleased]` section
into a dated one automatically, builds the exe, then commits, pushes,
tags, and (with the GitHub CLI installed and signed in) publishes the
GitHub release with the exe attached -- all in one run.

`setup_github.bat` (one-time repo creation), `push_update.bat`
(pushing ordinary code changes that aren't a release), and `release.bat`
(tag + publish an already-built exe, without the version bump/build
steps) still exist separately for those narrower cases.
