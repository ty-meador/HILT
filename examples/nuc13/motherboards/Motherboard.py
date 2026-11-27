"""The motherboard: the board's own identity, then everything built onto it.

    motherboard
    ├─ vendor, product, version    what the board reports about itself
    ├─ pci                         every function on its bus
    ├─ usb                         its root hubs, and what is wired to them
    └─ network                     its interfaces

The parts fitted to the board (processor, memory, drive, firmware) are not composed
here: each is a line of its own on the bill of materials, and the product composes
them beside the board. What the board does say about them is where they go. Its
memory and drive slots are part of its identity, and the product hands each one to
the part the bill of materials puts in it.

A board part number is one class, in a module of its own beside this one; it says
what that board is made of. The package's ``mpns`` lists them.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict, Unpack

from nuc13.kernel import read_text
from nuc13.network.Network import Network
from nuc13.pci.PciBus import PciBus
from nuc13.usb.Usb import Usb

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

# The DMI fields this class judges; each is also the ctx key holding its expectation.
type BoardField = Literal["board_vendor", "board_name", "board_version"]


class SodimmSlot(TypedDict):
    """Where a memory module sits on the board, as the kernel names it.

    Attributes:
        slot: The slot's label, such as ``Controller0-ChannelA-DIMM0``.
        edac_directory: The kernel's directory for the position, such as
            ``/sys/devices/system/edac/mc/mc0/dimm0``. EDAC numbers a controller's
            positions channel by channel and lists only the populated ones.
        location: The position as that directory words it, such as
            ``channel 0 slot 0``.
        eeprom: Shell pattern that resolves to the sysfs file of the identity EEPROM
            of a module in this slot, or ``None`` where the kernel exposes none to an
            ordinary user.
    """

    slot: str
    edac_directory: str
    location: str
    eeprom: str | None


class NvmeSlot(TypedDict):
    """Where a drive sits on the board, as the kernel names it.

    Attributes:
        controller: Kernel name of the controller in the slot, such as ``nvme0``.
        namespace: Kernel block device of its namespace, such as ``nvme0n1``.
        pci_address: The PCI function the slot is wired to.
    """

    controller: str
    namespace: str
    pci_address: str


class BoardIdentity(TypedDict):
    """What the board must report, keyed by the DMI field that reports it.

    Attributes:
        board_vendor: Who makes the board.
        board_name: The board's part number.
        board_version: Its revision.
    """

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
        sodimm_slots: The board's memory slots, in the order modules are fitted.
        nvme_slot: The board's M.2 drive slot.
        **identity: What the board must report about itself.
    """

    def __init__(
        self,
        *,
        name: str,
        pci: PciBus,
        usb: Usb,
        network: Network,
        sodimm_slots: Sequence[SodimmSlot],
        nvme_slot: NvmeSlot,
        **identity: Unpack[BoardIdentity],
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._pci = pci
        self._usb = usb
        self._network = network
        self._sodimm_slots = tuple(sodimm_slots)
        self._nvme_slot = nvme_slot

    @property
    def sodimm_slots(self) -> tuple[SodimmSlot, ...]:
        """Where memory modules go, in the order they are fitted."""
        return self._sodimm_slots

    @property
    def nvme_slot(self) -> NvmeSlot:
        """Where the drive goes."""
        return self._nvme_slot

    async def configure(self, utils: TestUtils[BoardIdentity]) -> BoardIdentity:
        """Provide the identity the board's own claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """All independent: a wrong board revision never hides a missing function."""
        return self.parallel(
            self.name,
            [
                self.test("vendor", self._dmi_field, field="board_vendor"),
                self.test("product", self._dmi_field, field="board_name"),
                self.test("version", self._dmi_field, field="board_version"),
                self._pci,
                self._usb,
                self._network,
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
