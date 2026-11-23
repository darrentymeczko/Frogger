# Organizing photographs for experiment setup

Open this guide from **Data → Photo organization guide** or **Help → Photograph
and folder organization** in the experiment designer, standalone or in the
workbench. Workbench Home also offers it in Help. The viewer displays this
repository Markdown as selectable, scrollable text; the repository version has
clickable links.

The [original lab organization survey](data-organization-survey.md) is preserved
separately, including its listing counts, historical workflow and future-work
boundaries. Its patterns are survey observations, **not** the generated-photo
acceptance examples below. In particular, its historical control-on-Panel and
schema-version descriptions should not replace current application instructions.

## Start here: three ways to tell the program what a photo is

The **Data** tab identifies every photo's treatment, plate, elapsed hours and
strain group in three steps. Each later step wins over the earlier one, so you
only go as far down the list as your folder names force you to.

1. **Automatic.** Folder and file names are read as soon as you choose the
   folder. The trees below show what that reading gives. **Detect again**
   regenerates it.
2. **Keyword rules.** *Photos whose folder or file name contains `Gluc` are
   treatment Glucose.* One sentence fixes every matching photo at once, without
   knowing which folder level the word is at. The text can be part of a word,
   case does not matter, and you choose whether to look in folder names, the
   file name, or both. When two keywords for the same detail both match a
   photo, the longer one wins (`Glucose + H2O2` beats `Glucose`). Keywords can
   also supply plates (`IMG_9` → plate 1), hours (`day2` → 48) and strain
   groups (`setB` → B). **Keyword rules...** opens the list of rules; adding
   one shows how many photos the keyword catches before you confirm it.
3. **By hand.** Select photos in the list and press **Treatment**, **Plate**,
   **Hours** or **Strain group**. Hand assignments beat everything else and are
   sufficient on their own: a photo list assigned entirely by hand passes the
   run check.

**Photo order in each folder...** is a fourth source for benches that always
photograph in the same sequence; it sits between steps 1 and 2 in precedence.
The regex reading rules behind step 1 remain available under **Advanced**, but
nothing in this guide requires them.

## Recommendations versus requirements

**Recommended:** keep one experiment under one photo root, retain original
camera files, use consistent nesting and explicit `Plate N` folders, and check
the Data table before running. The trees below are recommendations, not a
mandatory naming scheme. The selected root itself is not part of a relative
path: select the folder above the tree's first child.

**Required for automatic resolution:** the detected naming rules, keyword
rules, photo order or saved manual assignments must between them supply the
fields that the mode needs.
Handpicked intake checks condition and plate; time-course intake also checks
elapsed hours. With named strain groups it additionally needs a group key.
Handpicked **running** uses the photographs explicitly selected on Plates, even
when their names cannot be resolved; metadata suggestions do not pick them for
you. Multi-group runs still require correct group membership. An `ok` Data row
means metadata resolved, not that the photo is scientifically suitable or the
whole experiment is ready to run.

The scanner accepts `.tif`, `.tiff`, `.png`, `.jpg`, `.jpeg`, `.bmp`, ignoring
extension case. It skips dot-prefixed entries and stops beyond eight directory
levels (the selected root counts as level one), reporting unsearched folders.
It does not extract archives. On OneDrive, download original pixels before
analysis; placeholder detection produces a warning recommending **Always keep
on this device**. Intake naming itself does not inspect pixel labels or EXIF
capture times.

For every example, first create/import the intended plate layout on Panel and
assign actual strains to sample slots. The checked examples use the shipped
**Martin Lab standard 8x6**: eight slots, plate 1 with biological replicates 1+2,
plate 2 with 3+4, three dilutions. This is a layout choice, never a filename
inference. Other layouts are supported; verify their plate IDs and replicate
placements yourself. In Analysis / comparisons choose normalization and
statistical references explicitly. In handpicked mode select each condition's
photo for every template plate and a scoring dilution on Plates. In time-course
mode ensure each intended sitting has every template plate.

Examples use declared treatment `Glucose` (stable internal code `GLU`), and,
where present, `Glycerol` (`GLY`). Use **Add detected conditions** if necessary,
then confirm the names on Conditions. Strain names, replicate placements,
references, treatment biology and physical plate identity are manual knowledge.
No folder name establishes that knowledge by itself.

## 1. Simple experiment: one treatment, handpicked photos

Select `simple` as the photo root. Declare the single treatment **Glucose**.

```text
simple/
  Plate 1/
    IMG_0001.JPG
  Plate 2/
    IMG_0002.JPG
```

**Actual result:** plate numbers 1 and 2 are inferred; condition is `GLU` from
the sole declared condition, because this inferred profile has no condition
rule. Group and hours are null, shot is 1, both rows are `ok`; intake issues: none.
The report says there is no condition folder and asks you to declare a single
condition. **Manually:** confirm Glucose really applies to both photos, assign
strains, select each photo on Plates and a dilution. Missing or mislabeled plate
numbers can be corrected with **Data → Plate**. Adding a second treatment
requires a condition rule or per-photo Condition assignments; the sole-condition
fallback is no longer available. A saved explicit cleared condition assignment
also blocks that fallback (see recovery).

## 2. Multiple treatments, same strain panel

Select `treatments`; declare **Glucose** and **Glycerol**.

```text
treatments/
  Glucose/
    Plate 1/IMG_0001.JPG
    Plate 2/IMG_0002.JPG
  Glycerol/
    Plate 1/IMG_0001.JPG
    Plate 2/IMG_0002.JPG
```

**Actual result:** built-in `Capture tree` layout in handpicked mode; rows are
`(GLU,1)`, `(GLU,2)`, `(GLY,1)`, `(GLY,2)`, all `ok`, hours/group null, shot 1;
intake issues: none. Reusing the camera filename in different directories is
fine: assignments use the full root-relative path. **Manually:** confirm each
treatment's meaning and choose photos/dilutions for both conditions. If a label
is wrong or two labels should describe one condition, select the affected rows
and use **Condition** to assign the intended existing name. Do not assume a
typo is merged automatically. Unknown or composite labels are reviewable text,
not independently interpreted medium/dose/temperature factors.

## 3. Multiple strain groups

Select `groups`. This example deliberately has no time folder, but uses
time-course mode to show what must be supplied manually.

```text
groups/
  Set1/
    Glucose/
      Plate 1/IMG_0001.JPG
      Plate 2/IMG_0002.JPG
  Set2/
    Glucose/
      Plate 1/IMG_0001.JPG
      Plate 2/IMG_0002.JPG
```

**Actual initial result:** set keys are the strings `1` and `2` (not `Set1` and
`Set2`), condition `GLU`, plates 1/2. Hours are null, all four rows unresolved,
missing `timepoint`. Exact intake error: `none of the 4 photos found could be
identified; check the naming profile or assign them by hand`.

**Manually:** add strain groups named exactly **1** and **2**, assign each its
own eight strains and final reference choices, and select all four rows →
**Hours → 24**, only if the lab record establishes 24 elapsed hours. After those
assignments the four `(group, condition, hours, plate)` tuples are
`(1,GLU,24,1)`, `(1,GLU,24,2)`, `(2,GLU,24,1)`, `(2,GLU,24,2)`; all `ok`, no
intake issues. The group names identify panels; they do not supply strain names.
If you would rather name the groups **Set1** and **Set2**, two keyword rules do
it: *folder names contain `Set1` → strain group Set1*, and likewise `Set2`.
If you choose different group keys, explicitly assign photos to those keys with
**Strain group** or a keyword rule. Never pool panels because their treatment/plate names match.
Undefined/missing groups block multi-group running. For handpicked running,
choose each group's photos separately; the group selector edits a group and
does not exclude other defined groups from the run.

## 4. Biological replicates across plates, and alternative shots

Select `replicates`, handpicked mode, treatment Glucose and the standard layout.

```text
replicates/
  Glucose/
    Plate 1/
      IMG_9.JPG
      IMG_10.JPG
    Plate 2/
      IMG_11.JPG
```

**Actual result:** all rows `GLU`, hours/group null, `ok`, no intake issues.
`IMG_10.JPG` is plate 1 shot **1**; `IMG_9.JPG` is plate 1 shot **2**;
`IMG_11.JPG` is plate 2 shot **1**. Alternative-shot numbering uses
case-insensitive **lexical relative-path order**, not natural numeric order.
The shot count groups identical `(set, condition, hours, plate)` keys; it is not
a biological replicate ID or a guarantee of capture chronology.

**Manually:** verify which image shows which physical plate and choose one
plate-1 shot on Plates. Biological replicate IDs 1+2 and 3+4 come from the
layout's cells. Two pictures of plate 1 do not replace plate 2 or add biological
replicates. If an image actually shows another panel or sitting, correct its
group/hours/plate rather than accepting it as an interchangeable re-shot.
Missing plates must be supplied or the layout/experiment corrected to what was
actually performed; time-course sittings lacking a required plate are skipped
(or blocked if none are complete).

## 5. Time course with explicit elapsed hours

Select `timecourse` in time-course mode, treatment Glucose.

```text
timecourse/
  24 Hours/
    Glucose/
      Plate 1/IMG_0001.JPG
      Plate 2/IMG_0002.JPG
  48 Hours/
    Glucose/
      Plate 1/IMG_0001.JPG
      Plate 2/IMG_0002.JPG
```

**Actual result:** built-in `Capture tree`; `GLU`, hours 24.0/48.0, plates 1/2
at each time, group null, shot 1, all `ok`, no intake issues. Hours labels retain
`24 Hours` / `48 Hours`. Both sittings have both required plates.
**Manually:** verify hours since spotting, consistent strain/layout mapping and
the physical plates across times. Date/session labels do not establish hours.
Correct inaccurate Hours values in Data; add a missing plate photo only when
its identity is known. Optional multi-step physical-plate labels and exclusions
are separate analysis settings, not filename-derived replicate identities.

## 6. Assigning plates and groups by capture order

Select `order`, time-course mode, Glucose, with groups **A** and **B** already
defined and their strains assigned. Each immediate folder contains exactly the
four photos of one sitting:

```text
order/
  24 Hours/
    Glucose/
      IMG_9.JPG
      IMG_10.JPG
      IMG_11.JPG
      IMG_12.JPG
  48 Hours/
    Glucose/
      IMG_9.JPG
      IMG_10.JPG
      IMG_11.JPG
      IMG_12.JPG
```

**Before order:** `GLU` and 24/48 hours are inferred; plate/group are null in
all eight rows, missing `plate,set`. Intake reports `unassigned_strain_group`
and `no_photos_resolved` for each empty group. Camera counters give no plate ID.

**Manually:** open **Photo order in each folder...**, put groups A then B and
plates 1 then 2, choose every plate of one group first, inspect the preview,
then **Use this order**. The actual mapping in **both** folders is:

| Natural filename position | Group-first order | Plate-first alternative |
| --- | --- | --- |
| IMG_9.JPG | A, plate 1 | A, plate 1 |
| IMG_10.JPG | A, plate 2 | B, plate 1 |
| IMG_11.JPG | B, plate 1 | A, plate 2 |
| IMG_12.JPG | B, plate 2 | B, plate 2 |

Both explicitly configured orders were checked: all eight rows `ok`, shot 1,
no intake issues. Use only the order established by your lab record. The
software orders **filenames**, not file timestamps, EXIF times or the order you
selected them. Numeric runs sort numerically; ties use the original filename.
This differs from alternative-shot numbering above. Preview the actual list;
do not rely on an Explorer view configured with another sort column.

**Hard requirement for positional assignment:** each immediate folder must
contain exactly as many non-ignored images as the saved order. Condition and
hours still come from rules or manual assignments. The order is the only
automatic source for plate (and group if its slots specify groups), even if
names contain other plate labels. Per-photo corrections override it. A
single-panel order can assign plate alone.

**Checked missing-position recovery:** ignoring `24 Hours/Glucose/IMG_12.JPG`
leaves three photos for four positions. The three become unresolved for
plate/group; the 48-hour folder remains valid. Each affected row says
`its folder has 3 photos; the photo order lists 4`. Exact warning:

> 1 folder does not hold the 4 photos the photo order lists, so their photos were not assigned by position: 24 Hours/Glucose (3). Ignore extra photos, or assign these photos by hand in Data

There is also an error that three photos have no defined group. Manual
assignments of the remaining photos to A1, A2, B1 restore their identities but
retain the order mismatch warning and add:
`Strain group 'B': 1 of 2 groups are missing a plate and will be skipped: B GLU
24h (no plate 2)`. Including the known B2 photo again restores all eight rows
and removes all intake issues. If a photo is truly missing, do not shift the
others to fit. If there is an extra blurred shot, ignore only that known extra
and recheck the entire preview; ignoring an arbitrary shot can create a
count-correct but biologically wrong order. **Stop using photo order** restores
the naming rules; retained manual corrections still win.

## 7. Recovering incomplete or ambiguous names

Select `recovery`, time-course mode, treatment Glucose:

```text
recovery/
  next day/
    Glucose/
      R1/IMG_9.JPG
      R2/IMG_10.JPG
```

**Actual initial result:** both rows have `GLU`; plate and hours null, status
unresolved, missing `plate,timepoint`. Exact error: `none of the 2 photos found
could be identified; check the naming profile or assign them by hand`.
Neither R1/R2 nor the camera counters identify template plates, and “next day”
does not establish elapsed time.

**Recovery with keyword rules:** if your lab record establishes that R1 shows
template plate 1, R2 shows plate 2, and both were photographed 48 hours after
spotting, add three rules: *folder names contain `R1` → plate 1*, *`R2` →
plate 2*, *`next day` → 48 hours*. They keep working for photos added to those
folders later. **Recovery by hand:** select each row → **Plate → 1/2**, then
both → **Hours → 48** and **Condition → Glucose**. Either way the resolved
tuples are `(GLU,48,1)` and `(GLU,48,2)`, group null, shot 1, both `ok`, no
intake issues, and the run check passes. This mapping is a supplied
assignment; R never acquires an automatic biological meaning.

For other ambiguity, inspect full paths and the **Where is each detail
written?** choices, then **Use these choices**. Advanced rules can read a
folder, filename stem, whole relative path or treatment suggestion. A whole-path
rule finding conflicting distinct values leaves that facet unset; fixed-folder
rules only examine their configured level. Review mixed depths and conflicting
labels rather than trusting a plausible first value. A wrong but complete
inference can still be `ok`: choose a better rule or correct selected rows.
If the lab record cannot establish identity, leave unresolved or Ignore it;
do not invent hours, strain names or replicate assignments.

Renaming a treatment on Conditions changes its **display label**, retaining its
stable code, linked picks and saved photo aliases. It does not rename source
folders. The acceptance also renames Glucose to **Renamed glucose** and confirms
that its photos remain assigned to `GLU`.

Deleting conditions clears their linked assignments explicitly, leaving source
photos intact. Such an explicit null is deliberately unresolved: it cannot
re-adopt the deleted condition or fall into a sole remaining condition. Detect
organization / Add detected conditions is not a recovery for that deletion.
Undo the deletion, or deliberately assign an existing/new Condition. A blank
correction returns to the naming rule by removing the manual override, so use
it only when that rule is intended. In the checked explicit-clear stage, with
a manual profile and sole Glucose, clearing the first photo leaves it unresolved
for `condition`, including after save/reopen. Intake reports
`unresolved_photos` and `no_complete_group`; there is no silent reassignment.

### When the run check says nothing identifies a detail

The run check's `profile_incomplete` error now means exactly what it says:
*no* source at all — not the detected names, not a keyword rule, not the photo
order, not a single hand assignment — says what a required detail is. Any one
of them clears it. Whether every photo actually received a value is reported
separately by the photo table and `unresolved_photos`; unresolved photos are
left out of the run, so check the **Needs attention** filter before running.

The regex pattern editor under **Advanced** still exists for layouts where a
detail genuinely has to be parsed out of a longer string (a bare `40` that
means hours, a `7.2K-OAc` filename). It is no longer a step on the way to
running an experiment whose names simply do not say enough; use keyword rules
or hand assignments for those. Do not rename original files just to bypass an
unknown biological identity.

## Paths, case and keeping assignments

Assignments/ignores/picks are keyed by complete root-relative paths (stored with
forward slashes). Moving an intact tree can be handled by selecting its new root;
renaming inner folders/files changes those keys and needs review/reassignment.
Do not rely on case-only renames: the resolver uses exact relative-path keys,
even though scanning sorts case-insensitively and name-pattern matching ignores
case. Condition label matching normalizes case and whitespace, but does not
infer spelling corrections or semantic equivalence. Strain group keys are exact
stored strings. Keep their chosen spelling consistent.

The examples use ordinary Windows-compatible names. No particular zero padding
or camera filename is required. Prefer explicit labels and preview sorting
instead of assuming a numeric suffix means plate or replicate. Preserve
originals and keep derived montages/results outside the intake root: the
scanner discovers supported image extensions, not biological suitability.

## Verification boundary

The seven numbered trees above were created as real generated JPEGs and passed
through current `scan_images`, `infer_profile`, `resolve`, `order_folders`,
`build_order` / `place_by_order` and `check_resolution`, with the stated
conditions, layout, groups and manual assignments. Exact inference reports,
all row fields, issues and mismatch dictionaries for every stage are recorded in
`scratch/validation/task12/examples.json`; reproduction is
`scratch/validation/task12/verify_examples.py`. These are metadata acceptance
checks, not validation of biological identity or quantification on the synthetic
solid-colour pixels. See the task-12 handoff for native link tests and regression
commands. Survey patterns in the linked historical document have separate
provenance and are not all runnable examples in this acceptance.
