"""The nuc13 product: one unit at the station, judged against its bill of materials.

It reads the way the line works, top down:

- The shopfloor says what the unit was built from: the part number of every part.
  ``shopfloor_bom.json`` stands in for that answer.
- A part number the suite knows is a class, in a module of its own inside the
  package of the component it is a part number of. Each package lists its part
  numbers in ``mpns``, and that is where the product looks a line up.
- ``Nuc13`` is the product. It reaches the unit, turns each line of the bill of
  materials into its part, and judges them all:

      nuc13
      ├─ identity      the system part number the unit reports
      ├─ motherboard   → pci, usb, network
      ├─ firmware
      ├─ cpu           → one node per core, its caches inside it
      ├─ memory        → one node per module
      └─ drive         → partitions, one node per volume

- A system part number is a subclass of ``Nuc13``, in a module of its own beside
  this one, and that module is the suite to run. It says what the part number
  reports about itself, how many of each component it is built from, and how its
  drive leaves the line partitioned.

Nothing below the root knows the unit is remote, and nothing in the tree names one
unit. Which unit is the station's to say, never the suite's: the target comes from
the run's environment, so it is published on the run's record:

- ``DUT_HOST``: address or name of the unit (required)
- ``DUT_USER``: login on the unit (required)
- ``DUT_PORT``: SSH port (default 22)

Authentication is the transport's own: the agent, then the user's default keys.

The suite only reads: it runs as an ordinary user and changes nothing on the unit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, TypedDict, Unpack

from nuc13 import Bom, cpus, drives, firmwares, memory, motherboards, read_bom
from nuc13.drives.NvmeDrive import PartitionTable
from nuc13.kernel import read_text
from nuc13.memory.Memory import Memory
from nuc13.utils import mpn_to_class

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

_SHOPFLOOR_BOM = Path(__file__).with_name("shopfloor_bom.json")

# The suite fans out wide under the one hop to the unit: a claim per core, per cache,
# per PCI function. The unit's sshd grants 10 sessions per connection (its default)
# and refuses the rest, so the hop runs at most this many commands at once and the
# others take turns. Two sessions are left free for whatever else rides the
# connection.
_MAX_SESSIONS = 8

# The DMI fields the product judges; each is also the ctx key holding its expectation.
type SystemField = Literal[
    "sys_vendor",
    "product_name",
    "product_version",
    "product_sku",
    "product_family",
    "chassis_type",
]


class Nuc13Identity(TypedDict):
    """What a unit must report about itself, keyed by the DMI field that reports it.

    Nothing here describes one unit: a serial number or a hostname is the unit's, not
    the part number's, and both are left alone.

    Attributes:
        sys_vendor: Who makes the system.
        product_name: The model the part number belongs to.
        product_version: The model's revision.
        product_sku: The system part number, which is where a unit reports it.
        product_family: The product family.
        chassis_type: The SMBIOS chassis code, as the decimal text sysfs prints.
    """

    sys_vendor: str
    product_name: str
    product_version: str
    product_sku: str
    product_family: str
    chassis_type: str


class PartCounts(TypedDict):
    """How many of each component a system part number is built from.

    Keyed by component type, as the bill of materials names them. This is what a
    unit's bill of materials must list. Whether the unit itself has that many is not
    judged from here: that is each component's own claim, such as memory's
    ``populated-banks``.

    Attributes:
        motherboard: Boards.
        firmware: BIOS releases flashed.
        cpu: Processors.
        memory: Memory modules.
        drive: Drives.
    """

    motherboard: int
    firmware: int
    cpu: int
    memory: int
    drive: int


class Nuc13(TestComponent[Nuc13Identity]):
    """The product: reach the unit, then judge what it says it is and every part in it.

    The parts come from the unit's bill of materials. What the unit must report about
    itself, how many of each component it is built from, and how its drive is
    partitioned come from the system part number, which is a subclass.

    Args:
        name: Root node name used by logs, artifacts, and failures.
        part_counts: How many of each component the part number is built from. The
            product composes one board, firmware, processor, and drive; memory is
            the count that varies between part numbers.
        partitions: How the unit's drive leaves the line partitioned.
        **identity: What the unit must report about itself.

    Raises:
        ValueError: The bill of materials is malformed, is for another system part
            number, does not list the number of each component the part number is
            built from, names a part this suite has no class for, or calls for more
            memory modules than the board has slots.
    """

    def __init__(
        self,
        *,
        name: str,
        part_counts: PartCounts,
        partitions: PartitionTable,
        **identity: Unpack[Nuc13Identity],
    ) -> None:
        super().__init__(name=name)
        self._identity = identity

        bom = read_bom(_SHOPFLOOR_BOM)
        if bom.system != identity["product_sku"]:
            raise ValueError(
                f"the bill of materials is for system {bom.system!r}; "
                f"this suite judges {identity['product_sku']!r}"
            )
        self._require_part_counts(bom, part_counts)
        (board_mpn,) = bom.parts["motherboard"]
        (firmware_mpn,) = bom.parts["firmware"]
        (cpu_mpn,) = bom.parts["cpu"]
        (drive_mpn,) = bom.parts["drive"]
        memory_mpns = bom.parts["memory"]

        board = mpn_to_class(motherboards.mpns, board_mpn)(name="motherboard")
        # The board says where its slots are; the bill of materials says what is in them.
        slots = board.sodimm_slots
        if len(memory_mpns) > len(slots):
            raise ValueError(
                f"system {bom.system!r} is built with {len(memory_mpns)} memory modules; "
                f"board {board_mpn!r} has {len(slots)} slots"
            )
        banks = [
            mpn_to_class(memory.mpns, mpn)(name=f"bank-{index}", **slot)
            for index, (mpn, slot) in enumerate(zip(memory_mpns, slots, strict=False))
        ]
        self._parts: tuple[TestComponent[Any], ...] = (
            board,
            mpn_to_class(firmwares.mpns, firmware_mpn)(name="firmware"),
            mpn_to_class(cpus.mpns, cpu_mpn)(name="cpu"),
            Memory(name="memory", banks=banks),
            mpn_to_class(drives.mpns, drive_mpn)(
                name="drive", partitions=partitions, **board.nvme_slot
            ),
        )

    @staticmethod
    def _require_part_counts(bom: Bom, part_counts: PartCounts) -> None:
        """Hold the bill of materials to what the system part number is built from.

        Raises:
            ValueError: A component type is listed a different number of times than
                the part number is built with, which includes a type it does not have
                at all. The message names every such type.
        """
        expected = dict(part_counts.items())
        listed = {component: len(part_numbers) for component, part_numbers in bom.parts.items()}
        mismatches = [
            f"{component}: {listed.get(component, 0)} listed, {expected.get(component, 0)} expected"
            for component in sorted(expected.keys() | listed.keys())
            if listed.get(component, 0) != expected.get(component, 0)
        ]
        if mismatches:
            raise ValueError(
                f"the bill of materials does not fit system {bom.system!r}: "
                + "; ".join(mismatches)
            )

    async def configure(self, utils: TestUtils[Nuc13Identity]) -> Nuc13Identity:
        """Open the hop to the unit, then provide the identity its own claims judge against.

        An unreachable unit is a Fail here, before any claim.
        """
        host = utils.env.require("DUT_HOST")
        username = utils.env.require("DUT_USER")
        port = utils.env.port("DUT_PORT")
        try:
            await utils.use_shell(
                host=host, username=username, port=port, max_sessions=_MAX_SESSIONS
            )
        except Exception as error:
            raise TestFailure(f"SSH connect to {username}@{host}:{port} failed: {error}") from error
        return self._identity

    def build_suite(self) -> TestComponent:
        """What the unit says it is and every part in it are judged on their own.

        Nothing here gates anything else, so everything runs in parallel: a wrong
        system part number never hides a missing module.
        """
        return self.parallel(
            [
                self.parallel(
                    "identity",
                    [
                        self.test("vendor", self._dmi_field, field="sys_vendor"),
                        self.test("product-name", self._dmi_field, field="product_name"),
                        self.test("version", self._dmi_field, field="product_version"),
                        self.test("mpn", self._dmi_field, field="product_sku"),
                        self.test("family", self._dmi_field, field="product_family"),
                        self.test("chassis", self._dmi_field, field="chassis_type"),
                    ],
                    discrete_log_file=False,
                ),
                *self._parts,
            ]
        )

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[Nuc13Identity], *, field: SystemField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, "(not read)")
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)
