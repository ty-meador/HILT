"""The motherboard: the board's own identity, then everything built onto it.

    motherboard
    ├─ bom-check                   the unit's bill of materials lists this board
    ├─ vendor, product, version    what the board reports about itself
    ├─ pci                         every function on its bus
    ├─ usb                         its root hubs, and what is wired to them
    └─ network                     its interfaces

The parts fitted to the board (processor, memory, drive, firmware) are not composed
here: each is a line of its own on the bill of materials, and the product builds them
beside the board.

A board part number is one class, in a module of its own beside this one; it says
what that board is made of, and where its slots are. The slots are the class's own:
the product reads them off the class to fit a module or a drive, so no board has to
be built first. A fitted part never imports a slot type: it takes what a slot holds
as plain keyword arguments.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Protocol, TypedDict, Unpack

from nuc13.kernel import NOT_READ, read_text
from nuc13.network.Network import Network
from nuc13.pci.PciBus import PciBus
from nuc13.usb.Usb import Usb

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

# The DMI fields this class judges; each is also the ctx key holding its expectation.
type BoardField = Literal["board_vendor", "board_name", "board_version"]


class SodimmSlot(TypedDict):
    """One memory slot on a board, as the kernel shows whatever is fitted in it.

    Attributes:
        slot: The slot's label, such as ``Controller0-ChannelA-DIMM0``.
        edac_directory: The kernel's directory for the position.
        location: The position as that directory words it.
        eeprom: Shell pattern that resolves to the sysfs file of the identity EEPROM
            of a module in this slot, or ``None`` where the kernel exposes none to an
            ordinary user.
    """

    slot: str
    edac_directory: str
    location: str
    eeprom: str | None


class NvmeSlot(TypedDict):
    """One M.2 slot on a board, as the kernel shows a drive fitted in it.

    Attributes:
        controller: Kernel name of the controller, such as ``nvme0``.
        namespace: Kernel block device of its namespace, such as ``nvme0n1``.
        pci_address: The PCI function the slot is wired to.
    """

    controller: str
    namespace: str
    pci_address: str


class BoardIdentity(TypedDict):
    """What the board is, and what it must report, keyed by the DMI field that reports it.

    Attributes:
        part_number: The board as the bill of materials names it.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
        board_vendor: Who makes the board.
        board_name: The board's part number, as the board itself reports it.
        board_version: Its revision.
    """

    part_number: str
    bom_mpns: list[str]
    board_vendor: str
    board_name: str
    board_version: str


class Motherboard(TestComponent[BoardIdentity]):
    """Judge that the board is the one expected, then everything built onto it.

    Args:
        name: Node name used by logs, artifacts, and failures.
        pci: Every function on the board's PCI bus.
        usb: The board's USB root hubs and what is wired to them.
        network: The board's network interfaces.
        **identity: What the board is, and what it must report about itself.
    """

    def __init__(
        self,
        *,
        name: str,
        pci: PciBus,
        usb: Usb,
        network: Network,
        **identity: Unpack[BoardIdentity],
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._pci = pci
        self._usb = usb
        self._network = network

    async def configure(self, utils: TestUtils[BoardIdentity]) -> BoardIdentity:
        """Provide the identity the board's own claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """All independent: a wrong board revision never hides a missing function."""
        return self.parallel(
            self.name,
            [
                self.test("bom-check", self._bom_check),
                self.test("vendor", self._dmi_field, field="board_vendor"),
                self.test("product", self._dmi_field, field="board_name"),
                self.test("version", self._dmi_field, field="board_version"),
                self._pci,
                self._usb,
                self._network,
            ],
        )

    async def _bom_check(self, utils: TestUtils[BoardIdentity]) -> Verdict:
        expected = [utils.ctx["part_number"]]
        listed = utils.ctx["bom_mpns"]
        if listed == expected:
            return PASS
        raise TestFailure("the bill of materials does not list this board", expected, listed)

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[BoardIdentity], *, field: BoardField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, NOT_READ)
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)


class MotherboardPartNumber(Protocol):
    """A board part number's class: its slots, and how the product builds the board.

    Attributes:
        SODIMM_SLOTS: The board's memory slots, in the order modules are fitted.
        M2_SLOT: The board's M.2 slot.
    """

    # A type checker holds a class to these two tables. It does not hold the class's
    # constructor to ``__call__`` once a protocol has members beside it, so a board
    # class with another constructor is caught when the product builds it, not before.
    SODIMM_SLOTS: Sequence[SodimmSlot]
    M2_SLOT: NvmeSlot

    def __call__(self, *, name: str, bom_mpns: list[str]) -> Motherboard:
        """Build the board.

        Args:
            name: Node name used by logs, artifacts, and failures.
            bom_mpns: The part numbers the unit's bill of materials lists for this
                component.
        """
        ...
