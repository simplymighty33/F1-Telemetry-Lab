"""Shared native desktop tokens; no external fonts or runtime dependencies."""
import tkinter as tk
from tkinter import font as tkfont, ttk

BACKGROUND = "#10141c"
PANEL = "#19212d"
PANEL_ALT = "#252f3e"
TEXT = "#f4f7fb"
MUTED = "#9cabc0"
ACCENT = "#e10600"
GREEN = "#35c46a"
CYAN = "#49b8ff"
AMBER = "#f5b942"
RED = "#ff5a5f"
GRID = "#334052"


def configure_theme(root):
    style = ttk.Style(root)
    if style.theme_use() != "clam":
        style.theme_use("clam")
    root.configure(bg=BACKGROUND)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure("TButton", background=PANEL_ALT, foreground=TEXT, padding=(10, 5))
    style.map("TButton", background=[("active", "#34445a")], foreground=[("disabled", MUTED)])
    style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL_ALT, foreground=MUTED, padding=(12, 5))
    style.map("TNotebook.Tab", background=[("selected", PANEL)], foreground=[("selected", TEXT)])
    style.configure("Analysis.TCombobox", fieldbackground=PANEL_ALT, background=PANEL_ALT, foreground=TEXT)
    style.map("Analysis.TCombobox", fieldbackground=[("readonly", PANEL_ALT)],
              foreground=[("readonly", TEXT)], selectbackground=[("readonly", "#34445a")])
    style.configure("TEntry", fieldbackground=PANEL_ALT, foreground=TEXT, insertcolor=TEXT)
    for option, value in (("background", PANEL_ALT), ("foreground", TEXT),
                          ("selectBackground", "#34445a"), ("selectForeground", TEXT)):
        root.option_add("*TCombobox*Listbox." + option, value)
    line = tkfont.Font(root=root, font=("Segoe UI", 10)).metrics("linespace")
    for name in ("Telemetry.Treeview", "Metrics.Treeview"):
        style.configure(name, background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        rowheight=max(28, line + 10), borderwidth=0, font=("Segoe UI", 10))
        style.configure(name + ".Heading", background=PANEL_ALT, foreground=TEXT,
                        relief="flat", font=("Segoe UI Semibold", 10))
        style.map(name, background=[("selected", "#34445a")], foreground=[("selected", TEXT)])


def button(parent, text, command, accent=False):
    return tk.Button(parent, text=text, command=command,
                     bg=ACCENT if accent else PANEL_ALT,
                     activebackground="#b90500" if accent else "#34445a",
                     fg=TEXT, activeforeground=TEXT, disabledforeground=MUTED,
                     relief="flat", padx=12, pady=5, cursor="hand2",
                     highlightbackground=BACKGROUND, highlightcolor=CYAN, highlightthickness=1,
                     font=("Segoe UI Semibold", 9))
