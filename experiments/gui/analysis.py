"""Final reference editor shared by experiment setup and results review."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk, messagebox

from .. import analysis


class AnalysisEditor(ttk.Frame):
    def __init__(self, parent, strains, conditions, config, exclusions=None, condition_labels=None):
        super().__init__(parent, padding=12)
        self.strains = list(strains)
        self.conditions = list(conditions)
        self.condition_names = {}
        for code in self.conditions:
            label = (condition_labels or {}).get(code) or code
            display, suffix = label, 2
            while display in self.condition_names:
                display = f"{label} ({suffix})"
                suffix += 1
            self.condition_names[display] = code
        self.exclusions = exclusions or {}
        self.config = analysis.validate(deepcopy(config))
        if not self.config["references"]:
            self.config["references"]["*"] = analysis.default_references(self.strains)
        has_overrides = any(code in self.config["references"] for code in self.conditions)
        self.different_comparisons = tk.BooleanVar(value=has_overrides)
        self.current = next((code for code in self.conditions
                             if code in self.config["references"]), "*")
        self.condition = tk.StringVar(value=next(
            (label for label, code in self.condition_names.items() if code == self.current), ""))

        # Keep Apply outside this viewport so it stays reachable on small screens.
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0,
                                background=ttk.Style(self).lookup("TFrame", "background"))
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(self.canvas, padding=(0, 0, 12, 0))
        self._content_window = self.canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._content_window, width=e.width))
        ttk.Label(content, text="Normalization reference").pack(anchor="w")
        ttk.Label(content, text=(
            "Growth on each plate is normalized to this strain's growth."
        ), wraplength=650).pack(anchor="w", fill="x", pady=(3, 0))
        self.slots = {f"{i}: {name}": i for i, name in enumerate(strains, 1) if name}
        self.normalization = tk.StringVar()
        self.normalization_picker = ttk.Combobox(content, textvariable=self.normalization,
            values=["Not selected", *self.slots], state="readonly")
        self.normalization_picker.pack(fill="x", pady=(3, 8))
        self.normalization_picker.bind("<<ComboboxSelected>>", lambda e: self._changed(field="normalization"))
        self.comparison_scope = ttk.Label(content)
        self.comparison_scope.pack(anchor="w")
        self.different_comparisons_button = ttk.Button(
            content, command=self._toggle_different_comparisons)
        self.different_comparisons_button.pack(anchor="w", pady=(8, 8))
        self.treatment_options = ttk.Frame(content)
        self.condition_picker = ttk.Combobox(
            self.treatment_options, textvariable=self.condition,
            values=list(self.condition_names), state="readonly")
        self.condition_picker.pack(fill="x", pady=(3, 4))
        self.condition_picker.bind("<<ComboboxSelected>>", self._condition_changed)
        if not self.conditions:
            self.different_comparisons_button.state(["disabled"])
        self.names = list(dict.fromkeys(s for s in strains if s))
        self.refs = ttk.Frame(content)
        self.refs.pack(fill="x", pady=(3, 8))
        self.reference_vars = {}
        self.reference_checks = {}
        for name in self.names:
            var = tk.BooleanVar(value=False)
            check = ttk.Checkbutton(self.refs, text=name, variable=var,
                                    command=lambda: self._changed(field="references"))
            check.pack(anchor="w", fill="x")
            self.reference_vars[name] = var
            self.reference_checks[name] = check
        self.all_pairs = tk.BooleanVar(value=self.config["all_pairs"])
        ttk.Checkbutton(content, text="Compare every pair instead", variable=self.all_pairs,
                        command=lambda: self._changed(field="all_pairs")).pack(anchor="w", pady=4)
        self.note = ttk.Label(content, text="WT and EV comparisons start checked when present. Changes take effect with Apply.", wraplength=650)
        self.note.pack(anchor="w", pady=6)
        self._update_treatment_options()
        self._load()

        def bind_scrolling(widget):
            widget.bind("<MouseWheel>", self._scroll_content, add="+")
            widget.bind("<Button-4>", self._scroll_content, add="+")
            widget.bind("<Button-5>", self._scroll_content, add="+")
            for child in widget.winfo_children():
                bind_scrolling(child)
        bind_scrolling(content)
        self.canvas.bind("<MouseWheel>", self._scroll_content)
        def wrap_labels(event):
            def wrap(widget):
                if isinstance(widget, ttk.Label):
                    widget.configure(wraplength=max(120, event.width - 40))
                for child in widget.winfo_children():
                    wrap(child)
            wrap(content)
        content.bind("<Configure>", wrap_labels, add="+")

    def _scroll_content(self, event):
        if self.canvas.yview() != (0.0, 1.0):
            units = -1 if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0 else 1
            self.canvas.yview_scroll(units, "units")
        return "break"

    def _store(self, field=None):
        slot = self.slots.get(self.normalization.get())
        refs = [name for name, var in self.reference_vars.items() if var.get()]
        if field in (None, "normalization") and slot != analysis.normalization(self.config, self.current):
            self.config["normalization"][self.current] = slot
        reference_key = self.current if self.different_comparisons.get() else "*"
        if field in (None, "references") and set(refs) != set(analysis.references(self.config, reference_key)):
            self.config["references"][reference_key] = refs
        self.config["all_pairs"] = self.all_pairs.get()

    def _inherit(self, field):
        if self.current != "*":
            self.config[field].pop(self.current, None)
            self._load()

    def _load(self):
        slot = analysis.normalization(self.config, self.current)
        self.normalization.set(next((k for k, v in self.slots.items() if v == slot), "Not selected"))
        self.comparison_scope.configure(text=(
            f"Comparison references for {self.condition.get()}" if self.current != "*" else
            "Comparison references for all treatments"))
        refs = analysis.references(self.config, self.current)
        for name, var in self.reference_vars.items():
            var.set(name in refs)

    def _toggle_different_comparisons(self):
        self.different_comparisons.set(not self.different_comparisons.get())
        self._different_comparisons_changed()

    def _update_treatment_options(self):
        self.different_comparisons_button.configure(text=(
            "Use same comparisons for all treatments" if self.different_comparisons.get()
            else "Set comparisons by treatment..."))
        if self.different_comparisons.get():
            self.treatment_options.pack(fill="x", before=self.refs, pady=(0, 4))
        else:
            self.treatment_options.pack_forget()

    def _different_comparisons_changed(self):
        if not self.different_comparisons.get():
            for code in self.conditions:
                self.config["references"].pop(code, None)
        self.current = self.conditions[0] if self.different_comparisons.get() and self.conditions else "*"
        self.condition.set(next((label for label, code in self.condition_names.items()
                                 if code == self.current), ""))
        self._update_treatment_options()
        self._load()

    def _condition_changed(self, _event=None):
        # Merely visiting an inherited condition must not freeze an override.
        self.current = self.condition_names[self.condition.get()]
        self._load()

    def _changed(self, _event=None, field=None):
        self._store(field)

    def result(self):
        config = deepcopy(self.config)
        config = analysis.validate(config)
        analysis.check_panel(config, self.strains, self.conditions, self.exclusions)
        return config


def ask_analysis(parent, strains, conditions, config, exclusions=None, condition_labels=None):
    win = tk.Toplevel(parent)
    win.title("Normalization and comparison references")
    width = min(740, max(320, win.winfo_screenwidth() - 80))
    height = min(520, max(300, win.winfo_screenheight() - 120))
    win.geometry(f"{width}x{height}")
    win.minsize(min(420, width), min(300, height))
    win.transient(parent.winfo_toplevel())
    result = []
    bar = ttk.Frame(win, padding=8)
    bar.pack(side="bottom", fill="x")
    editor = AnalysisEditor(win, strains, conditions, config, exclusions, condition_labels)
    editor.pack(fill="both", expand=True)

    def apply():
        try:
            config = editor.result()
        except ValueError as exc:
            messagebox.showerror("Final analysis", str(exc), parent=win)
            return
        result.append(config)
        win.destroy()

    ttk.Button(bar, text="Apply and recalculate", command=apply).pack(side="right")
    ttk.Button(bar, text="Cancel", command=win.destroy).pack(side="right", padx=6)
    win.grab_set()
    win.wait_window()
    return result[0] if result else None
