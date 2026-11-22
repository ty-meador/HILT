"""Record one 70-DUT rack run and reopen it from saved bytes alone, for Inspector tests.

The Inspector's acceptance is a saved 70-DUT rack inspectable without live ``TestComponent``
objects, source discovery, or the station that produced it (``docs/inspector.md``).
Every Inspector test module proves that against the same shape of run; this helper is
the one place that shape is written, so a screen test and a model test open the same
evidence. Each device captures two ``survey`` artifacts, one carrying ``None`` and one
carrying the device name, so a comparison of the pair has a real difference to show.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hilt import PASS, Artifact, TestComponent, TestFailure, TestUtils, Verdict
from hilt.inspector_run import SavedRun, SavedRunReader

DEVICE_COUNT = 70


def device_row(index: int) -> int:
    """The tree row of device ``index``'s claim.

    Rows are declaration preorder: root, the ``devices`` parallel, then each device
    followed by its one ``voltage`` claim, so the claim of device ``i`` sits at
    ``3 + 2 * i``.
    """
    return 3 + 2 * index


class _Device(TestComponent):
    def build_suite(self) -> TestComponent:
        return self.test("voltage", self._voltage)

    async def _voltage(self, utils: TestUtils) -> Verdict:
        for value in [None, self.name]:
            await utils.capture_artifact(Artifact(name="reading", type="survey", data=value))
        if self.name:
            return PASS
        raise AssertionError("device name required")


class _Rack(TestComponent):
    def build_suite(self) -> TestComponent:
        return self.parallel("devices", [_Device(name=f"dut-{i:02d}") for i in range(DEVICE_COUNT)])


async def record_seventy_dut_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SavedRun:
    """Run the rack once, move its directory, forbid execution, and reopen the evidence.

    The move is what makes the test honest: every reference inside the run was written
    for the original location, and the reader must follow the recorded relocation
    evidence rather than the path that no longer exists. ``TestComponent.run`` is replaced
    so any attempt to reconstruct the suite fails loudly.
    """
    rack = _Rack(name="rack")
    await rack.run()
    assert rack.log_path is not None
    moved = tmp_path / "moved"
    rack.log_path.parent.parent.rename(moved)
    del rack

    def refuse_execution(*args: object, **kwargs: object) -> None:
        raise AssertionError("an Inspector test attempted suite execution")

    monkeypatch.setattr(TestComponent, "run", refuse_execution)
    saved = await SavedRunReader().read(moved / "result.json")
    assert isinstance(saved, SavedRun), saved
    return saved


def saved_bytes(run_root: Path) -> dict[Path, bytes]:
    """Every file under the run, for asserting that inspection changed nothing."""
    return {path: path.read_bytes() for path in run_root.rglob("*") if path.is_file()}


class _Mixed(TestComponent):
    """A small run with every recorded state, for failure navigation and the log pane.

    The sequence stops at the parallel's failure, so ``later`` is pending. Inside the
    parallel one claim fails and one is aborted by its time budget, so both kinds of
    attention are reachable. ``power`` passes after logging an error and capturing an
    artifact, so the log pane has a capture number and an errors-only row to show.
    """

    def build_suite(self) -> TestComponent:
        return self.sequence(
            "steps",
            [
                self.test("power", self._power),
                self.parallel(
                    "checks",
                    [
                        self.test("volts", self._volts),
                        self.test("slow", self._slow, abort_after=0.05),
                    ],
                ),
                self.test("later", self._later),
            ],
        )

    async def _power(self, utils: TestUtils) -> Verdict:
        await utils.capture_artifact(Artifact(name="rail", type="survey", data={"v": 12.1}))
        utils.logger.info("rail settled")
        utils.logger.error("ripple above expectation")
        if utils.exec_route == "local":
            return PASS
        raise AssertionError("the mixed run never leaves the test host")

    async def _volts(self, utils: TestUtils) -> Verdict:
        raise TestFailure("rail outside tolerance", "12.0 V", "12.1 V")

    async def _slow(self, utils: TestUtils) -> Verdict:
        await asyncio.sleep(10)
        return PASS

    async def _later(self, utils: TestUtils) -> Verdict:
        return PASS


MIXED_ROWS = ("rack", "steps", "power", "checks", "volts", "slow", "later")
"""The mixed run's preorder row names; ``volts`` is Fail, ``slow`` Aborted, ``later`` pending."""


async def record_mixed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, refuse_execution: bool = True
) -> SavedRun:
    """Record the mixed-state run, move it, forbid execution, and reopen it.

    A Re-Run test passes ``refuse_execution=False``: that tab is the one place the
    Inspector executes suite code by ruling, and its tests prove what it runs rather
    than that it runs nothing.
    """
    rack = _Mixed(name="rack")
    await rack.run()
    assert rack.log_path is not None
    moved = tmp_path / "mixed"
    rack.log_path.parent.parent.rename(moved)
    del rack

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an Inspector test attempted suite execution")

    if refuse_execution:
        monkeypatch.setattr(TestComponent, "run", refuse)
    saved = await SavedRunReader().read(moved / "result.json")
    assert isinstance(saved, SavedRun), saved
    return saved


def mixed_suite() -> TestComponent:
    """A fresh instance of the mixed suite, the shape ``record_mixed_run`` saved."""
    return _Mixed(name="rack")


def other_suite() -> TestComponent:
    """A fresh rack suite: a different declared tree, for the Re-Run refusal."""
    return _Rack(name="rack")


PARK: dict[str, asyncio.Event | None] = {"event": None}
"""Set ``PARK["event"]`` to make the parked suite wait; it passes at once otherwise."""


class _Parked(TestComponent):
    """Passes at once when recording; on re-run, parks until the operator interrupts."""

    def build_suite(self) -> TestComponent:
        return self.test("wait", self._wait)

    async def _wait(self, utils: TestUtils) -> Verdict:
        event = PARK["event"]
        if event is None:
            return PASS
        event.set()
        await asyncio.sleep(3600)
        return PASS


def parked_suite() -> TestComponent:
    """A fresh parked suite, the shape ``record_parked_run`` saved."""
    return _Parked(name="bench")


async def record_parked_run(tmp_path: Path) -> SavedRun:
    """Record the parked suite passing, move it, and reopen it for a live re-run test."""
    bench = _Parked(name="bench")
    await bench.run()
    assert bench.log_path is not None
    moved = tmp_path / "parked"
    bench.log_path.parent.parent.rename(moved)
    saved = await SavedRunReader().read(moved / "result.json")
    assert isinstance(saved, SavedRun), saved
    return saved


class _Drive(TestComponent):
    """One drive, authored the usual way: its root composition takes the drive's name.

    ``configure`` and ``cleanup`` log to the component's timeline and the claims to the
    root's, so the one line the Inspector draws for a drive has two files behind it.
    """

    def __init__(self, *, name: str, spins: bool = True) -> None:
        super().__init__(name=name)
        self._spins_up = spins

    async def configure(self, utils: TestUtils) -> dict[str, Any]:
        utils.logger.info("%s powered", self.name)
        return {}

    def build_suite(self) -> TestComponent:
        return self.sequence([self.test("present", self._present), self.test("spins", self._spins)])

    async def cleanup(self, utils: TestUtils) -> None:
        utils.logger.info("%s released", self.name)

    async def _present(self, utils: TestUtils) -> Verdict:
        utils.logger.info("%s answers", self.name)
        if utils.exec_route == "local":
            return PASS
        raise AssertionError("the bay run never leaves the test host")

    async def _spins(self, utils: TestUtils) -> Verdict:
        if self._spins_up:
            return PASS
        raise TestFailure("platters still", "7200 rpm", "0 rpm")


class _Fan(TestComponent):
    """A component whose root the author named for itself, a level they meant to show."""

    def build_suite(self) -> TestComponent:
        return self.sequence("checks", [self.test("turns", self._turns)])

    async def _turns(self, utils: TestUtils) -> Verdict:
        if utils.exec_route == "local":
            return PASS
        raise AssertionError("the bay run never leaves the test host")


class _Bay(TestComponent):
    """Components under a nameless root: the record doubles each name, the screen does not.

    ``sata`` passes and ``nvme`` fails a claim; each folds with its root, as the bay
    itself does. ``fan`` named its root ``checks``, a level of its own that stays drawn.
    """

    def build_suite(self) -> TestComponent:
        return self.parallel(
            [
                _Drive(name="sata"),
                _Drive(name="nvme", spins=False),
                _Fan(name="fan"),
            ]
        )


BAY_TREE = (
    "> bay  Fail",
    "  ├─ sata  Pass",
    "  │  ├─ present  Pass",
    "  │  └─ spins  Pass",
    "  ├─ nvme  Fail",
    "  │  ├─ present  Pass",
    "  │  └─ spins  Fail",
    "  └─ fan  Pass",
    "     └─ checks  Pass",
    "        └─ turns  Pass",
)
"""The bay run's Inspect tree with the root highlighted: thirteen saved nodes on ten lines."""


def bay_suite() -> TestComponent:
    """A fresh instance of the bay suite, the shape ``record_bay_run`` saved."""
    return _Bay(name="bay")


async def record_bay_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, refuse_execution: bool = True
) -> SavedRun:
    """Record the bay run, move it, forbid execution unless a Re-Run test asks, and reopen it."""
    bay = _Bay(name="bay")
    await bay.run()
    assert bay.log_path is not None
    moved = tmp_path / "bay"
    bay.log_path.parent.parent.rename(moved)
    del bay

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("an Inspector test attempted suite execution")

    if refuse_execution:
        monkeypatch.setattr(TestComponent, "run", refuse)
    saved = await SavedRunReader().read(moved / "result.json")
    assert isinstance(saved, SavedRun), saved
    return saved
