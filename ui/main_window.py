"""
ui/main_window.py — Root window: menu bar (Settings dropdown, as requested),
a search box to filter tables by name/number, status bar, and a scrollable
grid of one TableCard per active table.
"""
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from ui.table_card import TableCard
from ui.settings_window import SettingsWindow
from ui.history_window import HistoryWindow
from sync_manager import SyncManager

CARDS_PER_ROW = 3


class MainWindow:
    def __init__(self, root, db):
        self.root = root
        self.db = db
        self.sync_manager = SyncManager(db)
        self.cards = {}  # table_id -> TableCard
        self._all_tables = []  # current table list, in display order
        self.no_match_label = None
        self.history_window = None
        self._auto_sync_job = None

        root.title("BuildTime")
        root.geometry("1000x700")
        root.minsize(700, 500)

        self._build_menu()
        self._build_status_bar()
        self._build_search_bar()
        self._build_scrollable_area()

        self.refresh_tables()  # also restores any in-progress sessions
        self._schedule_auto_sync()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Menu
    # ------------------------------------------------------------------
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
        bar = ttk.Frame(self.root, relief="sunken")
        bar.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(bar, text="", anchor="w", padding=(8, 2))
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
        bar = ttk.Frame(self.root, padding=(8, 6))
        bar.pack(side="top", fill="x")
        ttk.Label(bar, text="Search table:").pack(side="left", padx=(0, 6))
        self.search_var = tk.StringVar()
        entry = ttk.Entry(bar, textvariable=self.search_var)
        entry.pack(side="left", fill="x", expand=True)
        self.search_var.trace_add("write", lambda *_args: self._apply_filter())
        ttk.Button(bar, text="Clear", command=lambda: self.search_var.set("")).pack(
            side="left", padx=(6, 0)
        )

    def _apply_filter(self):
        """Show only the cards whose table name matches the search box
        (e.g. typing "10" finds "Table 10" without scrolling to it)."""
        if not self.cards:
            return
        query = self.search_var.get().strip().lower()
        matches = (
            [t for t in self._all_tables if query in t["name"].lower()]
            if query else list(self._all_tables)
        )

        for card in self.cards.values():
            card.grid_forget()
        if self.no_match_label:
            self.no_match_label.grid_forget()

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
        outer = ttk.Frame(self.root)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
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
        self._all_tables = self.db.list_tables(active_only=True)
        if not self._all_tables:
            ttk.Label(
                self.cards_frame, text="No tables yet. Add one via Settings \u25b8 Manage Tables.",
                foreground="gray", padding=20,
            ).pack()
            return
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
        SettingsWindow(self, initial_tab=tab)
        self.refresh_status_bar()

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
        active = self.db.get_active_sessions()
        if active:
            names = ", ".join(s["table_name_snapshot"] for s in active)
            if not messagebox.askyesno(
                "Tables still active",
                f"These tables still have unsaved sessions in progress: {names}\n\n"
                "Your data is safe in the local database and will still be here next time you "
                "open the app. Close anyway?",
            ):
                return
        self.root.destroy()
