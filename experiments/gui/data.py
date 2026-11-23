"""Review inferred photo metadata and describe the user's filing system."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox, ttk

from uikit import tokens
from uikit.theme import FONT_STRONG

from ..profiles import FACETS, FacetRule, NamingProfile, apply_keywords
from .conditions import _ask_text
from .order import order_summary
from .organization import example_text, reading_choices

_SOURCES = {
    "Not specified": None,
    "Folder / path level": "segment",
    "Filename": "stem",
    "Whole relative path": "path",
    "Treatment suggestions": "treatment",
}
_TRANSFORMS = {"condition": "code", "plate": "int", "timepoint": "hours", "set": "raw"}
_MULTIPLE_GROUPS = "Multiple strain groups - assign photos below"
_BY_ORDER = "By position in each folder (photo order)"


class DataPanel(ttk.Frame):
    def __init__(self, master, controller, on_change, choose_folder):
        super().__init__(master, padding=(10, 8))
        self.ctl, self.on_change = controller, on_change
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        ttk.Button(bar, text="Choose data folder...", command=choose_folder).pack(side="left")
        from uikit.data_organization import show_data_organization
        ttk.Button(bar, text="Photo organization guide",
                   command=lambda: show_data_organization(self)).pack(side="left", padx=6)
        self.summary = ttk.Label(self, text="", justify="left", foreground=tokens.TEXT_MUTED, wraplength=850)
        self.summary.pack(fill="x", pady=6)
        self.bind("<Configure>", lambda e: self.summary.configure(wraplength=max(240, e.width - 24)))

        # The three ways a photo gets its treatment / plate / hours / group, in
        # the order a user should try them. Each later one beats the earlier.
        how = self.how = ttk.LabelFrame(self, text="How each photo is identified", padding=8)
        how.pack(fill="x", pady=(0, 6))
        how.columnconfigure(1, weight=1)
        # Centred, not top-anchored: its text is centred on the button beside it.
        ttk.Label(how, text="1", font=FONT_STRONG).grid(row=0, column=0, sticky="w", padx=(0, 8))
        detected = ttk.Frame(how)
        detected.grid(row=0, column=1, sticky="ew")
        # Button first so the text, not the button, gives way in a narrow window.
        ttk.Button(detected, text="Detect again", command=self._detect).pack(side="right")
        ttk.Label(detected, text="Read from names automatically:").pack(side="left")
        # One line, however long: a wrapped summary pushed the photo list down.
        self.interpretation = ttk.Label(detected, foreground=tokens.TEXT_MUTED)
        self.interpretation.pack(side="left", padx=6)

        ttk.Label(how, text="2", font=FONT_STRONG).grid(row=1, column=0, sticky="nw", padx=(0, 8), pady=(8, 0))
        keywords = ttk.Frame(how)
        keywords.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        # Buttons first, so a narrow window shortens the text, not the buttons.
        # Photo order is for folders whose photos say nothing but are always
        # taken in the same order: set A plate 1, set A plate 2, set B plate 1, ...
        keywords.columnconfigure(0, weight=1)
        keyword_buttons = ttk.Frame(keywords)
        keyword_buttons.grid(row=0, column=1, rowspan=2, sticky="ne", padx=(6, 0))
        ttk.Button(keyword_buttons, text="Photo order in each folder...",
                   command=self._edit_order).pack(side="right")
        ttk.Button(keyword_buttons, text="Keyword rules...", command=self._edit_keywords).pack(side="right", padx=(0, 6))
        keyword_text = ttk.Label(keywords, justify="left", text=(
            "Keyword rules: a word in a name means a treatment, plate, hours or strain group."))
        keyword_text.grid(row=0, column=0, sticky="w")
        self.keyword_summary = ttk.Label(keywords, foreground=tokens.TEXT_MUTED)
        self.keyword_summary.grid(row=1, column=0, sticky="w")
        scale = max(1.0, self.winfo_fpixels("1i") / 96)
        ttk.Style().configure("Data.Treeview", rowheight=int(24 * scale))

        ttk.Label(how, text="3", font=FONT_STRONG).grid(row=2, column=0, sticky="nw", padx=(0, 8), pady=(8, 0))
        by_hand = ttk.Label(how, justify="left", text=(
            "Still wrong for some photos? Select them below and press Treatment, Plate, Hours or "
            "Strain group; hand assignments win over everything."))
        by_hand.grid(row=2, column=1, sticky="w", pady=(8, 0))
        how.bind("<Configure>", lambda ev: [
            keyword_text.configure(wraplength=max(240, ev.width - keyword_buttons.winfo_reqwidth() - int(60 * scale))),
            by_hand.configure(wraplength=max(300, ev.width - 60))])
        self.organization_button = ttk.Button(how, text="Advanced: choose where each detail is written...",
                                              command=self._toggle_organization)
        self.organization_button.grid(row=3, column=1, sticky="w", pady=(8, 0))
        self.organization = ttk.LabelFrame(self, text="Where is each detail written?", padding=8)
        ttk.Label(self.organization, text="Choose how to read each detail. Each choice includes where to look; check the examples below it.",
                  foreground=tokens.TEXT_MUTED).pack(anchor="w", pady=(0, 6))
        simple = ttk.Frame(self.organization)
        simple.pack(fill="x")
        simple.columnconfigure(1, weight=1)
        self.simple_rules = {}
        for row, facet in enumerate(("condition", "timepoint", "plate", "set")):
            title = {"condition": "Treatment", "timepoint": "Time since spotting",
                     "plate": "Plate number", "set": "Strain group"}[facet]
            ttk.Label(simple, text=title).grid(row=row * 2, column=0, sticky="w", padx=(0, 10))
            selected = tk.StringVar()
            choice_box = ttk.Combobox(simple, textvariable=selected, state="readonly", width=60)
            choice_box.grid(row=row * 2, column=1, sticky="ew")
            example = ttk.Label(simple, text="", foreground=tokens.TEXT_MUTED, wraplength=750)
            example.grid(row=row * 2 + 1, column=1, sticky="w", pady=(1, 7))
            self.simple_rules[facet] = {
                "selected": selected, "choices": {},
                "choice_box": choice_box, "example": example,
            }
            choice_box.bind("<<ComboboxSelected>>", lambda _e, f=facet: self._preview_choice(f))
        simple.bind("<Configure>", lambda e: [
            fields["example"].configure(wraplength=max(200, e.width - 170))
            for fields in self.simple_rules.values()])
        actions = ttk.Frame(self.organization)
        actions.pack(fill="x")
        ttk.Button(actions, text="Use these choices", command=self._apply_choices).pack(side="left")
        ttk.Label(actions, text="Updates the photo list below; keyword rules and individual corrections are kept.",
                  foreground=tokens.TEXT_MUTED).pack(side="left", padx=8)
        self.advanced_button = ttk.Button(self.organization, text="Show advanced pattern editor",
                                         command=self._toggle_advanced)
        self.advanced_button.pack(anchor="w", pady=(6, 0))

        settings = self.advanced = ttk.LabelFrame(self.organization, text="Advanced naming patterns", padding=6)
        # Hidden until explicitly requested. Ordinary setup needs no regexes.
        for col, title in enumerate(("Meaning", "Read from", "Level", "Pattern (capture the value in parentheses)")):
            ttk.Label(settings, text=title).grid(row=0, column=col, sticky="w", padx=4)
        settings.columnconfigure(3, weight=1)
        self.rules = {}
        for row, facet in enumerate(FACETS, 1):
            source, depth, pattern = tk.StringVar(), tk.StringVar(), tk.StringVar()
            self.rules[facet] = source, depth, pattern
            ttk.Label(settings, text={"set": "Strain panel / set", "timepoint": "Elapsed hours"}.get(facet, facet.title())).grid(row=row, column=0, sticky="w", padx=4)
            ttk.Combobox(settings, textvariable=source, values=list(_SOURCES), state="readonly", width=23).grid(row=row, column=1, sticky="ew", padx=4)
            ttk.Entry(settings, textvariable=depth, width=5).grid(row=row, column=2, padx=4)
            ttk.Entry(settings, textvariable=pattern).grid(row=row, column=3, sticky="ew", padx=4)
        ttk.Label(settings, text="Level: 0 = first folder below the root; -2 = photo's parent folder; -3 = its parent. Separate alternative patterns with ;;").grid(row=5, column=0, columnspan=4, sticky="w", pady=(5, 0))
        ttk.Button(settings, text="Apply organization", command=self._apply).grid(row=1, column=4, rowspan=2, padx=6)

        self.review_bar = ttk.Frame(self)
        self.review_bar.pack(fill="x", pady=6)
        self.filter = tk.StringVar(value="All photos")
        for label in ("All photos", "Needs attention", "Manually edited", "Ignored"):
            ttk.Radiobutton(self.review_bar, text=label, value=label, variable=self.filter,
                            command=self.refresh).pack(side="left", padx=(0, 8))
        ttk.Label(self.review_bar, text="Find:").pack(side="left")
        self.search = tk.StringVar()
        ttk.Entry(self.review_bar, textvariable=self.search, width=24).pack(side="left", fill="x", expand=True, padx=6)
        self.search.trace_add("write", lambda *_: self.refresh())
        controls = self.selection_controls = ttk.Frame(self)
        self.selection_label = ttk.Label(controls)
        self.selection_label.pack(side="left")
        for facet, title in (("condition", "Condition"), ("plate", "Plate"), ("timepoint", "Hours"), ("set", "Strain group")):
            ttk.Button(controls, text=title, command=lambda f=facet: self._correct(f)).pack(side="left", padx=2)
        ttk.Button(controls, text="Ignore", command=lambda: self._ignore(True)).pack(side="left", padx=2)
        ttk.Button(controls, text="Include", command=lambda: self._ignore(False)).pack(side="left", padx=2)
        ttk.Button(controls, text="Reset assignments", command=self._reset_assignments).pack(side="left", padx=6)
        from .groups import add_group
        ttk.Button(controls, text="Add strain group...",
                   command=lambda: add_group(self, self.ctl, self.on_change)).pack(side="right")
        self.group_summary = ttk.Label(self, text="", foreground=tokens.TEXT_MUTED, wraplength=850)
        self.group_summary.pack(fill="x", pady=(0, 4))
        self.bind("<Configure>", lambda e: self.group_summary.configure(
            wraplength=max(240, e.width - 24)), add="+")
        area = ttk.Frame(self)
        area.pack(fill="both", expand=True)
        area.rowconfigure(0, weight=1)
        area.columnconfigure(0, weight=1)
        columns = ("path", "label", "plate", "hours", "set", "status")
        self.table = ttk.Treeview(area, columns=columns, show="headings",
                                 selectmode="extended", style="Data.Treeview")
        for key, width, title in zip(columns, (330, 250, 55, 65, 55, 200),
                                     ("Relative photo path", "Treatment", "Plate", "Hours", "Strain group", "Review")):
            self.table.heading(key, text=title)
            self.table.column(key, width=width, minwidth=45, stretch=key in ("path", "label", "status"))
        scroll = ttk.Scrollbar(area, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(area, orient="horizontal", command=self.table.xview)
        self.table.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.details = ttk.Label(self, text="Select photos to edit them together. Double-click a value to correct it.",
                                 foreground=tokens.TEXT_MUTED, wraplength=1000, justify="left")
        self.details.pack(fill="x", pady=(6, 0))
        self.table.bind("<<TreeviewSelect>>", self._selection_changed)
        self.table.bind("<Double-1>", self._edit_cell)
        self._profile = None
        self._files_signature = None

    def _toggle_advanced(self):
        if self.advanced.winfo_manager():
            self.advanced.pack_forget()
            self.advanced_button.configure(text="Show advanced pattern editor")
        else:
            self.advanced.pack(fill="x", pady=(6, 0))
            self.advanced_button.configure(text="Hide advanced pattern editor")

    def _toggle_organization(self):
        if self.organization.winfo_manager():
            self.organization.pack_forget()
            self.organization_button.configure(text="Advanced: choose where each detail is written...")
        else:
            self.organization.pack(fill="x", before=self.review_bar)
            self.organization_button.configure(text="Hide advanced settings")

    # -- keyword rules ---------------------------------------------------------

    def _edit_keywords(self):
        from .keywords import edit_keywords
        # Start from the search box: a user who has just filtered the list to
        # "Gluc" is one click from making that the rule.
        edit_keywords(self, self.ctl, self.on_change, text=self.search.get().strip())

    def _refresh_keywords(self):
        rules = self.ctl.experiment.keywords
        if not rules:
            text = "none yet"
        else:
            caught = sum(any(k.matches(f.parts) for k in rules) for f in self.ctl.files)
            text = f"{len(rules)} rule{'s' if len(rules) != 1 else ''}, {caught} photo{'s' if caught != 1 else ''} matched"
        self.keyword_summary.configure(text=text)

    def _edit_cell(self, event):
        path = self.table.identify_row(event.y)
        facet = {"#2": "condition", "#3": "plate", "#4": "timepoint", "#5": "set"}.get(
            self.table.identify_column(event.x))
        if path and facet:
            self.table.selection_set(path)
            self._correct(facet)

    def _reset_assignments(self):
        paths = self.table.selection()
        if not paths:
            return
        with self.ctl.transaction("Reset photo assignments"):
            for facet in ("condition", "condition_label", "plate", "timepoint", "set"):
                self.ctl.override_many(paths, facet, "")
        self.on_change()

    def _selection_changed(self, _event=None):
        paths = self.table.selection()
        self.selection_label.configure(text=f"{len(paths)} selected:")
        if paths:
            self.selection_controls.pack(fill="x", pady=6, before=self.group_summary)
        else:
            self.selection_controls.pack_forget()
        if len(paths) == 1:
            row = next((r for r in self.ctl.resolution.rows if r.relpath == paths[0]), None)
            if row is not None:
                e = self.ctl.experiment
                sources = []
                for facet, title, value in (("condition", "Treatment", row.condition),
                                           ("plate", "Plate", row.plate),
                                           ("timepoint", "Hours", row.timepoint),
                                           ("set", "Strain group", row.set_key)):
                    rule = e.profile.rule_for(facet)
                    keyword = apply_keywords(tuple(row.relpath.split("/")), e.keywords).get(facet)
                    if value is None:
                        source = "unassigned"
                    elif facet in e.overrides.get(row.relpath, {}):
                        source = "manual assignment"
                    elif keyword is not None:
                        source = "keyword rule"
                    elif facet in e.order_facets():
                        source = "photo order"
                    elif rule:
                        source = {"stem": "filename", "segment": "folder/path rule",
                                  "path": "relative path", "treatment": "treatment detection"}.get(rule.source, rule.source)
                    else:
                        source = "experiment default"
                    sources.append(f"{title}: {source}")
                self.details.configure(text=row.relpath + "\n" + " · ".join(sources))
                return
        self.details.configure(text=("Edits apply to every selected photo. Reset assignments returns them to the automatic rules."
                                     if paths else "Select photos to edit them together. Double-click a value to correct it."))

    def _matches_filter(self, row):
        choice = self.filter.get()
        if choice == "Needs attention" and not (row.status == "unresolved" or (row.cloud_only and row.status != "ignored")):
            return False
        if choice == "Manually edited" and not self.ctl.experiment.overrides.get(row.relpath):
            return False
        if choice == "Ignored" and row.status != "ignored":
            return False
        query = self.search.get().strip().casefold()
        return not query or query in " ".join((row.relpath, row.condition_label or "", row.set_key or "",
                                               self.ctl.condition_name(row.condition) if row.condition else "")).casefold()

    def _refresh_choices(self):
        by_order = self.ctl.experiment.order_facets()
        for facet, fields in self.simple_rules.items():
            current = self.ctl.experiment.profile.rule_for(facet)
            if facet in by_order:
                # The photo order is the authority; keep the shadowed rule as
                # it is, so stopping the order brings the old reading back.
                fields["choices"] = {_BY_ORDER: current}
                fields["choice_box"].configure(values=[_BY_ORDER], state="disabled")
                fields["selected"].set(_BY_ORDER)
                self._preview_choice(facet)
                continue
            fields["choice_box"].configure(state="readonly")
            choices = reading_choices(self.ctl.files, facet, current,
                                      self.ctl.experiment.profile.aliases)
            if facet == "set":
                if self.ctl.experiment.strain_groups:
                    choices = {label: rule for label, rule in choices.items() if rule is not None}
                choices[_MULTIPLE_GROUPS] = None
            fields["choices"] = choices
            fields["choice_box"].configure(values=list(choices))
            fields["selected"].set(next(label for label, rule in choices.items() if rule == current))
            if facet == "set" and current is None and self.ctl.experiment.strain_groups:
                fields["selected"].set(_MULTIPLE_GROUPS)
            self._preview_choice(facet)

    def _choice_rule(self, facet):
        fields = self.simple_rules[facet]
        return fields["choices"][fields["selected"].get()]

    def _preview_choice(self, facet):
        fields = self.simple_rules[facet]
        if fields["selected"].get() == _BY_ORDER:
            fields["example"].configure(
                text=order_summary(self.ctl.experiment, self.ctl.resolution)
                if facet == "plate" else "Read with the plate number, from the photo order above.")
            return
        if facet == "set" and fields["selected"].get() == _MULTIPLE_GROUPS:
            fields["example"].configure(text="Define each group on Panel, then select photos below and click Strain group to assign them.")
            return
        text = example_text(self.ctl.files, self._choice_rule(facet),
                            self.ctl.experiment.profile.aliases)
        if facet == "plate" and self._choice_rule(facet) is None and self.ctl.files:
            text += " If every folder's photos are taken in the same order, use Photo order in each folder."
        fields["example"].configure(text=text)

    def _apply_choices(self):
        with self.ctl.transaction("Change data organization"):
            self._apply_choices_together()

    def _apply_choices_together(self):
        if (self.simple_rules["set"]["selected"].get() == _MULTIPLE_GROUPS
                and not self.ctl.experiment.strain_groups):
            from .groups import add_group
            # Creation refreshes the form; retain the other pending choices.
            pending = {f: v["selected"].get() for f, v in self.simple_rules.items()}
            if not add_group(self, self.ctl, self.on_change):
                return
            for f, label in pending.items():
                self.simple_rules[f]["selected"].set(label)
        rules = [self._choice_rule(facet) for facet in FACETS]
        profile = NamingProfile("Manual organization", tuple(r for r in rules if r),
                                dict(self.ctl.experiment.profile.aliases))
        self.ctl.set_profile(profile)
        self.on_change()

    def refresh(self):
        from ..profiles import profile_to_dict
        profile = self.ctl.experiment.profile
        signature = {**profile_to_dict(profile), "groups": list(self.ctl.experiment.strain_groups),
                     "order": list(self.ctl.experiment.photo_order),
                     "keywords": list(self.ctl.experiment.keywords)}
        self._refresh_keywords()
        files_signature = tuple(f.relpath for f in self.ctl.files)
        if signature != self._profile or files_signature != self._files_signature:
            self._refresh_choices()
            self._files_signature = files_signature
        elif self.ctl.experiment.photo_order:
            # Ignoring a photo can make a folder fit the order again.
            for facet in self.ctl.experiment.order_facets():
                self._preview_choice(facet)
        if signature != self._profile:
            self._profile = signature
            for facet, (source, depth, pattern) in self.rules.items():
                rule = profile.rule_for(facet)
                source.set(next((label for label, value in _SOURCES.items() if rule and value == rule.source), "Not specified"))
                depth.set(str(rule.depth if rule else -2))
                pattern.set(" ;; ".join(rule.patterns) if rule else r"^(.+)$")
        selected = self.table.selection()
        y = self.table.yview()[0]
        self.table.delete(*self.table.get_children())
        rows = self.ctl.resolution.rows if self.ctl.resolution else []
        # Listed as Explorer lists them (IMG_9 before IMG_10), which is also
        # the order a photo order counts positions in.
        from ..intake import _natural_key
        for row in sorted(rows, key=lambda r: (_natural_key(r.relpath), r.relpath)):
            if not self._matches_filter(row):
                continue
            status = ", ".join(f"needs {v}" for v in row.missing) if row.status == "unresolved" else row.status
            if status == "ok":
                status = "Assigned"
            if self.ctl.experiment.overrides.get(row.relpath):
                status += "; manually edited"
            if row.note:
                status += f" ({row.note})"
            if row.cloud_only:
                status += "; cloud only"
            name = (self.ctl.condition_name(row.condition)
                    if row.condition and self.ctl.experiment.has_condition(row.condition)
                    else row.condition_label)
            self.table.insert("", "end", iid=row.relpath, values=(row.relpath, name, row.plate or "", row.timepoint if row.timepoint is not None else "", row.set_key or "", status))
        self.table.selection_set([key for key in selected if self.table.exists(key)])
        self.table.yview_moveto(y)
        assigned = sum(r.status == "ok" for r in rows)
        attention = sum(r.status == "unresolved" or (r.cloud_only and r.status != "ignored") for r in rows)
        ignored = sum(r.status == "ignored" for r in rows)
        root = self.ctl.experiment.photo_root or "Choose a data folder to begin."
        self.summary.configure(text=(f"{root}\n{len(rows)} photos · {assigned} assigned · {attention} need attention"
                                     f" · {ignored} ignored · {len(self.table.get_children())} shown"
                                     + (f"\n{self.ctl.scan_error}" if self.ctl.scan_error else "")))
        descriptions = []
        for facet, title in (("condition", "Treatment"), ("timepoint", "Hours"), ("plate", "Plate"), ("set", "Group")):
            rule = profile.rule_for(facet)
            source = ("photo order" if facet in self.ctl.experiment.order_facets() else
                      {"stem": "filenames", "segment": "folders", "path": "the path", "treatment": "names"}.get(rule.source, "custom rule")
                      if rule else "not read")
            descriptions.append(f"{title}: {source}")
        self.interpretation.configure(text=" · ".join(descriptions) if rows else "choose a data folder first")
        groups = self.ctl.experiment.strain_groups
        self.group_summary.configure(text=("Strain groups: " + ", ".join(groups) if groups else ""))
        self._selection_changed()

    def _detect(self):
        self.ctl.detect_organization()
        self.on_change()

    def _edit_order(self):
        from .order import edit_photo_order
        edit_photo_order(self, self.ctl, self.on_change)

    def _adopt(self):
        self.ctl.adopt_found_conditions()
        self.on_change()

    def _apply(self):
        try:
            rules = []
            for facet, (source, depth, pattern) in self.rules.items():
                value = _SOURCES[source.get()]
                if value:
                    rules.append(FacetRule(facet, value, int(depth.get()) if value == "segment" else -1,
                                           tuple(p.strip() for p in pattern.get().split(";;") if p.strip()), _TRANSFORMS[facet]))
            profile = NamingProfile("Manual organization", tuple(rules), dict(self.ctl.experiment.profile.aliases))
            self.ctl.set_profile(profile)
            self.on_change()
        except ValueError as exc:
            messagebox.showerror("Data organization", str(exc), parent=self)

    def _correct(self, facet):
        paths = self.table.selection()
        if not paths:
            return
        prompt = {"condition": "Choose a treatment, or type a new treatment name:", "plate": "Template plate number (not the biological replicate number):", "timepoint": "Elapsed hours since spotting:", "set": "Choose a defined strain group (add groups on Panel first):"}[facet]
        options = {}
        if facet == "set":
            options["choices"] = tuple(self.ctl.experiment.strain_groups)
        if facet == "condition":
            names = [c.display() for c in self.ctl.experiment.conditions]
            names += [self.ctl.condition_name(r.condition) for r in self.ctl.found_condition_rows()]
            options["choices"] = tuple(dict.fromkeys(names))
        value = _ask_text(self, f"Correct {len(paths)} photo(s)", prompt + "\nLeave blank to return to the naming rule.", **options)
        if value is None:
            return
        value = value.strip()
        try:
            if facet == "set" and value and value not in self.ctl.experiment.strain_groups:
                raise ValueError("Add this strain group on Panel first, including its strains and control.")
            if facet == "condition":
                self.ctl.assign_treatment(paths, value)
                self.on_change()
                return
            if value and facet == "plate":
                value = int(value)
                if value < 1:
                    raise ValueError("Plate numbers start at 1.")
            if value != "" and facet == "timepoint":
                value = float(value)
                if not math.isfinite(value) or value < 0:
                    raise ValueError("Hours must be a finite, nonnegative number.")
            self.ctl.override_many(paths, facet, value)
            self.on_change()
        except ValueError as exc:
            messagebox.showerror("Correct photos", str(exc), parent=self)

    def _ignore(self, ignored):
        if self.table.selection():
            self.ctl.set_ignored(self.table.selection(), ignored)
            self.on_change()

    def _set(self):
        value = _ask_text(self, "Choose strain panel", "Use photos from this set only (blank = all):", initial=self.ctl.experiment.set_key or "")
        if value is not None:
            self.ctl.set_set_key(value.strip())
            self.on_change()
