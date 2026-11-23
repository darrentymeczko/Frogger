# Saved result runs (task 5; extension contract for tasks 6/7)

Use **Results → Save Results**, **Save Results As…**, or **Open Results…** in
standalone review or a workbench review tab. The workbench File menu also opens
results directly from Home. No Jobs history is needed. Ctrl+S saves, Ctrl+Shift+S
forks to a new folder, Ctrl+O opens a result folder. Save As asks for a **new
folder name**, refuses existing/nested folders, and switches the current tab to
the new run. Save writes the current folder. Each saved run has a UUID, visible
in its title, independent of the experiment name. `scientific_label` separately
pins the original experiment identity, so saving never changes measurement row
labels or treatment identity. A saved run is protected from
experiment pipeline overwrite; the default next run uses a new numbered folder.

Existing folders containing `timecourse_candidates.csv` still open without
migration or writes. An explicit save adds `run.json` version 1, preserving the
existing `best/`, `figures/`, CSVs, `chosen/`, `experiment.json`, and `review.json`.
Save As copies ordinary files; it creates no hard links, symlinks or mutable
references to another run. It stages the copy beside the destination, then
renames it after saving successfully. An unsuccessful copy leaves the original
controller/folder untouched. Folders containing filesystem links are refused.

`experiment.json` owns the run's pipeline configuration, resolved photo
assignments, embedded layout, experiment snapshot, and data-review flags.
Template paths/IDs are historical provenance; the embedded snapshot wins.
The data-review `file` link is cleared on explicit save; subsequent experiment,
template, and data-review edits cannot alter this run. Review decisions, final
normalization and comparison references, inclusion, statistics, ROI method
overrides, and presentation live in the run's own `review.json`.
When a legacy external layout was already lost, its path is moved to
`template.source_path` provenance and the manifest reports a snapshot warning.
The run cannot silently adopt a different template later created at that path.

`measurements/*.json` version 1 owns **every candidate's** baseline measurement,
including unchosen dilutions/low-growth/failed rows, unavailable numeric values,
background samples, plate geometry, ROI boxes and boundaries, method settings,
source hashes, and scientific preprocessing provenance. No pickle is used.
Special float tags preserve nonfinite numeric values without nonstandard JSON;
derived CSVs keep empty values. Archives precede manual review edits. Their
identity includes candidate, strain panel, dilution layout, and measurement
method/settings. Final normalization/comparison edits reuse baseline raw data;
ROI method edits create additional archives and invalidate rebuilt frames.
`review_measurements/manifest.json` identifies derived CSVs belonging to the
saved review; unlisted files are historical. Presentation changes never
invalidate/recalculate measurements. Existing pipeline outputs are retained.

First save can take minutes while it materializes unchosen candidate measurements
from the pipeline/cache. They are measured in the background, two at a time, with
progress in the status line; the window stays usable and the save follows. Missing inputs do not destroy existing results: the
manifest records `measurement_archive.unavailable` with candidate/reason, and
the GUI reports incomplete preservation. It cannot reconstruct raw data or
layout geometry that were already lost before saving. Owned raw archives allow
numeric review/recalculation without photographs; photo-based exports/new ROI
measurements still require verified originals.

## Original source identity and relinking

`run.json.sources[]` records stable `id`, relative path, original absolute path,
byte size and SHA256. The original path is provenance; the active root is
`review.capture_root`. Known hashes are never replaced by a relink. Open reports
missing/changed originals without guessing another folder or using a rendered
sheet. **Relink Original Images…** / Locate photos accepts a new root only if
every recorded relative path exists and all known hashes match. Reorganized
roots must preserve relative paths. Traversal outside the root is rejected.
Relinking changes only this review, and persists on Save. An original missing
at first save and absent from historical measurement provenance has an explicit
unverified identity; an explicit relink may
establish its initial hash, stored in review extensions and the next manifest.
It cannot prove historical identity for such a source. There is no implicit
same-name search and no fallback to `figures/`, `best/` or `chosen/` exports.
When historical CSV measurements already record source hashes, first save
retains those identities and reports changed originals rather than relabeling
old measurements with a replacement image's hash. A folder with no surviving
scientific configuration or original assignments remains browsable; relinking
cannot infer missing scientific identity from display artifacts.

## Presentation and crop provenance v1

The task-6/7 editors share `review.presentation` (version 1). They must deep-copy
and merge existing dictionaries, preserving unknown keys at **every depth**,
including records they do not understand. `Review` serialization/undo/Save As
preserves the entire extension object and unknown top-level review fields.
Future presentation versions are retained opaquely, never downgraded. Consumers
must decline unsupported versions when interpreting edits; browsing/saving is
still possible. Unknown run **schema** versions are rejected explicitly.

```json
{
  "version": 1,
  "sheet": {"view": "fit", "rotate": false, "brightness": 1.0, "contrast": 1.0},
  "figure": {
    "title": "", "axis_labels": {}, "strain_labels": {}, "fonts": {},
    "colors": {}, "dimensions": {}, "spacing": {}, "legend": {},
    "annotations": [], "order": []
  },
  "montage": {
    "match_graph_order": false, "order": ["crop-uuid"],
    "crops": [{
      "version": 1, "id": "crop-uuid",
      "source_id": "source:<stable source UUID from this run's manifest>",
      "source_sha256": "<original bytes digest>",
      "candidate_key": ["condition", "24 Hours", "p1.png", "p2.png", "least"],
      "condition": "condition", "timepoint_hours": 24, "plate": 1,
      "strain_group": "group-id", "strain_slot": 2, "strain": "EV",
      "replicate": "rep1", "dilution_index": 0, "dilution_label": "least",
      "original_region": {"kind": "rectangle", "xywh": [100, 200, 80, 90]},
      "original_cells": [{"row": 0, "column": 1, "replicate": "rep1",
                          "strain_slot": 2, "dilution_index": 0}],
      "display": {"rotation_degrees": 0, "mirror_x": false, "mirror_y": false,
                  "brightness": 1.0, "contrast": 1.0, "label": "EV"},
      "separated_region": true
    }]
  }
}
```

Coordinates are pixels as stored in the measurement original (EXIF orientation
is ignored, matching the scientific reader), **before** any user display transforms, using top-left origin, x right,
y down, continuous pixel edges (a pixel center is x+0.5,y+0.5). Rectangle bounds
are half-open. `original_cells` are zero-based photograph grid cells. Strip
crops enumerate every member cell with its dilution/strain/replicate identity;
they must not imply a single replicate when combining several. `source_id`
references this run's source manifest. Different files retain distinct source
IDs even when their bytes happen to be identical. Run UUID is deliberately not baked into
crops, so Save As preserves provenance without a live link to the parent run.
Optional `original_region.kind="polygon"` uses `xy` vertex pairs in the same
coordinate system. Reject out-of-bounds/nonfinite/ambiguous coordinates when
editing or exporting; never infer identity from a user-facing label or order.

`order` is an explicit list of stable crop IDs (figure order uses strain/group
IDs), never an instruction to mutate plate layout or measurement row order.
Graph-order matching is an explicit presentation operation. Mark separated or
rearranged regions visibly in exports and retain provenance in export metadata.
Display fonts/colors/labels/rotation/tone/crop bounds never enter measurement,
normalization or statistics. Statistical annotations must carry their recorded
test/comparison identity or be labeled manual; a preset must not invent p-values.
Tasks 6/7 implement editing/rendering against this persistence contract; task 5
does not claim to implement their figure or montage editors.
