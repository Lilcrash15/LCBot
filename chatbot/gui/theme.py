"""ttk theming for LCBot, plus the built-in theme presets and the
custom-color-scheme machinery behind the Themes tab (main_window.py's
_build_themes_tab).

The default look ("Classic") is deliberately styled after the classic
AnkhBot look (black panels, a bright orange frame around the tab
content, light gray text) -- see PRESETS["classic"] below. Everything
in this module used to be a handful of hardcoded module-level
constants (BG, FG, ACCENT, ...); those still exist and are still what
the rest of the app reads (theme.BG, theme.FG, style_text_widget(),
popup_menu_kwargs(), ...), but they're now *live* -- apply_theme()
overwrites them whenever the user switches themes, so anything that
reads them at call time (rather than importing a copied value) picks
up the new theme automatically, no restart needed for ttk-styled
widgets. Plain tk widgets that set their colors once at creation time
(the chat log, moderation lists, popup menus, the orange border frame)
need to be told again explicitly -- see MainWindow._apply_theme_to_live_widgets.
"""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Optional

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

# -- built-in presets ------------------------------------------------------
# Every preset defines the same 10 keys. "classic" is LCBot's original
# (and still default) look; the rest are new options for the Themes tab.
PRESETS: dict[str, dict[str, str]] = {
    "classic": {
        "BG": "#1a1a1a", "PANEL_BG": "#202020", "FIELD_BG": "#2a2a2a", "TAB_BG": "#141414",
        "FG": "#e6e6e6", "MUTED_FG": "#9a9a9a", "ACCENT": "#e8720c", "ACCENT_DIM": "#7a3d08",
        "SELECT_BG": "#e8720c", "SELECT_FG": "#141414",
    },
    "dark": {
        "BG": "#181a20", "PANEL_BG": "#1f2128", "FIELD_BG": "#262932", "TAB_BG": "#121317",
        "FG": "#e8e9ec", "MUTED_FG": "#8b8f99", "ACCENT": "#4d9dff", "ACCENT_DIM": "#2a5a92",
        "SELECT_BG": "#4d9dff", "SELECT_FG": "#0d1117",
    },
    "light": {
        "BG": "#f2f2f4", "PANEL_BG": "#ffffff", "FIELD_BG": "#ffffff", "TAB_BG": "#e3e3e7",
        "FG": "#1c1c1e", "MUTED_FG": "#68686d", "ACCENT": "#d9640a", "ACCENT_DIM": "#c96410",
        "SELECT_BG": "#d9640a", "SELECT_FG": "#ffffff",
    },
    "synthwave": {
        "BG": "#1a0f2e", "PANEL_BG": "#241640", "FIELD_BG": "#2e1c52", "TAB_BG": "#150a24",
        "FG": "#f2e9ff", "MUTED_FG": "#a992c9", "ACCENT": "#ff2e97", "ACCENT_DIM": "#9c1a5c",
        "SELECT_BG": "#ff2e97", "SELECT_FG": "#1a0f2e",
    },
    "forest": {
        "BG": "#0f1c14", "PANEL_BG": "#16261c", "FIELD_BG": "#1e3327", "TAB_BG": "#0a140e",
        "FG": "#dcecdf", "MUTED_FG": "#8fae97", "ACCENT": "#4caf50", "ACCENT_DIM": "#2e6b31",
        "SELECT_BG": "#4caf50", "SELECT_FG": "#0f1c14",
    },
}

# Display order + labels for the Themes tab's dropdown. "custom" isn't a
# key in PRESETS -- its colors are worked out on the fly from whatever
# the user picked (see build_custom_colors).
THEME_ORDER = ["classic", "dark", "light", "synthwave", "forest", "custom"]
THEME_LABELS = {
    "classic": "Classic (AnkhBot)",
    "dark": "Dark Mode",
    "light": "Light Mode",
    "synthwave": "Synthwave",
    "forest": "Forest",
    "custom": "Custom",
}

# -- current live colors ---------------------------------------------------
# Kept as plain module globals (rather than e.g. a dict) so existing call
# sites across the app (theme.BG, theme.FG, theme.ACCENT, ...) keep
# working unchanged -- apply_theme() below just reassigns these each
# time the theme changes, and every function/tag-color lookup in this
# file and main_window.py reads them at call time, not import time.
BG = PRESETS["classic"]["BG"]
PANEL_BG = PRESETS["classic"]["PANEL_BG"]
FIELD_BG = PRESETS["classic"]["FIELD_BG"]
TAB_BG = PRESETS["classic"]["TAB_BG"]
FG = PRESETS["classic"]["FG"]
MUTED_FG = PRESETS["classic"]["MUTED_FG"]
ACCENT = PRESETS["classic"]["ACCENT"]
ACCENT_DIM = PRESETS["classic"]["ACCENT_DIM"]
SELECT_BG = PRESETS["classic"]["SELECT_BG"]
SELECT_FG = PRESETS["classic"]["SELECT_FG"]


def is_valid_hex_color(value: str) -> bool:
    return bool(_HEX_RE.match((value or "").strip()))


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(c))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _mix(color_a: str, color_b: str, weight_b: float) -> str:
    """weight_b=0 -> color_a, weight_b=1 -> color_b."""
    ar, ag, ab = _hex_to_rgb(color_a)
    br, bg_, bb = _hex_to_rgb(color_b)
    return _rgb_to_hex((
        ar + (br - ar) * weight_b,
        ag + (bg_ - ag) * weight_b,
        ab + (bb - ab) * weight_b,
    ))


def _relative_luminance(color: str) -> float:
    r, g, b = (c / 255.0 for c in _hex_to_rgb(color))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def build_custom_colors(bg: str, fg: str, accent: str) -> dict[str, str]:
    """Works out a full 10-color theme from just the three colors the
    Themes tab actually asks the user to pick (background, text,
    accent) -- panels/fields/tabs/muted-text/select-colors are all
    derived so a "Custom" theme still looks coherent instead of
    needing ten color pickers. Falls back to the Classic values for
    any input that isn't a real "#rrggbb" color, so a bad/blank entry
    can never crash theme application."""
    if not is_valid_hex_color(bg):
        bg = PRESETS["classic"]["BG"]
    if not is_valid_hex_color(fg):
        fg = PRESETS["classic"]["FG"]
    if not is_valid_hex_color(accent):
        accent = PRESETS["classic"]["ACCENT"]

    bg_is_dark = _relative_luminance(bg) < 0.5
    # Panels/fields step away from the background in whichever
    # direction reads as "raised" for this background -- lighter steps
    # on a dark bg, darker steps on a light bg.
    step_toward = fg if bg_is_dark else "#000000"
    panel_bg = _mix(bg, step_toward, 0.06)
    field_bg = _mix(bg, step_toward, 0.11)
    tab_bg = _mix(bg, "#000000" if bg_is_dark else fg, 0.10)
    muted_fg = _mix(fg, bg, 0.45)
    accent_dim = _mix(accent, "#000000", 0.45)
    select_fg = "#141414" if _relative_luminance(accent) > 0.5 else "#f5f5f5"

    return {
        "BG": bg, "PANEL_BG": panel_bg, "FIELD_BG": field_bg, "TAB_BG": tab_bg,
        "FG": fg, "MUTED_FG": muted_fg, "ACCENT": accent, "ACCENT_DIM": accent_dim,
        "SELECT_BG": accent, "SELECT_FG": select_fg,
    }


def serialize_custom_colors(bg: str, fg: str, accent: str) -> str:
    """The 3 user-picked colors behind a Custom theme, as the compact
    string stored in the settings table (key "theme_custom_colors") --
    just the inputs, not the 10 derived colors, so tweaking
    build_custom_colors later improves existing saved custom themes
    instead of leaving them stuck with whatever was derived at save
    time."""
    return f"{bg}|{fg}|{accent}"


def parse_custom_colors(raw: Optional[str]) -> Optional[tuple[str, str, str]]:
    """Reverses serialize_custom_colors(). Returns None for anything
    that isn't exactly 3 valid hex colors (missing setting, corrupted
    value, old format) -- callers should fall back to the Classic
    preset's colors in that case rather than crash."""
    if not raw:
        return None
    parts = raw.split("|")
    if len(parts) != 3 or not all(is_valid_hex_color(p) for p in parts):
        return None
    return parts[0], parts[1], parts[2]


def resolve_colors(theme_name: str, custom_raw: Optional[str] = None) -> dict[str, str]:
    """The single entry point for "what colors does this theme name
    resolve to right now" -- used both at startup (main_window.py
    reads theme_name/theme_custom_colors from the database) and when
    the user hits "Apply Theme". Never raises: an unknown theme name
    or an unparseable custom-color string both fall back to Classic
    rather than blocking the app from opening."""
    if theme_name == "custom":
        parsed = parse_custom_colors(custom_raw)
        if parsed is not None:
            return build_custom_colors(*parsed)
        return dict(PRESETS["classic"])
    return dict(PRESETS.get(theme_name, PRESETS["classic"]))


def apply_theme(root: tk.Tk, colors: dict[str, str]) -> ttk.Style:
    """Applies a resolved 10-color theme dict to the whole ttk.Style
    (which live-updates every ttk widget already on screen -- that's
    the whole point of ttk styling) and updates this module's live
    globals so everything else that reads theme.BG/theme.FG/etc, or
    calls style_text_widget()/style_listbox()/popup_menu_kwargs(),
    picks up the new colors too. Safe to call again any time, not just
    once at startup -- see MainWindow._apply_theme_to_live_widgets."""
    global BG, PANEL_BG, FIELD_BG, TAB_BG, FG, MUTED_FG, ACCENT, ACCENT_DIM, SELECT_BG, SELECT_FG
    BG, PANEL_BG, FIELD_BG, TAB_BG = colors["BG"], colors["PANEL_BG"], colors["FIELD_BG"], colors["TAB_BG"]
    FG, MUTED_FG = colors["FG"], colors["MUTED_FG"]
    ACCENT, ACCENT_DIM = colors["ACCENT"], colors["ACCENT_DIM"]
    SELECT_BG, SELECT_FG = colors["SELECT_BG"], colors["SELECT_FG"]

    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", background=BG, foreground=FG, fieldbackground=FIELD_BG,
                     bordercolor=ACCENT_DIM, lightcolor=BG, darkcolor=BG,
                     insertcolor=FG, font=("Segoe UI", 9))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED_FG)
    # A short "Saved!" confirmation next to a Save button, instead of a
    # popup you have to click OK on -- see MainWindow._flash_saved.
    style.configure("Success.TLabel", background=BG, foreground="#5cb85c", font=("Segoe UI", 9, "bold"))
    # A clickable "Update available" banner in the top bar -- see
    # MainWindow._check_for_update. Underlined + accent-colored so it
    # reads as a link, not just another status label.
    style.configure(
        "UpdateAvailable.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 9, "bold", "underline")
    )
    style.configure("Heading.TLabel", background=BG, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
    style.configure("Stat.TLabel", background=BG, foreground=FG, font=("Segoe UI", 18, "bold"))

    style.configure("TButton", background=FIELD_BG, foreground=FG, borderwidth=1,
                     focuscolor=ACCENT, padding=(10, 4))
    style.map("TButton",
              background=[("active", ACCENT), ("pressed", ACCENT_DIM)],
              foreground=[("active", SELECT_FG)])

    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("active", ACCENT)])

    style.configure("TEntry", fieldbackground=FIELD_BG, foreground=FG, insertcolor=FG,
                     bordercolor=ACCENT_DIM)
    style.configure("TSpinbox", fieldbackground=FIELD_BG, foreground=FG, arrowsize=12,
                     bordercolor=ACCENT_DIM)
    style.configure("TCombobox", fieldbackground=FIELD_BG, background=FIELD_BG, foreground=FG,
                     arrowsize=12, bordercolor=ACCENT_DIM)
    style.map("TCombobox", fieldbackground=[("readonly", FIELD_BG)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", FIELD_BG)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

    style.configure("TLabelframe", background=BG, bordercolor=ACCENT_DIM)
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=("Segoe UI", 9, "bold"))

    style.configure("TNotebook", background=BG, bordercolor=ACCENT, borderwidth=2, tabmargins=(2, 4, 2, 0))
    style.configure("TNotebook.Tab", background=TAB_BG, foreground=FG, padding=(7, 4), borderwidth=0,
                     font=("Segoe UI", 8))
    style.map("TNotebook.Tab",
              background=[("selected", BG)],
              foreground=[("selected", ACCENT)],
              expand=[("selected", (1, 1, 1, 0))])

    style.configure("Treeview", background=PANEL_BG, fieldbackground=PANEL_BG, foreground=FG,
                     bordercolor=ACCENT_DIM, borderwidth=1, rowheight=22)
    style.configure("Treeview.Heading", background=TAB_BG, foreground=ACCENT, relief="flat")
    style.map("Treeview.Heading", background=[("active", ACCENT_DIM)])
    style.map("Treeview", background=[("selected", SELECT_BG)], foreground=[("selected", SELECT_FG)])

    style.configure("TScrollbar", background=FIELD_BG, troughcolor=BG, bordercolor=BG, arrowcolor=FG)

    # The top toolbar strip that replaces the OS-native menu bar (see
    # main_window.py's _build_toolbar) -- Windows renders a real
    # tk.Menu attached via root.config(menu=...) with native chrome
    # that mostly ignores ttk/color styling, which is why it used to
    # show up as a jarring light-gray strip against the dark theme.
    # ttk.Menubutton is a normal themeable widget, so this one actually
    # takes the dark theme.
    style.configure("Toolbar.TFrame", background=TAB_BG)
    style.configure("Toolbar.TMenubutton", background=TAB_BG, foreground=FG, arrowcolor=FG,
                     borderwidth=0, relief="flat", padding=(10, 4), font=("Segoe UI", 9))
    style.map("Toolbar.TMenubutton",
              background=[("active", ACCENT_DIM), ("pressed", ACCENT_DIM)],
              foreground=[("active", FG)])

    return style


def apply_dark_theme(root: tk.Tk) -> ttk.Style:
    """Back-compat wrapper -- applies the Classic (AnkhBot) preset.
    Kept around since it's a smaller/simpler entry point than
    apply_theme() for anything (tests, a future headless tool) that
    just wants "the" default theme rather than a specific one."""
    return apply_theme(root, PRESETS["classic"])


def current_is_dark() -> bool:
    """Whether the live theme's background reads as dark. Used for
    anything that has to pick one of exactly two looks rather than a
    real color -- currently just the Windows native titlebar (see
    MainWindow._apply_windows_titlebar_mode), which Tkinter can't
    recolor itself and Windows only exposes as a light/dark toggle,
    not an arbitrary color."""
    return _relative_luminance(BG) < 0.5


def popup_menu_kwargs() -> dict:
    """Shared color kwargs for classic tk.Menu popups -- the toolbar's
    Credentials/Help dropdowns, the Console tab's per-user right-click
    menu. tk.Menu predates ttk and doesn't pick up ttk.Style, so every
    popup menu in the app passes these kwargs at construction time
    instead, to keep them looking like part of the same theme rather
    than Tk's stock light popup menu. Reads the live globals, so a
    freshly-built menu always matches the current theme; an
    already-built menu (see MainWindow._build_toolbar) needs
    .configure(**popup_menu_kwargs()) called again after a theme
    switch."""
    return dict(
        bg=FIELD_BG, fg=FG, activebackground=ACCENT, activeforeground=SELECT_FG,
        disabledforeground=MUTED_FG, relief="flat", borderwidth=1, activeborderwidth=0,
    )


def style_text_widget(widget: tk.Text) -> None:
    widget.configure(background=PANEL_BG, foreground=FG, insertbackground=FG,
                      selectbackground=ACCENT, selectforeground=SELECT_FG,
                      relief="flat", highlightthickness=1, highlightbackground=ACCENT_DIM,
                      highlightcolor=ACCENT)


def style_listbox(widget: tk.Listbox) -> None:
    widget.configure(background=PANEL_BG, foreground=FG, selectbackground=ACCENT,
                      selectforeground=SELECT_FG, relief="flat", highlightthickness=1,
                      highlightbackground=ACCENT_DIM, highlightcolor=ACCENT)
