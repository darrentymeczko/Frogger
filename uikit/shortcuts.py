"""Shared focus rules and a cancellable, display-label-only inline editor."""
import tkinter as tk
from tkinter import ttk, messagebox


def editing(widget):
    return isinstance(widget, (tk.Entry, ttk.Entry, tk.Text, tk.Spinbox,
                               ttk.Spinbox, ttk.Combobox))


class InlineLabel:
    def __init__(self, parent, text, commit, restore_focus, **placement):
        self.commit = commit
        self.restore_focus = restore_focus
        self.entry = ttk.Entry(parent)
        self.entry.insert(0, text)
        self.entry.place(**placement)
        self.entry.bind("<Return>", self.accept)
        self.entry.bind("<Escape>", self.cancel)
        self.entry.bind("<FocusOut>", self.cancel)
        self.entry.focus_set()
        self.entry.selection_range(0, "end")
        self.closed = False

    def accept(self, event=None):
        value = self.entry.get().strip()
        # Close before a controller refresh can replace the row underneath us.
        self.cancel()
        try:
            self.commit(value)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Rename", str(exc), parent=self.restore_focus)
        return "break"

    def cancel(self, event=None):
        if not self.closed:
            self.closed = True
            self.entry.destroy()
            if self.restore_focus.winfo_exists():
                self.restore_focus.focus_set()
        return "break"


HELP = """F2: rename the selected treatment on Conditions; otherwise rename the
experiment. In the plate designer it renames the active plate. In results,
it renames the active treatment; click/Tab to the result title for result F2.
Enter commits an inline name; Escape or leaving the field cancels.

Ctrl+Shift+S: Save As (also while an editing field has focus).
Ctrl+S: Save. Ctrl+Z / Ctrl+Y: document undo/redo outside editing controls.

Plate designer (outside editing fields):
Ctrl+Alt+S / R / D: increase first slot / replicate / dilution.
Ctrl+Alt+1 / 2 / 3 / 4: place samples / add dilutions / mark empty / erase.
Ctrl+Alt+G: grid size; Ctrl+Alt+N: add plate; Ctrl+Alt+C: duplicate plate;
Ctrl+Alt+X: delete plate (with existing safeguards). Ctrl+T: show tokens.
The Plate menu and toolbar tooltips list these same actions.

Renaming changes display names, preserving scientific IDs and file paths.
Save As creates a separately named file or independent result folder.
"""


def show_help(parent):
    messagebox.showinfo("Keyboard shortcuts", HELP, parent=parent)
