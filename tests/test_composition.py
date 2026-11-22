"""Composition factories and the serial semantics they expose to authored suites."""

from collections.abc import Callable, Iterator
from typing import cast

import pytest

from hilt import (
    PASS,
    ContractViolation,
    FrameworkContractViolation,
    TestComponent,
    TestFailure,
    TestUtils,
    Verdict,
)
from hilt.base import declared_tree
from hilt.lifecycle import AuditSignal, Outcome, RunAbort, Slot, State, TimeBudgetClock


class _NonTerminalChild:
    async def _run_lifecycle(
        self,
        _utils: TestUtils,
        _run_abort: RunAbort,
        *,
        audit_signal: AuditSignal | None = None,
        time_budget_clocks: tuple[TimeBudgetClock, ...] = (),
        subtree: tuple[int, ...] | None = None,
    ) -> Outcome:
        return Outcome(Slot.CONFIGURE, "corrupt child", None)


class _InconsistentChildren:
    def __iter__(self) -> Iterator[TestComponent]:
        return iter(())

    def __len__(self) -> int:
        return 1


class TestCompositionFactories:
    async def test_test_sequence_and_parallel_compose_with_bound_methods(self) -> None:
        calls: list[tuple[str, TestComponent]] = []

        class Suite(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.sequence(
                    self.name,
                    [
                        self.test("first claim", self._first_claim),
                        self.parallel(
                            "independent claims",
                            [self.test("second claim", self._second_claim)],
                        ),
                    ],
                )

            async def _first_claim(self, utils: object) -> Verdict:
                calls.append(("first", self))
                return PASS

            async def _second_claim(self, utils: object) -> Verdict:
                calls.append(("second", self))
                return PASS

        suite = Suite(name="rack")

        outcome = await suite.main(TestUtils())

        assert outcome.state is State.PASS
        assert calls == [("first", suite), ("second", suite)]

    def test_composites_require_at_least_one_child(self) -> None:
        suite = TestComponent(name="rack")

        with pytest.raises(ContractViolation, match=r"sequence.*at least one child"):
            suite.sequence("sequence", [])
        with pytest.raises(ContractViolation, match=r"parallel node.*at least one child"):
            suite.parallel("parallel", [])

    async def test_leaves_enumerate_at_runtime_by_passing_the_varying_argument(self) -> None:
        stressed: list[int] = []

        class Compute(TestComponent):
            def __init__(self, *, name: str, cpu_count: int) -> None:
                super().__init__(name=name)
                self._cpu_count = cpu_count

            def build_suite(self) -> TestComponent:
                return self.parallel(
                    "stress",
                    [
                        self.test(f"cpu{i}_stress", self._cpu_stress, cpu=i)
                        for i in range(self._cpu_count)
                    ],
                )

            async def _cpu_stress(self, utils: object, *, cpu: int) -> Verdict:
                stressed.append(cpu)
                return PASS

        outcome = await Compute(name="compute", cpu_count=3).main(TestUtils())

        assert outcome.state is State.PASS
        leaves = outcome.summary_tree.children[0].children
        assert [leaf.name for leaf in leaves] == ["cpu0_stress", "cpu1_stress", "cpu2_stress"]
        assert all(leaf.state is State.PASS for leaf in leaves)
        assert sorted(stressed) == [0, 1, 2]

    async def test_positional_arguments_reach_the_method_after_utils(self) -> None:
        seen: list[tuple[str, float]] = []

        class Rails(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.sequence(
                    "rails",
                    [
                        self.test("v12", self._rail, "12V", 0.5, fail_after=5.0),
                        self.test("v5", self._rail, "5V", limit=0.25),
                    ],
                )

            async def _rail(self, utils: object, rail: str, limit: float) -> Verdict:
                seen.append((rail, limit))
                return PASS

        outcome = await Rails(name="psu").main(TestUtils())

        assert outcome.state is State.PASS
        assert seen == [("12V", 0.5), ("5V", 0.25)]

    def test_arguments_that_do_not_fit_the_method_fail_at_composition(self) -> None:
        suite = TestComponent(name="rack")

        async def probe(_utils: object, *, cpu: int) -> Verdict:
            del cpu
            return PASS

        with pytest.raises(
            ContractViolation, match=r"missing a required keyword-only argument: 'cpu'"
        ):
            suite.test("no cpu", probe)
        with pytest.raises(ContractViolation, match=r"unexpected keyword argument 'core'"):
            suite.test("wrong name", probe, cpu=0, core=0)
        with pytest.raises(ContractViolation, match=r"too many positional arguments"):
            suite.test("extra", probe, 1, 2)

    def test_budgets_are_the_leaf_s_and_never_reach_the_method(self) -> None:
        suite = TestComponent(name="rack")

        async def clashes(_utils: object, *, fail_after: float) -> Verdict:
            del fail_after
            return PASS

        with pytest.raises(
            ContractViolation, match=r"missing a required keyword-only argument: 'fail_after'"
        ):
            suite.test("clash", clashes, fail_after=1.0)

    def test_test_rejects_a_function_that_is_not_a_coroutine_function(self) -> None:
        suite = TestComponent(name="rack")

        def plain(_utils: object) -> Verdict:
            return PASS

        with pytest.raises(TypeError, match="must be defined with async def"):
            suite.test("plain", plain)  # pyright: ignore[reportArgumentType]
        with pytest.raises(TypeError, match="must be defined with async def"):
            suite.test("lambda", lambda _utils: PASS)  # pyright: ignore[reportArgumentType, reportUnknownLambdaType]

    def test_composed_nodes_own_a_timeline_unless_rolled_up(self) -> None:
        suite = TestComponent(name="rack")

        async def claim(_utils: object) -> Verdict:
            return PASS

        child = suite.test("claim", claim)

        # Rolling a node's records into its parent is the opt-in; the default is a
        # timeline of its own, which is what the FT opens for that node.
        assert suite.parallel("parallel", [child]).discrete_log_file is True
        assert suite.sequence("sequence", [child]).discrete_log_file is True
        rolled = suite.parallel("rolled", [child], discrete_log_file=False)
        assert rolled.discrete_log_file is False

    async def test_internal_nodes_report_direct_build_as_framework_bugs(self) -> None:
        async def passes(_utils: object) -> Verdict:
            return PASS

        suite = TestComponent(name="rack")
        leaf = suite.test("leaf", passes)
        escalate = "report it to the framework maintainers with the full log and reproduction steps"
        nodes = (
            (leaf, f"an internal test has no child suite; {escalate}"),
            (
                suite.parallel("parallel", [leaf]),
                f"an internal parallel node has no child suite; {escalate}",
            ),
            (
                suite.sequence("sequence", [leaf]),
                f"an internal sequence has no child suite; {escalate}",
            ),
        )

        for node, detail in nodes:
            with pytest.raises(FrameworkContractViolation) as raised:
                node.build_suite()
            assert raised.value.node == node.name
            assert raised.value.detail == detail

    async def test_sequence_reports_a_nonterminal_child_as_a_framework_bug(self) -> None:
        sequence = TestComponent(name="dependent claims")
        child = cast("TestComponent", _NonTerminalChild())

        with pytest.raises(FrameworkContractViolation) as raised:
            await sequence._run_sequence(TestUtils(), RunAbort(), (child,))

        assert raised.value.node == sequence.name
        assert raised.value.detail == (
            "a sequence child settled without a terminal state; "
            "report it to the framework maintainers with the full log and reproduction steps"
        )

    async def test_sequence_reports_an_incomplete_pass_check_as_a_framework_bug(
        self,
    ) -> None:
        sequence = TestComponent(name="dependent claims")
        children = cast("tuple[TestComponent, ...]", _InconsistentChildren())

        with pytest.raises(FrameworkContractViolation) as raised:
            await sequence._run_sequence(TestUtils(), RunAbort(), children)

        assert raised.value.node == sequence.name
        assert raised.value.detail == (
            "a sequence reached no explicit terminal verdict; "
            "report it to the framework maintainers with the full log and reproduction steps"
        )


class TestSequenceExecution:
    async def test_sequence_bails_at_the_first_failing_child(self) -> None:
        calls: list[str] = []

        class Suite(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.sequence(
                    "dependent claims",
                    [
                        self.test("first claim", self._first_claim),
                        self.test("failing claim", self._failing_claim),
                        self.test("unreachable claim", self._unreachable_claim),
                    ],
                )

            async def _first_claim(self, utils: object) -> Verdict:
                calls.append("first")
                return PASS

            async def _failing_claim(self, utils: object) -> Verdict:
                calls.append("failing")
                raise TestFailure("dependent precondition failed")

            async def _unreachable_claim(self, utils: object) -> Verdict:
                calls.append("unreachable")
                return PASS

        outcome = await Suite(name="rack").main(TestUtils())
        children = cast("tuple[Outcome, ...]", outcome.value)

        assert calls == ["first", "failing"]
        assert outcome.state is State.FAIL
        assert [child.state for child in children] == [State.PASS, State.FAIL]

    async def test_sequence_passes_only_after_every_child_passes(self) -> None:
        calls: list[str] = []

        class Suite(TestComponent):
            def build_suite(self) -> TestComponent:
                return self.sequence(
                    "dependent claims",
                    [
                        self.test("first claim", self._first_claim),
                        self.test("second claim", self._second_claim),
                    ],
                )

            async def _first_claim(self, utils: object) -> Verdict:
                calls.append("first")
                return PASS

            async def _second_claim(self, utils: object) -> Verdict:
                calls.append("second")
                return PASS

        outcome = await Suite(name="rack").main(TestUtils())
        children = cast("tuple[Outcome, ...]", outcome.value)

        assert calls == ["first", "second"]
        assert outcome.state is State.PASS
        assert len(children) == 2
        assert all(child.state is State.PASS for child in children)


class _Board(TestComponent):
    """A component whose root takes its name, with a named level beneath it."""

    def build_suite(self) -> TestComponent:
        return self.sequence(
            [
                self.test("powers", self._claim),
                self.parallel("rails", [self.test("3v3", self._claim)]),
            ]
        )

    async def _claim(self, utils: TestUtils) -> Verdict:
        return PASS


class TestNamelessRoot:
    """The root of ``build_suite()`` need not repeat the component's name (issue #165)."""

    @pytest.mark.parametrize("kind", ["sequence", "parallel"])
    async def test_a_nameless_composition_takes_its_component_s_name(self, kind: str) -> None:
        class Suite(_Board):
            def build_suite(self) -> TestComponent:
                compose = self.sequence if kind == "sequence" else self.parallel
                return compose([self.test("powers", self._claim)])

        suite = Suite(name="board A")

        declared = declared_tree(suite)
        (authored,) = declared.children
        assert (declared.name, declared.type.value) == ("board A", "suite")
        assert (authored.name, authored.type.value) == ("board A", kind)
        assert (await suite.main(TestUtils())).state is State.PASS

    async def test_the_named_form_is_unchanged_and_may_sit_under_a_nameless_root(self) -> None:
        suite = _Board(name="board")

        (authored,) = declared_tree(suite).children
        assert [child.name for child in authored.children] == ["powers", "rails"]
        assert authored.name == "board"
        assert _Board(name="x").sequence("named", [suite]).name == "named"
        assert (await suite.main(TestUtils())).state is State.PASS

    def test_a_nameless_composition_keeps_its_keyword_options(self) -> None:
        board = _Board(name="board")
        child = board.test("powers", board._claim)  # pyright: ignore[reportPrivateUsage]

        rolled = board.sequence([child], discrete_log_file=False, fail_after=2.0)
        owned = board.parallel([child], abort_after=3.0)

        assert (rolled.name, owned.name) == ("board", "board")
        assert rolled._discrete_log_file is False  # pyright: ignore[reportPrivateUsage]
        assert owned._discrete_log_file is True  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(ContractViolation, match="at least one child"):
            board.sequence([])

    @pytest.mark.parametrize("outer", ["sequence", "parallel"])
    @pytest.mark.parametrize("inner", ["sequence", "parallel"])
    def test_a_nameless_composition_nested_deeper_is_refused_where_it_is_nested(
        self, outer: str, inner: str
    ) -> None:
        board = _Board(name="board")
        child = board.test("powers", board._claim)  # pyright: ignore[reportPrivateUsage]
        nameless = (board.sequence if inner == "sequence" else board.parallel)([child])
        compose = board.sequence if outer == "sequence" else board.parallel

        with pytest.raises(ContractViolation) as named:
            compose("stage", [child, nameless])
        with pytest.raises(ContractViolation) as rooted:
            compose([nameless])

        for refusal, owner in ((named, "stage"), (rooted, "board")):
            message = str(refusal.value)
            assert f"nested under {owner!r}" in message
            assert "only the root of build_suite() may omit its name" in message
            assert "self.sequence(name, children)" in message

    @pytest.mark.parametrize("kind", ["sequence", "parallel"])
    def test_a_call_that_is_neither_form_names_both(self, kind: str) -> None:
        board = _Board(name="board")
        child = board.test("powers", board._claim)  # pyright: ignore[reportPrivateUsage]
        compose = cast(Callable[..., TestComponent], getattr(board, kind))
        forms = (
            f"call self.{kind}(children) at the root of build_suite(), "
            f"or self.{kind}(name, children) for a named level"
        )

        with pytest.raises(ContractViolation) as lone_name:
            compose("stage")
        with pytest.raises(ContractViolation) as two_lists:
            compose([child], [child])

        assert f"self.{kind}() was given a name and no children; {forms}" in str(lone_name.value)
        assert "IN stage" in str(lone_name.value)
        assert f"self.{kind}() was given two child lists and no name; {forms}" in str(
            two_lists.value
        )
        assert "IN board" in str(two_lists.value)
