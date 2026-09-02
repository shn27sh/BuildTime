"""
ui/main_window.py — Root window: menu bar (Settings dropdown, as requested),
a search box to filter tables by name/number, status bar, and a scrollable
grid of one TableCard per active table.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui.table_card import TableCard
from ui.walkin_card import WalkInCard
from ui.settings_window import SettingsWindow
from ui.history_window import HistoryWindow
from sync_manager import SyncManager
from database import WALKIN_TABLE_ID
from ui.theme import COLORS, apply_theme, make_brick_logo

CARDS_PER_ROW = 3


class MainWindow:
    def __init__(self, root, db):
        self.root = root
        self.db = db
        self.sync_manager = SyncManager(db)
        self.cards = {}  # table_id -> TableCard (plus WALKIN_TABLE_ID -> WalkInCard)
        self.walkin_card = None
        self._all_tables = []  # current table list, in display order
        self.no_match_label = None
        self.no_tables_label = None
        self.history_window = None
        self._auto_sync_job = None
        self._settings_windows = []  # open SettingsWindow instances (non-modal, can be several)

        root.title("BuildTime")
        apply_theme(root)
        # Wide enough to show 3 table columns *plus* the pinned Walk-in Sale
        # column without the user needing to resize on first launch — the
        # card area only scrolls vertically, so a too-narrow window would
        # otherwise clip that column with no way to reach it.
        root.geometry("1250x700")
        root.minsize(700, 500)

        self._build_menu()
        self._build_brand_header()
        self._build_status_bar()
        self._build_search_bar()
        self._build_scrollable_area()

        self.refresh_tables()  # also restores any in-progress sessions
        self._schedule_auto_sync()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
    def _build_brand_header(self):
        header = tk.Frame(self.root, bg=COLORS["red"], height=74)
        header.pack(side="top", fill="x")
        header.pack_propagate(False)
        logo = make_brick_logo(header, 46)
        logo.pack(side="left", padx=(18, 8), pady=12)
        brand = tk.Frame(header, bg=COLORS["red"])
        brand.pack(side="left", pady=10)
        tk.Label(brand, text="BuildTime", bg=COLORS["red"], fg="white", font=("Segoe UI", 18, "bold")).pack(anchor="w")
        tk.Label(brand, text="LEGO CENTER OPERATIONS", bg=COLORS["red"], fg="#ffd92f", font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(header, text="TABLES  /  SALES  /  CONTROL", bg=COLORS["red"], fg="#ffd9dc", font=("Segoe UI", 9, "bold")).pack(side="right", padx=20)

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="Manage Tables...", command=lambda: self.open_settings(0))
        settings_menu.add_command(label="Manage Items (Snacks/Drinks)...", command=lambda: self.open_settings(1))
        settings_menu.add_command(label="Hourly Rate & Billing...", command=lambda: self.open_settings(2))
        settings_menu.add_command(label="Cloud Sync (Supabase)...", command=lambda: self.open_settings(3))
        menubar.add_cascade(label="Settings", menu=settings_menu)

        data_menu = tk.Menu(menubar, tearoff=0)
        data_menu.add_command(label="History / Records...", command=self.open_history)
        data_menu.add_command(label="Sync Now", command=self.sync_now)
        menubar.add_cascade(label="Data", menu=data_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _show_about(self):
        messagebox.showinfo(
            "About",
            "BuildTime\n\n"
            "Offline-first table timer, snack/drink tracker, and billing.\n"
            "Local SQLite storage with optional Supabase cloud sync.",
        )

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------
    def _build_status_bar(self):
        bar = ttk.Frame(self.root, padding=(12, 5), style="Surface.TFrame")
        bar.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(bar, text="", anchor="w", style="Muted.TLabel")
        self.status_label.pack(side="left", fill="x", expand=True)
        self.refresh_status_bar()

    def refresh_status_bar(self):
        stats = self.db.sync_stats()
        cloud = "configured" if self.sync_manager.is_configured() else "not configured"
        self.status_label.config(
            text=f"Local DB: {self.db.db_path}   |   "
                 f"{stats['total']} record(s), {stats['pending']} pending sync   |   Cloud sync: {cloud}"
        )

    # ------------------------------------------------------------------
    # Search bar (filter the table grid by name/number as you type)
    # ------------------------------------------------------------------
    def _build_search_bar(self):
        bar = ttk.Frame(self.root, padding=(18, 14, 18, 10), style="Surface.TFrame")
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="TABLES", style="Title.TLabel").pack(side="left", padx=(0, 22))
        ttk.Label(bar, text="Search table:", style="Muted.TLabel").pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_args: self._apply_filter())
        ttk.Button(bar, text="Clear", style="Accent.TButton", command=lambda: self.search_var.set("")).pack(
            side="left", padx=(6, 0)
        )

    def _apply_filter(self):
        """Show only the cards whose table name matches the search box
        (e.g. typing "10" finds "Table 10" without scrolling to it). The
        Walk-in Sale card is pinned in its own column to the right of the
        table grid -- it isn't a "table", so it never shifts where any
        table card sits and is never affected by the search box."""
        if not self.cards:
            return

        self.walkin_card.grid(row=0, column=CARDS_PER_ROW, padx=8, pady=8, sticky="nsew")
        self.cards_frame.columnconfigure(CARDS_PER_ROW, weight=1)

        for table_id, card in self.cards.items():
            if table_id != WALKIN_TABLE_ID:
                card.grid_forget()
        if self.no_match_label:
            self.no_match_label.grid_forget()
        if self.no_tables_label:
            self.no_tables_label.grid_forget()

        if not self._all_tables:
            # No configured tables at all -- the walk-in card still works
            # fine on its own, so just note there are no tables alongside it.
            if self.no_tables_label:
                self.no_tables_label.grid(row=0, column=0, columnspan=CARDS_PER_ROW, sticky="nsew")
            return

        query = self.search_var.get().strip().lower()
        matches = (
            [t for t in self._all_tables if query in t["name"].lower()]
            if query else list(self._all_tables)
        )

        if not matches:
            if self.no_match_label:
                self.no_match_label.grid(row=0, column=0, columnspan=CARDS_PER_ROW, sticky="nsew")
            return

        for idx, table in enumerate(matches):
            r, c = divmod(idx, CARDS_PER_ROW)
            card = self.cards[table["id"]]
            card.grid(row=r, column=c, padx=8, pady=8, sticky="nsew")
            self.cards_frame.columnconfigure(c, weight=1)

    # ------------------------------------------------------------------
    # Scrollable card area
    # ------------------------------------------------------------------
    def _build_scrollable_area(self):
        outer = ttk.Frame(self.root, padding=(12, 0, 12, 12), style="Surface.TFrame")
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0, bg=COLORS["canvas"])
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.cards_frame = ttk.Frame(canvas)

        self.cards_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.cards_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            if event.num == 4:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                canvas.yview_scroll(1, "units")
            else:
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        # Windows/Mac use <MouseWheel>, Linux uses <Button-4>/<Button-5>.
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        canvas.bind_all("<Button-4>", _on_mousewheel)
        canvas.bind_all("<Button-5>", _on_mousewheel)

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------
    def refresh_tables(self):
        """Rebuild the card grid from the current table list, then re-attach
        any session that's still running or awaiting checkout."""
        for w in self.cards_frame.winfo_children():
            w.destroy()
        self.cards = {}
        self.no_match_label = None
        self.no_tables_label = None

        # The walk-in card isn't a configured table -- it's a fixed,
        # always-on fixture independent of Settings \u25b8 Manage Tables, so
        # it's created before, and regardless of, whether any tables exist.
        self.walkin_card = WalkInCard(self.cards_frame, self)
        self.cards[WALKIN_TABLE_ID] = self.walkin_card

        self._all_tables = self.db.list_tables(active_only=True)
        if not self._all_tables:
            self.no_tables_label = ttk.Label(
                self.cards_frame, text="No tables yet. Add one via Settings \u25b8 Manage Tables.",
                foreground="gray", padding=20,
            )
        else:
            for table in self._all_tables:
                card = TableCard(self.cards_frame, self, table)
                self.cards[table["id"]] = card

        self.no_match_label = ttk.Label(
            self.cards_frame, text="No table matches your search.", foreground="gray", padding=20,
        )
        self._apply_filter()
        self._restore_active_sessions()

    def table_has_active_session(self, table_id):
        return any(s["table_id"] == table_id for s in self.db.get_active_sessions())

    def any_stopwatch_running(self):
        """True if at least one table's timer is actively running right now.
        Used to lock the Settings window while a customer's time is ticking."""
        return self.db.has_running_session()

    def _restore_active_sessions(self):
        for session in self.db.get_active_sessions():
            card = self.cards.get(session["table_id"])
            if card:
                card.restore(session)

    def on_session_completed(self):
        self.refresh_status_bar()
        self.history_window_refresh()

    # ------------------------------------------------------------------
    # Settings / history windows
    # ------------------------------------------------------------------
    def open_settings(self, tab=0):
        # Settings is now a true modal dialog, so there's only ever one --
        # if it's already open, just switch it to the requested tab and
        # bring it forward rather than trying to open a second one (which
        # its own grab would block anyway; this is the same
        # already-open-so-just-refocus guard open_history() below uses).
        if self._settings_windows:
            existing = self._settings_windows[0]
            if existing.winfo_exists():
                existing._notebook.select(tab)
                existing.lift()
                existing.focus_force()
                self.refresh_status_bar()
                return
        SettingsWindow(self, initial_tab=tab)
        self.refresh_status_bar()

    def register_settings_window(self, window):
        """Settings is a true modal dialog, so in practice there's only
        ever one of these at a time now -- kept list-based rather than a
        single optional reference anyway, since the live-lock
        notification below doesn't care how many there are, and there's
        no reason to touch otherwise-working, already-tested code for a
        difference that's now purely cosmetic."""
        self._settings_windows.append(window)

    def unregister_settings_window(self, window):
        if window in self._settings_windows:
            self._settings_windows.remove(window)

    def notify_stopwatch_state_changed(self):
        """Call this whenever a table's timer starts, stops, or resumes.
        Settings can be opened before any table is started, so any Settings
        window(s) already open need to lock/unlock themselves live rather
        than only checking once when they were first opened."""
        for window in list(self._settings_windows):
            if window.winfo_exists():
                window.refresh_lock_state(show_popup_if_locked=True)
            else:
                self._settings_windows.remove(window)

    def notify_items_catalog_changed(self):
        """Call this whenever Settings adds, edits, hides, or shows an item.
        A table's inline item buttons only get rebuilt on Start/Resume, but
        Settings is locked the whole time any table is running, so that
        gap never shows up there in practice. The Walk-in Sale card is
        different -- its item buttons stay visible at all times and
        Settings is deliberately NOT locked while its cart is just sitting
        open, so without this it would keep showing a stale catalog until
        the cart happened to cycle through Complete Sale/Finish. Every card
        gets asked here (harmlessly a no-op for one that isn't currently
        showing its item buttons) rather than special-casing which type of
        card needs it."""
        for card in self.cards.values():
            refresh = getattr(card, "refresh_items_catalog", None)
            if refresh:
                refresh()

    def open_history(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.lift()
            return
        self.history_window = HistoryWindow(self)

    def history_window_refresh(self):
        if self.history_window and self.history_window.winfo_exists():
            self.history_window.refresh()

    # ------------------------------------------------------------------
    # Sync
    # ------------------------------------------------------------------
    def reload_sync_settings(self):
        self.refresh_status_bar()
        self._schedule_auto_sync()

    def sync_now(self):
        if not self.sync_manager.is_configured():
            messagebox.showwarning("Cloud Sync", "Set up your Supabase URL and API key in Settings first.")
            return
        threading.Thread(target=self._do_sync, daemon=True).start()

    def _do_sync(self):
        def progress(i, n):
            self.root.after(0, lambda: self.status_label.config(text=f"Syncing {i}/{n}..."))

        result = self.sync_manager.sync_all(progress_callback=progress)
        self.root.after(0, lambda: self._on_sync_done(result))

    def _on_sync_done(self, result):
        self.refresh_status_bar()
        self.history_window_refresh()
        messagebox.showinfo("Sync Result", result["message"])

    def _schedule_auto_sync(self):
        if self._auto_sync_job:
            self.root.after_cancel(self._auto_sync_job)
            self._auto_sync_job = None
        enabled = self.db.get_setting("auto_sync_enabled", "0") == "1"
        if not enabled:
            return
        interval_min = int(self.db.get_setting("auto_sync_interval_minutes", "15") or 15)
        self._auto_sync_job = self.root.after(interval_min * 60 * 1000, self._auto_sync_tick)

    def _auto_sync_tick(self):
        if self.sync_manager.is_configured():
            threading.Thread(target=self._do_sync, daemon=True).start()
        self._schedule_auto_sync()

    # ------------------------------------------------------------------
    # Close
    # ------------------------------------------------------------------
    def _on_close(self):
        # A native modal dialog also blocks the owner window from being
        # closed while it's open (Alt+F4 on a window with an active modal
        # child typically just reactivates the child) -- grab_current()
        # is Tkinter's own way to ask "what currently holds this
        # application's grab, if anything", so this works for Settings,
        # History, or any nested dialog between them without needing to
        # track each one specifically here.
        modal = self.root.grab_current()
        if modal is not None and modal is not self.root:
            modal.bell()
            modal.lift()
            modal.focus_force()
            return
        active = self.db.get_active_sessions()
        if active:
            names = ", ".join(s["table_name_snapshot"] for s in active)
            if not messagebox.askyesno(
                "Tables still active",
                f"These still have unsaved sessions in progress: {names}\n\n"
                "Your data is safe in the local database and will still be here next time you "
                "open the app. Close anyway?",
            ):
                return
        self.root.destroy()
