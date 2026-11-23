"""The existing plate designer editing a detached experiment layout draft."""

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from plate_template.app import DesignerApp
from plate_template.schema import from_dict, to_dict


class LayoutEditor(DesignerApp):
    def __init__(self, parent, template, on_apply):
        self.on_apply = on_apply
        window = tk.Toplevel(parent)
        window.transient(parent)
        super().__init__(window, from_dict(to_dict(template)))
        window.grab_set()

    def _build_menu(self):
        super()._build_menu()
        # Ctrl+S applies the draft, with no file dialog. Reusable-file saving
        # is offered on the experiment's final tab instead.
        self.file_menu.delete("Use layout in experiment")
        self.file_menu.entryconfigure("Save", label="Use layout in experiment")
        self.file_menu.entryconfigure("Save As...", label="Save reusable template as...")
        self.file_menu.entryconfigure("New blank template", label="New blank layout")
        self.file_menu.entryconfigure("Open...", label="Copy from template...")
        self.edit_menu.entryconfigure("Rename template...", label="Rename layout...")

    def refresh(self):
        super().refresh()
        self.host.set_title(f"Experiment plate layout - {self.template.name}")

    def _build_body(self):
        super()._build_body()
        ttk.Button(self.use_layout_button.master, text="Cancel",
                   command=self.host.close).pack(side="right")

    def use_in_experiment(self):
        self.on_apply(from_dict(to_dict(self.template)))
        self.controller.mark_saved()
        self.host.close()

    def save_file(self):
        self.use_in_experiment()
        return True

    def save_file_as(self):
        """Export the draft without applying it or marking it accepted."""
        from plate_template.schema import save
        chosen = filedialog.asksaveasfilename(
            parent=self.root, title="Save reusable plate template",
            initialfile=f"{self.template.name}.json", defaultextension=".json",
            filetypes=[("Plate template", "*.json")])
        if not chosen:
            return False
        try:
            save(self.template, Path(chosen))
        except (OSError, ValueError) as exc:
            messagebox.showerror("Save template", str(exc), parent=self.root)
            return False
        return True

    def _confirm_discard(self):
        return not self.controller.dirty or messagebox.askyesno(
            "Discard layout edits?",
            "Discard this layout draft? The experiment will keep its current layout.",
            parent=self.root, default="no")
