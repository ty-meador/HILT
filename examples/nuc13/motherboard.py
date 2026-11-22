"""The motherboard: the claims that belong to the board itself.

The board is one component of the product among the others. It judges only what is
its own: that it is the board the record says it is. The parts mounted on it
(firmware, processor, memory, drive, buses) are not composed here; the MPN composes
them beside the board, in ``nuc13/mpn/__init__.py``.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from nuc13.probe import read_text
from nuc13.spec import BoardSpec

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence


class BoardIdentity(TypedDict):
    """What the board must report, keyed by the DMI field that reports it."""

    board_vendor: str
    board_name: str
    board_version: str


# The DMI fields this class judges; each is also the ctx key holding its expectation.
type BoardField = Literal["board_vendor", "board_name", "board_version"]


class Motherboard(TestComponent[BoardIdentity]):
    """Judge that the board is the one the record names."""

    def __init__(self, *, name: str, spec: BoardSpec) -> None:
        """Hold the board's identity.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: What the board must report about itself.
        """
        super().__init__(name=name)
        self._spec = spec

    async def configure(self, utils: TestUtils[BoardIdentity]) -> BoardIdentity:
        """Provide the identity the claims judge against."""
        return {
            "board_vendor": self._spec.vendor,
            "board_name": self._spec.product,
            "board_version": self._spec.version,
        }

    def build_suite(self) -> TestComponent:
        """Three independent readings, so a wrong vendor and a wrong version both show."""
        return self.parallel(
            self.name,
            [
                self.test("vendor", self._dmi_field, field="board_vendor"),
                self.test("product", self._dmi_field, field="board_name"),
                self.test("version", self._dmi_field, field="board_version"),
            ],
        )

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[BoardIdentity], *, field: BoardField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, "(not read)")
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)
