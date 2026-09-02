"""Shared visual language for the BuildTime LEGO center interface."""
import tkinter as tk
from tkinter import ttk


COLORS = {
    "red": "#d71920",
    "red_dark": "#a80f16",
    "red_soft": "#fce8e8",
    "yellow": "#f7c600",
    "yellow_soft": "#fff5cc",
    "blue": "#0878c9",
    "blue_soft": "#e7f3fc",
    "green": "#08a045",
    "green_soft": "#e5f6ec",
    "ink": "#17233c",
    "muted": "#64748b",
    "surface": "#ffffff",
    "canvas": "#f3f6f9",
    "line": "#d9e1ea",
    "disabled": "#aab5c2",
}

FONT = "Segoe UI"
MONO_FONT = "Consolas"


def apply_theme(root):
    """Configure the app-wide ttk styles once, before child widgets exist."""
    root.configure(background=COLORS["canvas"])
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure("TFrame", background=COLORS["canvas"])
    style.configure("Surface.TFrame", background=COLORS["surface"])
    style.configure("TLabel", background=COLORS["canvas"], foreground=COLORS["ink"], font=(FONT, 10))
    style.configure("Muted.TLabel", background=COLORS["canvas"], foreground=COLORS["muted"], font=(FONT, 9))
    style.configure("Title.TLabel", background=COLORS["canvas"], foreground=COLORS["ink"], font=(FONT, 18, "bold"))
    style.configure("Header.TLabel", background=COLORS["red"], foreground="white", font=(FONT, 10, "bold"))
    style.configure("TButton", padding=(12, 7), font=(FONT, 9, "bold"), borderwidth=0)
    style.map("TButton", background=[("active", COLORS["blue"]), ("disabled", COLORS["line"])], foreground=[("disabled", COLORS["disabled"])])
    style.configure("Accent.TButton", background=COLORS["red"], foreground="white")
    style.map("Accent.TButton", background=[("active", COLORS["red_dark"]), ("pressed", COLORS["red_dark"])])
    style.configure("Start.TButton", background=COLORS["green"], foreground="white")
    style.map("Start.TButton", background=[("active", "#07853a"), ("pressed", "#066c30")])
    style.configure("Stop.TButton", background=COLORS["red"], foreground="white")
    style.map("Stop.TButton", background=[("active", COLORS["red_dark"]), ("pressed", COLORS["red_dark"])])
    style.configure("Checkout.TButton", background=COLORS["yellow"], foreground=COLORS["ink"])
    style.map("Checkout.TButton", background=[("active", "#e2b300"), ("pressed", "#c59d00")])
    style.configure("TEntry", padding=7, fieldbackground=COLORS["surface"], foreground=COLORS["ink"])
    style.configure("TCombobox", padding=6, fieldbackground=COLORS["surface"])
    style.configure("TLabelframe", background=COLORS["surface"], foreground=COLORS["ink"], bordercolor=COLORS["line"], relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=COLORS["surface"], foreground=COLORS["ink"], font=(FONT, 10, "bold"))
    style.configure("Treeview", background=COLORS["surface"], fieldbackground=COLORS["surface"], foreground=COLORS["ink"], rowheight=30, font=(FONT, 9))
    style.configure("Treeview.Heading", background=COLORS["red"], foreground="white", font=(FONT, 9, "bold"), padding=7)
    style.map("Treeview", background=[("selected", COLORS["blue"])], foreground=[("selected", "white")])
    style.configure("TNotebook", background=COLORS["canvas"], borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 8), font=(FONT, 9, "bold"))
    style.configure("Vertical.TScrollbar", troughcolor=COLORS["canvas"], background=COLORS["line"])


def make_brick_logo(parent, size=42):
    """Return a small Canvas LEGO brick mark for the app header."""
    canvas = tk.Canvas(parent, width=size, height=size, bg=COLORS["red"], highlightthickness=0)
    left, top = 5, 14
    right, bottom = size - 5, size - 7
    canvas.create_rectangle(left, top, right, bottom, fill=COLORS["yellow"], outline="#c49d00", width=1)
    canvas.create_polygon(left, top, left + 7, top - 6, right + 1, top - 6, right, top, fill="#ffd92f", outline="#c49d00")
    for x in (left + 10, left + 24):
        canvas.create_oval(x, top - 4, x + 7, top + 3, fill="#ffe66b", outline="#c49d00")
    return canvas
