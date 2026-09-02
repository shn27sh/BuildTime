"""
ui/modal_toplevel.py — A tk.Toplevel base class that behaves like a native
Windows modal dialog: it blocks all interaction with its parent window
while open, and if the user tries to click through to the blocked parent
anyway, plays the system beep and brings this dialog back to the front —
matching what a real EnableWindow(FALSE)-backed modal does.

Usage: subclasses call ModalToplevel.__init__(self, parent) as the first
line of their own __init__, then build their UI exactly as before (no
self.transient(...)/self.grab_set() of their own -- this base class
already does both). Nothing else changes: subclasses can still call
self.destroy() from a Cancel button, a Save button, or leave the window's
own close button to the default WM_DELETE_WINDOW handling -- every one of
those paths already ends up calling destroy(), which this class overrides
to release the grab (and hand it back to the parent, if the parent is
itself a modal dialog -- see the nested-modal handling below) before the
window actually goes away. A subclass with its OWN close cleanup (like
unregistering itself from a tracking list) can still override
WM_DELETE_WINDOW and call self.destroy() at the end of it as usual --
this base class's destroy() runs either way.
"""
import tkinter as tk


class ModalToplevel(tk.Toplevel):
    def __init__(self, parent, use_transient=True):
        super().__init__(parent)
        self._modal_parent = parent

        # transient() ties this window to its parent at the window-manager
        # level, which is also what makes Windows drop the Minimize/
        # Maximize title-bar buttons -- appropriate for a small, single-
        # purpose dialog (Add Item, Edit Received) where those buttons
        # wouldn't mean anything useful anyway. It's independent of the
        # actual blocking below, though: grab_set() alone already gives
        # full modal behavior with or without transient() (verified
        # directly -- grab_current(), FocusIn detection, and focus_force()
        # all work identically either way). So a caller that wants a
        # LARGER window (Settings, History) to stay fully resizable,
        # maximizable, and minimizable -- while remaining just as modal --
        # can pass use_transient=False and keep every bit of the blocking
        # behavior below.
        if use_transient:
            self.transient(parent)

        # The actual block: grab_set redirects ALL Tk input (mouse,
        # keyboard) to this window and its descendants only, so clicks on
        # the parent (or any other window in this app) are simply not
        # delivered to their normal handlers while this is open.
        self.grab_set()

        # Detecting "user tried to click the blocked parent anyway": the
        # window manager still lets the parent's OS-level window receive
        # focus even though Tk's grab blocks its widgets from responding,
        # so binding <FocusIn> on the parent catches exactly that attempt.
        self._modal_focus_binding = parent.bind("<FocusIn>", self._on_parent_focus, add="+")

        # Forces an initial focus transition onto this window immediately,
        # so the parent has genuinely lost focus from the very start --
        # otherwise, if the window manager doesn't automatically focus a
        # newly-opened Toplevel, clicking the still-focused parent
        # wouldn't produce a NEW <FocusIn> event for the binding above to
        # catch at all.
        self.focus_force()

    def _on_parent_focus(self, event):
        # Only react when the parent window ITSELF took focus, not some
        # unrelated FocusIn bubbling from one of its child widgets.
        if event.widget is self._modal_parent:
            self.bell()
            self.lift()
            self.focus_force()

    def destroy(self):
        # Runs no matter which path closes the window (a Cancel button
        # calling self.destroy directly, a Save button doing the same
        # after committing its result, or the default WM_DELETE_WINDOW
        # handling from the title bar's X) -- so the parent can never be
        # left permanently blocked, and the FocusIn binding never leaks
        # onto a parent that outlives this window.
        if getattr(self, "_modal_focus_binding", None) is not None:
            try:
                self._modal_parent.unbind("<FocusIn>", self._modal_focus_binding)
            except tk.TclError:
                pass
            self._modal_focus_binding = None
        try:
            self.grab_release()
        except tk.TclError:
            pass

        super().destroy()

        # Nested modals (e.g. the "Edit Item" dialog opened from within
        # Settings, itself opened from the main window): Tk's grab isn't
        # stack-based, so closing the inner dialog leaves NOTHING holding
        # the grab unless the outer one explicitly re-establishes it here
        # -- otherwise the main window would be briefly, incorrectly
        # interactive again even though Settings is still open.
        parent = self._modal_parent
        if isinstance(parent, ModalToplevel):
            try:
                if parent.winfo_exists():
                    parent.grab_set()
                    parent.focus_force()
            except tk.TclError:
                pass
