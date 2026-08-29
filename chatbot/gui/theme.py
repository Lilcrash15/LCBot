"""A dark, orange-accented ttk theme -- deliberately styled after the
classic AnkhBot look (black panels, a bright orange frame around the
tab content, light gray text) rather than a stock light-mode Tk app.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BG = "#1a1a1a"          # window/tab background
PANEL_BG = "#202020"    # frames, list/tree backgrounds
FIELD_BG = "#2a2a2a"    # entries, comboboxes, spinboxes
TAB_BG = "#141414"      # inactive tab background
FG = "#e6e6e6"          # normal text
MUTED_FG = "#9a9a9a"    # secondary text
ACCENT = "#e8720c"      # AnkhBot orange
ACCENT_DIM = "#7a3d08"
SELECT_BG = "#e8720c"
SELECT_FG = "#141414"


def apply_dark_theme(root: tk.Tk) -> ttk.Style:
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


def popup_menu_kwargs() -> dict:
    """Shared color kwargs for classic tk.Menu popups -- the toolbar's
    Credentials/Help dropdowns, the Console tab's per-user right-click
    menu. tk.Menu predates ttk and doesn't pick up ttk.Style, so every
    popup menu in the app passes these kwargs at construction time
    instead, to keep them looking like part of the same dark/orange
    theme rather than Tk's stock light popup menu."""
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
