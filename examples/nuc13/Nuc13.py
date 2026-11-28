"""The nuc13 product: one unit at the station, judged part by part.

It reads the way the line works, top down:

- A system part number is a subclass of ``Nuc13``, in a module of its own beside
  this one, and that module is the suite to run. It says what the part number
  reports about itself and names the part number of every part it is built from. It
  builds none of them. The ``nuc13`` package lists the system part numbers in its
  ``mpns``, where a harness looks a unit's system line up.
- A part number the suite knows is a class, in a module of its own inside the
  package of the component it is a part number of.
- ``Nuc13`` is the product. It reaches the unit, builds every part where it lists it,
  and judges the unit and each of them:

      nuc13
      ├─ identity      the system part number the unit reports
      ├─ motherboard   → pci, usb, network
      ├─ firmware
      ├─ cpu           → one node per core, its caches inside it
      ├─ memory        → one node per module
      └─ drive         → partitions, one node per volume

- The shopfloor says what the unit was really built from: the part number of every
  part. ``shopfloor_bom.json`` stands in for that answer. It does not choose the
  parts; the system part number does. The product reads it once and hands each part
  its own line as it builds the part, and each holds that line to what it is in a
  ``bom-check`` claim of its own.
- A value goes to a part from whoever owns it, when the part is built: its line from
  the bill of materials, its slot from the board's class, the drive's layout from the
  system part number. A part number that already knows all of something builds it
  itself, as a board does its PCI functions.

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

from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict, Unpack

from nuc13.Bom import Bom
from nuc13.cpus.Cpu import Cpu
from nuc13.drives.NvmeDrive import DrivePartNumber, VolumeLayout
from nuc13.firmwares.Firmware import Firmware
from nuc13.kernel import NOT_READ, read_text
from nuc13.memory.Memory import Memory, SodimmPartNumber
from nuc13.motherboards.Motherboard import MotherboardPartNumber
from nuc13.PartNumber import PartNumber

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


class Nuc13(TestComponent[Nuc13Identity]):
    """The product: reach the unit, then judge what it says it is and every part in it.

    What the unit must report about itself and the part numbers it is built from come
    from the system part number, which is a subclass. A subclass whose parts do not
    fit this tree overrides ``build_suite``.

    Args:
        name: Root node name used by logs, artifacts, and failures.
        motherboard: The part number of the board it is built on.
        firmware: The part number of the BIOS release flashed on it.
        cpu: The part number of the processor.
        sodimms: The part number of the module in each of the board's memory slots,
            in slot order.
        drive: The part number of the drive.
        partition_table: Partition table type the drive leaves the line with.
        volumes: Every partition's layout by node name, in table order.
        **identity: What the unit must report about itself.

    Raises:
        ValueError: The unit's bill of materials cannot be read, or is not one.
    """

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
        motherboard: MotherboardPartNumber,
        firmware: PartNumber[Firmware],
        cpu: PartNumber[Cpu],
        sodimms: Sequence[SodimmPartNumber],
        drive: DrivePartNumber,
        partition_table: str,
        volumes: Mapping[str, VolumeLayout],
        **identity: Unpack[Nuc13Identity],
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._bom = Bom.read()
        self._motherboard = motherboard
        self._firmware = firmware
        self._cpu = cpu
        self._sodimms = tuple(sodimms)
        self._drive = drive
        self._partition_table = partition_table
        self._volumes = dict(volumes)

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
        """The unit's own identity, then every part the system part number is built from.

        Each part is built here, from the class the system part number named and what
        the part is owed: its line of the bill of materials, and the board's slot for a
        part that is fitted in one.

        Nothing here gates anything else, so everything runs in parallel: a wrong
        system part number never hides a missing module.
        """
        return self.parallel(
            [
                self.parallel(
                    "identity",
                    [
                        self.test("bom-check", self._bom_check, listed=self._bom.system),
                        self.test("vendor", self._dmi_field, field="sys_vendor"),
                        self.test("product-name", self._dmi_field, field="product_name"),
                        self.test("version", self._dmi_field, field="product_version"),
                        self.test("mpn", self._dmi_field, field="product_sku"),
                        self.test("family", self._dmi_field, field="product_family"),
                        self.test("chassis", self._dmi_field, field="chassis_type"),
                    ],
                    discrete_log_file=False,
                ),
                self._motherboard(name="motherboard", bom_mpns=self._bom.line("motherboard")),
                self._firmware(name="firmware", bom_mpns=self._bom.line("firmware")),
                self._cpu(name="cpu", bom_mpns=self._bom.line("cpu")),
                Memory(
                    name="memory",
                    bom_mpns=self._bom.line("memory"),
                    sodimms=self._sodimms,
                    slots=self._motherboard.SODIMM_SLOTS,
                ),
                self._drive(
                    name="drive",
                    bom_mpns=self._bom.line("drive"),
                    **self._motherboard.M2_SLOT,
                    partition_table=self._partition_table,
                    volumes=self._volumes,
                ),
            ]
        )

    async def _bom_check(self, utils: TestUtils[Nuc13Identity], *, listed: str) -> Verdict:
        expected = utils.ctx["product_sku"]
        if listed == expected:
            return PASS
        raise TestFailure(
            "the bill of materials is for another system part number", expected, listed
        )

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
