"""The highlighted claim must never inherit a delayed load from another selection."""

import asyncio
import json
from pathlib import Path

import pytest

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict
from hilt.inspector_selection import InspectorSelection
from hilt.inspector_timeline import (
    TimelineDiagnostic,
    TimelineRead,
    TimelineReader,
    TimelineRecord,
)
from hilt.inspector_tree import InspectorTree, TreeReader
from hilt.run_data import JsonObject, JsonValue


def _node(name: str, **changes: JsonValue) -> JsonObject:
    return {
        "name": name,
        "type": "test",
        "state": "Pass",
        "failure": None,
        "timeout": None,
        "log_path": None,
        "children": [],
        "evidence": None,
        "evidence_declared": None,
        "measurements": [],
        **changes,
    }


def _line(message: str) -> str:
    return (
        json.dumps(
            {
                "format_version": 1,
                "timestamp": "2026-09-08T12:00:00Z",
                "scope": ["rack", message],
                "level": "info",
                "message": message,
            }
        )
        + "\n"
    )


class ControlledReader(TimelineReader):
    """Expose read completion order without sleeps or filesystem timing assumptions."""

    def __init__(self) -> None:
        self.requests: asyncio.Queue[tuple[str, asyncio.Future[TimelineRead]]] = asyncio.Queue()

    async def read_reference(self, run_root: Path, reference: str) -> TimelineRead:
        future: asyncio.Future[TimelineRead] = asyncio.get_running_loop().create_future()
        await self.requests.put((reference, future))
        return await future


class TestInspectorSelection:
    async def test_selection_loads_nearest_owner_and_preserves_scopes_and_damage(
        self, tmp_path: Path
    ) -> None:
        tree = InspectorTree(
            {"summary_tree": _node("rack", log_path="timeline.jsonl", children=[_node("leaf/a")])}
        )
        raw = _line("sibling") + "bad JSON\n" + _line("leaf/a") + '{"cut":'
        log = tmp_path / "timeline.jsonl"
        log.write_text(raw)
        selection = InspectorSelection(tree, tmp_path)
        assert selection.view.status == "idle"
        await selection.select(1)
        view = selection.view
        assert view.selected is tree.rows[1]
        assert view.owner is tree.rows[0]
        assert view.status == "ready"
        assert view.timeline is not None
        assert view.timeline.reference == "timeline.jsonl"
        assert [row.line_number for row in view.timeline.rows] == [1, 2, 3, 4]
        first, invalid, third, cut = view.timeline.rows
        assert isinstance(first, TimelineRecord)
        assert first.data["scope"] == ["rack", "sibling"]
        assert isinstance(third, TimelineRecord)
        assert third.data["scope"] == ["rack", "leaf/a"]
        assert isinstance(invalid, TimelineDiagnostic)
        assert invalid.kind == "invalid-record"
        assert isinstance(cut, TimelineDiagnostic)
        assert cut.kind == "truncated"
        assert log.read_text() == raw

    @pytest.mark.parametrize("last_index", [0, 1, 2])
    async def test_only_latest_selection_can_publish_even_when_cursor_returns_to_same_row(
        self, tmp_path: Path, last_index: int
    ) -> None:
        tree = InspectorTree(
            {
                "summary_tree": _node(
                    "rack",
                    log_path="root.jsonl",
                    children=[_node("leaf", log_path="leaf.jsonl"), _node("pending", state=None)],
                )
            }
        )
        reader = ControlledReader()
        selection = InspectorSelection(tree, tmp_path, reader=reader)
        old = asyncio.create_task(selection.select(0))
        _, first = await reader.requests.get()
        new = asyncio.create_task(selection.select(1))
        _, second = await reader.requests.get()
        assert selection.view.selected.index == 1
        assert selection.view.status == "loading"
        assert selection.view.timeline is None
        second.set_result(TimelineRead(tmp_path / "leaf.jsonl", (), "leaf.jsonl"))
        await new
        latest = asyncio.create_task(selection.select(last_index))
        if last_index != 2:
            reference, third = await reader.requests.get()
            assert selection.view.timeline is None
            third.set_result(TimelineRead(tmp_path / reference, (), reference))
        await latest
        expected = selection.view
        first.set_result(TimelineRead(tmp_path / "root.jsonl", (), "root.jsonl"))
        await old
        assert selection.view is expected
        assert selection.view.selected.index == last_index
        assert selection.view.status == ("not-run" if last_index == 2 else "ready")

    @pytest.mark.parametrize("outdated", [False, True])
    @pytest.mark.parametrize("cancel", [False, True])
    async def test_cancel_or_reader_defect_propagates_without_restoring_stale_content(
        self, tmp_path: Path, outdated: bool, cancel: bool
    ) -> None:
        tree = InspectorTree({"summary_tree": _node("rack", log_path="root.jsonl")})
        reader = ControlledReader()
        selection = InspectorSelection(tree, tmp_path, reader=reader)
        task = asyncio.create_task(selection.select(0))
        _, first = await reader.requests.get()
        if outdated:
            newer = asyncio.create_task(selection.select(0))
            _, second = await reader.requests.get()
            second.set_result(TimelineRead(tmp_path / "root.jsonl", ()))
            await newer
        expected = selection.view
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            first.set_exception(RuntimeError("packaged schema defect"))
            with pytest.raises(RuntimeError, match="schema defect"):
                await task
        if outdated:
            assert selection.view is expected
        else:
            assert selection.view.status == "idle"
            assert selection.view.timeline is None

    async def test_pending_missing_owner_and_missing_file_are_distinct_and_reselection_reads_afresh(
        self, tmp_path: Path
    ) -> None:
        tree = InspectorTree(
            {
                "summary_tree": _node(
                    "rack",
                    children=[
                        _node("pending", state=None, log_path="misleading.jsonl"),
                        _node("missing", log_path="missing.jsonl"),
                    ],
                )
            }
        )
        selection = InspectorSelection(tree, tmp_path)
        await selection.select(0)
        assert selection.view.status == "unavailable"
        assert selection.view.owner is None
        assert selection.view.message == "log unavailable: no recorded timeline owner"
        await selection.select(1)
        assert selection.view.status == "not-run"
        assert selection.view.timeline is None
        await selection.select(2)
        assert selection.view.status == "unavailable"
        assert selection.view.timeline is not None
        assert selection.view.timeline.reference == "missing.jsonl"
        (tmp_path / "missing.jsonl").write_text(_line("restored"))
        await selection.select(2)
        assert selection.view.status == "ready"
        assert selection.view.timeline is not None
        assert isinstance(selection.view.timeline.rows[0], TimelineRecord)
        assert selection.view.timeline.rows[0].data["message"] == "restored"

    @pytest.mark.parametrize("reference", ["../neighbor.jsonl", "/neighbor.jsonl", "escape.jsonl"])
    async def test_selection_never_uses_the_trusted_path_entry_point(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference: str
    ) -> None:
        root = tmp_path / "run"
        root.mkdir()
        neighbor = tmp_path / "neighbor.jsonl"
        neighbor.write_text(_line("wrong run"))
        (root / "escape.jsonl").symlink_to(neighbor)

        async def refuse_read(*args: object, **kwargs: object) -> TimelineRead:
            raise AssertionError("selection used an uncontained read")

        monkeypatch.setattr(TimelineReader, "read", refuse_read)
        tree = InspectorTree({"summary_tree": _node("rack", log_path=reference)})
        selection = InspectorSelection(tree, root)
        await selection.select(0)
        assert selection.view.status == "unavailable"
        assert selection.view.timeline is not None
        assert selection.view.timeline.reference == reference
        assert isinstance(selection.view.timeline.rows[0], TimelineDiagnostic)

    async def test_navigation_loads_the_shared_failure_rows_and_rejects_invalid_selection(
        self, tmp_path: Path
    ) -> None:
        tree = InspectorTree(
            {
                "summary_tree": _node(
                    "rack",
                    children=[
                        _node("good"),
                        _node("bad", state="Fail"),
                        _node("pending", state=None),
                        _node("abort", state="Aborted"),
                    ],
                )
            }
        )
        selection = InspectorSelection(tree, tmp_path)
        await selection.next_failure()
        assert selection.view.selected is tree.failures[0]
        await selection.next_failure()
        assert selection.view.selected is tree.failures[1]
        await selection.next_failure()
        assert selection.view.selected.index == 4
        await selection.previous_failure()
        assert selection.view.selected.index == 2
        await selection.previous_failure()
        assert selection.view.selected.index == 2
        await selection.move(1)
        assert selection.view.status == "not-run"
        await selection.move(-100)
        assert selection.view.selected.index == 0
        view = selection.view
        with pytest.raises(IndexError):
            await selection.select(-1)
        assert selection.view is view

    async def test_moved_seventy_dut_saved_run_loads_each_selected_claim_without_execution(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Device(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.test("voltage", self._voltage)

            async def _voltage(self, utils: TestUtils) -> Verdict:
                utils.logger.info("reading %s", self.name)
                if self.name != "dut-35":
                    return PASS({"volts": 3.3})
                raise TestFailure("rail low", evidence={"volts": 0})

        class Rack(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.parallel("devices", [Device(name=f"dut-{i:02d}") for i in range(70)])

        rack = Rack(name="rack")
        await rack.run()
        assert rack.log_path is not None
        moved = tmp_path / "moved"
        rack.log_path.parent.parent.rename(moved)
        del rack

        def refuse_execution(*args: object, **kwargs: object) -> None:
            raise AssertionError("saved selection attempted suite execution")

        monkeypatch.setattr(TestComponent, "run", refuse_execution)
        before = {path: path.read_bytes() for path in moved.rglob("*") if path.is_file()}
        tree = await TreeReader().read(moved / "result.json")
        assert isinstance(tree, InspectorTree)
        selection = InspectorSelection(tree, moved)
        for number in range(70):
            await selection.select(3 + number * 2)
            view = selection.view
            name = f"dut-{number:02d}"
            assert view.selected.ancestor_names == ("rack", "devices", name, "voltage")
            assert view.owner is tree.rows[2 + number * 2]
            assert view.status == "ready"
            assert view.timeline is not None
            assert any(
                isinstance(row, TimelineRecord) and row.data["message"] == f"reading {name}"
                for row in view.timeline.rows
            )
        assert {path: path.read_bytes() for path in before} == before


def _stamped(message: str, second: int) -> str:
    return (
        json.dumps(
            {
                "format_version": 1,
                "timestamp": f"2026-09-08T12:00:{second:02d}Z",
                "scope": ["rack", message],
                "level": "info",
                "message": message,
            }
        )
        + "\n"
    )


def _messages(selection: InspectorSelection) -> list[object]:
    timeline = selection.view.timeline
    assert timeline is not None
    return [
        row.data["message"] if isinstance(row, TimelineRecord) else row.kind
        for row in timeline.rows
    ]


def _component(root_log: str | None, **changes: JsonValue) -> JsonObject:
    """A component ``rack`` and its same-named root, each node with the log it is given."""
    root = _node("rack", type="sequence", log_path=root_log, children=[_node("claim")])
    return _node("rack", type="suite", log_path="rack.jsonl", children=[root], **changes)


class TestFoldedSelection:
    """A folded line has two nodes behind it, and its chronology is both of their logs."""

    async def test_a_folded_line_reads_the_component_and_its_root_in_time_order(
        self, tmp_path: Path
    ) -> None:
        tree = InspectorTree({"summary_tree": _component("root.jsonl")})
        (tmp_path / "rack.jsonl").write_text(_stamped("configure", 1) + _stamped("cleanup", 9))
        (tmp_path / "root.jsonl").write_text(_stamped("claim ran", 5))
        selection = InspectorSelection(tree, tmp_path)

        await selection.select(0)
        view = selection.view
        assert view.status == "ready"
        assert view.owner is tree.rows[0]
        assert view.merged == (tree.rows[1],)
        assert view.timeline is not None
        assert view.timeline.reference == "rack.jsonl"
        assert _messages(selection) == ["configure", "claim ran", "cleanup"]

        # A claim under the folded root rolled into the root's file, and reads that alone.
        await selection.select(2)
        assert selection.view.owner is tree.rows[1]
        assert selection.view.merged == ()
        assert _messages(selection) == ["claim ran"]

        # The record stays addressable: the root by its own index is read as recorded.
        await selection.select(1)
        assert selection.view.owner is tree.rows[1]
        assert selection.view.merged == ()
        assert _messages(selection) == ["claim ran"]

    async def test_a_root_that_rolled_into_its_component_is_one_file_read_once(
        self, tmp_path: Path
    ) -> None:
        tree = InspectorTree({"summary_tree": _component(None)})
        (tmp_path / "rack.jsonl").write_text(_stamped("configure", 1) + _stamped("claim ran", 5))
        selection = InspectorSelection(tree, tmp_path)

        await selection.select(0)

        assert selection.view.owner is tree.rows[0]
        assert selection.view.merged == ()
        assert _messages(selection) == ["configure", "claim ran"]

    async def test_one_missing_file_is_named_and_the_other_is_still_shown(
        self, tmp_path: Path
    ) -> None:
        tree = InspectorTree({"summary_tree": _component("root.jsonl")})
        (tmp_path / "rack.jsonl").write_text(_stamped("configure", 1))
        selection = InspectorSelection(tree, tmp_path)

        await selection.select(0)

        assert selection.view.status == "unavailable"
        assert _messages(selection) == ["unavailable", "configure"]
        assert selection.view.timeline is not None
        missing = selection.view.timeline.rows[0]
        assert isinstance(missing, TimelineDiagnostic)
        assert "root.jsonl" in missing.message

    async def test_the_layout_tree_decides_what_is_merged(self, tmp_path: Path) -> None:
        # The Re-Run tab draws the saved run's lines over a re-run's records. Here the
        # re-run's own states would draw the pair apart; the layout still folds them.
        saved = InspectorTree({"summary_tree": _component("root.jsonl")})
        rerun = InspectorTree({"summary_tree": _component("root.jsonl", state="Fail")})
        assert len(rerun.lines) == 3
        (tmp_path / "rack.jsonl").write_text(_stamped("configure", 1))
        (tmp_path / "root.jsonl").write_text(_stamped("claim ran", 5))

        alone = InspectorSelection(rerun, tmp_path)
        await alone.select(0)
        assert _messages(alone) == ["configure"]

        folded = InspectorSelection(rerun, tmp_path, layout=saved)
        await folded.select(0)
        assert folded.view.owner is rerun.rows[0]
        assert folded.view.merged == (rerun.rows[1],)
        assert _messages(folded) == ["configure", "claim ran"]

    async def test_a_folded_node_the_bench_never_reached_contributes_no_log(
        self, tmp_path: Path
    ) -> None:
        saved = InspectorTree({"summary_tree": _component("root.jsonl")})
        pending_root = _node("rack", type="sequence", state=None, log_path="root.jsonl")
        rerun = InspectorTree(
            {
                "summary_tree": _node(
                    "rack", type="suite", log_path="rack.jsonl", children=[pending_root]
                )
            }
        )
        (tmp_path / "rack.jsonl").write_text(_stamped("configure", 1))
        (tmp_path / "root.jsonl").write_text(_stamped("never part of this run", 5))
        selection = InspectorSelection(rerun, tmp_path, layout=saved)

        await selection.select(0)

        assert selection.view.merged == ()
        assert _messages(selection) == ["configure"]
