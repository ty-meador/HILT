"""The Inspector screen renders the models and forwards the guide's keys, nothing more."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from tests.inspector_rack import (
    BAY_TREE,
    DEVICE_COUNT,
    MIXED_ROWS,
    PARK,
    bay_suite,
    device_row,
    mixed_suite,
    parked_suite,
    record_bay_run,
    record_mixed_run,
    record_parked_run,
    record_seventy_dut_run,
    saved_bytes,
)
from textual._xterm_parser import XTermParser
from textual.binding import Binding
from textual.events import Key
from textual.widgets import Footer
from textual.widgets._footer import FooterKey

from hilt.inspector_app import (
    LOG_FLOOR_LINES,
    LOG_HEADER_LINES,
    ArtifactScreen,
    InspectorApp,
    RowDisplay,
    _log_header,  # pyright: ignore[reportPrivateUsage]
    _log_row_text,  # pyright: ignore[reportPrivateUsage]
    _modal_body,  # pyright: ignore[reportPrivateUsage]
    _modal_title,  # pyright: ignore[reportPrivateUsage]
    _pane_lines,  # pyright: ignore[reportPrivateUsage]
    _pinned_window,  # pyright: ignore[reportPrivateUsage]
    _row_text,  # pyright: ignore[reportPrivateUsage]
    _tree_prefixes,  # pyright: ignore[reportPrivateUsage]
    _window,  # pyright: ignore[reportPrivateUsage]
)
from hilt.inspector_artifact_view import ArtifactModal
from hilt.inspector_artifacts import ArtifactReader
from hilt.inspector_rerun import RerunRecord, RerunSession
from hilt.inspector_run import SavedRun, SavedRunReader
from hilt.inspector_selection import SelectionView
from hilt.inspector_timeline import TimelineDiagnostic, TimelineRead, TimelineRecord
from hilt.inspector_timeline_view import TimelineEntry, TimelineView
from hilt.inspector_tree import InspectorTree, TreeRow

SIZE = (100, 30)

TALL = (100, 60)
"""A terminal whose log pane, a quarter of the body, holds twelve records on one page.

For the tests that read a whole merged chronology at once and still look at the tree.
"""

MIXED_TREE = (
    "> rack  Aborted",
    "  └─ steps  Aborted",
    "     ├─ power  Pass",
    "     ├─ checks  Aborted",
    "     │  ├─ volts  Fail",
    "     │  └─ slow  Aborted",
    "     └─ later  Pending",
)
"""The mixed run's Inspect tree with the root highlighted: the legacy glyphs, read back."""


def _row_name(line: str) -> str:
    """The node name on a rendered tree line, whatever its depth, glyphs, or cursor."""
    return line[2:].lstrip(" │├└─").split("  ")[0]


def _selected(app: InspectorApp) -> str:
    return app.selection.view.selected.ancestor_names[-1]


def _path(app: InspectorApp) -> str:
    """The highlighted row's drawn path, which tells two rows of one name apart."""
    return "/".join(app.saved_tree.line(app.selection.view.selected.index).path)


def _pinned_rule(app: InspectorApp) -> int | None:
    """Where the rule under the pinned ancestors sits in the tree pane, if anything is pinned."""
    rules = [number for number, line in enumerate(app.tree_lines) if set(line) == {"─"}]
    assert len(rules) <= 1
    return rules[0] if rules else None


def _assert_highlight_is_below_the_pinned_block(app: InspectorApp) -> None:
    """The pane shows the highlighted row exactly once, and never among the pinned lines."""
    marked = [number for number, line in enumerate(app.tree_lines) if line.startswith("> ")]
    assert len(marked) == 1, app.tree_lines
    assert _row_name(app.tree_lines[marked[0]]) == _selected(app)
    rule = _pinned_rule(app)
    assert rule is None or marked[0] > rule, app.tree_lines


def _parents(depths: list[int]) -> list[int | None]:
    """Each row's parent position, from preorder depths: the nearest shallower row above."""
    parents: list[int | None] = []
    for position, depth in enumerate(depths):
        above = [other for other in range(position) if depths[other] < depth]
        parents.append(above[-1] if above else None)
    return parents


def _text_of(file: str) -> str:
    return Path(file).read_text(encoding="utf-8")


def _truncate_leaf_log(saved: SavedRun, name: str) -> Path:
    """Simulate an interrupted run: an unfinished record at the end of the log ``name`` reads."""
    row = next(row for row in saved.tree.rows if row.ancestor_names[-1] == name)
    assert row.log_owner_index is not None
    reference = cast(str, saved.tree.rows[row.log_owner_index].record["log_path"])
    path = saved.run_root / reference
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"format_version": 1, "timestamp": "2026-09-09T2')
    return path


class TestInspectorApp:
    async def test_opens_on_the_root_with_its_log_loaded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()

            assert app.tab == "inspect"
            assert [_row_name(line) for line in app.tree_lines] == list(MIXED_ROWS)
            assert app.tree_lines == MIXED_TREE
            assert app.log_lines[0] == "log: rack"
            assert app.log_lines[1].startswith("page ")
            assert app.timeline_view is not None
            # A chronology opens at its tail, where the run stopped.
            assert app.timeline_view.page_index == app.timeline_view.page_count - 1

    async def test_cursor_movement_loads_the_highlighted_node_s_log_without_enter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()

            assert _selected(app) == "power"
            assert app.tree_lines[2].startswith(">    ├─ power  Pass")
            # A leaf composed with self.test rolls into its parent's chronology; the
            # pane names the owner and says so, never showing the log under the leaf,
            # and the owner's row carries the legacy arrow so the eye finds it.
            assert app.log_lines[0] == "log: rack/steps  (rolled up: power has no own log)"
            assert app.tree_lines[1] == "  └─ steps  Aborted  ←"
            assert sum(line.endswith("←") for line in app.tree_lines) == 1
            assert any("[1] " in line and "rail" in line for line in app.log_lines)
            assert any("ripple above expectation" in line for line in app.log_lines)
            assert "captures: 1" in app.log_lines[1]

            await pilot.press("up")
            await pilot.pause()
            assert _selected(app) == "steps"
            assert app.log_lines[0] == "log: rack/steps"
            assert not any(line.endswith("←") for line in app.tree_lines)

    async def test_arrows_jump_between_failures_skipping_passed_and_pending_without_wrap(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("right")
            await pilot.pause()
            assert _selected(app) == "steps"
            await pilot.press("right")
            await pilot.pause()
            assert _selected(app) == "checks"
            await pilot.press("right")
            await pilot.pause()
            assert _selected(app) == "volts"
            await pilot.press("right")
            await pilot.pause()
            assert _selected(app) == "slow"
            await pilot.press("right")
            await pilot.pause()
            assert _selected(app) == "slow"
            assert app.log_lines[0] == "log: rack/steps/checks  (rolled up: slow has no own log)"

            for _ in range(6):
                await pilot.press("left")
            await pilot.pause()
            assert _selected(app) == "rack"

    async def test_h_hides_passing_rows_and_keeps_the_cursor_on_a_visible_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "power"

            await pilot.press("h")
            await pilot.pause()
            assert app.hide_passes is True
            names = [_row_name(line) for line in app.tree_lines]
            assert names == ["rack", "steps", "checks", "volts", "slow", "later"]
            # The highlighted row vanished with the passes; the cursor lands on the
            # row above it, and the glyphs close the branch on what is still shown.
            assert _selected(app) == "steps"
            assert app.tree_lines[1] == "> └─ steps  Aborted"
            assert app.tree_lines[2] == "     ├─ checks  Aborted"
            assert app.tree_lines[5] == "     └─ later  Pending"

            await pilot.press("down")
            await pilot.pause()
            assert _selected(app) == "checks"
            assert app.log_lines[0] == "log: rack/steps/checks"

            await pilot.press("h")
            await pilot.pause()
            assert app.hide_passes is False
            assert [_row_name(line) for line in app.tree_lines] == list(MIXED_ROWS)
            assert _selected(app) == "checks"
            assert app.tree_lines[3].startswith("> ")

    async def test_paging_and_the_errors_filter_keep_a_truncation_marker_visible(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        _truncate_leaf_log(saved, "power")
        app = InspectorApp(saved)

        async with app.run_test(size=(100, 12)) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "power"
            assert app.timeline_view is not None
            assert app.timeline_view.page_count > 1
            last = app.timeline_view.page_index
            assert any(line.startswith("--- truncated") for line in app.log_lines)

            await pilot.press("p")
            await pilot.pause()
            assert app.timeline_view.page_index == last - 1
            await pilot.press("n", "n")
            await pilot.pause()
            assert app.timeline_view.page_index == last

            await pilot.press("f")
            await pilot.pause()
            assert app.timeline_view.errors_only is True
            assert "errors only: on" in app.log_lines[1]
            body = app.log_lines[2:]
            assert any("ripple above expectation" in line for line in body)
            assert any(line.startswith("--- truncated") for line in body)
            assert not any("rail settled" in line for line in body)

            # A pending node has no chronology: the page and filter keys have nothing
            # to act on and the pane says why.
            for _ in range(4):
                await pilot.press("down")
            await pilot.press("n", "p", "f")
            await pilot.pause()
            assert _selected(app) == "later"
            assert app.timeline_view is None
            assert app.log_lines == ("log: not run: the node never executed", "")

    async def test_quit_keys_leave_the_saved_bytes_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        before = saved_bytes(saved.run_root)

        for key in ("q", "ctrl+c"):
            app = InspectorApp(saved)
            async with app.run_test(size=SIZE) as pilot:
                await pilot.press("down")
                await pilot.press(key)
                await pilot.pause()
            assert app.return_code == 0

        assert saved_bytes(saved.run_root) == before

    async def test_a_tall_tree_windows_around_the_selection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_seventy_dut_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            pane_height = len(app.tree_lines)
            assert 1 < pane_height < len(saved.tree.rows)

            for _ in range(device_row(25)):
                await pilot.press("down")
            await pilot.pause()

            assert app.selection.view.selected.index == device_row(25)
            assert app.selection.view.selected.ancestor_names == (
                "rack",
                "devices",
                "dut-25",
                "voltage",
            )
            assert len(app.tree_lines) == pane_height
            assert sum(line.startswith("> ") for line in app.tree_lines) == 1
            assert app.log_lines[0] == (
                "log: rack/devices/dut-25  (rolled up: voltage has no own log)"
            )
            assert "captures: 2" in app.log_lines[1]

            screenshot = app.save_screenshot(filename="inspector.svg", path=str(tmp_path))
            assert "Inspect" in _text_of(screenshot)

            # A taller terminal resizes the same presentation model rather than
            # rebuilding it, so the page cursor survives the resize.
            view_before = app.timeline_view
            await pilot.resize_terminal(80, 48)
            await pilot.pause()
            assert app.timeline_view is view_before
            assert "captures: 2" in app.log_lines[1]

            # Every node passed: hiding passes says so instead of showing nothing.
            await pilot.press("h")
            await pilot.pause()
            assert app.tree_lines == ("every node passed; h shows them again",)
            await pilot.press("down", "h")
            await pilot.pause()
            assert 1 < len(app.tree_lines) < len(saved.tree.rows)
            assert sum(line.startswith("> ") for line in app.tree_lines) == 1


class TestRerunTab:
    async def test_enter_re_runs_the_highlighted_node_and_shows_its_fresh_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch, refuse_execution=False)
        before = saved_bytes(saved.run_root)
        session = RerunSession(saved, mixed_suite, scratch_parent=tmp_path)
        app = InspectorApp(saved, session)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "power"

            # Enter on Inspect never runs anything: the archive has no such key.
            await pilot.press("enter")
            await pilot.pause()
            assert session.scratch_root is None

            await pilot.press("tab")
            await pilot.pause()
            assert app.tab == "rerun"
            assert session.available is True, session.unavailable
            assert _selected(app) == "power"
            assert [_row_name(line) for line in app.tree_lines] == list(MIXED_ROWS)
            assert all(line.endswith("not re-run") for line in app.tree_lines)
            assert app.log_lines == (
                "log: not re-run yet: press Enter on a node to execute it",
                "",
            )

            await pilot.press("enter")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()

            assert session.record(2) is not None
            # The bench re-ran one leaf. Its ancestors configured and rolled their
            # state up over that one child, so they carry no verdict here; drawing
            # them Pass would hide the sibling failure the archive still holds.
            assert app.tree_lines[0] == "  rack  partly re-run"
            assert app.tree_lines[1] == "  └─ steps  partly re-run  ←"
            assert app.tree_lines[2] == ">    ├─ power  Pass"
            assert app.tree_lines[4] == "     │  ├─ volts  not re-run"
            assert app.log_lines[0] == "log: rack/steps  (rolled up: power has no own log)"
            assert any("ripple above expectation" in line for line in app.log_lines)

            # Moving onto a node the bench never reached says so; the archive's log
            # for that node is not shown in its place.
            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "volts"
            assert app.log_lines[0] == "log: not re-run yet: press Enter on a node to execute it"

            await pilot.press("tab")
            await pilot.pause()
            assert app.tab == "inspect"
            assert app.tree_lines[4].startswith(">    │  ├─ volts  Fail")
            assert app.log_lines[0] == "log: rack/steps/checks  (rolled up: volts has no own log)"

        # The archive is untouched, and the bench's evidence went with the screen.
        assert saved_bytes(saved.run_root) == before
        assert session.scratch_root is None

    async def test_without_a_suite_the_tab_says_why_and_enter_is_inert(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("tab", "down", "down", "enter")
            await pilot.pause()

            assert app.tab == "rerun"
            assert app.session.available is False
            assert app.log_lines[0] == (
                "log: re-run unavailable: no suite was supplied to this Inspector"
            )
            assert app.session.scratch_root is None

    async def test_tab_is_held_during_a_live_re_run_and_q_interrupts_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_parked_run(tmp_path)
        session = RerunSession(saved, parked_suite, scratch_parent=tmp_path)
        app = InspectorApp(saved, session)
        started = asyncio.Event()
        monkeypatch.setitem(PARK, "event", started)
        at_close: list[RerunRecord | None] = []
        original_close = session.close

        def spy_close() -> None:
            at_close.append(session.record(1))
            original_close()

        monkeypatch.setattr(session, "close", spy_close)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("tab", "down")
            await pilot.pause()
            assert app.tab == "rerun"
            assert _selected(app) == "wait"

            await pilot.press("enter")
            await asyncio.wait_for(started.wait(), 5)
            await pilot.pause()
            assert session.running == 1
            assert "Running" in app.tree_lines[1]
            assert app.log_lines[0] == "log: running bench/wait"

            # Tab is disabled while hardware is live, and a second Enter starts nothing.
            await pilot.press("tab", "enter")
            await pilot.pause()
            assert app.tab == "rerun"
            assert session.running == 1

            await pilot.press("q")
            await asyncio.wait_for(
                app.workers.wait_for_complete(),  # pyright: ignore[reportUnknownMemberType]
                10,
            )
            await pilot.pause()

        assert app.return_code == 0
        record = at_close[0]
        assert record is not None
        assert record.row.state == "Aborted"


BAY_ROOT_NOTE = "  (with its root's log, in time order)"


class TestFoldedTree:
    """A component and its same-named root are two saved nodes and one row on the screen."""

    async def test_each_component_is_drawn_once_and_reads_as_one_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch)
        assert len(saved.tree.rows) == 13
        app = InspectorApp(saved)

        async with app.run_test(size=TALL) as pilot:
            await pilot.pause()
            assert app.tree_lines == BAY_TREE
            assert app.log_lines[0] == "log: bay" + BAY_ROOT_NOTE

            await pilot.press("down")
            await pilot.pause()
            assert _selected(app) == "sata"
            assert app.selection.view.selected.record["type"] == "suite"
            # One name in the path, and the component's records around its root's.
            assert app.log_lines[0] == "log: bay/sata" + BAY_ROOT_NOTE
            messages = [line for line in app.log_lines[2:] if "sata " in line]
            assert [line.split()[-1] for line in messages] == ["powered", "answers", "released"]
            assert not any(line.endswith("←") for line in app.tree_lines)

            # A claim rolled into the root's file. The root is drawn inside the
            # component, so that row carries the arrow and the path names it once.
            await pilot.press("down")
            await pilot.pause()
            assert _selected(app) == "present"
            assert app.log_lines[0] == "log: bay/sata  (rolled up: present has no own log)"
            assert app.tree_lines[1] == "  ├─ sata  Pass  ←"
            assert sum(line.endswith("←") for line in app.tree_lines) == 1
            assert any("sata answers" in line for line in app.log_lines)
            assert not any("sata powered" in line for line in app.log_lines)

    async def test_failure_arrows_and_hidden_passes_keep_to_drawn_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            visited: list[tuple[str, object]] = []
            for _ in range(3):
                await pilot.press("right")
                await pilot.pause()
                visited.append((_selected(app), app.selection.view.selected.record["type"]))
            # Never the folded root, which fails with its component and has no row.
            assert visited == [("nvme", "suite"), ("spins", "test"), ("spins", "test")]

            await pilot.press("h")
            await pilot.pause()
            assert app.tree_lines == (
                "  bay  Fail",
                "  └─ nvme  Fail  ←",
                ">    └─ spins  Fail",
            )
            await pilot.press("up", "up")
            await pilot.pause()
            assert app.tree_lines[0] == "> bay  Fail"
            assert app.log_lines[0] == "log: bay" + BAY_ROOT_NOTE

    async def test_a_pair_that_settled_differently_keeps_both_rows(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded = await record_bay_run(tmp_path, monkeypatch)
        path = recorded.run_root / "result.json"
        result = json.loads(path.read_text())
        sata = result["summary_tree"]["children"][0]["children"][0]
        assert (sata["name"], sata["children"][0]["name"]) == ("sata", "sata")
        sata["children"][0]["state"] = "Aborted"
        path.write_text(json.dumps(result))
        saved = await SavedRunReader().read(path)
        assert isinstance(saved, SavedRun), saved
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert app.tree_lines[:6] == (
                "> bay  Fail",
                "  ├─ sata  Pass",
                "  │  └─ sata  Aborted",
                "  │     ├─ present  Pass",
                "  │     └─ spins  Pass",
                "  ├─ nvme  Fail",
            )
            # Each row of the pair is read on its own, as the record has it.
            await pilot.press("down")
            await pilot.pause()
            assert app.log_lines[0] == "log: bay/sata"
            await pilot.press("down")
            await pilot.pause()
            assert app.log_lines[0] == "log: bay/sata/sata"

    async def test_enter_on_a_folded_row_re_runs_the_component(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch, refuse_execution=False)
        before = saved_bytes(saved.run_root)
        session = RerunSession(saved, bay_suite, scratch_parent=tmp_path)
        app = InspectorApp(saved, session)

        async with app.run_test(size=TALL) as pilot:
            await pilot.press("down", "tab")
            await pilot.pause()
            assert app.tab == "rerun"
            assert session.available is True, session.unavailable
            assert [_row_name(line) for line in app.tree_lines] == [
                _row_name(line) for line in BAY_TREE
            ]
            assert all(line.endswith("not re-run") for line in app.tree_lines)

            await pilot.press("enter")
            await app.workers.wait_for_complete()  # pyright: ignore[reportUnknownMemberType]
            await pilot.pause()

            # The cursor rests on the component, so the component is what ran: its own
            # lifecycle, its root, and both claims. The bay above it ran one branch.
            assert [session.record(index) is not None for index in range(6)] == [True] * 6
            assert session.record(6) is None
            assert app.tree_lines[:5] == (
                "  bay  partly re-run",
                "> ├─ sata  Pass",
                "  │  ├─ present  Pass",
                "  │  └─ spins  Pass",
                "  ├─ nvme  not re-run",
            )
            assert app.log_lines[0] == "log: bay/sata" + BAY_ROOT_NOTE
            messages = [line for line in app.log_lines[2:] if "sata " in line]
            assert [line.split()[-1] for line in messages] == ["powered", "answers", "released"]

            await pilot.press("down")
            await pilot.pause()
            assert app.log_lines[0] == "log: bay/sata  (rolled up: present has no own log)"
            assert app.tree_lines[1] == "  ├─ sata  Pass  ←"

        assert saved_bytes(saved.run_root) == before
        assert session.scratch_root is None


class TestPinnedAncestors:
    async def test_a_scrolled_tree_pins_the_ancestors_of_its_first_row_above_a_rule(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_seventy_dut_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            # Unscrolled, the ancestors are simply the first rows: nothing to pin.
            pane_height = len(app.tree_lines)
            assert _pinned_rule(app) is None

            for _ in range(device_row(25)):
                await pilot.press("down")
            await pilot.pause()

            assert app.tree_lines[0] == "  rack  Pass"
            assert app.tree_lines[1] == "  └─ devices  Pass"
            assert _pinned_rule(app) == 2
            assert app.tree_lines[2] == "─" * 78
            # The rows under the rule are consecutive rows of the saved tree, and the
            # first of them is a child of the innermost pinned line.
            below = [_row_name(line) for line in app.tree_lines[3:]]
            assert below[0].startswith("dut-")
            first = next(row.index for row in saved.tree.rows if row.ancestor_names[-1] == below[0])
            assert below == [
                row.ancestor_names[-1] for row in saved.tree.rows[first : first + len(below)]
            ]
            assert len(app.tree_lines) == pane_height
            _assert_highlight_is_below_the_pinned_block(app)

            # The Re-Run tab pins the same lines with the labels that tab draws.
            await pilot.press("tab")
            await pilot.pause()
            assert app.tree_lines[0] == "  rack  not re-run"
            assert app.tree_lines[1] == "  └─ devices  not re-run"
            assert _pinned_rule(app) == 2
            _assert_highlight_is_below_the_pinned_block(app)

    async def test_a_capped_block_keeps_the_innermost_ancestor_with_its_owner_mark(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        # Thirteen lines leave the tree four rows, so a third of the pane is one line.
        async with app.run_test(size=(100, 13)) as pilot:
            for _ in range(4):
                await pilot.press("down")
            await pilot.pause()

            assert _selected(app) == "volts"
            # rack and steps are dropped; checks is nearest, and it owns the log.
            assert app.tree_lines == (
                "     ├─ checks  Aborted  ←",
                "─" * 98,
                ">    │  ├─ volts  Fail",
                "     │  └─ slow  Aborted",
            )

            # With the passes hidden the chain closes on rows that are still shown.
            await pilot.press("h", "down")
            await pilot.pause()
            assert _selected(app) == "slow"
            assert app.tree_lines == (
                "     ├─ checks  Aborted  ←",
                "─" * 98,
                ">    │  └─ slow  Aborted",
                "     └─ later  Pending",
            )

    async def test_a_folded_component_is_pinned_as_the_one_line_it_is_drawn_on(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(100, 13)) as pilot:
            await pilot.press("right", "right")
            await pilot.pause()
            assert _selected(app) == "spins"
            # The claim's saved parent is nvme's root, which has no line: the line
            # above it on screen is the component, and that is what is pinned. It
            # also draws the root that owns the claim's log, so it carries the mark.
            assert app.tree_lines == (
                "  ├─ nvme  Fail  ←",
                "─" * 98,
                "> │  └─ spins  Fail",
                "  └─ fan  Pass",
            )

            # Room for two pinned lines, and nvme itself now leads the scrolling rows:
            # above it there is one line, the bay, though the record has two nodes.
            await pilot.resize_terminal(100, 17)
            await pilot.pause()
            assert app.tree_lines == (
                "  bay  Fail",
                "─" * 98,
                "  ├─ nvme  Fail  ←",
                "  │  ├─ present  Pass",
                "> │  └─ spins  Fail",
                "  └─ fan  Pass",
                "     └─ checks  Pass",
                "        └─ turns  Pass",
            )

            for key in ("down", "down", "down", "alt+up", "left", "up", "up", "up", "up"):
                await pilot.press(key)
                await pilot.pause()
                _assert_highlight_is_below_the_pinned_block(app)

    async def test_the_highlighted_row_is_never_drawn_under_the_pinned_block(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_seventy_dut_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(80, 24)) as pilot:
            pinned_at_some_point = False
            # Down through the scroll, then the ways the selection can jump or the
            # pane be redrawn: a sibling at a time, a log page, the errors filter.
            keys = (
                ["down"] * 40
                + ["alt+up"] * 6
                + ["p", "up", "p", "n", "f", "left", "right"]
                + ["up"] * 30
                + ["alt+down"] * 12
            )
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
                _assert_highlight_is_below_the_pinned_block(app)
                pinned_at_some_point = pinned_at_some_point or _pinned_rule(app) is not None
            assert pinned_at_some_point

            # The selection moves while the tree is out of sight; it comes back clear.
            await pilot.press("l", "down", "down", "down", "l")
            await pilot.pause()
            _assert_highlight_is_below_the_pinned_block(app)

            for height in (12, 13, 16, 40):
                await pilot.resize_terminal(80, height)
                await pilot.pause()
                _assert_highlight_is_below_the_pinned_block(app)

    async def test_jumps_between_failures_and_the_pass_filter_keep_the_highlight_clear(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(100, 13)) as pilot:
            keys = ["right"] * 4 + ["p", "left", "h", "left", "h", "left", "left"]
            for key in keys:
                await pilot.press(key)
                await pilot.pause()
                _assert_highlight_is_below_the_pinned_block(app)
            assert _selected(app) == "rack"
            assert _pinned_rule(app) is None


class TestSiblingJump:
    async def test_alt_arrows_move_between_siblings_and_stay_put_at_either_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            # The root has no parent and so no sibling.
            await pilot.press("alt+down", "alt+up")
            await pilot.pause()
            assert _selected(app) == "rack"

            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "power"

            # checks has two rows nested under it; the jump goes over them to later.
            await pilot.press("alt+down")
            await pilot.pause()
            assert _selected(app) == "checks"
            assert app.log_lines[0] == "log: rack/steps/checks"
            await pilot.press("alt+down")
            await pilot.pause()
            assert _selected(app) == "later"
            assert app.log_lines[0] == "log: not run: the node never executed"
            await pilot.press("alt+down")
            await pilot.pause()
            assert _selected(app) == "later"

            await pilot.press("alt+up", "alt+up", "alt+up")
            await pilot.pause()
            assert _selected(app) == "power"

    async def test_the_jump_never_leaves_the_parent_on_either_tab(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_seventy_dut_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        def device(index: int) -> int:
            return device_row(index) - 1

        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(device_row(3)):
                await pilot.press("down")
            # A device's one claim is an only child. The next device's claim sits at
            # the same depth one row further down, and is a cousin: not reached.
            for key in ("alt+down", "alt+up"):
                await pilot.press(key)
                await pilot.pause()
                assert app.selection.view.selected.index == device_row(3)

            # The devices are siblings: one to the next, over the claim between them.
            await pilot.press("up", "alt+down")
            await pilot.pause()
            assert app.selection.view.selected.index == device(4)
            assert app.log_lines[0] == "log: rack/devices/dut-04"
            await pilot.press("alt+up", "alt+up")
            await pilot.pause()
            assert app.selection.view.selected.index == device(2)

            # The same key on the Re-Run tab, where the log follows the cursor too.
            await pilot.press("tab", "alt+down")
            await pilot.pause()
            assert app.tab == "rerun"
            assert app.selection.view.selected.index == device(3)

            for _ in range(DEVICE_COUNT):
                await pilot.press("alt+down")
            await pilot.pause()
            assert app.selection.view.selected.index == device(DEVICE_COUNT - 1)

    async def test_on_a_folded_tree_claims_keep_to_their_component_and_components_step(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            assert app.tree_lines[2] == "> │  ├─ present  Pass"

            visited: list[str] = []
            for key in ("alt+down", "alt+down", "alt+up", "alt+up"):
                await pilot.press(key)
                await pilot.pause()
                visited.append(_path(app))
            # sata's two claims and no further: nvme's claims are drawn in the same
            # column directly below, under another parent.
            assert visited == [
                "bay/sata/spins",
                "bay/sata/spins",
                "bay/sata/present",
                "bay/sata/present",
            ]

            # Components step to components, landing on the node Re-Run would execute.
            # The bay is their one parent on screen, though the record hangs them on
            # its folded root.
            await pilot.press("up")
            await pilot.pause()
            assert _path(app) == "bay/sata"
            visited = []
            for key in ("alt+down", "alt+down", "alt+down", "alt+up", "alt+up", "alt+up"):
                await pilot.press(key)
                await pilot.pause()
                assert app.selection.view.selected.record["type"] == "suite"
                visited.append(_path(app))
            assert visited == [
                "bay/nvme",
                "bay/fan",
                "bay/fan",
                "bay/nvme",
                "bay/sata",
                "bay/sata",
            ]

    async def test_the_jump_steps_over_hidden_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down", "down", "h")
            await pilot.pause()
            assert _selected(app) == "checks"
            # power is a sibling but hidden with the passes: nothing above to land on.
            await pilot.press("alt+up")
            await pilot.pause()
            assert _selected(app) == "checks"
            await pilot.press("alt+down")
            await pilot.pause()
            assert _selected(app) == "later"

    async def test_the_footer_offers_the_sibling_keys_and_the_log_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            for tab in ("inspect", "rerun"):
                await pilot.pause()
                assert app.tab == tab
                offered = app.screen.active_bindings
                for key in ("shift+up", "shift+down", "l"):
                    assert offered[key].binding.show, key
                    assert offered[key].enabled, key
                # Alt works wherever the terminal passes it on, and is not advertised:
                # the footer names the pair that arrives in every terminal tried.
                for key in ("alt+up", "alt+down"):
                    assert not offered[key].binding.show, key
                    assert offered[key].enabled, key
                await pilot.press("tab")

    @pytest.mark.parametrize("modifier", ["shift", "alt"])
    async def test_either_modifier_makes_the_same_jump(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, modifier: str
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down")
            await pilot.pause()
            assert _path(app) == "bay/sata"
            visited: list[str] = []
            for key in ("down", "down", "down", "up", "up", "up"):
                await pilot.press(f"{modifier}+{key}")
                await pilot.pause()
                visited.append(_path(app))
            assert visited == ["bay/nvme", "bay/fan", "bay/fan", "bay/nvme", "bay/sata", "bay/sata"]

    @pytest.mark.parametrize(
        ("sequence", "key"),
        [
            ("\x1b[1;2A", "shift+up"),
            ("\x1b[1;2B", "shift+down"),
            ("\x1b[1;3A", "alt+up"),
            ("\x1b[1;3B", "alt+down"),
        ],
    )
    def test_the_bound_names_are_what_a_terminal_s_bytes_are_read_as(
        self, sequence: str, key: str
    ) -> None:
        # The pilot presses a key by name and so never meets a terminal. These are
        # the bytes an xterm-compatible terminal sends for each key; the binding is
        # only reachable if the reader gives them the name it is registered under.
        read = [event.key for event in XTermParser().feed(sequence) if isinstance(event, Key)]

        assert read == [key]
        bound = {
            binding.key: binding.action
            for binding in InspectorApp.BINDINGS
            if isinstance(binding, Binding)
        }
        assert bound[key] == ("sibling(-1)" if key.endswith("up") else "sibling(1)")

    async def test_the_footer_shows_the_keys_that_cannot_be_guessed_whole_at_100_columns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            footer = app.query_one(Footer)
            # The footer is one line: an entry past its right edge, or under the
            # palette key docked there, is cut. These four must not be.
            docked = [key.region.x for key in footer.query("FooterKey.-command-palette")]
            edge = min([footer.region.right, *docked])
            whole = {
                key.key
                for key in footer.query(FooterKey)
                if key.region.width and key.region.right <= edge
            }
            assert {"tab", "q", "h", "l"} <= whole
            # Further right the list is cut, as it always was at this width.
            assert "shift+down" not in whole


class TestLogSplit:
    @pytest.mark.parametrize(
        ("body", "tree", "log"),
        [
            # A 24-line terminal: a quarter of the 22-line body is under the floor.
            (22, 15, LOG_FLOOR_LINES),
            (28, 20, 7),
            (46, 34, 11),
            # Shorter still, the log holds its six lines and the tree gives way,
            (10, 3, LOG_FLOOR_LINES),
            (8, 1, LOG_FLOOR_LINES),
            # until no row fits above the tree's rule and the log has the body.
            (7, 0, 7),
            (4, 0, 4),
            (0, 0, 0),
        ],
    )
    def test_the_log_has_a_quarter_of_the_body_with_a_floor_and_the_tree_yields_first(
        self, body: int, tree: int, log: int
    ) -> None:
        assert _pane_lines(body, log_full=False) == (tree, log)
        assert _pane_lines(body, log_full=True) == (0, body)

    async def test_the_panes_on_screen_are_the_split_at_every_terminal_height(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_seventy_dut_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(80, 24)) as pilot:
            for height, tree, log in ((24, 15, 6), (30, 20, 7), (48, 34, 11), (12, 3, 6)):
                await pilot.resize_terminal(80, height)
                await pilot.pause()
                # What Textual laid out is what the redraw paged and windowed by.
                assert app.query_one("#tree").size.height == tree
                assert app.query_one("#log").size.height == log
                assert len(app.tree_lines) == tree
                assert app.timeline_view is not None
                assert len(app.log_lines) <= log
                assert app.timeline_view.page_count == 1

            # Too short for a tree row: the log is all there is between tabs and footer.
            await pilot.resize_terminal(80, 9)
            await pilot.pause()
            assert app.query_one("#tree").display is False
            assert app.query_one("#log").size.height == 7
            assert app.tree_lines == ()
            assert app.log_lines[0] == "log: rack"

    async def test_l_shows_the_log_full_screen_and_returns_to_the_same_first_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=(100, 12)) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "power"
            assert app.timeline_view is not None
            # The chronology opened at its tail, on the second of two pages.
            assert app.timeline_view.page_index == 1
            tree_before, log_before = app.tree_lines, app.log_lines
            first = log_before[LOG_HEADER_LINES]

            await pilot.press("l")
            await pilot.pause()
            assert app.log_full is True
            assert app.query_one("#tree").display is False
            assert app.query_one("#log").size.height == 10
            assert app.query_one("#tabs").size.height == 1
            assert app.tree_lines == ()
            assert _selected(app) == "power"
            # The taller page starts earlier and still holds the record the reader
            # was on; nothing was reloaded to get there.
            assert app.log_lines[0] == log_before[0]
            assert app.log_lines[1].startswith("page 1/1")
            assert app.log_lines[LOG_HEADER_LINES] != first
            assert first in app.log_lines

            await pilot.press("l")
            await pilot.pause()
            assert app.log_full is False
            assert app.log_lines == log_before
            assert app.tree_lines == tree_before

            # The errors-only filter rides through the toggle in both directions.
            await pilot.press("f", "l")
            await pilot.pause()
            assert "errors only: on" in app.log_lines[1]
            filtered = app.log_lines[LOG_HEADER_LINES:]
            await pilot.press("l")
            await pilot.pause()
            assert "errors only: on" in app.log_lines[1]
            assert app.log_lines[LOG_HEADER_LINES:] == filtered

    async def test_every_key_keeps_working_while_the_log_is_full_screen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("l", "down", "down")
            await pilot.pause()
            # The tree is out of sight; the log header says where the cursor went.
            assert app.tree_lines == ()
            assert _selected(app) == "power"
            assert app.log_lines[0] == "log: rack/steps  (rolled up: power has no own log)"

            await pilot.press("alt+down", "right")
            await pilot.pause()
            assert _selected(app) == "volts"
            assert app.log_lines[0] == "log: rack/steps/checks  (rolled up: volts has no own log)"

            # The layout belongs to the screen, not to a tab.
            await pilot.press("tab")
            await pilot.pause()
            assert app.tab == "rerun"
            assert app.log_full is True
            assert app.tree_lines == ()

            await pilot.press("tab", "l")
            await pilot.pause()
            assert app.log_full is False
            assert app.tree_lines[4].startswith(">    │  ├─ volts  Fail")


class TestPinnedWindow:
    RACK: tuple[int, ...] = (0, 1, *([2, 3] * 10))
    """Depths of a rack: the root, one parallel, then ten devices of one claim each."""

    def test_a_tree_that_fits_or_is_not_scrolled_pins_nothing(self) -> None:
        assert _pinned_window(_parents([0, 1, 2, 2]), 3, 10) == ((), range(0, 4))
        assert _pinned_window(_parents(list(self.RACK)), 7, 15) == ((), range(0, 15))

    def test_the_first_scrolling_row_s_ancestors_are_pinned_root_first(self) -> None:
        parents = _parents(list(self.RACK))
        # Two pinned lines and the rule leave twelve rows; the first is a device, so
        # no claim is ever shown without the device above it.
        assert _pinned_window(parents, 8, 15) == ((0, 1), range(4, 16))
        assert _pinned_window(parents, 9, 15) == ((0, 1), range(6, 18))
        assert _pinned_window(parents, 21, 15) == ((0, 1), range(10, 22))

    def test_a_header_leaves_when_its_subtree_has_scrolled_past(self) -> None:
        # Root, then two groups: rows 2-6 under row 1, rows 8-13 under row 7.
        parents = _parents([0, 1, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2])
        # The first group's last row is still on screen: its header stays.
        assert _pinned_window(parents, 6, 6) == ((0, 1), range(6, 9))
        # The second group's own row now leads: only the root is above it.
        assert _pinned_window(parents, 7, 6) == ((0,), range(7, 11))
        # One of its children leads: the second header has taken the first one's place.
        assert _pinned_window(parents, 9, 6) == ((0, 7), range(9, 12))

    def test_the_cap_is_a_third_of_the_pane_and_drops_the_outermost_levels(self) -> None:
        # A chain twenty deep in a nine-line pane: three lines may be pinned.
        pinned, scrolling = _pinned_window(_parents(list(range(20))), 12, 9)
        assert pinned == (9, 10, 11)
        assert scrolling == range(12, 17)

    @pytest.mark.parametrize("height", [0, 1, 2, 3])
    def test_a_pane_too_short_for_the_block_pins_nothing(self, height: int) -> None:
        pinned, scrolling = _pinned_window(_parents(list(self.RACK)), 9, height)
        assert pinned == ()
        assert 9 in scrolling
        assert len(scrolling) == max(1, height)

    @pytest.mark.parametrize("height", range(1, 24))
    def test_the_selection_is_always_among_the_scrolling_rows_and_the_pane_never_overflows(
        self, height: int
    ) -> None:
        # Deep, shallow, and uneven subtrees, so every transition of the block occurs.
        depths = [0, 1, 2, 3, 4, 5, 5, 1, 2, 2, 1, 2, 3, 3, 3, 4, 5, 6, 7, 2, 1, 1, 2, 3, 3, 1]
        parents = _parents(depths)
        for selected in range(len(depths)):
            pinned, scrolling = _pinned_window(parents, selected, height)
            assert selected in scrolling
            assert len(pinned) <= height // 3
            assert len(pinned) + bool(pinned) + len(scrolling) <= max(1, height)
            # The pinned lines are exactly the nearest ancestors of the first row.
            chain: list[int] = []
            parent = parents[scrolling.start]
            while parent is not None:
                chain.append(parent)
                parent = parents[parent]
            assert list(pinned) == chain[: len(pinned)][::-1]
            assert not pinned or len(pinned) == min(len(chain), height // 3)


class TestRendering:
    def test_tree_row_shows_cursor_glyphs_state_readings_not_taken_and_owner(self) -> None:
        display = RowDisplay(
            name="volts",
            prefix="│  ├─ ",
            label="Fail",
            style="bold red",
            not_taken=("missing",),
            selected=True,
            owner=False,
        )

        assert _row_text(display).plain == "> │  ├─ volts  Fail  not taken: missing"
        assert (
            _row_text(replace(display, selected=False, owner=True)).plain
            == "  │  ├─ volts  Fail  not taken: missing  ←"
        )

    def test_tree_prefixes_close_each_branch_on_its_last_visible_child(self) -> None:
        assert _tree_prefixes([0, 1, 2, 2, 3, 3, 2]) == [
            "",
            "└─ ",
            "   ├─ ",
            "   ├─ ",
            "   │  ├─ ",
            "   │  └─ ",
            "   └─ ",
        ]
        # Two children of the root: the first keeps a vertical line beside its subtree.
        assert _tree_prefixes([0, 1, 2, 1, 2]) == ["", "├─ ", "│  └─ ", "└─ ", "   └─ "]
        assert _tree_prefixes([0]) == [""]

    def test_log_rows_number_captures_and_keep_damage_in_place(self) -> None:
        record = TimelineRecord(
            7,
            {
                "timestamp": "2026-09-09T21:00:00Z",
                "level": "warning",
                "message": "measurement: rail = 12.1 V limits=[null, 12.0] in_limits=false",
                "artifact_path": "/run/rail.json",
            },
        )
        entry = _log_row_text(TimelineEntry(record, 3)).plain
        assert entry.startswith("[3] 2026-09-09T21:00:00Z warning  measurement: rail")
        assert _log_row_text(TimelineEntry(record, None)).plain.startswith("    2026-09-09")
        damage = _log_row_text(TimelineDiagnostic(12, "truncated", "unfinished record")).plain
        assert damage == "--- truncated at line 12: unfinished record ---"
        assert (
            _log_row_text(TimelineDiagnostic(None, "unavailable", "gone")).plain
            == "--- unavailable: gone ---"
        )

    @pytest.mark.parametrize(
        ("count", "selected", "height", "expected"),
        [
            (5, 0, 10, range(0, 5)),
            (20, 0, 5, range(0, 5)),
            (20, 10, 5, range(8, 13)),
            (20, 19, 5, range(15, 20)),
            (20, 3, 0, range(3, 4)),
        ],
    )
    def test_window_keeps_the_selection_visible(
        self, count: int, selected: int, height: int, expected: range
    ) -> None:
        assert _window(count, selected, height) == expected

    async def test_the_mixed_recording_is_what_the_screen_tests_assume(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The screen tests read row positions by name; pin the recording's shape once.
        saved = await record_mixed_run(tmp_path, monkeypatch)
        result = json.loads((saved.run_root / "result.json").read_text())
        assert result["state"] == "Aborted"
        assert [row.ancestor_names[-1] for row in saved.tree.rows] == list(MIXED_ROWS)
        assert [row.state for row in saved.tree.failures] == [
            "Aborted",
            "Aborted",
            "Aborted",
            "Fail",
            "Aborted",
        ]


class TestLogHeader:
    def test_states_other_than_ready_are_named_and_carry_no_page_line(self) -> None:
        row = TreeRow(
            index=6,
            parent_index=1,
            ancestor_names=("rack", "steps", "later"),
            record={"name": "later", "state": None, "evidence": None, "evidence_declared": None},
            log_owner_index=None,
        )
        # Only a ready view names an owner, so these states never consult the tree.
        tree = InspectorTree(
            {"summary_tree": {"name": "rack", "state": None, "log_path": None, "children": []}}
        )

        owner, page = _log_header(SelectionView(row, None, "loading"), None, tree)
        assert (owner.plain, page.plain) == ("log: loading", "")
        owner, page = _log_header(SelectionView(row, None, "not-run"), None, tree)
        assert (owner.plain, page.plain) == ("log: not run: the node never executed", "")
        owner, page = _log_header(SelectionView(row, None, "unavailable", None, "gone"), None, tree)
        assert (owner.plain, page.plain) == ("log: unavailable: gone", "")
        owner, page = _log_header(SelectionView(row, None, "idle"), None, tree)
        assert (owner.plain, page.plain) == ("log: idle: no chronology", "")


class TestArtifactScreen:
    async def test_a_digit_opens_the_numbered_capture_and_b_returns_to_the_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down")
            await pilot.pause()
            assert _selected(app) == "power"

            # A digit with no capture behind it does nothing.
            await pilot.press("5")
            await pilot.pause()
            assert not isinstance(app.screen, ArtifactScreen)

            await pilot.press("1")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ArtifactScreen)
            assert screen.modal_lines[0].startswith(
                "type 1/1: survey   capture 1/1   [1] rail (survey)"
            )
            assert screen.modal_lines[1].startswith("page 1/1")
            assert '  "data": {' in screen.modal_lines
            assert '    "v": 12.1' in screen.modal_lines

            await pilot.press("b")
            await pilot.pause()
            assert not isinstance(app.screen, ArtifactScreen)
            assert _selected(app) == "power"

    async def test_the_modal_owns_its_keys_and_compares_two_captures(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_seventy_dut_run(tmp_path, monkeypatch)
        before = saved_bytes(saved.run_root)
        app = InspectorApp(saved)

        async with app.run_test(size=(100, 16)) as pilot:
            for _ in range(device_row(3)):
                await pilot.press("down")
            await pilot.press("2")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ArtifactScreen)
            assert screen.modal_lines[0].startswith("type 1/1: survey   capture 2/2   [2] reading")
            assert '  "data": "dut-03",' in screen.modal_lines

            # The arrows move between captures inside the modal, not the tree cursor.
            await pilot.press("left")
            await pilot.pause()
            assert screen.modal_lines[0].startswith("type 1/1: survey   capture 1/2   [1] reading")
            assert '  "data": null,' in screen.modal_lines
            assert _selected(app) == "voltage"

            await pilot.press("d")
            await pilot.pause()
            assert screen.modal.diff is True
            assert "diff  [1] reading" in screen.modal_lines[0]
            assert "|  [2] reading" in screen.modal_lines[0]
            body = screen.modal_lines[2:]
            assert any('"data": null,' in line and '"data": "dut-03",' in line for line in body)

            # A collision swaps the sides rather than comparing a capture with itself.
            await pilot.press("right")
            await pilot.pause()
            assert "diff  [2] reading" in screen.modal_lines[0]
            assert "|  [1] reading" in screen.modal_lines[0]

            await pilot.press("n")
            await pilot.pause()
            assert screen.modal.page_index == min(1, screen.modal.page_count - 1)
            await pilot.press("p")
            await pilot.pause()
            assert screen.modal.page_index == 0

            await pilot.press("d")
            await pilot.pause()
            assert screen.modal.diff is False
            assert screen.modal_lines[0].startswith("type 1/1: survey   capture 1/2")

            # One type only: the bracket keys wrap onto the same type's first capture.
            await pilot.press("right_square_bracket")
            await pilot.pause()
            assert screen.modal_lines[0].startswith("type 1/1: survey   capture 1/2")
            await pilot.press("right", "left_square_bracket")
            await pilot.pause()
            assert screen.modal_lines[0].startswith("type 1/1: survey   capture 1/2")

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ArtifactScreen)

        assert saved_bytes(saved.run_root) == before

    async def test_quitting_from_the_modal_quits_the_inspector(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_mixed_run(tmp_path, monkeypatch)
        app = InspectorApp(saved)

        async with app.run_test(size=SIZE) as pilot:
            await pilot.press("down", "down", "1")
            await pilot.pause()
            assert isinstance(app.screen, ArtifactScreen)
            await pilot.press("q")
            await pilot.pause()
        assert app.return_code == 0


class TestModalRendering:
    async def test_the_modal_says_loading_until_its_read_lands(self, tmp_path: Path) -> None:
        root = tmp_path / "run"
        capture = root / "1" / "reading.json"
        capture.parent.mkdir(parents=True)
        capture.write_text(
            json.dumps(
                {
                    "data": {"v": 12.1},
                    "format_version": 1,
                    "name": "reading",
                    "timestamp": "2026-09-09T18:00:00Z",
                    "type": "survey",
                }
            )
        )
        pointer = TimelineRecord(
            2,
            {
                "format_version": 1,
                "timestamp": "2026-09-09T18:00:00Z",
                "scope": ["rack"],
                "level": "info",
                "message": "capture",
                "artifact_name": "reading",
                "artifact_type": "survey",
                "artifact_path": str(capture),
                "artifact_timestamp": "2026-09-09T18:00:00Z",
            },
        )
        view = TimelineView(TimelineRead(None, (pointer,)), page_size=1)
        modal = ArtifactModal(view, ArtifactReader(root), initial_number=1, page_size=4)

        assert _modal_title(modal).plain.split("\n") == [
            "type 1/1: survey   loading",
            "page 1/1   b back  [ ] type  ← → capture  n p page  d diff",
        ]
        assert [line.plain for line in _modal_body(modal, 80)] == ["loading"]

        await modal.open()

        assert _modal_title(modal).plain.startswith(
            "type 1/1: survey   capture 1/1   [1] reading (survey) 2026-09-09T18:00:00Z"
        )
        assert [line.plain for line in _modal_body(modal, 80)] == [
            "{",
            '  "data": {',
            '    "v": 12.1',
            "  },",
        ]

    async def test_a_diagnostic_is_rendered_in_warning_style(self, tmp_path: Path) -> None:
        missing = tmp_path / "run" / "gone.json"
        pointer = TimelineRecord(
            2,
            {
                "format_version": 1,
                "timestamp": "2026-09-09T18:00:00Z",
                "scope": ["rack"],
                "level": "info",
                "message": "capture",
                "artifact_name": "reading",
                "artifact_type": "survey",
                "artifact_path": str(missing),
                "artifact_timestamp": "2026-09-09T18:00:00Z",
            },
        )
        view = TimelineView(TimelineRead(None, (pointer,)), page_size=1)
        modal = ArtifactModal(view, ArtifactReader(tmp_path / "run"), initial_number=1, page_size=4)
        await modal.open()

        assert modal.left is not None
        diagnostic = modal.left.diagnostic
        assert diagnostic is not None
        body = _modal_body(modal, 80)
        assert [line.plain for line in body] == [
            f"unavailable: {diagnostic.message}",
            f"recorded reference: {missing}",
        ]
        assert all(str(span.style) == "bold yellow" for line in body for span in line.spans)
