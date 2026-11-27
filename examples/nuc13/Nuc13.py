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
  drive leaves the line partitioned. The ``nuc13`` package lists the system part
  numbers in its own ``mpns``, where a harness looks a unit's system line up.

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
from typing import Literal, TypedDict, Unpack

from nuc13 import cpus, drives, firmwares, memory, motherboards
from nuc13.Bom import Bom
from nuc13.drives.NvmeDrive import PartitionTable
from nuc13.kernel import NOT_READ, read_text
from nuc13.memory.Memory import Memory
from nuc13.utils import mpn_to_class

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

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

    The product has one board, and the board one firmware, one processor socket, and
    one M.2 slot, so those counts can only be 1. Memory is the count that varies
    between part numbers.

    Attributes:
        motherboard: Boards.
        firmware: BIOS releases flashed.
        cpu: Processors.
        memory: Memory modules.
        drive: Drives.
    """

    motherboard: Literal[1]
    firmware: Literal[1]
    cpu: Literal[1]
    memory: int
    drive: Literal[1]


class Nuc13(TestComponent[Nuc13Identity]):
    """The product: reach the unit, then judge what it says it is and every part in it.

    The parts come from the unit's bill of materials, which ``build_suite`` reads.
    What the unit must report about itself, how many of each component it is built
    from, and how its drive is partitioned come from the system part number, which is
    a subclass.

    Args:
        name: Root node name used by logs, artifacts, and failures.
        part_counts: How many of each component the part number is built from.
        partitions: How the unit's drive leaves the line partitioned.
        **identity: What the unit must report about itself.
    """

    _SHOPFLOOR_BOM = Path(__file__).with_name("shopfloor_bom.json")

    # The suite fans out wide under the one hop to the unit: a claim per core, per cache,
    # per PCI function. The unit's sshd grants 10 sessions per connection (its default)
    # and refuses the rest, so the hop runs at most this many commands at once and the
    # others take turns. Two sessions are left free for whatever else rides the
    # connection.
    _MAX_SESSIONS = 8

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
        self._part_counts = part_counts
        self._partitions = partitions

    async def configure(self, utils: TestUtils[Nuc13Identity]) -> Nuc13Identity:
        """Open the hop to the unit, then provide the identity its own claims judge against.

        An unreachable unit is a Fail here, before any claim.
        """
        host = utils.env.require("DUT_HOST")
        username = utils.env.require("DUT_USER")
        port = utils.env.port("DUT_PORT")
        try:
            await utils.use_shell(
                host=host, username=username, port=port, max_sessions=self._MAX_SESSIONS
            )
        except Exception as error:
            raise TestFailure(f"SSH connect to {username}@{host}:{port} failed: {error}") from error
        return self._identity

    def build_suite(self) -> TestComponent:
        """Read the unit's bill of materials and turn each line of it into its part.

        The board says where its slots are; the bill of materials says what is in
        them. Nothing here gates anything else, so everything runs in parallel: a
        wrong system part number never hides a missing module.

        Raises:
            ValueError: The bill of materials is malformed, is for another system
                part number, does not list the number of each component the part
                number is built from, names a part this suite has no class for, or
                calls for more memory modules than the board has slots.
        """
        bom = self._read_bom()
        board = mpn_to_class(motherboards.mpns, bom.parts["motherboard"][0])(name="motherboard")
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
                board,
                mpn_to_class(firmwares.mpns, bom.parts["firmware"][0])(name="firmware"),
                mpn_to_class(cpus.mpns, bom.parts["cpu"][0])(name="cpu"),
                Memory(
                    name="memory",
                    sodimms=[
                        mpn_to_class(memory.mpns, mpn)(name=f"bank-{index}", **slot)
                        for index, (mpn, slot) in enumerate(
                            zip(
                                bom.parts["memory"],
                                board.sodimm_slots_for(len(bom.parts["memory"])),
                                strict=True,
                            )
                        )
                    ],
                ),
                mpn_to_class(drives.mpns, bom.parts["drive"][0])(
                    name="drive", partitions=self._partitions, **board.nvme_slot
                ),
            ]
        )

    def _read_bom(self) -> Bom:
        """Read the unit's bill of materials and hold it to the system part number.

        Raises:
            ValueError: The bill of materials is malformed, is for another system
                part number, or lists a component type a different number of times
                than the part number is built with, which includes a type it does
                not have at all. The message names every such type.
        """
        bom = Bom.read(self._SHOPFLOOR_BOM)
        if bom.system != self._identity["product_sku"]:
            raise ValueError(
                f"the bill of materials is for system {bom.system!r}; "
                f"this suite judges {self._identity['product_sku']!r}"
            )
        expected = dict(self._part_counts.items())
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
        return bom

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[Nuc13Identity], *, field: SystemField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, NOT_READ)
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)
