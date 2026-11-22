# HILT Inspector — target specification

This is the first deliverable of [#89](https://gitlab.com/Tyler-Meador/HILT/-/issues/89).
It carries the legacy Floor Technician guide into the Python port, adjusted for the
signed decisions on [#88](https://gitlab.com/Tyler-Meador/HILT/-/issues/88),
[#106](https://gitlab.com/Tyler-Meador/HILT/-/issues/106), and
[#107](https://gitlab.com/Tyler-Meador/HILT/-/issues/107).
It specifies the terminal UI, which `hilt inspect` now opens. The
[Floor Technician guide](floor-technician.md) remains the station page for reading the
evidence by hand. The
[review rulings on !119](https://gitlab.com/Tyler-Meador/HILT/-/merge_requests/119#note_3796094775)
set the implementation order: Inspect first, Run as a separate increment behind its
own decision. Ty ruled on that increment on 2026-09-10: the Re-Run tab ships, the
Failures list is folded into Inspect as a key that hides passing rows, and the cut
between the two tabs is the one the legacy tool drew. Inspect is the archive of the
production run and executes nothing. Re-Run is the bench, where a node is executed
again with the code that ran in production and nothing it produces persists.

The internal timeline reader (`hilt.inspector_timeline.TimelineReader`) is implemented
as a foundation for the log pane. It asynchronously reads a selected timeline path,
validates each version-1 object against the packaged published schema, and returns
records and line-numbered diagnostics in physical order. Additive fields and recorded
scopes and pointers survive unchanged. A complete final JSON object is readable without
a trailing newline; an unfinished final object or UTF-8 sequence produces a truncation
diagnostic. Malformed complete records, duplicate keys, non-finite numbers, and
unsupported versions are explicit diagnostics, and later valid records remain readable.
Missing or unreadable logs produce a file-level unavailable diagnostic. These are reader
observations, never test verdicts. The timeline reader does not open artifact pointers
or resolve tree ownership. Its `read_reference(run_root, reference)` boundary opens
a recorded POSIX `log_path` only beneath the selected run directory, preserving the
complete relative path after a move. Empty, absolute, traversing, missing, non-file,
and symlink-escaping references produce unavailable diagnostics. The read retains the
recorded reference and the resolved source path; a reference that cannot resolve has
no source path. It never searches another directory for a matching basename. Timeline
and capture readers share one checked, cached loader for the packaged schemas; evidence
is read afresh on every call. The operator's selected run directory may itself use
symlinks or `..`; the reader resolves that directory before containing recorded links.

The internal capture reader (`hilt.inspector_artifacts.ArtifactReader`) now reads
absolute pointers beneath an opened run root. An explicit, established original root
enables relocation while preserving the full relative tail; without that mapping a
pointer outside the opened root is unavailable. The reader checks resolved containment,
refuses non-file targets, and never falls back to the original run or a basename match.
It validates the artifact envelope or root result against its packaged version-1 schema,
retaining additive fields and explicit null observations. Missing files, malformed JSON,
and unsupported versions are diagnostics that retain the recorded reference. JSON parsing
is shared with the timeline reader, so duplicate keys and non-finite values are refused
consistently. The original-root mapping remains an input to this internal component;
the saved-run loader establishes it from publication evidence.

The internal tree reader (`hilt.inspector_tree.TreeReader`) now opens a selected
result through that validated result boundary. It resolves the operator's selection
before deriving the run root, so a symlink such as `latest/result.json` or a path with
`..` opens the same saved evidence as its direct path. References inside saved records
still obey the capture reader's traversal and containment checks.
`read_selected()` retains the resolved run directory alongside the validated tree;
`read()` returns just that tree through the same selection boundary. Unresolvable
operator paths produce unavailable diagnostics in either form.
Its `InspectorTree` preserves every recorded node in declaration preorder, including
pending descendants, and keeps full name components rather than splitting display
breadcrumbs. Stable row indices keep duplicate names distinct. Inspect selection and
the Failures projection share the same rows: previous/next failure includes Fail and
Aborted, skips Pass and pending, and does not wrap. The model retains failure causes,
timeout fields, and additive evidence; declared readings absent from evidence are
exposed by name, while null and zero remain present observations.
Beside those rows the tree exposes its drawn lines (`InspectorTree.lines`): the same
nodes with each same-named root folded into its component, as
[the fold](#the-fold-one-row-per-component) specifies. A line carries the node the
cursor selects, the nodes drawn with it, and the depth, parent, and path of the drawing.
The cursor and the failure projection move over lines; indices stay those of the rows.

For an executed selection, the model identifies its own or nearest ancestor's recorded
timeline link. A pending selection has no owner and must render **not run**. An owner
identifies a recorded link, not proof the file is available: the model opens no timeline,
and its caller must still enforce run-root containment and report unavailable files.

The internal selection model (`hilt.inspector_selection.InspectorSelection`) joins
that tree to contained timeline loading for both Inspect and Failures. Its caller supplies
the validated tree and the opened directory containing that same result, then navigates
through the selection model. Selection clears the previous chronology before I/O, keeps
the claim and its recorded owner together, and reads only through `read_reference`.
A folded line has more than one recorded owner behind it; the model reads each and
publishes them as one chronology merged in time order, naming the further owners.
Every request has its own identity: a delayed load cannot overwrite a later selection,
even if the cursor returned to the same row. Pending rows render **not run**; executed
rows with no owner or an unreadable file render **log unavailable**. Complete records,
their scopes, partial reads, and decoding diagnostics remain intact. Reselection reads
evidence afresh. Cancellation and reader defects propagate to the caller and return only
the affected current load to idle; they never restore an earlier log or become verdicts.

The internal saved-run loader (`hilt.inspector_run.SavedRunReader`) now opens a
validated result, its contained root timeline, and a capture reader for that same run.
It uses `TreeReader.read_selected()` so selection errors and the resolved run boundary
have one owner. The tree's chronology ownership also governs this opening: a pending
root supplies no timeline or relocation origin, even if a damaged record gives it a link.
It establishes the original root only from one final root-scoped pointer with
`artifact_type="run-result"`, `artifact_name="result"`, and an absolute,
non-traversing path to `result.json`. The result's ancestor names must agree with its
tree root; full name components are compared without splitting breadcrumbs, and a
disagreement names the result defect instead of directing the technician to the log. A missing,
damaged, ambiguous, or misplaced publication leaves the original root explicitly
unavailable while preserving the saved tree and timeline diagnostics. Complete records
without a trailing newline remain readable. The loader never guesses from a basename
or timestamp, reads the original station, or edits saved pointers. Reopening reads the
evidence afresh. Without a mapping, captures already beneath the opened root remain
readable, but pointers outside it cannot relocate.

The internal timeline presentation model (`hilt.inspector_timeline_view.TimelineView`)
pages a completed selection's chronology without changing its recorded data. It opens
at the tail with filtering off; toggling errors returns to the first page. The filter
retains Python `error` and `critical` records (case-insensitively) and every reader
diagnostic. Custom levels have no inferred severity and remain in the full view.
Each page retains physical line numbers, scopes, timestamps, and additive fields.
Resizing changes the number of records per page and clamps the current page; an empty
projection has one empty page. The terminal supplies that size after reserving its
header and controls. This model counts records, not wrapped terminal lines.

Structured pointers receive one-based numbers across the complete loaded chronology,
before paging or filtering. Lookup keeps the same target even when its row is hidden;
message text never supplies a path. An empty recorded path remains numbered so the
capture reader can diagnose it. All numbers remain addressable, including those above
the `1`–`9` direct shortcuts. The returned record supplies the pointer fields to the
saved run's capture reader, including the distinct `run-result` decoding kind. A new
completed selection read gets a fresh presentation model; selection/load state remains
owned by `InspectorSelection`. The moved 70-DUT test exercises this composition without
executing suite code or changing saved bytes.

The internal artifact navigation model
(`hilt.inspector_artifact_navigation.ArtifactNavigation`) now groups the complete
timeline's numbered pointers by recorded type. Types follow first appearance; captures
within a type retain chronology order. Opening selects exactly the requested number,
including one above nine or hidden by a filter; an invalid number is rejected instead
of opening another capture. Single-view type navigation wraps and selects the new
type's first capture, while capture navigation clamps at either end.

Diff selection requires at least two captures of the same type. It starts with the
single-view capture on the left and the first other capture on the right. The bracket
actions cycle the left target and the arrow actions cycle the right target within that
type. When a move reaches the other target, the two sides swap; they never collapse
onto one capture. Leaving diff restores the original single-view selection. Targets
retain their timeline number, full pointer record, recorded reference, and decoding
kind for the existing contained capture reader. Empty paths remain selectable and
produce that reader's unavailable diagnostic. A moved 70-DUT test opens null and named
captures and the root result without suite execution or saved-byte changes.

The internal artifact modal model (`hilt.inspector_artifact_view.ArtifactModal`)
joins that selection to the saved run's capture reader and pages what it reads. A
capture is printed in its recorded key order, so two captures of one type line up; an
unavailable or undecodable capture shows the reader's diagnostic with the recorded
reference and is never replaced by another file. In diff mode the two captures are
aligned row by row and one page cursor moves both sides together, as the artifact
table above specifies; a singleton type stays single without a reload, and every
change of selection returns to the first page because a capture is read from its top.
A delayed read never replaces a newer selection, in the pattern of the selection model.
The shared 70-DUT recording in `tests/inspector_rack.py` proves the composition without
suite execution or saved-byte changes.

The main view (`hilt.inspector_app.InspectorApp`) is the first terminal screen,
built on Textual by Ty's ruling of 2026-09-09. It renders the tab row, the tree, and
the chronology pane from the models above and forwards the quick-reference keys, one
model call per key: Inspect and Re-Run share one cursor, the chronology loads on
cursor movement without Enter and names its owner when the node's records rolled up,
the owner's row carries the cyan arrow, arrows jump between Fail and Aborted rows
without wrapping, `Shift-↑`/`Shift-↓` (or `Alt-↑`/`Alt-↓`) step between siblings, `h` hides passing
rows, `n`/`p` page, `f` filters to errors while a truncation marker stays in place, and
`l` gives the log the whole body and back. It draws the tree's lines, not its rows, so a
component and its same-named root are one row. Tree depth is drawn with the legacy glyphs
(`├─`, `└─`, `│`), computed over the rows on screen so a hidden branch still closes on
its last visible child. The split between the two panes and the ancestors pinned above
a scrolled tree are each one function of the terminal's height and the selection, so
the screen holds no scroll position that could disagree with the cursor. The plain text
of every redraw is kept on the app so the
self-tests drive it against the recorded runs in `tests/inspector_rack.py` and a
screenshot can be read back against this guide.

The re-run session (`hilt.inspector_rerun.RerunSession`) is the adapter behind the
Re-Run tab. It receives a suite factory from the command line, never a suite derived
from evidence; loads it on the tab's first opening, off the event loop; refuses a
suite whose declared tree differs from the saved one, naming the first row that
differs; executes the pressed node through `TestComponent.run(subtree=...)`, the sealed
lifecycle with every leaf outside that subtree left pending; reads the result back
through the same saved-run reader the archive uses, so the tab shows a re-run's
states and chronology with the same code paths; and keeps that evidence under a
scratch directory emptied per node on each re-run and removed when the Inspector
closes. Interrupting a live re-run raises the operator signal, so the framework's own
termination contract settles the node Aborted with bounded cleanup.

The artifact modal screen (`hilt.inspector_app.ArtifactScreen`) opens from a digit
in the log pane over the capture carrying that number and owns the artifact keys until
`b` closes it: `[` and `]` move between types or the left side of a comparison, the
arrows between captures of the type or the right side, `n` and `p` page both sides
together, `d` enters and leaves the comparison. It renders the modal model only: one
capture as its lines, a comparison as two aligned columns with differing rows
highlighted, a diagnostic as the reader's words with the recorded reference.

The entry point is `hilt inspect [PATH]` (`hilt.cli`): PATH is a run directory or
its `result.json`; without PATH the most recently modified `result.json` under the
artifact root opens (`HILT_ARTIFACT_ROOT`, default `artifacts/`; `--artifact-root DIR`
overrides), the legacy discovery rule carried over. Nothing under the root, or a
selection that is not a saved version-1 run, is refused with the reader's diagnostic on
stderr and exit status 3. Opening runs no suite code. `--suite TARGET` names the suite
the Re-Run tab executes, in the form `hilt run` accepts, and is loaded at the prompt
so a wrong target is exit 3 there; without it the tab uses the `suite_target` the run
recorded, loaded only when the tab is first opened.

The Inspector serves the person at the station when a test stops the line. Its test of
success is concrete: find what happened, categorize it, and begin the station's recovery
protocol within sixty seconds, without opening suite source.

## What the Inspector reads

Inspect mode replays saved evidence. Moving the cursor, paging a log, opening an artifact,
or comparing two captures never touches the DUT and never executes suite code.

The input is a run directory containing `result.json`, its per-node `timeline.jsonl`
files, and the point-in-time artifacts referenced by those timelines. The structural tree
comes entirely from `summary_tree`; the temporal view comes entirely from the linked
JSON Lines records. A saved 70-DUT rack must be inspectable without live `TestComponent`
objects, source discovery, an SSH connection, or the test station that produced it.

The [Inspector JSON contract](inspector-json.md) owns the field definitions and version
policy. This page owns their presentation. It introduces no new result fields or
verdict rules. The result and each timeline record have independent `format_version`
values; select and validate each decoder separately, accept additive fields, and report
unsupported versions explicitly rather than guessing their meaning.

## Opening a run

`hilt inspect PATH` opens a selected `result.json`, or the `result.json` inside a
selected run directory, and treats its containing directory as the run root. `hilt
inspect` alone opens the most recently modified `result.json` under the artifact root
(`HILT_ARTIFACT_ROOT`, default `artifacts/`; `--artifact-root DIR` overrides), the
legacy convenience carried over by Ty's ruling of 2026-09-09 on #89. The legacy
`npm run inspect` command and `summary-tree.artifact` input are not Python interfaces.

If no result is available, explain that there is no saved tree to inspect. Do not start
a test to manufacture one: `hilt inspect` with nothing under the root exits 3 saying
so, and a selection that is not a saved version-1 run exits 3 with the reader's
diagnostic, never another file opened in its place. Absence of `result.json` is not a
verdict: an invocation may have failed before a run, or a process may have ended before
result publication. Preserve any surviving timeline evidence for diagnosis.

## The screen layout

The terminal has a tab bar, the tree above, and the highlighted node's timeline below.
This illustration shows the arrangement, not a captured run. The tree is scrolled: its
first rows have left the pane, and the ancestors of the first row still in view are
pinned above the rule.

```text
  [ Inspect ]   Re-Run
  ---------------------------------------------------------
    rack  Aborted
    └─ servers  Aborted  ←
    ─────────────────────────────────────────────────────
       ├─ server-07  Pass
       │  └─ thermal check  Pass
       ├─ server-08  Fail
  >    │  └─ thermal check  Fail  not taken: fan_rpm
       ├─ server-09  Aborted
       │  └─ thermal check  Aborted
       ├─ server-10  Pass
       │  └─ thermal check  Pass
       ├─ server-11  Pass
  ---------------------------------------------------------
  log: rack/servers  (rolled up: thermal check has no own log)
  page 9/14  errors only: off  captures: 1  (1-9 opens a capture)
      14:02:11Z info     starting thermal ramp
  [1] 14:02:45Z info     survey captured
      14:03:02Z error    temperature 87 C exceeds 75 C
  Tab Inspect / Re-Run  q Quit  ← → Failures  h Hide passes  l Log full  ⏎ Re-run node  n p Page  f Errors only  shift+↑ shift+↓ Sibling
```

The footer lists the keys on one line, and a terminal too narrow for all of them cuts
the list at its right edge. Quit, the tab key, and the two keys that change what the
screen shows, `h` and `l`, come first and are whole at 100 columns. The
[quick reference](#key-bindings-quick-reference) is complete at any width.

**The split.** The body is everything between the tab bar and the footer. The log has a
quarter of it, rounded down, and never fewer than six lines: its two header lines and
four records. The tree has the rest. On a terminal too short for both, the tree gives up
its rows first, because the log is the authoritative record; when not one tree row would
fit, the log has the whole body.

**The log full screen.** `l` puts the tree away and gives the log the whole body; `l`
again brings the tree back. The tab bar and the footer stay. Nothing else changes: the
highlighted node, the errors-only filter, and the reader's place in the log are kept.
The record that headed the page before the toggle is on the page after it, and toggling
twice returns the same page. Every key works in both layouts. The arrows still move the
selection while the tree is out of sight, and the log's first line names the node.

**Pinned ancestors.** When the tree is scrolled, the ancestors of the first row in view
stay pinned at the top of the pane: one line per level, root first, each drawn exactly as
its own row, with its indentation and glyphs, its name, its state, and the `←` owner mark
when it owns the timeline. A dim rule separates the pinned lines from the rows that
scroll beneath them. A header leaves when the last row of its subtree has scrolled past,
and the next one takes its place. No row is drawn under the pinned lines, so the
highlighted row is never hidden by them, whichever key moved it there. The pinned lines
take at most a third of the tree pane. A deeper chain drops its outermost levels and
keeps the nearest, because those are the context the highlighted row needs and the log's
first line already names the full path. A tree pane under four rows pins nothing. Pinning
holds with passing rows hidden, where a hidden ancestor is stepped over, and on the
Re-Run tab, where a pinned line carries that tab's label. The ancestors are those of the
drawn tree: a component [folded](#the-fold-one-row-per-component) with its root is pinned
as the one row it is drawn on.

The log updates on cursor movement. Enter is never required to read it. A delayed load
for the previous selection must not overwrite the currently highlighted node's view.
Show the selected node and, when different, the ancestor that owns its timeline: the
pane names that owner, and the owner's row ends in the cyan `←` so the eye finds the
file the highlighted node's records went into.

Depth is drawn with the legacy glyphs: `├─` for a child with siblings below it, `└─`
for the last child, `│` continuing a branch that has more children further down. They
are computed over the rows on screen, so when passing rows are hidden each remaining
branch still closes on its last visible child.

Tree order is declaration order, including in parallel compositions. A parent marked
Fail may summarize a failing child; keep the descendants visible so the technician can
reach the actual claim. Use the root-to-node array of names as the path, not a split of
the display breadcrumbs: names may themselves contain `/`.

### The fold: one row per component

The record holds two nodes for every component: the component itself (type `suite`,
which owns `configure` and `cleanup`) and the composition its `build_suite()` returned.
The root takes the component's name, so a tree drawn node for node prints nearly every
name twice and spends half its depth doing it:

```text
  ├─ sata  Pass
  │  └─ sata  Pass
  │     ├─ present  Pass
  │     └─ spins  Pass
```

The Inspector draws such a pair as one row (Ty, 2026-11-21, issue #165):

```text
  ├─ sata  Pass
  │  ├─ present  Pass
  │  └─ spins  Pass
```

This is a rule of the drawing only. `result.json` keeps both nodes at `format_version`
1, a run saved before the rule opens and folds the same way, and every row index and
Re-Run position still refers to the saved nodes. An author gets the pair by writing
the root without a name, `self.sequence([...])`
([idioms](idioms.md#composition-factories-selftest-selfsequence-selfparallel));
`self.sequence(self.name, [...])` records the same two nodes and folds like it.

**The rule.** A `suite` row is drawn together with its child when that child is its
only child, carries exactly the same name (compared as written, case included), and
recorded the same state. The rule applies again to that child, so a component whose
root is a same-named component folds with that component's root as well. Nothing else
folds:

- A root the author named for itself, `self.sequence("checks", [...])` under `wifi`,
  is a level they meant to show. It keeps its row.
- A pair whose states differ keeps both rows, so the row that failed is the row the
  technician sees. The engine settles a component and its root alike in every run it
  writes today: a `cleanup` failure is logged without changing the verdict, and a
  component that fails in `configure` records no root at all. The rule is for a record
  that says otherwise.

**What the one row shows.**

- **State and readings.** The component's state, which is its root's too, and every
  reading either node declared and did not take.
- **Log.** Highlighting the row loads both nodes' timelines as one chronology, merged
  in time order, so `configure`, the claims, and `cleanup` read top to bottom as they
  happened. The header says so: `log: rack/sata  (with its root's log, in time order)`.
  Each file keeps its own order in the merge. A damaged line stays behind the record
  it followed and is labelled with its file, because a line number alone no longer
  names one. A root that rolled into its component is one file, read once.
- **Owner arrow.** A claim under a folded root rolled into the root's timeline. The
  root has no row of its own, so the arrow sits on the folded row, and the pane shows
  the root's file alone: `log: rack/sata  (rolled up: present has no own log)`.
- **Paths.** Wherever the screen names a node it uses the drawn path,
  `rack/sata/present`, not the saved one, `rack/rack/sata/sata/present`.
- **Cursor.** Up, down, and the failure arrows move over drawn rows and never rest on
  a folded root. `h` hides or keeps a folded row as the one row it is.
- **Re-Run.** Enter on a folded row re-runs the component, which runs its root and
  everything beneath it; the root has no key of its own. The Re-Run tab keeps the rows
  the archive drew, so the tree does not change shape under the cursor after a re-run:
  the row shows the component's re-run state, and its log merges the same two nodes'
  re-run timelines.

## The tabs

### Inspect — the default

Display every declared node, with its recorded state and failure message. Color and
symbols help scanning, but the state must remain legible as text.

| State | Presentation and meaning |
|---|---|
| `Pass` | Green success mark. The framework accepted an explicit verdict and any required evidence. A string `"PASS"` is not a passing verdict. |
| `Fail` | Red failure mark. Read the failure object: this can be a modeled `TestFailure` or a software contract violation. |
| `Aborted` | Distinct, named state. No verdict was produced; the cause may be integration, operator interrupt, missing verdict, or an abort budget. |
| `null` | Dim Pending / unreached row. This declared step never ran; it is not a fourth terminal state. |

Left and right arrows move backward and forward through nodes needing attention,
skipping Pass and pending rows. Preserve the legacy implementation's inclusion of both
Fail and Aborted in that navigation, while keeping their labels and recovery meanings
distinct. At either end, leave the cursor where it is; do not wrap unexpectedly.

`Shift-↑` and `Shift-↓` move to the highlighted row's previous and next sibling: the
row before or after it under the same parent, stepping over everything nested between.
From one server to the next, without walking the first one's checks. The jump never
leaves the parent (Ty, 2026-11-22): a row at the same depth under the next parent is a
cousin and is not reached, so on a parent's first or last row, and on an only child, the
cursor stays where it is. The parent is the drawn one: a component folded with its root
is one parent. Hidden passes are stepped over. Both tabs have the key. `Alt-↑` and `Alt-↓` do
the same. Two modifiers reach one action because a terminal may keep one for itself:
Terminator moves between its own panes on Alt with an arrow and never passes the key
on. The footer names Shift, the pair that arrives there too.

`h` hides every Pass row and shows them again. This replaces the legacy Failures list
(Ty, 2026-09-10): the same recorded tree with the passing branches folded away, so a
failure buried inside a large rack is found without leaving the tree or losing its
place in it. Pending rows stay visible; they are not passes. If the cursor was on a
row that just disappeared, it lands on the nearest visible row above it. When every
node passed, the pane says so rather than showing nothing.

Show the selected leaf's `evidence` beside its claim and failure. For every name in
`evidence_declared` absent from `evidence`, show **not taken** by name. An absent reading
is neither zero nor a successful measurement. Do not present the reserved empty
`run_data["measurements"]` slot as an implemented measurement or SPC feature.

### Re-Run — the bench

The same tree, unhydrated: every row reads **not re-run** until the technician executes
it. Move to any node and press Enter to execute that node and everything beneath it
with the code that ran in production. Switching tabs or highlighting a node never runs
anything; Enter on the Inspect tab does nothing. The purpose is the floor's quick
check: the ping failed, the cable is reseated, the one claim is re-run with the
production code, and only then is the whole suite restarted through the station's
normal procedure.

**What executes is the sealed lifecycle, confined to one subtree.** `TestComponent.run`
takes `subtree=`, the child positions from the root to the pressed node. Every node on
the path to it runs its own configure and cleanup, because that is their lifecycle and
the only way the pressed node meets the hop its ancestors own; every leaf outside the
subtree is left pending, never executed. The framework continues to own configure and
cleanup. This is not the legacy `↑ctx` ancestor-configure replay, which stays
prohibited: no parent's configure is replayed outside the lifecycle to make a device
runnable, and the
[instance-local context idiom](idioms.md#immutable-self-convention) is untouched.

**The suite is supplied, never derived.** `hilt run` records the loader target it
used in `result.json` as `suite_target` (absolute file or module name, `::`, class),
and `hilt inspect` loads that suite the first time the tab is opened; `--suite
TARGET` names another, and is loaded at the prompt. Nothing is reconstructed from
`result.json`, artifact contents, or source found beside the run directory. Before any
node runs, the supplied suite's declared tree is compared with the saved one, name and
type in preorder; a difference is refused by path, and the pane says which suite to
name instead. A run that recorded no target, one started from a developer's own
harness, leaves the tab unavailable with that reason until `--suite` is given.

**Nothing the bench produces persists.** Each re-run writes its evidence under a
scratch directory, in a subdirectory per pressed node. Re-running the same node
empties that subdirectory first, so the ninth re-run leaves nothing of the eighth. The
whole scratch directory is removed when the Inspector closes. After a re-run the
pressed node and its descendants that ran show their fresh states; rows the re-run
never reached keep whatever an earlier re-run left, or **not re-run**. The log pane
shows the highlighted node's re-run chronology, read by the same reader as the archive,
with the same owner arrow; a node no re-run reached says so instead of borrowing the
archive's log.

**A node above the pressed one reads partly re-run, never a verdict.** Its lifecycle
ran and its chronology is worth reading, but the state it rolled up covers only the
child the bench was pointed at: every sibling was left pending and contributed nothing
to it. Drawn as a verdict, that rollup turns a group whose other child failed in
production into a Pass and carries the Pass to the root, which is the one reading the
floor must never be given (issue #141). The bench answers for the branch it ran; the
verdict on the whole node belongs to the archive on the Inspect tab, or to a re-run of
that node, which runs every claim beneath it and so reads as a verdict again.

The run's recorded environment seeds the re-run, so the station's variables travel with
the record; the live environment overrides it, so a technician can change one
deliberately before pressing Enter.

**A live re-run is live hardware.** Tab is held while a node runs, as in the legacy UI,
and Enter starts nothing else. `q` during a re-run is the operator interrupt: it takes
the framework's
[termination contract](contracts.md#in-flight-termination-contract), the node settles
Aborted with bounded cleanup, and only then does the Inspector exit. A second `q`
during that cleanup exits immediately, as a second signal does at the station. The
legacy promise that Ctrl-C always exits immediately with nothing at stake applies to
the archive, not to the bench.

## The main workflow: diagnosing a failure

1. **Find the node needing attention.** Use right/left to move forward/backward without
   scrolling through passed leaves. Read the recorded state before choosing a recovery.
2. **Read the failure message and comparison.** Show `fail_message`, and `expected` /
   `received` when present. Preserve bug banners, nested causes, hook names, and operator
   signals; a plain-language message alone does not establish hardware fault.
3. **Read the timeline already below the tree.** `n` advances a page, `p` returns a
   page, and `f` toggles the errors-only filter. `l` gives a long log the whole screen
   and returns to the tree at the same place. Log severity does not replace the verdict.
4. **Open a referenced artifact.** Press its displayed digit, `1`–`9`. Survey captures
   can show whether the device's reported health agrees with what actually happened.
5. **Choose the recovery protocol.** Read the failure's cause and the available evidence.
   The human makes the final determination; uncertainty is a reason to escalate.

The five categories follow the current
[Failure Taxonomy Contract](contracts.md#failure-taxonomy-contract), which supersedes
the legacy guide's separate, automatically assigned infrastructure category:

| Category | Evidence to read | Recovery direction |
|---|---|---|
| DUT failed a check | Modeled `TestFailure`, comparisons, and captured readings. A configure failure means the device did not reach a testable state; hardware versus infrastructure remains a human determination. | Follow the station protocol for the established cause. |
| Hardware lying | Survey evidence contradicts a claimed healthy state or the apparent verdict. | Preserve the discrepancy and seek deeper inspection, even after Pass. |
| Test method bug | Contract-violation banner, `bug`, or `MissingVerdict`. The latter is Aborted; thrown bugs are Fail. A framework-contract banner identifies the framework maintainer as the owner. | Escalate the software defect; do not condemn the unit from this signal. |
| External integration failure | `ExternalIntegrationFailure`, its hook, and its cause. | Restore the harness or coordination service before retrying. |
| Operator interrupt | `OperatorInterrupt`, `operator: true`, and its signal. | The rack was stopped deliberately; resume only under the station's procedure. |

Time-budget evidence supplements this taxonomy. Display the winning `timeout.clock`,
declaration, budget, and elapsed time. `fail_after` produces Fail; `abort_after` produces
Aborted and may have no failure object at all. Do not relabel the latter as a hook
failure just because both are Aborted. The
[station recovery table](floor-technician.md#sixty-second-recovery-protocol) gives the
full reading procedure.

## The timeline and its owner

Resolve a node's non-null `log_path` relative to the selected run directory. A terminal
node with a null link rolled its records into the nearest ancestor with a link; indicate
that ancestor with the legacy cyan owner arrow. Keep each entry's `scope` visible so
sibling records in the shared timeline cannot be mistaken for this leaf's readings.
A [folded row](#the-fold-one-row-per-component) has two nodes behind it, each with its
own link; its chronology is both files merged in time order, and the header says so.

A pending node has no chronology anywhere. Show **not run**, not its parent's timeline
as though the pending node produced evidence. A missing file for a node that did run
is a separate, explicit **log unavailable** condition. Neither condition changes state.

Read JSON Lines incrementally, preserving file order and every complete valid record.
For an interrupted final line, display the complete preceding records and a visible
truncation marker; do not fill in the unfinished object. Report malformed complete
records or unsupported versions as decoding errors rather than silently dropping them.
An errors-only filter must not hide these reader diagnostics.

## Artifacts — the evidence behind the verdict

Number structured artifact-pointer entries in the loaded timeline and show `[N]` on the
corresponding row. Digits `1`–`9` open the matching numbered capture in a modal. Take
the link from the pointer fields, not from a path-looking substring in `message`.
Retain numbering when paging or filtering so a digit never silently changes its target.

The modal groups captures by `artifact_type` and labels each with its name and timestamp.
Show the persisted content, including an explicit null observation; nothing in the
viewer recomputes a verdict. The root `run-result` pointer opens `result.json`, whose
shape differs from a point-in-time artifact envelope. The capture reader checks the
envelope's own version and published schema as required by
the [JSON version policy](inspector-json.md#published-schemas).

An unavailable or unreadable capture produces a visible diagnostic containing the
reference, while the log and tree remain usable. The saved files are never edited.
Version 1 stores absolute `artifact_path` pointers, whereas tree log links are relative.
The reader resolves captures under the selected run root. For a moved run, try
re-anchoring the recorded path's run-root-relative tail beneath the opened run root
before declaring the capture unavailable. Preserve that full relative path: two nodes
may capture different evidence with the same filename. Never search for or substitute
a file merely because its basename matches.

Re-anchoring requires an established original run root. If the reader cannot establish
that mapping, or the mapped capture cannot be read under the opened root, show
**artifact unavailable** with the recorded reference; do not guess a root or silently
fall back to another run's evidence. The reader implementation must demonstrate that
mapping and its unavailable cases in tests before claiming moved-run support.

This is a reader rule. Saved pointers remain unchanged and absolute at version 1.
Changing their stored representation to relative paths is a record-contract change
under the [version policy](inspector-json.md#published-schemas), requiring Ty's decision
before an implementation MR.

### Reading and comparing artifacts

| Key | Single-artifact view | Diff view |
|---|---|---|
| `[` / `]` | Previous / next artifact type. | Change the left artifact within the current type. |
| `←` / `→` | Previous / next artifact within the current type. | Change the right artifact within the current type. |
| `n` / `p` | Next / previous content page. | Page both sides together. |
| `d` | Enter diff when the type has at least two artifacts. | Return to the single-artifact view. |
| `b` | Close the modal and return to the log. | Close the modal and return to the log. |

Diff shows two captures of the same type side by side, highlighting changed lines and
dimming unchanged lines. Preserve the legacy guide's intended swap when the two selected
indices collide, so a two-capture comparison can reverse sides. This is a display of
recorded differences, not an automatic diagnosis of hardware truth.

## Key bindings quick reference

| Context | Key | Action |
|---|---|---|
| Saved inspection, including a modal | `q` / `Ctrl-C` | Quit without changing evidence or touching hardware. |
| Live re-run | `q` / `Ctrl-C` | Operator interrupt: the node settles Aborted through bounded cleanup, then the Inspector exits. A second press during cleanup exits at once. |
| Main view | `Tab` | Switch Inspect / Re-Run. Held during a live re-run. |
| Either tree | `↑` / `↓` | Move through the rows on screen; hidden passes are stepped over. |
| Either tree | `←` / `→` | Previous / next Fail or Aborted node of the saved run; skip passed and pending rows. |
| Either tree | `Shift-↑` / `Shift-↓`, or `Alt-↑` / `Alt-↓` | Previous / next sibling: the row on screen before or after the highlighted one under the same parent; stay put when there is none. Use Shift where the terminal keeps Alt with an arrow for itself. |
| Inspect tree | `h` | Hide passing rows, or show them again. |
| Re-Run tree | `Enter` | Execute the highlighted node and everything beneath it; nothing else runs anything. |
| Log pane, no modal | `n` / `p` | Next / previous page. |
| Log pane, no modal | `f` | Toggle errors-only filtering. |
| Log pane, no modal | `1`–`9` | Open the artifact carrying that number. |
| Main view | `l` | Show the log full screen, or bring the tree back; the selection, the filter, and the place in the log are kept. |
| Artifact modal | `b`, `[`, `]`, `←`, `→`, `n`, `p`, `d` | Use the artifact controls above; the modal owns these keys until it closes. |

## Things that need careful reading

- **An ancestor owns the log.** The owner arrow explains roll-up; it says nothing about
  context inheritance. The legacy `↑ctx` ancestor-configure replay is prohibited.
- **One row, two records.** A component and its same-named root are one row on the
  screen and two nodes in `result.json`, each with its own timeline. The row's log is
  both, merged. Reading the files by hand, look in both directories.
- **An error appears in a passing test.** Survey errors are logged but do not decide
  the verdict. Read the state and the surrounding capture, not just the line's color.
- **Many nodes are Aborted.** An integration halt or operator interruption can affect
  the whole in-flight tree. A missing verdict or an abort budget is node-local. Read
  the cause instead of inferring a rack-wide hardware failure from repeated labels.
- **The record ends abruptly.** Retain the accepted chronology and mark the missing
  tail. Neither incomplete evidence nor missing declared readings may become Pass.

## Implementation acceptance

The screen implementation (`hilt.inspector_app`) and the entry point (`hilt
inspect`) follow this document. The view model is testable independently of the
terminal and driven by a recorded 70-DUT rack:

- Reconstruct tree order, peer states, pending descendants, failure causes, timeout
  evidence, and missing declared readings from version-1 records alone.
- Navigate failures and load the selected node's own or rolled-up timeline immediately,
  with correct owner and scope; never show stale content after a cursor change.
- Draw a component and its same-named, same-state only child as one row on both tabs
  and with passes hidden, with the merged chronology, the owner arrow, and the drawn
  path; keep both rows for a differently named root and for a pair whose states differ.
  The saved record is read, never rewritten.
- Open numbered pointers, group artifacts by type, and compare captures using the
  specified keys, including the two-artifact index collision.
- Preserve complete lines under truncation and distinguish unavailable evidence,
  malformed records, unsupported versions, and nodes that never ran.
- Keep the reader's place on a deep tree and a long log. The highlighted row is never
  drawn under the pinned ancestors, including after a jump upward or a filter change;
  the log has a quarter of the body and at least six lines; and showing the log full
  screen and returning lands on the same first visible record.
- Make Inspect usable from saved evidence without source or hardware, hidden passes
  included; it never waits for a suite. The saved-view command and discovery rule are
  the ones under "Opening a run".
- Verify moved-run resolution preserves the complete run-relative path and remains
  under the opened root. Exercise captures with identical basenames in different nodes,
  an unestablished root mapping, and unavailable files; preserve the recorded reference
  in every unavailable diagnostic and never substitute another capture.
- Hold Re-Run to its ruling. Its adapter accepts only an operator-supplied suite
  through the command line (`suite_target` or `--suite`), never reconstruction from
  evidence or neighboring source, and refuses a suite whose declared tree differs from
  the saved one. Execution is `TestComponent.run(subtree=...)`: ancestors' own lifecycle,
  the pressed subtree, every other leaf pending. Ancestor-configure replay stays
  prohibited. A node above the pressed one shows **partly re-run**, never the state it
  rolled up over the one child the bench ran. Scratch evidence is replaced per node and
  removed on close. A live
  re-run holds Tab, and quitting it goes through the operator-interrupt path to
  Aborted with bounded cleanup before the Inspector exits.

If the message, chronology, and captures do not establish a recovery category, keep the
run directory and escalate with the selected node's path. The Inspector must make that
uncertainty visible. Evidence earns a conclusion; the screen cannot supply one for it.

## Migration references

The behavioral reference is the read-only `hilt-test-legacy/docs/inspector.md`, with
navigation checked against `src/inspector/InspectTab.mts`, `RunTab.mts`,
`TreePanel.mts`, `DebugView.mts`, and `ArtifactModal.mts` in that repository. The
guide's workflow, split panes, Run tab, tree glyphs, owner arrow, failure navigation,
modal, and diff controls are retained; the Failures list became the `h` key.
Python record names, the five current categories, instance-local ctx, explicit evidence,
and interruption semantics follow this port's contracts. No TypeScript runtime or
language-server behavior is required to specify this saved-evidence viewer.
