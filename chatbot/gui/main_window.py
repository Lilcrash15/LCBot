"""The Tkinter desktop UI. One window, tabbed, talking to a single Bot
instance. Bot/IRC/scheduler callbacks fire on background threads, so
they only ever push onto a thread-safe queue here; a `self.after()`
poll loop drains that queue on the Tk main thread, which is the only
thread allowed to touch widgets.

Tab layout and dark/orange styling are modeled on the original AnkhBot
R2's UI (Console, Dashboard, Commands, Timers, Quotes, Give Away, SFX,
Currency System, Mini Games, Event System, Song Requests, Queue,
Settings) with a couple of additions (Moderation, Users) that AnkhBot
didn't have a dedicated tab for.
"""
from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Optional

from chatbot import __version__
from chatbot.core import oauth, overlay_server
from chatbot.core.bot import Bot
from chatbot.core.config import ConfigStore
from chatbot.core.database import Database
from chatbot.core.friendly_errors import friendly_error_text
from chatbot.core.irc_client import ChatMessage
from chatbot.gui import theme
from chatbot.gui.emote_cache import EmoteBadgeCache
from chatbot.gui.theme import apply_dark_theme, style_listbox, style_text_widget

# The Console tab's own font choice -- bigger and easier to read than the
# rest of the app's controls, closer to what a modern chat client (Twitch,
# Streamlabs' own chat widget) or the original AnkhBot's console used than
# a small default Tk font. Segoe UI is Windows' own system font, so it's
# always present with no bundling needed.
CHAT_FONT_FAMILY = "Segoe UI"
CHAT_FONT_SIZE = 12

POLL_MS = 150
REFRESH_MS = 5000


class MainWindow(tk.Tk):
    def __init__(self, config: ConfigStore, db: Database):
        super().__init__()
        self.title(f"Twitch Chat Bot -- v{__version__}")
        self.geometry("1180x720")
        self.minsize(1180, 600)
        self.style = apply_dark_theme(self)
        self._apply_windows_dark_titlebar()
        self._apply_app_icon()

        self.config_store = config
        self.db = db
        self._event_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()

        self.bot = Bot(
            config, db,
            on_chat=lambda m: self._event_queue.put(("chat", m)),
            on_status=lambda s: self._event_queue.put(("status", s)),
            on_outgoing=lambda s, identity: self._event_queue.put(("outgoing", (s, identity))),
        )

        # Serves the overlay folder at http://localhost:17564/ so OBS's
        # Browser Source can fetch() the live state file (see
        # overlay_server.py for why "Local file" mode can't do this).
        self._overlay_server = overlay_server.start(os.path.join(os.getcwd(), "overlay"))

        # Twitch emote / chat badge images for the Console tab.
        self.emote_cache = EmoteBadgeCache(
            cache_dir=os.path.join(os.getcwd(), "emote_cache"),
            get_twitch_api=lambda: self.bot.twitch_api,
            get_broadcaster_login=lambda: self.config_store.data.channel,
        )

        self._build_toolbar()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(POLL_MS, self._drain_queue)
        self.after(REFRESH_MS, self._periodic_refresh)

        if config.data.autoconnect and config.data.is_ready_to_connect():
            self.after(500, self._connect)

    # -- window chrome --------------------------------------------------
    def _apply_windows_dark_titlebar(self) -> None:
        """Switches the native title bar (the strip with the window
        title and minimize/maximize/close buttons -- Tkinter has no way
        to draw or recolor that itself, it's OS chrome) into Windows'
        own dark mode, via the same undocumented DWM attribute apps
        like Discord and Spotify use for their dark titlebars. This
        can't be tested from the Linux dev sandbox, only on Ryan's
        actual Windows machine -- it's a no-op (silently, safely)
        anywhere else, including older Windows builds without this
        attribute. A fully custom-colored (e.g. orange) titlebar isn't
        something Windows exposes to a normal app without replacing the
        whole window frame and reimplementing drag/resize/snap by
        hand, which is a much bigger, riskier undertaking than this."""
        if not sys.platform.startswith("win"):
            return
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            dark_mode = ctypes.c_int(1)
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode)
            )
            if result != 0:
                # Older Windows 10 builds (before the 20H1 update) used
                # attribute id 19 instead of 20 for the same thing.
                DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, ctypes.byref(dark_mode), ctypes.sizeof(dark_mode)
                )
        except Exception:
            pass  # never let a titlebar cosmetic fail startup

    def _apply_app_icon(self) -> None:
        """Sets the window/taskbar icon from assets/icon.ico if that
        file exists next to the app. Safe no-op if it doesn't -- so
        this can be wired up ahead of actually having an icon file."""
        icon_path = os.path.join(os.getcwd(), "assets", "icon.ico")
        if os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except tk.TclError:
                pass

    # -- toolbar ---------------------------------------------------------
    def _build_toolbar(self) -> None:
        """Replaces the OS-native menu bar (a real tk.Menu attached via
        root.config(menu=...)) with a themed strip of ttk.Menubutton
        widgets. The native menu bar renders with Windows' own chrome
        and mostly ignores color styling, which is why it used to show
        up as a plain light-gray strip clashing with the dark theme --
        ttk.Menubutton is an ordinary themeable widget, so this one
        actually follows the dark/orange theme (see theme.py's
        "Toolbar.*" styles)."""
        toolbar = ttk.Frame(self, style="Toolbar.TFrame")
        toolbar.pack(side="top", fill="x")

        cred_menu = tk.Menu(self, tearoff=0, **theme.popup_menu_kwargs())
        cred_menu.add_command(label="Twitch / YouTube credentials...",
                               command=lambda: self.notebook.select(self.settings_tab))
        ttk.Menubutton(toolbar, text="Credentials", menu=cred_menu, style="Toolbar.TMenubutton").pack(
            side="left", padx=(4, 0), pady=2
        )

        help_menu = tk.Menu(self, tearoff=0, **theme.popup_menu_kwargs())
        help_menu.add_command(label="About", command=self._show_about)
        ttk.Menubutton(toolbar, text="Help", menu=help_menu, style="Toolbar.TMenubutton").pack(
            side="left", padx=(2, 0), pady=2
        )

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About",
            f"Twitch Chat Bot v{__version__}\n\n"
            "A self-hosted bot in the spirit of the original AnkhBot, "
            "before it became Streamlabs Chatbot. No cloud account, "
            "no subscription -- everything runs and is stored locally.",
        )

    # -- layout -----------------------------------------------------------
    def _build_ui(self) -> None:
        top = ttk.Frame(self, padding=6)
        top.pack(fill="x")
        self.connect_btn = ttk.Button(top, text="Connect", command=self._toggle_connect)
        self.connect_btn.pack(side="left")
        self.status_label = ttk.Label(top, text="Disconnected", style="Muted.TLabel")
        self.status_label.pack(side="left", padx=10)
        # Live viewer count, shown right next to the "Joined #channel"
        # status text -- same Bot.last_stream_info cache the Dashboard
        # tab's "Live Viewers" tile reads (see _refresh_live_viewers),
        # so this never makes its own Helix call either. Blank/hidden
        # text (rather than "offline"/"not set up") keeps this small
        # top-bar slot uncluttered when there's nothing to show.
        self.viewers_label = ttk.Label(top, text="", style="Muted.TLabel")
        self.viewers_label.pack(side="left", padx=10)

        # An orange-bordered frame around the notebook echoes the frame
        # AnkhBot draws around its whole tab body.
        border = tk.Frame(self, bg="#e8720c")
        border.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        inner = tk.Frame(border, bg="#1a1a1a")
        inner.pack(fill="both", expand=True, padx=2, pady=2)

        self.notebook = notebook = ttk.Notebook(inner)
        notebook.pack(fill="both", expand=True)

        self.console_tab = ttk.Frame(notebook)
        self.dashboard_tab = ttk.Frame(notebook)
        self.commands_tab = ttk.Frame(notebook)
        self.timers_tab = ttk.Frame(notebook)
        self.quotes_tab = ttk.Frame(notebook)
        self.giveaway_tab = ttk.Frame(notebook)
        self.sfx_tab = ttk.Frame(notebook)
        self.currency_tab = ttk.Frame(notebook)
        self.minigames_tab = ttk.Frame(notebook)
        self.moderation_tab = ttk.Frame(notebook)
        self.event_tab = ttk.Frame(notebook)
        self.songs_tab = ttk.Frame(notebook)
        self.queue_tab = ttk.Frame(notebook)
        self.users_tab = ttk.Frame(notebook)
        self.settings_tab = ttk.Frame(notebook)

        for tab, label in [
            (self.console_tab, "Console"), (self.dashboard_tab, "Dashboard"),
            (self.commands_tab, "Commands"), (self.timers_tab, "Timers"),
            (self.quotes_tab, "Quotes"), (self.giveaway_tab, "Give Away"),
            (self.sfx_tab, "SFX"), (self.currency_tab, "Currency"),
            (self.minigames_tab, "Mini Games"), (self.moderation_tab, "Moderation"),
            (self.event_tab, "Events"), (self.songs_tab, "Song Req"),
            (self.queue_tab, "Queue"), (self.users_tab, "Users"),
            (self.settings_tab, "Settings"),
        ]:
            notebook.add(tab, text=label)

        self._build_console_tab()
        self._build_dashboard_tab()
        self._build_commands_tab()
        self._build_timers_tab()
        self._build_quotes_tab()
        self._build_giveaway_tab()
        self._build_sfx_tab()
        self._build_currency_tab()
        self._build_minigames_tab()
        self._build_moderation_tab()
        self._build_event_tab()
        self._build_songs_tab()
        self._build_queue_tab()
        self._build_users_tab()
        self._build_settings_tab()

    # -- console ------------------------------------------------------
    def _build_console_tab(self) -> None:
        frame = self.console_tab
        self.chat_log = tk.Text(
            frame, state="disabled", wrap="word", height=28,
            font=(CHAT_FONT_FAMILY, CHAT_FONT_SIZE),
            spacing1=2, spacing3=7, padx=10, pady=6,
        )
        style_text_widget(self.chat_log)
        self._chat_color_tags: set[str] = set()
        self._chat_click_tags: set[str] = set()
        self.chat_log.tag_configure(
            "timestamp", foreground=theme.MUTED_FG, font=(CHAT_FONT_FAMILY, CHAT_FONT_SIZE - 3)
        )
        self.chat_log.tag_configure("username", foreground=theme.FG, font=(CHAT_FONT_FAMILY, CHAT_FONT_SIZE, "bold"))
        self.chat_log.tag_configure(
            "system", foreground=theme.MUTED_FG, font=(CHAT_FONT_FAMILY, CHAT_FONT_SIZE - 1, "italic")
        )
        self.chat_log.tag_configure(
            "outgoing_prefix", foreground=theme.ACCENT, font=(CHAT_FONT_FAMILY, CHAT_FONT_SIZE, "bold")
        )
        # Pack the send row FIRST, pinned to the bottom, before the chat
        # log. Tk's pack() carves out each widget's space in the order
        # it's packed -- packing the expanding, fill="both" Text widget
        # first (as this used to do) let it claim the entire tab at its
        # requested size (28 lines at the new bigger font is tall), and
        # once that ate all the vertical space there was nothing left
        # for the send row below it, so it got squeezed off-screen.
        # Reserving the send row's space first guarantees it always has
        # room, and the chat log fills whatever's left.
        send_frame = ttk.Frame(frame)
        send_frame.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        # The original AnkhBot had a small dropdown here to switch
        # whether a typed message goes out as the bot account or as the
        # streamer -- the bot account sends over the existing IRC
        # connection; "Streamer" posts via the Helix Chat API using the
        # broadcaster's own authorized token instead.
        self.send_identity_var = tk.StringVar(value="Bot")
        ttk.Combobox(
            send_frame, textvariable=self.send_identity_var, state="readonly", width=8,
            values=["Bot", "Streamer"],
        ).pack(side="left", padx=(0, 4))

        self.send_entry = ttk.Entry(send_frame)
        self.send_entry.pack(side="left", fill="x", expand=True)
        self.send_entry.bind("<Return>", lambda e: self._send_manual_message())
        ttk.Button(send_frame, text="Send", command=self._send_manual_message).pack(side="left", padx=4)

        self.chat_log.pack(side="top", fill="both", expand=True, padx=8, pady=8)

    def _append_log(self, text: str, tag: str = "system") -> None:
        """For system/status lines and the bot's own outgoing echo --
        anything that isn't a live chat message from a viewer (those go
        through _append_chat_message instead, for badges/emotes)."""
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", text + "\n", (tag,))
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def _append_outgoing(self, text: str, prefix: str = "Bot") -> None:
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"[{time.strftime('%H:%M:%S')}] ", ("timestamp",))
        self.chat_log.insert("end", f"{prefix}: ", ("outgoing_prefix",))
        self.chat_log.insert("end", text + "\n")
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def _username_tag(self, color: str) -> str:
        """A per-user Text tag colored to match Twitch's own username
        color for that person (the 'color' IRC tag), same as real Twitch
        chat -- falls back to the default 'username' tag if the viewer
        never picked one, or the value looks malformed."""
        if not (color.startswith("#") and len(color) == 7):
            return "username"
        try:
            int(color[1:], 16)
        except ValueError:
            return "username"
        tag_name = f"user_color_{color[1:].lower()}"
        if tag_name not in self._chat_color_tags:
            self.chat_log.tag_configure(tag_name, foreground=color, font=(CHAT_FONT_FAMILY, CHAT_FONT_SIZE, "bold"))
            self._chat_color_tags.add(tag_name)
        return tag_name

    def _append_chat_message(self, msg: ChatMessage) -> None:
        """Renders a live chat line with inline chat badges and Twitch
        emotes, instead of raw text -- badge, emote, and username-color
        ids all come straight off the IRC tags Twitch already sends (see
        irc_client.py), no extra API calls needed per message."""
        self.chat_log.configure(state="normal")
        self.chat_log.insert("end", f"[{time.strftime('%H:%M:%S')}] ", ("timestamp",))

        for badge_spec in (msg.tags.get("badges") or "").split(","):
            if "/" not in badge_spec:
                continue
            set_id, version = badge_spec.split("/", 1)
            image = self.emote_cache.get_badge_image(set_id, version)
            if image is not None:
                self.chat_log.image_create("end", image=image)
                self.chat_log.insert("end", " ")

        username_tag = self._username_tag(msg.tags.get("color") or "")
        click_tag = self._user_click_tag(msg.username, msg.display_name)
        self.chat_log.insert("end", f"{msg.display_name}: ", (username_tag, click_tag))
        self._insert_message_with_emotes(msg.text, msg.tags.get("emotes", ""))
        self.chat_log.insert("end", "\n")
        self.chat_log.see("end")
        self.chat_log.configure(state="disabled")

    def _user_click_tag(self, username: str, display_name: str) -> str:
        """A per-user Text tag over just that display-name span, like
        clicking a name in real Twitch chat -- opens a small menu with
        moderation actions and a "view their recent messages" option.
        Reused across every message from the same person; only bound
        once (tag_bind would just be replaced with an equivalent
        binding on repeat calls, but there's no reason to redo it)."""
        tag_name = f"user_click_{username}"
        if tag_name not in self._chat_click_tags:
            # Text tags on Tk 8.6 (what Python bundles on Windows) have
            # no "-cursor" option of their own -- that's Tk 8.7+ only --
            # so the standard workaround is to swap the whole widget's
            # cursor on hover-enter/leave over the tagged span instead.
            self.chat_log.tag_bind(tag_name, "<Enter>", lambda e: self.chat_log.configure(cursor="hand2"))
            self.chat_log.tag_bind(tag_name, "<Leave>", lambda e: self.chat_log.configure(cursor=""))
            self.chat_log.tag_bind(
                tag_name, "<Button-1>",
                lambda event, u=username, d=display_name: self._show_user_menu(event, u, d),
            )
            self._chat_click_tags.add(tag_name)
        return tag_name

    def _show_user_menu(self, event: tk.Event, username: str, display_name: str) -> None:
        """The click-a-name popup: view recent messages, timeout/ban/
        unban. Moderation actions reuse the same "/timeout"/"/ban"/
        "/unban" chat commands the Moderation tab's auto-enforcement
        already sends (see Bot._apply_moderation) -- sent as the "Bot"
        identity, over the bot's own connection, same as any other
        chat command, so they need the bot account to actually be
        modded in the channel to take effect (same requirement the
        existing moderation system already has)."""
        menu = tk.Menu(self, tearoff=0, **theme.popup_menu_kwargs())
        menu.add_command(label=f"@{display_name}", state="disabled")
        menu.add_separator()
        menu.add_command(label="View recent messages...",
                          command=lambda: self._show_user_messages_dialog(username, display_name))
        menu.add_separator()
        menu.add_command(label="Timeout 1 min", command=lambda: self._timeout_user(username, 60))
        menu.add_command(label="Timeout 10 min", command=lambda: self._timeout_user(username, 600))
        menu.add_command(label="Timeout 1 hour", command=lambda: self._timeout_user(username, 3600))
        menu.add_command(label="Timeout (custom)...", command=lambda: self._timeout_user_custom(username))
        menu.add_separator()
        menu.add_command(label="Ban", command=lambda: self._ban_user(username, display_name))
        menu.add_command(label="Unban", command=lambda: self._unban_user(username))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _timeout_user(self, username: str, seconds: int) -> None:
        self.bot.send_chat(f"/timeout {username} {seconds}")

    def _timeout_user_custom(self, username: str) -> None:
        seconds = simpledialog.askinteger(
            "Timeout", f"Timeout @{username} for how many seconds?", parent=self, minvalue=1, maxvalue=1209600,
        )
        if seconds:
            self._timeout_user(username, seconds)

    def _ban_user(self, username: str, display_name: str) -> None:
        if messagebox.askyesno("Ban", f"Ban @{display_name}? This can be undone with Unban."):
            self.bot.send_chat(f"/ban {username}")

    def _unban_user(self, username: str) -> None:
        self.bot.send_chat(f"/unban {username}")

    def _show_user_messages_dialog(self, username: str, display_name: str) -> None:
        rows = self.db.get_recent_messages(username, limit=50)
        dialog = self._toplevel(f"Recent messages -- @{display_name}", "480x420")
        text = tk.Text(dialog, state="disabled", wrap="word", font=(CHAT_FONT_FAMILY, 10))
        style_text_widget(text)
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.configure(state="normal")
        if not rows:
            text.insert("end", "No messages logged from this person yet.")
        else:
            # rows are newest-first from the DB; show oldest-first so it
            # reads top-to-bottom like a chat log.
            for row in reversed(rows):
                stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["logged_at"]))
                text.insert("end", f"[{stamp}] {row['text']}\n")
        text.configure(state="disabled")

    def _insert_message_with_emotes(self, text: str, emotes_tag: str) -> None:
        # Twitch's emotes tag: "id:start-end,start-end/id2:start-end" --
        # start/end are inclusive character offsets into the message.
        spans: list[tuple[int, int, str]] = []
        for part in (emotes_tag or "").split("/"):
            if ":" not in part:
                continue
            emote_id, ranges = part.split(":", 1)
            for rng in ranges.split(","):
                if "-" not in rng:
                    continue
                start_s, end_s = rng.split("-", 1)
                try:
                    spans.append((int(start_s), int(end_s), emote_id))
                except ValueError:
                    continue
        spans.sort(key=lambda s: s[0])

        pos = 0
        for start, end, emote_id in spans:
            if start < pos or start >= len(text) or end < start:
                continue  # overlapping/out-of-range -- skip rather than corrupt the line
            if start > pos:
                self.chat_log.insert("end", text[pos:start])
            image = self.emote_cache.get_emote_image(emote_id)
            if image is not None:
                self.chat_log.image_create("end", image=image)
            else:
                self.chat_log.insert("end", text[start:end + 1])
            pos = end + 1
        if pos < len(text):
            self.chat_log.insert("end", text[pos:])

    def _send_manual_message(self) -> None:
        text = self.send_entry.get().strip()
        if not text:
            return
        if self.send_identity_var.get() == "Streamer":
            try:
                self.bot.send_chat_as_broadcaster(text)
            except Exception as exc:
                messagebox.showerror("Send as Streamer", friendly_error_text(exc))
                return
        else:
            self.bot.send_chat(text)
        self.send_entry.delete(0, "end")

    # -- dashboard (quick stats) ---------------------------------------
    def _build_dashboard_tab(self) -> None:
        frame = ttk.Frame(self.dashboard_tab, padding=16)
        frame.pack(fill="both", expand=True)

        self._build_dashboard_basic_section(frame)

        self.stat_labels: dict[str, ttk.Label] = {}
        stats = [
            ("channel", "Channel"), ("uptime", "Bot Uptime"), ("live_viewers", "Live Viewers"),
            ("commands", "Commands"), ("users", "Known Viewers"), ("points_name", "Currency"),
            ("top_user", "Top Viewer"), ("queue_len", "Song Queue"), ("giveaway", "Giveaway"),
        ]
        for i, (key, label) in enumerate(stats):
            r, c = divmod(i, 2)
            box = ttk.LabelFrame(frame, text=label, padding=12)
            box.grid(row=r + 1, column=c, sticky="nsew", padx=8, pady=8)
            value_label = ttk.Label(box, text="--", style="Stat.TLabel")
            value_label.pack(anchor="w")
            self.stat_labels[key] = value_label
        for c in range(2):
            frame.columnconfigure(c, weight=1)

        self._bot_started_at: Optional[float] = None
        self._refresh_dashboard()

    def _build_dashboard_basic_section(self, parent: ttk.Frame) -> None:
        """A "Basic" title/game box at the top of the Dashboard tab,
        modeled directly on the original AnkhBot's own Dashboard (Ryan
        sent a screenshot of it as the reference): a refresh button, a
        Title field, and a Game field, with a second button to push the
        edited values to Twitch. Replaces the earlier pencil-icon
        popup dialog Ryan tried and didn't like in person.

        The Game field is a free-typed, live-searched combobox rather
        than a plain text box: typing (2+ chars) queries Twitch's own
        category search (the same list twitch.tv's own category box
        searches) in the background and fills the dropdown with real
        matches, and only a name that came from a load or a search
        (i.e. one we have a real Twitch game_id for) is accepted on
        Save -- this is what makes sure the category that gets set is
        the one Ryan actually meant, not a typo'd near-miss."""
        box = ttk.LabelFrame(parent, text="Basic", padding=12)
        box.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=8)
        box.columnconfigure(2, weight=1)

        self.dash_title_var = tk.StringVar()
        self.dash_game_var = tk.StringVar()
        # display name (lowercased) -> Twitch game_id, for every name
        # we've actually seen come back from Twitch (a load or a
        # search) -- Save only trusts a name that's a key in here.
        self._dash_game_options: dict[str, str] = {}
        self._game_search_after_id: Optional[str] = None

        self.dash_refresh_btn = ttk.Button(
            box, text="↻", width=3, command=self._refresh_stream_info_fields,
        )
        self.dash_refresh_btn.grid(row=0, column=0, padx=(0, 8))

        ttk.Label(box, text="Title:").grid(row=0, column=1, sticky="w")
        title_entry = ttk.Entry(box, textvariable=self.dash_title_var)
        title_entry.grid(row=0, column=2, sticky="we", padx=8)

        ttk.Label(box, text="Game:").grid(row=0, column=3, sticky="w")
        self.dash_game_combo = ttk.Combobox(box, textvariable=self.dash_game_var, width=22)
        self.dash_game_combo.grid(row=0, column=4, sticky="we", padx=8)
        self.dash_game_combo.bind("<KeyRelease>", self._on_dashboard_game_typed)

        self.dash_save_btn = ttk.Button(
            box, text="↑", width=3, command=self._save_stream_info_from_dashboard,
        )
        self.dash_save_btn.grid(row=0, column=5, padx=(8, 0))

        self.dash_stream_info_status = ttk.Label(box, text="", style="Muted.TLabel")
        self.dash_stream_info_status.grid(row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        if self.bot.twitch_api is not None:
            self._refresh_stream_info_fields()
        else:
            self.dash_title_var.set("[NONE]")
            self.dash_game_var.set("[NONE]")
            self.dash_stream_info_status.configure(
                text='Authorize the broadcaster in Settings to load/edit this.'
            )

    def _refresh_stream_info_fields(self) -> None:
        if self.bot.twitch_api is None:
            self.dash_stream_info_status.configure(
                text='Authorize the broadcaster in Settings to load/edit this.'
            )
            return
        self.dash_stream_info_status.configure(text="Loading current title/game...")

        def worker():
            info = self.bot.get_channel_info()
            self._event_queue.put(("dashboard_stream_info_loaded", info))
        threading.Thread(target=worker, daemon=True).start()

    def _on_dashboard_game_typed(self, _event=None) -> None:
        if self._game_search_after_id is not None:
            self.after_cancel(self._game_search_after_id)
        self._game_search_after_id = self.after(400, self._search_dashboard_games)

    def _search_dashboard_games(self) -> None:
        self._game_search_after_id = None
        query = self.dash_game_var.get().strip()
        if len(query) < 2 or self.bot.twitch_api is None:
            return
        self.dash_stream_info_status.configure(text="Searching Twitch categories...")

        def worker():
            try:
                matches = self.bot.twitch_api.search_categories(query)
                self._event_queue.put(("dashboard_game_search_result", (matches, "")))
            except Exception as exc:
                self._event_queue.put(("dashboard_game_search_result", ([], str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def _save_stream_info_from_dashboard(self) -> None:
        if self.bot.twitch_api is None:
            messagebox.showerror(
                "Update Title/Game",
                "Broadcaster isn't authorized yet -- use \"Authorize (broadcaster)\" in Settings.",
            )
            return
        title = self.dash_title_var.get().strip()
        game_text = self.dash_game_var.get().strip()
        if not game_text:
            game_id = None
        else:
            game_id = self._dash_game_options.get(game_text.lower())
            if game_id is None:
                messagebox.showerror(
                    "Update Title/Game",
                    f"\"{game_text}\" doesn't match a Twitch category. Type a few letters and "
                    "pick one from the dropdown so the exact category gets set.",
                )
                return
        self.dash_save_btn.configure(state="disabled")
        self.dash_stream_info_status.configure(text="Saving...")

        def worker():
            try:
                # An empty title box is treated as "leave it alone"
                # rather than blanking out the real title.
                self.bot.update_stream_info(title=title or None, game_id=game_id)
                self._event_queue.put(("dashboard_stream_info_saved", (True, "")))
            except Exception as exc:
                self._event_queue.put(("dashboard_stream_info_saved", (False, str(exc))))
        threading.Thread(target=worker, daemon=True).start()

    def _refresh_dashboard(self) -> None:
        cfg = self.config_store.data
        self.stat_labels["channel"].configure(text=cfg.channel or "(not set)")
        if self.bot.connected and self._bot_started_at:
            elapsed = int(time.time() - self._bot_started_at)
            self.stat_labels["uptime"].configure(text=f"{elapsed // 60}m {elapsed % 60}s")
        else:
            self.stat_labels["uptime"].configure(text="offline")
        self._refresh_live_viewers()
        self.stat_labels["commands"].configure(text=str(len(self.db.all_commands())))
        self.stat_labels["users"].configure(text=str(len(self.db.query("SELECT username FROM users"))))
        self.stat_labels["points_name"].configure(text=self.bot.currency.currency_name())
        top = self.db.top_users(limit=1)
        self.stat_labels["top_user"].configure(
            text=f"{top[0]['username']} ({top[0]['points']})" if top else "--"
        )
        self.stat_labels["queue_len"].configure(text=str(len(self.db.queued_songs())))
        gstate = self.bot.giveaway.state
        self.stat_labels["giveaway"].configure(
            text=f"{gstate.prize} ({len(gstate.entries)} entered)" if gstate and gstate.open else "none active"
        )

    def _refresh_live_viewers(self) -> None:
        """Reads the latest live/viewer-count snapshot the bot's
        background scheduler already fetched (see Bot.last_stream_info)
        -- deliberately never makes its own Helix call here, since this
        runs on the GUI thread every 5s and a blocking network call
        here would risk freezing the window on a slow/unreachable
        Twitch response. Drives both the Dashboard tab's "Live Viewers"
        stat tile and the small "Viewers: N" label in the top bar next
        to the connection status ("Joined #channel" etc.) -- the top
        bar one stays blank rather than showing "offline"/"not set up"
        since it's a compact always-visible slot, not a dedicated tile."""
        if self.bot.twitch_api is None:
            self.stat_labels["live_viewers"].configure(text="not set up")
            self.viewers_label.configure(text="")
            return
        info = self.bot.last_stream_info
        if info is None:
            self.stat_labels["live_viewers"].configure(text="--")
            self.viewers_label.configure(text="")
        elif not info.live:
            self.stat_labels["live_viewers"].configure(text="offline")
            self.viewers_label.configure(text="")
        else:
            self.stat_labels["live_viewers"].configure(text=str(info.viewer_count))
            self.viewers_label.configure(text=f"Viewers: {info.viewer_count}")

    # -- commands -----------------------------------------------------
    def _build_commands_tab(self) -> None:
        frame = self.commands_tab
        columns = ("name", "permission", "cooldown", "user_cooldown", "enabled", "uses", "builtin")
        self.commands_tree = ttk.Treeview(frame, columns=columns, show="headings", height=20)
        for col, width in zip(columns, (140, 100, 80, 100, 70, 70, 70)):
            self.commands_tree.heading(col, text=col.replace("_", " ").title())
            self.commands_tree.column(col, width=width, anchor="center")
        self.commands_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Add", command=self._add_command_dialog).pack(side="left")
        ttk.Button(btns, text="Edit", command=self._edit_command_dialog).pack(side="left", padx=4)
        ttk.Button(btns, text="Delete", command=self._delete_command).pack(side="left")
        ttk.Button(btns, text="Refresh", command=self._refresh_commands).pack(side="right")
        self._refresh_commands()

    def _refresh_commands(self) -> None:
        self.commands_tree.delete(*self.commands_tree.get_children())
        for row in self.db.all_commands():
            self.commands_tree.insert("", "end", iid=row["name"], values=(
                row["name"], row["permission"], row["cooldown_seconds"], row["user_cooldown_seconds"],
                "yes" if row["enabled"] else "no", row["uses"], "yes" if row["builtin"] else "no",
            ))

    def _add_command_dialog(self) -> None:
        self._command_dialog(existing=None)

    def _edit_command_dialog(self) -> None:
        sel = self.commands_tree.selection()
        if not sel:
            return
        row = self.db.get_command(sel[0])
        if row:
            self._command_dialog(existing=row)

    def _command_dialog(self, existing) -> None:
        dialog = self._toplevel("Command", "480x320")

        ttk.Label(dialog, text="Name (no !)").pack(anchor="w", padx=8, pady=(8, 0))
        name_entry = ttk.Entry(dialog)
        name_entry.pack(fill="x", padx=8)

        ttk.Label(dialog, text="Response (use $(user), $(touser), $(count), $(1)...)").pack(anchor="w", padx=8, pady=(8, 0))
        response_text = tk.Text(dialog, height=6)
        style_text_widget(response_text)
        response_text.pack(fill="both", padx=8, expand=True)

        perm_frame = ttk.Frame(dialog)
        perm_frame.pack(fill="x", padx=8, pady=8)
        ttk.Label(perm_frame, text="Permission").pack(side="left")
        perm_var = tk.StringVar(value="everyone")
        ttk.Combobox(perm_frame, textvariable=perm_var, state="readonly",
                     values=["everyone", "subscriber", "vip", "moderator", "broadcaster"]).pack(side="left", padx=4)
        ttk.Label(perm_frame, text="Cooldown (s)").pack(side="left", padx=(12, 0))
        cooldown_var = tk.IntVar(value=5)
        ttk.Spinbox(perm_frame, from_=0, to=3600, textvariable=cooldown_var, width=6).pack(side="left", padx=4)
        ttk.Label(perm_frame, text="Per-user cd (s)").pack(side="left", padx=(12, 0))
        user_cooldown_var = tk.IntVar(value=0)
        ttk.Spinbox(perm_frame, from_=0, to=3600, textvariable=user_cooldown_var, width=6).pack(side="left", padx=4)

        if existing is not None:
            name_entry.insert(0, existing["name"])
            if existing["builtin"]:
                name_entry.configure(state="disabled")
            response_text.insert("1.0", existing["response"])
            perm_var.set(existing["permission"])
            cooldown_var.set(existing["cooldown_seconds"])
            user_cooldown_var.set(existing["user_cooldown_seconds"])

        def save():
            name = name_entry.get().strip().lstrip("!").lower()
            if not name:
                messagebox.showerror("Command", "Name is required.")
                return
            response = response_text.get("1.0", "end").strip()
            self.bot.engine.add_custom_command(
                name, response, permission=perm_var.get(),
                cooldown_seconds=cooldown_var.get(), user_cooldown_seconds=user_cooldown_var.get(),
            )
            self._refresh_commands()
            dialog.destroy()

        ttk.Button(dialog, text="Add/Modify", command=save).pack(pady=8)

    def _delete_command(self) -> None:
        sel = self.commands_tree.selection()
        if not sel:
            return
        self.db.delete_command(sel[0])
        self._refresh_commands()

    # -- currency system (points + leaderboard) -----------------------
    def _build_currency_tab(self) -> None:
        frame = self.currency_tab
        form = ttk.LabelFrame(frame, text="Payout settings", padding=10)
        form.pack(fill="x", padx=8, pady=8)

        self.currency_vars: dict[str, tk.Variable] = {}
        fields = [
            ("currency_name", "Currency name"),
            ("currency_earn_amount", "Earn amount"),
            ("currency_earn_interval_minutes", "Earn interval (minutes)"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=self.db.get_setting(key, ""))
            self.currency_vars[key] = var
            ttk.Entry(form, textvariable=var, width=20).grid(row=i, column=1, sticky="w", padx=8)

        ttk.Button(form, text="Save", command=self._save_currency_settings).grid(
            row=len(fields), column=0, pady=8, sticky="w"
        )
        self.currency_saved_label = ttk.Label(form, text="", style="Muted.TLabel")
        self.currency_saved_label.grid(row=len(fields), column=1, sticky="w", padx=8)

        ttk.Label(frame, text="Leaderboard", style="Heading.TLabel").pack(anchor="w", padx=8)
        self.leaderboard_tree = ttk.Treeview(frame, columns=("user", "points", "watch"), show="headings", height=12)
        for col, label in [("user", "User"), ("points", "Points"), ("watch", "Watch Minutes")]:
            self.leaderboard_tree.heading(col, text=label)
        self.leaderboard_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self._refresh_leaderboard()

    def _save_currency_settings(self) -> None:
        for key, var in self.currency_vars.items():
            self.db.set_setting(key, var.get())
        self._flash_saved(self.currency_saved_label)

    def _refresh_leaderboard(self) -> None:
        self.leaderboard_tree.delete(*self.leaderboard_tree.get_children())
        for row in self.db.top_users(limit=25):
            self.leaderboard_tree.insert("", "end", values=(row["username"], row["points"], row["watch_minutes"]))

    # -- mini games (gamble/slots/roulette settings) --------------------
    def _build_minigames_tab(self) -> None:
        frame = ttk.Frame(self.minigames_tab, padding=10)
        frame.pack(fill="both", expand=True)

        self.minigame_vars: dict[str, tk.StringVar] = {}

        def add_settings_frame(title: str, fields: list[tuple[str, str]]) -> None:
            box = ttk.LabelFrame(frame, text=title, padding=10)
            box.pack(fill="x", pady=(0, 10))
            for i, (key, label) in enumerate(fields):
                ttk.Label(box, text=label).grid(row=i, column=0, sticky="w", pady=3)
                var = tk.StringVar(value=self.db.get_setting(key, "0"))
                self.minigame_vars[key] = var
                ttk.Entry(box, textvariable=var, width=12).grid(row=i, column=1, sticky="w", padx=8)

        add_settings_frame("!gamble / !slots / !roulette", [
            ("gamble_min_bet", "Minimum bet"),
            ("gamble_win_chance_pct", "!gamble win chance %"),
            ("slots_min_bet", "!slots minimum bet"),
        ])
        add_settings_frame("!heist", [
            ("heist_min_wager", "Minimum wager"),
            ("heist_join_window_seconds", "Join window (seconds)"),
        ])
        add_settings_frame("!boss / !attack", [
            ("boss_default_hp", "Default boss HP"),
            ("boss_default_seconds", "Default time limit (seconds)"),
            ("boss_min_damage", "Min damage per !attack"),
            ("boss_max_damage", "Max damage per !attack"),
            ("boss_victory_reward", "Reward per fighter on kill"),
            ("boss_mvp_bonus", "Bonus for top damage dealer"),
        ])

        minigames_save_row = ttk.Frame(frame)
        minigames_save_row.pack(anchor="w")
        ttk.Button(minigames_save_row, text="Save", command=self._save_minigame_settings).pack(side="left")
        self.minigames_saved_label = ttk.Label(minigames_save_row, text="", style="Muted.TLabel")
        self.minigames_saved_label.pack(side="left", padx=(8, 0))

        ttk.Label(
            frame,
            text="Duel and Free-for-All (from the original AnkhBot) aren't built yet -- see the "
                 "README roadmap. Heist and Boss Battle are chat-command only, no visuals, by design.",
            style="Muted.TLabel", wraplength=900,
        ).pack(anchor="w", pady=(12, 0))

    def _save_minigame_settings(self) -> None:
        for key, var in self.minigame_vars.items():
            self.db.set_setting(key, var.get())
        self._flash_saved(self.minigames_saved_label)

    # -- moderation --------------------------------------------------
    def _build_moderation_tab(self) -> None:
        frame = self.moderation_tab
        toggles = ttk.LabelFrame(frame, text="Filters", padding=8)
        toggles.pack(fill="x", padx=8, pady=8)

        self.mod_vars: dict[str, tk.BooleanVar] = {}
        bool_fields = [
            ("moderation_enabled", "Moderation enabled"),
            ("moderation_links_enabled", "Block links (not on whitelist)"),
            ("moderation_caps_enabled", "Block excessive caps"),
            ("moderation_symbols_enabled", "Block symbol spam"),
            ("moderation_repetition_enabled", "Block repeated messages"),
            ("moderation_banned_words_enabled", "Block banned phrases"),
        ]
        for i, (key, label) in enumerate(bool_fields):
            var = tk.BooleanVar(value=self.db.get_setting_bool(key, True))
            self.mod_vars[key] = var
            ttk.Checkbutton(toggles, text=label, variable=var).grid(row=i, column=0, sticky="w")

        thresholds = ttk.LabelFrame(frame, text="Thresholds", padding=8)
        thresholds.pack(fill="x", padx=8, pady=(0, 8))
        self.mod_int_vars: dict[str, tk.StringVar] = {}
        int_fields = [
            ("moderation_caps_threshold_pct", "Caps % that triggers"),
            ("moderation_symbols_threshold_pct", "Symbol % that triggers"),
            ("moderation_strikes_before_timeout", "Strikes before timeout"),
            ("moderation_timeout_seconds", "Timeout length (s)"),
        ]
        for i, (key, label) in enumerate(int_fields):
            ttk.Label(thresholds, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=self.db.get_setting(key, "0"))
            self.mod_int_vars[key] = var
            ttk.Entry(thresholds, textvariable=var, width=10).grid(row=i, column=1, sticky="w", padx=8)

        moderation_save_row = ttk.Frame(frame)
        moderation_save_row.pack(anchor="w", padx=8)
        ttk.Button(moderation_save_row, text="Save", command=self._save_moderation_settings).pack(side="left")
        self.moderation_saved_label = ttk.Label(moderation_save_row, text="", style="Muted.TLabel")
        self.moderation_saved_label.pack(side="left", padx=(8, 0))

        lists_frame = ttk.Frame(frame)
        lists_frame.pack(fill="both", expand=True, padx=8, pady=8)

        banned_frame = ttk.LabelFrame(lists_frame, text="Banned phrases", padding=6)
        banned_frame.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.banned_listbox = tk.Listbox(banned_frame, height=10)
        style_listbox(self.banned_listbox)
        self.banned_listbox.pack(fill="both", expand=True)
        banned_btns = ttk.Frame(banned_frame)
        banned_btns.pack(fill="x")
        ttk.Button(banned_btns, text="Add", command=self._add_banned_phrase).pack(side="left")
        ttk.Button(banned_btns, text="Remove", command=self._remove_banned_phrase).pack(side="left", padx=4)

        whitelist_frame = ttk.LabelFrame(lists_frame, text="Link whitelist (domains)", padding=6)
        whitelist_frame.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.whitelist_listbox = tk.Listbox(whitelist_frame, height=10)
        style_listbox(self.whitelist_listbox)
        self.whitelist_listbox.pack(fill="both", expand=True)
        ttk.Button(whitelist_frame, text="Add", command=self._add_whitelist_domain).pack(anchor="w")

        self._refresh_moderation_lists()

    def _save_moderation_settings(self) -> None:
        for key, var in self.mod_vars.items():
            self.db.set_setting(key, "1" if var.get() else "0")
        for key, var in self.mod_int_vars.items():
            self.db.set_setting(key, var.get())
        self._flash_saved(self.moderation_saved_label)

    def _refresh_moderation_lists(self) -> None:
        self.banned_listbox.delete(0, "end")
        self._banned_rows = self.db.all_banned_phrases()
        for row in self._banned_rows:
            self.banned_listbox.insert("end", row["phrase"])
        self.whitelist_listbox.delete(0, "end")
        for domain in self.db.all_link_whitelist():
            self.whitelist_listbox.insert("end", domain)

    def _add_banned_phrase(self) -> None:
        phrase = simpledialog.askstring("Banned phrase", "Phrase to block:")
        if phrase:
            self.db.add_banned_phrase(phrase)
            self._refresh_moderation_lists()

    def _remove_banned_phrase(self) -> None:
        sel = self.banned_listbox.curselection()
        if not sel:
            return
        row = self._banned_rows[sel[0]]
        self.db.delete_banned_phrase(row["id"])
        self._refresh_moderation_lists()

    def _add_whitelist_domain(self) -> None:
        domain = simpledialog.askstring("Whitelist domain", "Domain (e.g. twitch.tv):")
        if domain:
            self.db.add_link_whitelist(domain)
            self._refresh_moderation_lists()

    # -- timers ---------------------------------------------------------
    def _build_timers_tab(self) -> None:
        frame = self.timers_tab
        columns = ("name", "message", "interval", "min_msgs", "enabled")
        self.timers_tree = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        for col, width in zip(columns, (120, 380, 80, 80, 70)):
            self.timers_tree.heading(col, text=col.replace("_", " ").title())
            self.timers_tree.column(col, width=width)
        self.timers_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Add", command=self._add_timer_dialog).pack(side="left")
        ttk.Button(btns, text="Delete", command=self._delete_timer).pack(side="left", padx=4)
        ttk.Button(btns, text="Toggle Enabled", command=self._toggle_timer).pack(side="left")
        self._refresh_timers()

    def _refresh_timers(self) -> None:
        self.timers_tree.delete(*self.timers_tree.get_children())
        for row in self.db.all_timers():
            self.timers_tree.insert("", "end", iid=str(row["id"]), values=(
                row["name"], row["message"], row["interval_minutes"],
                row["min_messages_since_last"], "yes" if row["enabled"] else "no",
            ))

    def _add_timer_dialog(self) -> None:
        dialog = self._toplevel("Add Timer", "420x260")
        ttk.Label(dialog, text="Name").pack(anchor="w", padx=8, pady=(8, 0))
        name_entry = ttk.Entry(dialog)
        name_entry.pack(fill="x", padx=8)
        ttk.Label(dialog, text="Message").pack(anchor="w", padx=8, pady=(8, 0))
        message_text = tk.Text(dialog, height=4)
        style_text_widget(message_text)
        message_text.pack(fill="both", padx=8, expand=True)
        row2 = ttk.Frame(dialog)
        row2.pack(fill="x", padx=8, pady=8)
        ttk.Label(row2, text="Every (minutes)").pack(side="left")
        interval_var = tk.IntVar(value=15)
        ttk.Spinbox(row2, from_=1, to=1440, textvariable=interval_var, width=6).pack(side="left", padx=4)
        ttk.Label(row2, text="Min chat msgs since last").pack(side="left", padx=(12, 0))
        min_msgs_var = tk.IntVar(value=5)
        ttk.Spinbox(row2, from_=0, to=1000, textvariable=min_msgs_var, width=6).pack(side="left", padx=4)

        def save():
            name = name_entry.get().strip()
            message = message_text.get("1.0", "end").strip()
            if not name or not message:
                messagebox.showerror("Timer", "Name and message are required.")
                return
            self.db.add_timer(name, message, interval_var.get(), min_msgs_var.get())
            self._refresh_timers()
            dialog.destroy()

        ttk.Button(dialog, text="Add/Modify", command=save).pack(pady=4)

    def _delete_timer(self) -> None:
        sel = self.timers_tree.selection()
        if not sel:
            return
        self.db.delete_timer(int(sel[0]))
        self._refresh_timers()

    def _toggle_timer(self) -> None:
        sel = self.timers_tree.selection()
        if not sel:
            return
        row = self.db.query_one("SELECT enabled FROM timers WHERE id = ?", (int(sel[0]),))
        if row is not None:
            self.db.update_timer(int(sel[0]), enabled=0 if row["enabled"] else 1)
            self._refresh_timers()

    # -- quotes ----------------------------------------------------------
    def _build_quotes_tab(self) -> None:
        frame = self.quotes_tab
        columns = ("id", "text", "author", "game")
        self.quotes_tree = ttk.Treeview(frame, columns=columns, show="headings", height=18)
        widths = (40, 480, 120, 120)
        for col, width in zip(columns, widths):
            self.quotes_tree.heading(col, text=col.title())
            self.quotes_tree.column(col, width=width)
        self.quotes_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Add", command=self._add_quote_dialog).pack(side="left")
        ttk.Button(btns, text="Delete", command=self._delete_quote).pack(side="left", padx=4)
        self._refresh_quotes()

    def _refresh_quotes(self) -> None:
        self.quotes_tree.delete(*self.quotes_tree.get_children())
        for row in self.db.all_quotes():
            self.quotes_tree.insert("", "end", iid=str(row["id"]), values=(
                row["id"], row["text"], row["author"] or "", row["game"] or "",
            ))

    def _add_quote_dialog(self) -> None:
        dialog = self._toplevel("Add Quote", "420x220")
        ttk.Label(dialog, text="Text").pack(anchor="w", padx=8, pady=(8, 0))
        text_box = tk.Text(dialog, height=4)
        style_text_widget(text_box)
        text_box.pack(fill="both", padx=8, expand=True)
        row2 = ttk.Frame(dialog)
        row2.pack(fill="x", padx=8, pady=8)
        ttk.Label(row2, text="Author").pack(side="left")
        author_entry = ttk.Entry(row2, width=16)
        author_entry.pack(side="left", padx=4)
        ttk.Label(row2, text="Game").pack(side="left", padx=(12, 0))
        game_entry = ttk.Entry(row2, width=16)
        game_entry.pack(side="left", padx=4)

        def save():
            text = text_box.get("1.0", "end").strip()
            if not text:
                return
            self.db.add_quote(text, author_entry.get().strip(), game_entry.get().strip(), "gui")
            self._refresh_quotes()
            dialog.destroy()

        ttk.Button(dialog, text="Add/Modify", command=save).pack(pady=4)

    def _delete_quote(self) -> None:
        sel = self.quotes_tree.selection()
        if not sel:
            return
        self.db.delete_quote(int(sel[0]))
        self._refresh_quotes()

    # -- give away / raffle ---------------------------------------------
    def _build_giveaway_tab(self) -> None:
        frame = ttk.Frame(self.giveaway_tab, padding=10)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(frame, text="Start a giveaway", padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Keyword").grid(row=0, column=0, sticky="w")
        self.giveaway_keyword_entry = ttk.Entry(form, width=16)
        self.giveaway_keyword_entry.grid(row=0, column=1, padx=6)
        ttk.Label(form, text="Prize").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.giveaway_prize_entry = ttk.Entry(form, width=20)
        self.giveaway_prize_entry.grid(row=0, column=3, padx=6)
        ttk.Label(form, text="Entry cost").grid(row=0, column=4, sticky="w", padx=(12, 0))
        self.giveaway_cost_var = tk.IntVar(value=0)
        ttk.Spinbox(form, from_=0, to=100000, textvariable=self.giveaway_cost_var, width=8).grid(row=0, column=5, padx=6)
        ttk.Button(form, text="Start", command=self._start_giveaway).grid(row=0, column=6, padx=(12, 0))
        ttk.Button(form, text="Close Entries", command=self._close_giveaway).grid(row=0, column=7, padx=4)
        ttk.Button(form, text="Draw Winner", command=self._draw_giveaway).grid(row=0, column=8, padx=4)

        self.giveaway_status_label = ttk.Label(frame, text="No giveaway running.", style="Heading.TLabel")
        self.giveaway_status_label.pack(anchor="w", pady=(10, 4))

        ttk.Label(frame, text="Entrants and ticket counts:").pack(anchor="w")
        self.giveaway_tree = ttk.Treeview(frame, columns=("user", "tickets"), show="headings", height=16)
        self.giveaway_tree.heading("user", text="User")
        self.giveaway_tree.heading("tickets", text="Tickets")
        self.giveaway_tree.pack(fill="both", expand=True, pady=8)

        self._refresh_giveaway()

    def _start_giveaway(self) -> None:
        keyword = self.giveaway_keyword_entry.get().strip()
        if not keyword:
            messagebox.showerror("Give Away", "Enter a keyword viewers will type to enter.")
            return
        prize = self.giveaway_prize_entry.get().strip() or "a prize"
        from chatbot.modules.giveaway import GiveawayState
        self.bot.giveaway.state = GiveawayState(
            keyword=keyword, prize=prize, entry_cost=self.giveaway_cost_var.get()
        )
        self._append_log(f"* Giveaway started: \"{keyword}\" for {prize}")
        self._refresh_giveaway()

    def _close_giveaway(self) -> None:
        if self.bot.giveaway.state:
            self.bot.giveaway.state.open = False
        self._refresh_giveaway()

    def _draw_giveaway(self) -> None:
        state = self.bot.giveaway.state
        if not state or not state.entries:
            messagebox.showinfo("Give Away", "No entries yet.")
            return
        import random
        winner = random.choice([u for u, t in state.entries.items() for _ in range(t)])
        self.bot.send_chat(f"🎉 The winner of {state.prize} is @{winner}! Congratulations!")
        messagebox.showinfo("Give Away", f"Winner: {winner}")

    def _refresh_giveaway(self) -> None:
        self.giveaway_tree.delete(*self.giveaway_tree.get_children())
        state = self.bot.giveaway.state
        if state is None:
            self.giveaway_status_label.configure(text="No giveaway running.")
            return
        status = "open" if state.open else "closed"
        self.giveaway_status_label.configure(
            text=f"\"{state.keyword}\" for {state.prize} -- {status}, {len(state.entries)} entered"
        )
        for user, tickets in state.entries.items():
            self.giveaway_tree.insert("", "end", values=(user, tickets))

    # -- SFX (sound files) ------------------------------------------
    def _build_sfx_tab(self) -> None:
        frame = ttk.Frame(self.sfx_tab, padding=10)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(frame, text="Add a sound", padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Command name").grid(row=0, column=0, sticky="w")
        self.sfx_name_entry = ttk.Entry(form, width=16)
        self.sfx_name_entry.grid(row=0, column=1, padx=6)
        ttk.Label(form, text="File (.wav)").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.sfx_path_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.sfx_path_var, width=36).grid(row=0, column=3, padx=6)
        ttk.Button(form, text="Open...", command=self._browse_sfx_file).grid(row=0, column=4, padx=4)
        ttk.Label(form, text="Volume").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.sfx_volume_var = tk.IntVar(value=100)
        ttk.Scale(form, from_=0, to=100, variable=self.sfx_volume_var, orient="horizontal", length=150).grid(
            row=1, column=1, columnspan=2, sticky="w", pady=(6, 0)
        )
        ttk.Label(form, text="Permission").grid(row=1, column=3, sticky="w", padx=(12, 0), pady=(6, 0))
        self.sfx_perm_var = tk.StringVar(value="everyone")
        ttk.Combobox(form, textvariable=self.sfx_perm_var, state="readonly", width=12,
                     values=["everyone", "subscriber", "vip", "moderator", "broadcaster"]).grid(
            row=1, column=4, padx=4, pady=(6, 0)
        )
        ttk.Button(form, text="Add/Modify", command=self._add_sfx).grid(row=2, column=0, pady=8, sticky="w")
        ttk.Button(form, text="Preview", command=self._preview_sfx).grid(row=2, column=1, pady=8, sticky="w")

        columns = ("name", "file", "volume", "permission")
        self.sfx_tree = ttk.Treeview(frame, columns=columns, show="headings", height=16)
        for col, width in zip(columns, (120, 420, 70, 100)):
            self.sfx_tree.heading(col, text=col.title())
            self.sfx_tree.column(col, width=width)
        self.sfx_tree.pack(fill="both", expand=True, pady=8)
        ttk.Button(frame, text="Delete Selected", command=self._delete_sfx).pack(anchor="w")

        self._refresh_sfx()

    def _browse_sfx_file(self) -> None:
        path = filedialog.askopenfilename(title="Choose a sound", filetypes=[("WAV audio", "*.wav")])
        if path:
            self.sfx_path_var.set(path)

    def _add_sfx(self) -> None:
        name = self.sfx_name_entry.get().strip().lower()
        path = self.sfx_path_var.get().strip()
        if not name or not path:
            messagebox.showerror("SFX", "Name and file are required.")
            return
        self.db.add_sfx(name, path, self.sfx_volume_var.get(), self.sfx_perm_var.get())
        self._refresh_sfx()

    def _preview_sfx(self) -> None:
        path = self.sfx_path_var.get().strip()
        if path:
            self.bot.sfx.play(path, self.sfx_volume_var.get())

    def _delete_sfx(self) -> None:
        sel = self.sfx_tree.selection()
        if not sel:
            return
        self.db.delete_sfx(sel[0])
        self._refresh_sfx()

    def _refresh_sfx(self) -> None:
        self.sfx_tree.delete(*self.sfx_tree.get_children())
        for row in self.db.all_sfx():
            self.sfx_tree.insert("", "end", iid=row["name"], values=(
                row["name"], row["file_path"], row["volume"], row["permission"],
            ))

    # -- event system (on join / on speak) -------------------------
    def _build_event_tab(self) -> None:
        frame = ttk.Frame(self.event_tab, padding=10)
        frame.pack(fill="both", expand=True)
        notebook = ttk.Notebook(frame)
        notebook.pack(fill="both", expand=True)
        join_tab = ttk.Frame(notebook)
        speak_tab = ttk.Frame(notebook)
        notebook.add(join_tab, text="On Join Event System")
        notebook.add(speak_tab, text="On Speak Event System")
        self._event_trees: dict[str, ttk.Treeview] = {}
        self._build_event_subtab(join_tab, "join")
        self._build_event_subtab(speak_tab, "speak")

    def _build_event_subtab(self, frame: ttk.Frame, trigger_type: str) -> None:
        form = ttk.Frame(frame, padding=8)
        form.pack(fill="x")
        ttk.Label(form, text="Group").grid(row=0, column=0, sticky="w")
        group_var = tk.StringVar(value="everyone")
        ttk.Combobox(form, textvariable=group_var, state="readonly", width=14,
                     values=["everyone", "user_specific"]).grid(row=0, column=1, padx=4)
        ttk.Label(form, text="Username (if specific)").grid(row=0, column=2, sticky="w", padx=(12, 0))
        user_entry = ttk.Entry(form, width=16)
        user_entry.grid(row=0, column=3, padx=4)
        ttk.Label(form, text="SFX (optional)").grid(row=0, column=4, sticky="w", padx=(12, 0))
        sfx_var = tk.StringVar()
        sfx_combo = ttk.Combobox(form, textvariable=sfx_var, width=14,
                                  values=[r["name"] for r in self.db.all_sfx()])
        sfx_combo.grid(row=0, column=5, padx=4)

        ttk.Label(form, text="Message (use $(user))").grid(row=1, column=0, sticky="w", pady=(6, 0))
        message_entry = ttk.Entry(form, width=60)
        message_entry.grid(row=1, column=1, columnspan=4, sticky="we", pady=(6, 0))

        columns = ("id", "group", "username", "message", "sfx", "enabled")
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=14)
        for col, width in zip(columns, (40, 100, 120, 400, 100, 70)):
            tree.heading(col, text=col.title())
            tree.column(col, width=width)
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        self._event_trees[trigger_type] = tree

        def add():
            username = user_entry.get().strip() if group_var.get() == "user_specific" else None
            self.db.add_event(trigger_type, group_var.get(), username, message_entry.get().strip(), sfx_var.get() or None)
            self._refresh_events(trigger_type)

        def delete():
            sel = tree.selection()
            if sel:
                self.db.delete_event(int(sel[0]))
                self._refresh_events(trigger_type)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Add/Modify", command=add).pack(side="left")
        ttk.Button(btns, text="Delete Selected", command=delete).pack(side="left", padx=4)

        self._refresh_events(trigger_type)

    def _refresh_events(self, trigger_type: str) -> None:
        tree = self._event_trees[trigger_type]
        tree.delete(*tree.get_children())
        for row in self.db.all_events(trigger_type):
            tree.insert("", "end", iid=str(row["id"]), values=(
                row["id"], row["user_group"], row["username"] or "", row["message"],
                row["sfx_name"] or "", "yes" if row["enabled"] else "no",
            ))

    # -- song requests -----------------------------------------------
    def _build_songs_tab(self) -> None:
        frame = self.songs_tab
        form = ttk.Frame(frame, padding=8)
        form.pack(fill="x")
        self.song_vars: dict[str, tk.StringVar] = {}
        fields = [
            ("songrequests_max_duration_seconds", "Max duration (seconds)"),
            ("songrequests_max_per_user_queued", "Max queued per user"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=self.db.get_setting(key, "0"))
            self.song_vars[key] = var
            ttk.Entry(form, textvariable=var, width=10).grid(row=i, column=1, sticky="w", padx=8)
        self.songrequests_enabled_var = tk.BooleanVar(value=self.db.get_setting_bool("songrequests_enabled", True))
        ttk.Checkbutton(form, text="Song requests enabled", variable=self.songrequests_enabled_var).grid(
            row=len(fields), column=0, sticky="w"
        )
        ttk.Button(form, text="Save", command=self._save_song_settings).grid(row=len(fields) + 1, column=0, pady=6, sticky="w")
        self.song_saved_label = ttk.Label(form, text="", style="Muted.TLabel")
        self.song_saved_label.grid(row=len(fields) + 1, column=1, sticky="w", padx=8)
        ttk.Label(
            frame,
            text="In OBS/Streamlabs Desktop, add a Browser Source, leave \"Local file\" "
                 f"UNCHECKED, and set the URL to http://localhost:{overlay_server.DEFAULT_PORT}/song_overlay.html "
                 "-- the bot is serving it while this app is open. (\"Local file\" mode can't "
                 "see the live now-playing state due to browser security, so it'll look blank.)",
            style="Muted.TLabel", wraplength=900,
        ).pack(anchor="w", padx=8)

        self.now_playing_label = ttk.Label(frame, text="Now playing: (nothing)", style="Heading.TLabel")
        self.now_playing_label.pack(anchor="w", padx=8, pady=(8, 0))

        self.songqueue_tree = ttk.Treeview(frame, columns=("title", "user"), show="headings", height=12)
        self.songqueue_tree.heading("title", text="Title")
        self.songqueue_tree.heading("user", text="Requested By")
        self.songqueue_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Skip Current", command=lambda: self._call_bot(self.bot.songrequests.tick)).pack(side="left")
        ttk.Button(btns, text="Clear Queue", command=self._clear_song_queue).pack(side="left", padx=4)
        ttk.Button(btns, text="Refresh", command=self._refresh_songs).pack(side="right")
        self._refresh_songs()

    def _save_song_settings(self) -> None:
        for key, var in self.song_vars.items():
            self.db.set_setting(key, var.get())
        self.db.set_setting("songrequests_enabled", "1" if self.songrequests_enabled_var.get() else "0")
        self._flash_saved(self.song_saved_label)

    def _clear_song_queue(self) -> None:
        self.db.clear_queue()
        self._refresh_songs()

    def _refresh_songs(self) -> None:
        self.songqueue_tree.delete(*self.songqueue_tree.get_children())
        for row in self.db.queued_songs():
            self.songqueue_tree.insert("", "end", values=(row["title"], row["requested_by"]))
        np = self.bot.songrequests.now_playing
        self.now_playing_label.configure(
            text=f"Now playing: {np['title']} ({np['requested_by']})" if np else "Now playing: (nothing)"
        )

    def _call_bot(self, fn) -> None:
        fn()
        self._refresh_songs()

    # -- queue (game sign-ups, separate from song requests) -----------
    def _build_queue_tab(self) -> None:
        frame = ttk.Frame(self.queue_tab, padding=10)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(frame, text="Open a queue", padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Game").grid(row=0, column=0, sticky="w")
        self.gamequeue_game_entry = ttk.Entry(form, width=20)
        self.gamequeue_game_entry.grid(row=0, column=1, padx=6)
        ttk.Label(form, text="Entry cost").grid(row=0, column=2, sticky="w", padx=(12, 0))
        self.gamequeue_cost_var = tk.IntVar(value=self.db.get_setting_int("queue_cost", 0))
        ttk.Spinbox(form, from_=0, to=100000, textvariable=self.gamequeue_cost_var, width=8).grid(row=0, column=3, padx=6)
        ttk.Button(form, text="Open", command=self._open_game_queue).grid(row=0, column=4, padx=(12, 0))
        ttk.Button(form, text="Close", command=self._close_game_queue).grid(row=0, column=5, padx=4)
        ttk.Button(form, text="Clear", command=self._clear_game_queue).grid(row=0, column=6, padx=4)

        self.gamequeue_status_label = ttk.Label(frame, text="Queue closed.", style="Heading.TLabel")
        self.gamequeue_status_label.pack(anchor="w", pady=(10, 4))

        self.gamequeue_tree = ttk.Treeview(frame, columns=("user", "note"), show="headings", height=16)
        self.gamequeue_tree.heading("user", text="User")
        self.gamequeue_tree.heading("note", text="Note")
        self.gamequeue_tree.pack(fill="both", expand=True, pady=8)

        pick_frame = ttk.Frame(frame)
        pick_frame.pack(fill="x")
        self.gamequeue_pick_var = tk.IntVar(value=1)
        ttk.Spinbox(pick_frame, from_=1, to=100, textvariable=self.gamequeue_pick_var, width=6).pack(side="left")
        ttk.Button(pick_frame, text="Pick First N", command=lambda: self._pick_game_queue(random_pick=False)).pack(side="left", padx=4)
        ttk.Button(pick_frame, text="Pick Random N", command=lambda: self._pick_game_queue(random_pick=True)).pack(side="left")

        self._refresh_game_queue()

    def _open_game_queue(self) -> None:
        game = self.gamequeue_game_entry.get().strip() or "the game"
        self.db.set_setting("queue_open", "1")
        self.db.set_setting("queue_game", game)
        self.db.set_setting("queue_cost", self.gamequeue_cost_var.get())
        self.db.queue_clear()
        self.bot.send_chat(f"Queue is open for {game} -- type !join to sign up!")
        self._refresh_game_queue()

    def _close_game_queue(self) -> None:
        self.db.set_setting("queue_open", "0")
        self._refresh_game_queue()

    def _clear_game_queue(self) -> None:
        self.db.queue_clear()
        self._refresh_game_queue()

    def _pick_game_queue(self, random_pick: bool) -> None:
        import random as _random
        entries = list(self.db.queue_all())
        if not entries:
            messagebox.showinfo("Queue", "The queue is empty.")
            return
        n = self.gamequeue_pick_var.get()
        chosen = _random.sample(entries, min(n, len(entries))) if random_pick else entries[:n]
        names = ", ".join(r["username"] for r in chosen)
        self.bot.send_chat(f"Picked: {names}")
        messagebox.showinfo("Queue", f"Picked: {names}")

    def _refresh_game_queue(self) -> None:
        self.gamequeue_tree.delete(*self.gamequeue_tree.get_children())
        for row in self.db.queue_all():
            self.gamequeue_tree.insert("", "end", values=(row["username"], row["note"] or ""))
        is_open = self.db.get_setting_bool("queue_open", False)
        game = self.db.get_setting("queue_game", "")
        self.gamequeue_status_label.configure(
            text=f"Open for {game} -- {len(self.db.queue_all())} signed up" if is_open else "Queue closed."
        )

    # -- users --------------------------------------------------------
    def _build_users_tab(self) -> None:
        frame = self.users_tab
        columns = ("username", "points", "watch_minutes", "rank")
        self.users_tree = ttk.Treeview(frame, columns=columns, show="headings", height=20)
        for col in columns:
            self.users_tree.heading(col, text=col.replace("_", " ").title())
        self.users_tree.pack(fill="both", expand=True, padx=8, pady=8)

        btns = ttk.Frame(frame)
        btns.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btns, text="Set Points", command=self._set_user_points).pack(side="left")
        ttk.Button(btns, text="Refresh", command=self._refresh_users).pack(side="right")
        self._refresh_users()

    def _refresh_users(self) -> None:
        self.users_tree.delete(*self.users_tree.get_children())
        for row in self.db.query("SELECT * FROM users ORDER BY points DESC LIMIT 200"):
            self.users_tree.insert("", "end", iid=row["username"], values=(
                row["username"], row["points"], row["watch_minutes"], row["rank"],
            ))

    def _set_user_points(self) -> None:
        sel = self.users_tree.selection()
        if not sel:
            return
        amount = simpledialog.askinteger("Set Points", f"New point total for {sel[0]}:")
        if amount is not None:
            self.db.set_points(sel[0], amount)
            self._refresh_users()

    # -- settings ----------------------------------------------------
    def _build_settings_tab(self) -> None:
        frame = ttk.Frame(self.settings_tab, padding=10)
        frame.pack(fill="both", expand=True)
        cfg = self.config_store.data

        self.settings_vars: dict[str, tk.StringVar] = {}

        def row(label_text, key, secret=False, r=None):
            r = r if r is not None else len(self.settings_vars)
            ttk.Label(frame, text=label_text).grid(row=r, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=getattr(cfg, key, ""))
            self.settings_vars[key] = var
            entry = ttk.Entry(frame, textvariable=var, width=45, show="*" if secret else "")
            entry.grid(row=r, column=1, sticky="w", padx=8)
            return r

        r = 0
        ttk.Label(frame, text="Connection", style="Heading.TLabel").grid(row=r, column=0, sticky="w", pady=(0, 4))
        r += 1
        row("Twitch channel to join", "channel", r=r); r += 1
        row("Bot account username", "bot_username", r=r); r += 1
        row("Bot chat OAuth token", "oauth_token", secret=True, r=r)
        ttk.Button(frame, text="Get chat token via browser", command=self._authorize_chat).grid(row=r, column=2, padx=6)
        r += 1

        ttk.Label(frame, text="Twitch Developer App (for stream info)", style="Heading.TLabel").grid(
            row=r, column=0, sticky="w", pady=(12, 4)
        )
        r += 1
        row("Client ID", "client_id", r=r); r += 1
        row("Client Secret", "client_secret", secret=True, r=r); r += 1
        row("Broadcaster access token", "helix_access_token", secret=True, r=r)
        ttk.Button(frame, text="Authorize (broadcaster)", command=self._authorize_helix).grid(row=r, column=2, padx=6)
        r += 1

        ttk.Label(frame, text="Song Requests", style="Heading.TLabel").grid(row=r, column=0, sticky="w", pady=(12, 4))
        r += 1
        row("YouTube Data API key", "youtube_api_key", secret=True, r=r); r += 1

        ttk.Label(frame, text="Discord Announcements", style="Heading.TLabel").grid(
            row=r, column=0, sticky="w", pady=(12, 4)
        )
        r += 1
        row("Webhook URL", "discord_webhook_url", secret=True, r=r)
        ttk.Button(frame, text="Send test message", command=self._test_discord_webhook).grid(
            row=r, column=2, padx=6
        )
        r += 1
        row("Went-live message", "discord_went_live_message", r=r); r += 1
        ttk.Label(
            frame, text="Placeholders: {channel} {title} {game}", style="Muted.TLabel"
        ).grid(row=r, column=1, sticky="w", padx=8)
        r += 1

        self.discord_enabled_var = tk.BooleanVar(value=cfg.discord_announce_enabled)
        ttk.Checkbutton(
            frame, text="Announce in Discord when I go live on Twitch", variable=self.discord_enabled_var
        ).grid(row=r, column=0, sticky="w", pady=(4, 0))
        r += 1

        self.autoconnect_var = tk.BooleanVar(value=cfg.autoconnect)
        ttk.Checkbutton(frame, text="Connect automatically on launch", variable=self.autoconnect_var).grid(
            row=r, column=0, sticky="w", pady=(12, 0)
        )
        r += 1

        ttk.Button(frame, text="Save Settings", command=self._save_settings).grid(row=r, column=0, pady=16, sticky="w")
        self.settings_saved_label = ttk.Label(frame, text="", style="Muted.TLabel")
        self.settings_saved_label.grid(row=r, column=1, sticky="w", padx=8)

    def _save_settings(self) -> None:
        values = {k: v.get() for k, v in self.settings_vars.items()}
        values["autoconnect"] = self.autoconnect_var.get()
        values["discord_announce_enabled"] = self.discord_enabled_var.get()
        self.config_store.update(**values)
        self.bot.refresh_apis()
        self._flash_saved(self.settings_saved_label)

    def _test_discord_webhook(self) -> None:
        url = self.settings_vars["discord_webhook_url"].get().strip()
        if not url:
            messagebox.showerror(
                "Discord", "Please check your Discord webhook URL in Settings -- it's empty."
            )
            return

        def worker():
            try:
                self.bot.discord.send(
                    url, "Test message from the bot -- if you can see this, the webhook works!"
                )
                self._event_queue.put(("discord_test_result", (True, "")))
            except Exception as exc:
                self._event_queue.put(("discord_test_result", (False, str(exc))))

        threading.Thread(target=worker, daemon=True).start()

    def _authorize_chat(self) -> None:
        client_id = self.settings_vars["client_id"].get().strip()
        if not client_id:
            messagebox.showerror(
                "Authorize",
                "Please check your Client ID in Settings first (from your Twitch Dev "
                "Console app).",
            )
            return
        self._run_oauth(client_id, oauth.CHAT_SCOPES, "oauth_token", prefix="oauth:")

    def _authorize_helix(self) -> None:
        client_id = self.settings_vars["client_id"].get().strip()
        if not client_id:
            messagebox.showerror(
                "Authorize",
                "Please check your Client ID in Settings first (from your Twitch Dev "
                "Console app).",
            )
            return
        self._run_oauth(client_id, oauth.HELIX_SCOPES, "helix_access_token", prefix="")

    def _run_oauth(self, client_id: str, scopes: list[str], target_field: str, prefix: str) -> None:
        def worker():
            result = oauth.authorize(client_id, scopes)
            self._event_queue.put(("oauth_result", (target_field, prefix, result)))
        threading.Thread(target=worker, daemon=True).start()
        messagebox.showinfo("Authorize", "Opening your browser to sign in to Twitch...")

    # -- connect/disconnect -------------------------------------------
    def _toggle_connect(self) -> None:
        if self.bot.connected:
            self.bot.disconnect()
            self.connect_btn.configure(text="Connect")
        else:
            self._connect()

    def _connect(self) -> None:
        if not self.config_store.data.is_ready_to_connect():
            messagebox.showerror(
                "Connect",
                "Please check your Bot settings -- fill in your Twitch channel, Bot "
                "account username, and Bot chat OAuth token in Settings first.",
            )
            return
        self._bot_started_at = time.time()

        def worker():
            try:
                self.bot.connect()
            except Exception as exc:
                self._event_queue.put(("connect_failed", exc))
        threading.Thread(target=worker, daemon=True).start()
        self.connect_btn.configure(text="Disconnect")

    # -- helpers -------------------------------------------------------
    def _toplevel(self, title: str, geometry: str) -> tk.Toplevel:
        dialog = tk.Toplevel(self)
        dialog.title(title)
        dialog.geometry(geometry)
        dialog.configure(bg="#1a1a1a")
        return dialog

    def _flash_saved(self, label: ttk.Label, text: str = "Saved!", ms: int = 2500) -> None:
        """Shows a short confirmation next to a Save button, then clears
        itself a couple seconds later. Used instead of a "Saved."
        popup you have to click OK on every time -- confirming a save
        shouldn't need that."""
        existing = getattr(label, "_fade_after_id", None)
        if existing is not None:
            try:
                self.after_cancel(existing)
            except Exception:
                pass
        label.configure(text=text, style="Success.TLabel")
        label._fade_after_id = self.after(ms, lambda: label.configure(text=""))

    def _friendly_status_text(self, text: str) -> str:
        """Translates the raw connection-status/log lines irc_client.py
        reports (straight off the Twitch IRC wire) into something that
        says what to actually do about it, for the handful of cases
        that are both common and mean something specific -- a bad bot
        token, or the connection dropping. Everything else passes
        through unchanged rather than guessing."""
        if text.startswith("NOTICE:"):
            notice = text[len("NOTICE:"):].strip()
            notice_low = notice.lower()
            if "login authentication failed" in notice_low or "improperly formatted auth" in notice_low:
                return (
                    "Twitch rejected the bot's login -- check the Bot chat OAuth token in "
                    "Settings (try \"Get chat token via browser\" again)."
                )
            return f"Twitch says: {notice}"
        if text.startswith("Connection error:"):
            return "Connection to Twitch dropped -- check your internet connection."
        return text

    # -- background -> UI thread bridge --------------------------------
    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload = self._event_queue.get_nowait()
                if kind == "chat":
                    msg: ChatMessage = payload
                    self._append_chat_message(msg)
                elif kind == "status":
                    friendly = self._friendly_status_text(str(payload))
                    self.status_label.configure(text=friendly)
                    self._append_log(f"* {friendly}")
                    # The connection can also drop on its own (a bad
                    # token, a network hiccup) rather than through the
                    # Disconnect button -- keep the button's label
                    # matching what's actually happened either way.
                    self.connect_btn.configure(text="Disconnect" if self.bot.connected else "Connect")
                elif kind == "connect_failed":
                    exc = payload
                    self.connect_btn.configure(text="Connect")
                    messagebox.showerror("Connect", friendly_error_text(exc))
                elif kind == "outgoing":
                    text, identity = payload
                    self._append_outgoing(text, prefix=identity)
                elif kind == "oauth_result":
                    field, prefix, result = payload
                    if result and result.get("access_token"):
                        self.settings_vars[field].set(prefix + result["access_token"])
                        messagebox.showinfo("Authorize", "Signed in. Click Save Settings to keep it.")
                    else:
                        messagebox.showwarning(
                            "Authorize",
                            "Didn't get signed in -- the browser sign-in either timed out or "
                            "was cancelled. Click the button again to retry.",
                        )
                elif kind == "discord_test_result":
                    ok, err = payload
                    if ok:
                        messagebox.showinfo("Discord", "Test message sent -- check your Discord channel.")
                    else:
                        messagebox.showerror("Discord", friendly_error_text(err))
                elif kind == "dashboard_stream_info_loaded":
                    info = payload
                    title = info.get("title", "") or ""
                    game_name = info.get("game_name") or ""
                    game_id = info.get("game_id") or ""
                    self.dash_title_var.set(title)
                    self.dash_game_var.set(game_name or "[NONE]")
                    if game_name and game_id:
                        self._dash_game_options[game_name.lower()] = game_id
                        self.dash_game_combo.configure(values=[game_name])
                    if not title and not game_name:
                        # Twitch's own API errors here get swallowed by
                        # Bot.get_channel_info() (it's only ever used to
                        # pre-fill a form, so it never raises) -- an
                        # authorized channel almost always has *some*
                        # title, so a totally empty result is more
                        # likely a stale/missing token than a genuinely
                        # blank channel.
                        self.dash_stream_info_status.configure(
                            text="Please check your Broadcaster access token in Settings "
                                 "(nothing came back from Twitch)."
                        )
                    else:
                        self.dash_stream_info_status.configure(text="Loaded current title/game.")
                elif kind == "dashboard_game_search_result":
                    matches, err = payload
                    if err:
                        self.dash_stream_info_status.configure(text=friendly_error_text(err))
                    else:
                        for m in matches:
                            self._dash_game_options[m["name"].lower()] = m["id"]
                        self.dash_game_combo.configure(values=[m["name"] for m in matches])
                        self.dash_stream_info_status.configure(
                            text=f"{len(matches)} match(es) -- pick one from the dropdown."
                            if matches else "No matches."
                        )
                elif kind == "dashboard_stream_info_saved":
                    ok, err = payload
                    self.dash_save_btn.configure(state="normal")
                    if ok:
                        self._flash_saved(self.dash_stream_info_status, "Saved!")
                    else:
                        self.dash_stream_info_status.configure(text=friendly_error_text(err))
        except queue.Empty:
            pass
        self.after(POLL_MS, self._drain_queue)

    def _periodic_refresh(self) -> None:
        self._refresh_leaderboard()
        self._refresh_users()
        self._refresh_songs()
        self._refresh_commands()
        self._refresh_dashboard()
        self._refresh_giveaway()
        self._refresh_game_queue()
        self.after(REFRESH_MS, self._periodic_refresh)

    def _on_close(self) -> None:
        try:
            self.bot.disconnect()
        finally:
            if self._overlay_server is not None:
                self._overlay_server.shutdown()
                self._overlay_server.server_close()
            self.db.close()
            self.destroy()


def run() -> None:
    config = ConfigStore()
    db = Database()
    app = MainWindow(config, db)
    app.mainloop()
