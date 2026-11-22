"""Project saved result trees into Inspector rows and selection, without running suites.

The result reader owns schema validation; this model owns declaration order, cursor
movement, and recorded log ownership. It neither opens timeline paths nor infers an
artifact root. A future terminal can use the same rows for Inspect and Failures without
recalculating verdicts or reconstructing live framework objects.
"""

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from hilt.inspector_artifacts import ArtifactDiagnostic, ArtifactReader
from hilt.run_data import JsonObject

type RecordedState = Literal["Pass", "Fail", "Aborted"] | None


@dataclass(frozen=True, slots=True)
class TreeRow:
    """One recorded node, identified by position even when sibling names are identical.

    ``ancestor_names`` retains complete name components, including embedded slashes.
    ``record`` retains every saved field, including additive evidence and nested causes;
    consumers must treat it as read-only. The owner index is absent for pending nodes.
    """

    index: int
    parent_index: int | None
    ancestor_names: tuple[str, ...]
    record: JsonObject
    log_owner_index: int | None

    @property
    def state(self) -> RecordedState:
        """Return the saved peer state, or None for an unreached node."""
        return cast(RecordedState, self.record["state"])

    @property
    def needs_attention(self) -> bool:
        """Keep Fail and Aborted reachable without collapsing their recovery meanings."""
        return self.state in ("Fail", "Aborted")

    @property
    def not_taken(self) -> tuple[str, ...]:
        """Name absent declared readings in declaration order; null and zero are present."""
        declared = cast(list[str], self.record["evidence_declared"] or [])
        evidence = cast(JsonObject, self.record["evidence"] or {})
        return tuple(key for key in declared if key not in evidence)


@dataclass(frozen=True, slots=True)
class TreeLine:
    """One drawn line of the tree: a recorded node and any same-named root folded into it.

    A component is recorded as two nodes, itself and the composition its ``build_suite``
    returned, and the two usually carry one name. The record keeps both; the screen
    draws them once (``docs/inspector.md``, The fold). ``row`` is the node the cursor
    selects and Re-Run executes. ``folded`` holds the nodes drawn with it, outermost
    first. ``depth``, ``parent_index`` and ``path`` describe the drawn tree and so step
    over folded nodes; ``parent_index`` is the ``index`` of the parent line.
    """

    row: TreeRow
    folded: tuple[TreeRow, ...]
    depth: int
    parent_index: int | None
    path: tuple[str, ...]

    @property
    def index(self) -> int:
        """The saved index of the node this line selects; folded nodes keep their own."""
        return self.row.index

    @property
    def name(self) -> str:
        """The one name every node on this line carries."""
        return self.path[-1]

    @property
    def state(self) -> RecordedState:
        """The state the line shows; folded nodes agree with it, or they are not folded."""
        return self.row.state

    @property
    def nodes(self) -> tuple[TreeRow, ...]:
        """Every recorded node this line draws, the selected one first."""
        return (self.row, *self.folded)

    @property
    def not_taken(self) -> tuple[str, ...]:
        """Readings any node on the line declared and did not take, each named once."""
        return readings_not_taken(self.nodes)


def readings_not_taken(nodes: Iterable[TreeRow]) -> tuple[str, ...]:
    """Name every declared reading the given nodes did not take, once, in node order."""
    return tuple(dict.fromkeys(key for node in nodes for key in node.not_taken))


class InspectorTree:
    """Own a mutable cursor over a validated, saved root result's fixed preorder rows.

    Construct through ``TreeReader`` at the file boundary. The supplied result must
    already satisfy the version-1 schema. Result and row records are retained intact
    and treated as read-only; cursor operations never edit them or touch the filesystem.

    ``rows`` is the record, one row per saved node. ``lines`` is what a screen draws:
    the same rows with each same-named root folded into its component. The cursor moves
    over lines, so it never rests on a node the screen does not draw.
    """

    def __init__(self, result: JsonObject) -> None:
        self.result = result
        self._rows = _rows(cast(JsonObject, result["summary_tree"]))
        self._lines = _lines(self._rows)
        self._line_positions = {
            node.index: position for position, line in enumerate(self._lines) for node in line.nodes
        }
        self._cursor = 0

    @property
    def rows(self) -> tuple[TreeRow, ...]:
        """All declared nodes in preorder, including pending and composite nodes."""
        return self._rows

    @property
    def lines(self) -> tuple[TreeLine, ...]:
        """The drawn tree in preorder: every node appears on exactly one line."""
        return self._lines

    def line(self, index: int) -> TreeLine:
        """The line that draws the node at ``index``; raise IndexError outside the tree."""
        if not 0 <= index < len(self._rows):
            raise IndexError(f"tree row {index} is outside the saved tree")
        return self._lines[self._line_positions[index]]

    @property
    def failures(self) -> tuple[TreeRow, ...]:
        """The drawn rows restricted to Fail and Aborted, preserving order and identity."""
        return tuple(line.row for line in self._lines if line.row.needs_attention)

    @property
    def selected(self) -> TreeRow:
        """The current row; a schema-valid result always supplies at least its root."""
        return self._rows[self._cursor]

    @property
    def log_owner(self) -> TreeRow | None:
        """The selected node's nearest recorded owner, never a pending node's ancestor.

        An owner names the recorded link, not proof that the file remains available.
        Run-root containment and availability must be checked before a caller opens it.
        """
        index = self.selected.log_owner_index
        return None if index is None else self._rows[index]

    def select(self, index: int) -> None:
        """Select a tree row by index; raise IndexError without moving on invalid input.

        Failure-list rows carry these same indices, so switching views requires no
        lookup by ambiguous display name or reconstruction of a second tree. A screen
        selects a line's ``index``; a folded node's own index is still accepted, for a
        caller that addresses the record instead of the drawing.
        """
        if not 0 <= index < len(self._rows):
            raise IndexError(f"tree row {index} is outside the saved tree")
        self._cursor = index

    def move(self, offset: int) -> None:
        """Move by a signed count of drawn lines, stopping at either end without wrapping."""
        position = self._line_positions[self._cursor] + offset
        self._cursor = self._lines[min(max(0, position), len(self._lines) - 1)].index

    def next_failure(self) -> None:
        """Select the next Fail or Aborted line; stay put when none follows."""
        self._jump(range(self._line_positions[self._cursor] + 1, len(self._lines)))

    def previous_failure(self) -> None:
        """Select the previous Fail or Aborted line; stay put when none precedes."""
        self._jump(range(self._line_positions[self._cursor] - 1, -1, -1))

    def _jump(self, candidates: range) -> None:
        for position in candidates:
            if self._lines[position].row.needs_attention:
                self._cursor = self._lines[position].index
                return


@dataclass(frozen=True, slots=True)
class SelectedTree:
    """A validated tree and the resolved directory containing its selected result.

    Keep these together so evidence readers use the boundary that validated the tree,
    rather than resolving the operator's selection again after a symlink might move.
    """

    run_root: Path
    tree: InspectorTree


class TreeReader:
    """Open a selected saved result using the existing versioned result-file boundary."""

    async def read(self, path: Path) -> InspectorTree | ArtifactDiagnostic:
        """Return a tree or an explicit result diagnostic; never manufacture a missing run.

        Call ``read_selected`` when opening timelines or captures as well: it retains
        the resolved run directory alongside the tree, through this same boundary.
        """
        selected = await self.read_selected(path)
        if isinstance(selected, ArtifactDiagnostic):
            return selected
        return selected.tree

    async def read_selected(self, path: Path) -> SelectedTree | ArtifactDiagnostic:
        """Validate one operator selection and retain the run boundary it establishes.

        The resolved selected file's parent is the opened run root. Operator selections
        may use symlinks or ``..``; references inside saved records remain constrained
        by ``ArtifactReader``, which owns schema validation and contained file I/O.
        Unresolvable selections return unavailable diagnostics with the original input;
        cancellation and unexpected reader defects propagate rather than becoming data.
        No suite source is discovered, imported, or executed by this reader.
        """
        reference = str(path)
        try:
            path = await asyncio.to_thread(path.resolve)
        except (OSError, ValueError) as error:
            return ArtifactDiagnostic(reference, "unavailable", f"result unavailable: {error}")
        content = await ArtifactReader(path.parent).read(str(path), kind="result")
        if isinstance(content, ArtifactDiagnostic):
            return content
        return SelectedTree(path.parent, InspectorTree(content.data))


def _rows(root: JsonObject) -> tuple[TreeRow, ...]:
    """Flatten the validated tree, carrying its actual ancestors rather than breadcrumbs."""
    rows: list[TreeRow] = []
    frontier: list[tuple[JsonObject, int | None, tuple[str, ...], int | None]] = [
        (root, None, (), None)
    ]
    while frontier:
        record, parent, ancestors, owner = frontier.pop()
        index = len(rows)
        names = (*ancestors, cast(str, record["name"]))
        if record["state"] is None:
            # A pending row produced no chronology, even if a damaged writer gave
            # it a link. Borrowing an ancestor's log would imply the step executed.
            owner = None
        elif record["log_path"] is not None:
            owner = index
        rows.append(TreeRow(index, parent, names, record, owner))
        children = cast(list[JsonObject], record["children"])
        frontier.extend((child, index, names, owner) for child in reversed(children))
    return tuple(rows)


def _lines(rows: tuple[TreeRow, ...]) -> tuple[TreeLine, ...]:
    """Fold every same-named root into its component and give the rest a line each."""
    heads: dict[int, int] = {}
    folded: dict[int, list[TreeRow]] = {}
    shapes: dict[int, tuple[int, int | None, tuple[str, ...]]] = {}
    for row in rows:
        name = row.ancestor_names[-1]
        if row.parent_index is None:
            shapes[row.index] = (0, None, (name,))
        else:
            parent = rows[row.parent_index]
            head = heads[parent.index]
            if _folds(parent, row):
                heads[row.index] = head
                folded[head].append(row)
                continue
            depth, _, path = shapes[head]
            shapes[row.index] = (depth + 1, head, (*path, name))
        heads[row.index] = row.index
        folded[row.index] = []
    return tuple(TreeLine(rows[index], tuple(folded[index]), *shapes[index]) for index in shapes)


def _folds(parent: TreeRow, child: TreeRow) -> bool:
    """Whether ``child`` is the same-named root of the component ``parent``.

    The name must match exactly, because a root the author named differently is a level
    they meant to show. The states must agree, because a component can settle apart
    from its root (a cleanup failure, a time budget on one of them), and then the row
    that failed is the row the technician has to see.
    """
    return (
        parent.record["type"] == "suite"
        and len(cast(list[JsonObject], parent.record["children"])) == 1
        and child.record["name"] == parent.record["name"]
        and child.state == parent.state
    )
