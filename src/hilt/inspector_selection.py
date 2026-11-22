"""Join the saved tree cursor to contained chronology for Inspect and Failures.

This mutable view model owns selection-load state, not verdicts or terminal rendering.
Every load retains the selected claim and its recorded log owner together. Superseded
reads may finish, but cannot publish evidence beneath a different highlighted claim.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, cast

from hilt.inspector_timeline import TimelineDiagnostic, TimelineRead, TimelineReader, merge_reads
from hilt.inspector_tree import InspectorTree, TreeRow


@dataclass(frozen=True, slots=True)
class SelectionView:
    """One selection and its load state; reader diagnostics never change the saved state.

    ``idle`` means no load has completed (including cancellation or a raised reader
    defect). ``loading`` carries no previous chronology. ``ready`` may include decoding
    diagnostics beside records; ``unavailable`` identifies a missing owner or file and
    retains any partial read. ``not-run`` is reserved for a pending recorded node.

    ``merged`` names the further owners of a folded line: the selected component's
    same-named root keeps its own timeline, and the chronology interleaves it with the
    owner's in time order so one line of the tree shows one log.
    """

    selected: TreeRow
    owner: TreeRow | None
    status: Literal["idle", "loading", "ready", "unavailable", "not-run"]
    timeline: TimelineRead | None = None
    message: str | None = None
    merged: tuple[TreeRow, ...] = ()


class InspectorSelection:
    """Own a mutable selection over one validated tree and its opened run directory.

    The caller supplies a tree from TreeReader and the directory containing that same
    selected result. Navigate through this model rather than mutating the tree cursor
    separately; failure-list rows use the tree's same stable indices. Call select for
    the initial load and on cursor changes. All methods run on one event loop; concurrent
    loads are permitted, and only the latest request may update ``view``.

    ``layout`` is the tree whose drawn lines decide which nodes share a chronology. It
    is this tree unless the caller draws another one: the Re-Run tab draws the saved
    run's lines over a re-run's records, and passes the saved tree so the bench merges
    exactly the nodes the screen folded.
    """

    def __init__(
        self,
        tree: InspectorTree,
        run_root: Path,
        *,
        reader: TimelineReader | None = None,
        layout: InspectorTree | None = None,
    ) -> None:
        self._tree = tree
        self._layout = tree if layout is None else layout
        self._run_root = run_root
        self._reader = reader if reader is not None else TimelineReader()
        self._view = SelectionView(tree.selected, tree.log_owner, "idle")

    @property
    def view(self) -> SelectionView:
        """The current claim and chronology, replaced together without changing saved data."""
        return self._view

    async def select(self, index: int) -> None:
        """Select and load a row; invalid indices raise IndexError without changing the view.

        The old log is cleared before file I/O. Reselecting a row reads evidence afresh.
        Cancellation and reader defects propagate, returning the current load to idle;
        neither a late success nor a late error can disturb a subsequent selection.
        """
        self._tree.select(index)
        selected = self._tree.selected
        if selected.state is None:
            self._view = SelectionView(selected, None, "not-run")
            return
        owners = self._owners(selected)
        if not owners:
            self._view = SelectionView(
                selected, None, "unavailable", message="log unavailable: no recorded timeline owner"
            )
            return

        pending = SelectionView(selected, owners[0], "loading", merged=owners[1:])
        self._view = pending
        try:
            reads = [
                await self._reader.read_reference(
                    self._run_root, cast(str, owner.record["log_path"])
                )
                for owner in owners
            ]
            timeline = reads[0] if len(reads) == 1 else merge_reads(reads)
        except BaseException:
            # A cancelled worker may finish independently. Clear only this request's
            # loading state, and never turn a packaging defect into damaged evidence.
            if self._view is pending:
                self._view = replace(pending, status="idle")
            raise
        if self._view is not pending:
            return
        unavailable = any(
            isinstance(row, TimelineDiagnostic) and row.kind == "unavailable"
            for row in timeline.rows
        )
        self._view = replace(
            pending, status="unavailable" if unavailable else "ready", timeline=timeline
        )

    def _owners(self, selected: TreeRow) -> tuple[TreeRow, ...]:
        """The recorded timeline owners behind the selected line, the selected node's first.

        A line draws one node, or a component with its folded root. Each node that ran
        contributes its nearest recorded owner once; a folded node selected by its own
        index is read alone, as the record has it.
        """
        line = self._layout.line(selected.index)
        indices = [node.index for node in line.nodes] if line.index == selected.index else []
        owners: dict[int, TreeRow] = {}
        for node in [self._tree.rows[index] for index in indices] or [selected]:
            if node.log_owner_index is not None:
                owners.setdefault(node.log_owner_index, self._tree.rows[node.log_owner_index])
        return tuple(owners.values())

    async def move(self, offset: int) -> None:
        """Move by a signed line count, clamp at either end, and load that selection."""
        self._tree.move(offset)
        await self.select(self._tree.selected.index)

    async def next_failure(self) -> None:
        """Load the next Fail or Aborted row without wrapping or recalculating a verdict."""
        self._tree.next_failure()
        await self.select(self._tree.selected.index)

    async def previous_failure(self) -> None:
        """Load the previous Fail or Aborted row without wrapping or changing its meaning."""
        self._tree.previous_failure()
        await self.select(self._tree.selected.index)
