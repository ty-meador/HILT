"""Saved tree navigation preserves the claims and their evidence ownership."""

import asyncio
import json
from pathlib import Path

import pytest
from tests.inspector_rack import record_bay_run

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict
from hilt.inspector_artifacts import ArtifactDiagnostic
from hilt.inspector_run import SavedRunReader
from hilt.inspector_tree import InspectorTree, SelectedTree, TreeLine, TreeReader
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


def _save(root: Path, node: JsonObject) -> Path:
    path = root / "result.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "state": "Aborted",
                "failure": None,
                "timeout": None,
                "test_case": node["name"],
                "type": "suite",
                "ancestor_names": [node["name"]],
                "bread_crumbs": node["name"],
                "summary_tree": node,
                "physical_location": "unknown",
                "env": {},
                "run_data": {"measurements": []},
                "future": {"keep": True},
            }
        )
    )
    return path


class TestInspectorTree:
    @pytest.mark.parametrize("reader_type", [TreeReader, SavedRunReader])
    async def test_selection_io_failure_is_diagnosed_by_both_readers(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader_type: type[TreeReader] | type[SavedRunReader],
    ) -> None:
        path = tmp_path / "result.json"

        def denied(self: Path) -> Path:
            raise PermissionError("selection denied")

        monkeypatch.setattr(Path, "resolve", denied)
        loaded = await reader_type().read(path)

        assert isinstance(loaded, ArtifactDiagnostic)
        assert loaded.reference == str(path)
        assert loaded.kind == "unavailable"
        assert "selection denied" in loaded.message

    @pytest.mark.parametrize("reader_type", [TreeReader, SavedRunReader])
    @pytest.mark.parametrize("error_type", [asyncio.CancelledError, RuntimeError])
    async def test_selection_cancellation_and_defects_propagate_from_both_readers(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        reader_type: type[TreeReader] | type[SavedRunReader],
        error_type: type[BaseException],
    ) -> None:
        def interrupted(self: Path) -> Path:
            raise error_type("selection interrupted")

        monkeypatch.setattr(Path, "resolve", interrupted)

        with pytest.raises(error_type, match="selection interrupted"):
            await reader_type().read(tmp_path / "result.json")

    @pytest.mark.parametrize("selection", ["symlink-loop", "null-byte"])
    async def test_unresolvable_operator_selection_is_an_unavailable_result(
        self, tmp_path: Path, selection: str
    ) -> None:
        path = tmp_path / "bad\x00name"
        if selection == "symlink-loop":
            path = tmp_path / "loop"
            path.symlink_to(path)

        loaded = await TreeReader().read(path)

        assert isinstance(loaded, ArtifactDiagnostic)
        assert loaded.kind == "unavailable"
        assert loaded.reference == str(path)

    @pytest.mark.parametrize("selection", ["absolute", "relative", "symlink", "parent"])
    async def test_operator_path_spellings_open_the_same_saved_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str
    ) -> None:
        run_root = tmp_path / "recorded"
        run_root.mkdir()
        path = _save(run_root, _node("rack"))
        original = path.read_bytes()
        latest = tmp_path / "latest"
        latest.symlink_to(run_root, target_is_directory=True)
        monkeypatch.chdir(tmp_path)
        selections = {
            "absolute": path,
            "relative": Path("recorded/result.json"),
            "symlink": latest / "result.json",
            "parent": run_root / ".." / "recorded" / "result.json",
        }

        tree = await TreeReader().read(selections[selection])

        assert isinstance(tree, InspectorTree)
        assert tree.result == json.loads(original)
        assert tree.selected.ancestor_names == ("rack",)
        assert path.read_bytes() == original

        selected = await TreeReader().read_selected(selections[selection])
        assert isinstance(selected, SelectedTree)
        assert selected.run_root == run_root
        assert selected.tree.result == tree.result

    async def test_declaration_order_name_components_and_duplicate_names_stay_distinct(
        self, tmp_path: Path
    ) -> None:
        node = _node(
            "rack/a",
            children=[
                _node("same", children=[_node("leaf/b")]),
                _node("same"),
            ],
        )
        path = _save(tmp_path, node)
        original = path.read_bytes()
        tree = await TreeReader().read(path)
        assert isinstance(tree, InspectorTree)
        assert [row.ancestor_names for row in tree.rows] == [
            ("rack/a",),
            ("rack/a", "same"),
            ("rack/a", "same", "leaf/b"),
            ("rack/a", "same"),
        ]
        assert [row.index for row in tree.rows] == [0, 1, 2, 3]
        assert [row.parent_index for row in tree.rows] == [None, 0, 1, 0]
        assert tree.result["future"] == {"keep": True}
        assert tree.rows[0].record == node
        tree.select(3)
        assert tree.selected is tree.rows[3]
        assert path.read_bytes() == original

    async def test_navigation_and_failure_projection_keep_both_peer_states_without_wrapping(
        self, tmp_path: Path
    ) -> None:
        path = _save(
            tmp_path,
            _node(
                "rack",
                state="Aborted",
                children=[
                    _node("good"),
                    _node("bad", state="Fail"),
                    _node("pending", state=None),
                    _node("no verdict", state="Aborted"),
                    _node("last"),
                ],
            ),
        )
        tree = await TreeReader().read(path)
        assert isinstance(tree, InspectorTree)
        assert [row.index for row in tree.failures] == [0, 2, 4]
        assert [row.state for row in tree.failures] == ["Aborted", "Fail", "Aborted"]
        assert tree.selected.index == 0
        tree.previous_failure()
        assert tree.selected.index == 0
        for expected in (2, 4, 4):
            tree.next_failure()
            assert tree.selected.index == expected
        tree.move(1)
        assert tree.selected.index == 5
        tree.next_failure()
        assert tree.selected.index == 5
        for expected in (4, 2, 0, 0):
            tree.previous_failure()
            assert tree.selected.index == expected
        tree.move(-100)
        assert tree.selected.index == 0
        tree.move(100)
        assert tree.selected.index == 5
        for index in (-1, 6):
            with pytest.raises(IndexError, match="tree row"):
                tree.select(index)
            assert tree.selected.index == 5

    async def test_pending_nodes_never_borrow_chronology_and_rollup_uses_nearest_owner(
        self, tmp_path: Path
    ) -> None:
        path = _save(
            tmp_path,
            _node(
                "rack",
                log_path="rack/timeline.jsonl",
                children=[
                    _node("rolled", children=[_node("leaf")]),
                    _node("owner", log_path="dut/timeline.jsonl", children=[_node("leaf")]),
                    _node("pending", state=None, log_path="misleading/timeline.jsonl"),
                ],
            ),
        )
        tree = await TreeReader().read(path)
        assert isinstance(tree, InspectorTree)
        for index, owner in enumerate((0, 0, 0, 3, 3, None)):
            tree.select(index)
            assert tree.log_owner is (None if owner is None else tree.rows[owner])
        tree.select(4)
        assert tree.log_owner is not None
        assert tree.log_owner.record["log_path"] == "dut/timeline.jsonl"
        assert not (tmp_path / "dut/timeline.jsonl").exists()

    async def test_no_owner_and_no_failures_remain_absent(self, tmp_path: Path) -> None:
        tree = await TreeReader().read(_save(tmp_path, _node("root")))
        assert isinstance(tree, InspectorTree)
        assert tree.log_owner is None
        assert tree.failures == ()
        tree.next_failure()
        tree.previous_failure()
        tree.move(1)
        assert tree.selected.index == 0

    async def test_missing_readings_use_presence_and_preserve_failure_timeout_and_additions(
        self, tmp_path: Path
    ) -> None:
        node = _node(
            "voltage",
            state="Fail",
            failure={
                "name": "TestFailure",
                "state": "Fail",
                "fail_message": "rail low",
                "cause": {"raw": "kept"},
            },
            timeout={
                "reason": "timeout",
                "declared": "fail_after",
                "budget_seconds": 1,
                "elapsed_seconds": 1,
                "clock": "voltage",
            },
            evidence={"zero": 0, "null": None, "false": False},
            evidence_declared=["zero", "missing", "null", "false", "last"],
            future={"reading": []},
        )
        tree = await TreeReader().read(_save(tmp_path, node))
        assert isinstance(tree, InspectorTree)
        assert tree.selected.not_taken == ("missing", "last")
        assert tree.selected.record == node
        node["evidence"] = None
        tree = await TreeReader().read(_save(tmp_path, node))
        assert isinstance(tree, InspectorTree)
        assert tree.selected.not_taken == ("zero", "missing", "null", "false", "last")

    @pytest.mark.parametrize(
        ("raw", "kind"),
        [
            ("{}", "unsupported-version"),
            ("[]", "invalid-record"),
            ('{"format_version":1}', "invalid-record"),
        ],
    )
    async def test_invalid_result_is_a_diagnostic_without_a_fabricated_tree(
        self, tmp_path: Path, raw: str, kind: str
    ) -> None:
        path = tmp_path / "result.json"
        path.write_text(raw)
        result = await TreeReader().read(path)
        assert isinstance(result, ArtifactDiagnostic)
        assert result.kind == kind
        assert path.read_text() == raw

    async def test_missing_result_is_unavailable(self, tmp_path: Path) -> None:
        result = await TreeReader().read(tmp_path / "result.json")
        assert isinstance(result, ArtifactDiagnostic)
        assert result.kind == "unavailable"

    async def test_recorded_seventy_dut_rack_is_navigable_after_the_run_moves(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Device(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.test("voltage", self._voltage)

            async def _voltage(self, utils: TestUtils) -> Verdict:
                if self.name != "dut-35":
                    return PASS({"volts": 3.3})
                raise TestFailure("rail low", evidence={"volts": 0})

        class Rack(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.parallel("devices", [Device(name=f"dut-{i:02d}") for i in range(70)])

        rack = Rack(name="rack")
        result = await rack.run()
        assert rack.log_path is not None
        moved = tmp_path / "moved"
        rack.log_path.parent.parent.rename(moved)
        recorded = result.to_json()
        del rack, result

        def refuse_suite_execution(*args: object, **kwargs: object) -> None:
            raise AssertionError("inspection attempted suite execution")

        monkeypatch.setattr(TestComponent, "run", refuse_suite_execution)
        tree = await TreeReader().read(moved / "result.json")
        assert isinstance(tree, InspectorTree)
        assert tree.result == recorded
        assert len(tree.rows) == 142
        assert [row.index for row in tree.failures] == [0, 1, 72, 73]
        for i in range(70):
            tree.select(3 + i * 2)
            assert tree.selected.ancestor_names == ("rack", "devices", f"dut-{i:02d}", "voltage")
            assert tree.selected.not_taken == ()
            owner = tree.log_owner
            assert owner is tree.rows[2 + i * 2]
            assert owner is not None
            assert owner.record["name"] == f"dut-{i:02d}"


def _suite(name: str, *children: JsonObject, **changes: JsonValue) -> JsonObject:
    return _node(name, type="suite", children=list(children), **changes)


def _drawn(tree: InspectorTree) -> list[tuple[int, str, int, int | None]]:
    return [(line.index, line.name, line.depth, line.parent_index) for line in tree.lines]


class TestFold:
    """The record keeps a component and its root as two nodes; the drawn tree shows one line."""

    async def test_a_same_named_root_is_drawn_inside_its_component(self, tmp_path: Path) -> None:
        root = _suite(
            "rack",
            _node(
                "rack",
                type="parallel",
                children=[
                    _suite("sata", _node("sata", type="sequence", children=[_node("present")])),
                    _node("label"),
                ],
            ),
        )
        path = _save(tmp_path, root)
        original = path.read_bytes()
        tree = await TreeReader().read(path)
        assert isinstance(tree, InspectorTree)

        # The record is untouched: five nodes, their indices and their full name paths.
        assert [row.index for row in tree.rows] == [0, 1, 2, 3, 4, 5]
        assert tree.rows[4].ancestor_names == ("rack", "rack", "sata", "sata", "present")
        assert tree.rows[4].parent_index == 3
        # The drawing has one line per name, at the depth the eye expects.
        assert _drawn(tree) == [
            (0, "rack", 0, None),
            (2, "sata", 1, 0),
            (4, "present", 2, 2),
            (5, "label", 1, 0),
        ]
        assert [line.path for line in tree.lines] == [
            ("rack",),
            ("rack", "sata"),
            ("rack", "sata", "present"),
            ("rack", "label"),
        ]
        assert [[node.index for node in line.folded] for line in tree.lines] == [[1], [3], [], []]
        assert tree.lines[1].nodes == (tree.rows[2], tree.rows[3])
        # Either node of a folded pair leads to the line that draws it.
        assert tree.line(2) is tree.line(3) is tree.lines[1]
        for index in (-1, 6):
            with pytest.raises(IndexError, match="tree row"):
                tree.line(index)
        assert path.read_bytes() == original

    @pytest.mark.parametrize(
        ("component", "reason"),
        [
            (_suite("wifi", _node("checks", type="sequence")), "a root named for itself"),
            (_suite("wifi", _node("Wifi", type="sequence")), "a name that differs in case"),
            (
                _suite("wifi", _node("wifi", type="sequence", state=None), state="Fail"),
                "a component that failed before its root ran",
            ),
            (
                _suite("wifi", _node("wifi", type="sequence", state="Aborted"), state="Fail"),
                "a pair that settled differently",
            ),
            (_suite("wifi", _node("wifi"), _node("other")), "a child that is not the only one"),
            (
                _node("wifi", type="sequence", children=[_node("wifi")]),
                "a composition, which has no root of its own",
            ),
        ],
    )
    async def test_every_other_pair_keeps_both_rows(
        self, tmp_path: Path, component: JsonObject, reason: str
    ) -> None:
        tree = await TreeReader().read(_save(tmp_path, _suite("rack", component)))
        assert isinstance(tree, InspectorTree)

        assert len(tree.lines) == len(tree.rows), reason
        assert all(line.folded == () for line in tree.lines), reason
        assert [line.depth for line in tree.lines] == [len(r.ancestor_names) - 1 for r in tree.rows]

    async def test_a_chain_of_same_named_components_folds_onto_one_line(
        self, tmp_path: Path
    ) -> None:
        # A component whose root is another component of the same name, which has its
        # own same-named root. Pending nodes agree with each other and fold as well.
        root = _suite(
            "rack",
            _suite("dut", _suite("dut", _node("dut", type="sequence", children=[_node("claim")]))),
            _suite("spare", _node("spare", type="sequence", state=None), state=None),
        )
        tree = await TreeReader().read(_save(tmp_path, root))
        assert isinstance(tree, InspectorTree)

        assert _drawn(tree) == [
            (0, "rack", 0, None),
            (1, "dut", 1, 0),
            (4, "claim", 2, 1),
            (5, "spare", 1, 0),
        ]
        assert [node.index for node in tree.lines[1].folded] == [2, 3]
        assert tree.lines[3].state is None

    async def test_the_cursor_and_the_failure_list_move_over_drawn_lines(
        self, tmp_path: Path
    ) -> None:
        failing = _suite(
            "nvme",
            _node("nvme", type="sequence", state="Fail", children=[_node("spins", state="Fail")]),
            state="Fail",
        )
        root = _suite(
            "rack",
            _suite("sata", _node("sata", type="sequence", children=[_node("present")])),
            failing,
            state="Fail",
        )
        tree = await TreeReader().read(_save(tmp_path, root))
        assert isinstance(tree, InspectorTree)

        # rack 0; sata 1+2, present 3; nvme 4+5, spins 6.
        assert [line.index for line in tree.lines] == [0, 1, 3, 4, 6]
        assert [row.index for row in tree.failures] == [0, 4, 6]
        visited: list[int] = []
        for _ in range(5):
            tree.move(1)
            visited.append(tree.selected.index)
        assert visited == [1, 3, 4, 6, 6]
        tree.move(-2)
        assert tree.selected.index == 3
        tree.next_failure()
        assert tree.selected.index == 4
        tree.next_failure()
        assert tree.selected.index == 6
        tree.previous_failure()
        assert tree.selected.index == 4
        # The record stays addressable: a folded node is selected by its own index, and
        # the cursor then moves on from the line that draws it.
        tree.select(5)
        assert tree.selected is tree.rows[5]
        tree.move(1)
        assert tree.selected.index == 6

    async def test_a_folded_line_names_the_readings_of_every_node_it_draws(
        self, tmp_path: Path
    ) -> None:
        # A component whose root is a single claim of the same name.
        claim = _node("probe", evidence_declared=["volts", "amps"], evidence={"volts": 3.3})
        tree = await TreeReader().read(_save(tmp_path, _suite("rack", _suite("probe", claim))))
        assert isinstance(tree, InspectorTree)

        (rack, probe) = tree.lines
        assert isinstance(probe, TreeLine)
        assert (probe.index, [node.index for node in probe.folded]) == (1, [2])
        assert probe.row.not_taken == ()
        assert probe.not_taken == ("amps",)
        assert rack.not_taken == ()

    async def test_the_recorded_bay_folds_each_component_that_settled_with_its_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        saved = await record_bay_run(tmp_path, monkeypatch)
        tree = saved.tree

        assert len(tree.rows) == 13
        assert [(line.name, line.depth, line.state) for line in tree.lines] == [
            ("bay", 0, "Fail"),
            ("sata", 1, "Pass"),
            ("present", 2, "Pass"),
            ("spins", 2, "Pass"),
            ("nvme", 1, "Fail"),
            ("present", 2, "Pass"),
            ("spins", 2, "Fail"),
            ("fan", 1, "Pass"),
            ("checks", 2, "Pass"),
            ("turns", 3, "Pass"),
        ]
        folded = [line.name for line in tree.lines if line.folded]
        assert folded == ["bay", "sata", "nvme"]
        assert {str(node.record["type"]) for line in tree.lines for node in line.folded} == {
            "parallel",
            "sequence",
        }
