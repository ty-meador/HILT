"""The product's part numbers: how one MPN is judged, and one record per MPN.

``Mpn`` is the judgement, the same for every part number of the product. Each other
module in this package is one MPN's record, an ``MpnSpec`` named ``RECORD``, and it
is the only thing that differs between two part numbers.

A unit is first judged to be the MPN it was built as (the part number and the
product identity its firmware reports) and then each component that MPN calls for is
judged. Each component has claims of its own, and most compose parts in turn:

    NUC13ANKi50WC
    ├─ identity      vendor, product-name, version, mpn, family, chassis
    └─ components
       ├─ motherboard   the board's own identity
       ├─ firmware
       ├─ cpu           → one node per core, its caches inside it
       ├─ memory        → one node per bank
       ├─ drive         → one node per volume
       ├─ pci           → one node per function
       ├─ usb           → one node per root hub and attached device
       └─ network       → one node per interface

``Mpn`` never tells a component where to run, and hands it nothing beyond its slice
of the record. A component dropped into another product's suite works unchanged.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from nuc13.cpu import Cpu
from nuc13.firmware import Firmware
from nuc13.memory import Memory
from nuc13.motherboard import Motherboard
from nuc13.network import Network
from nuc13.pci import PciBus
from nuc13.probe import read_text
from nuc13.spec import MpnSpec
from nuc13.storage import NvmeDrive
from nuc13.usb import Usb

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence


class MpnIdentity(TypedDict):
    """What a unit of the MPN must report, keyed by the DMI field that reports it."""

    sys_vendor: str
    product_name: str
    product_version: str
    product_sku: str
    product_family: str
    chassis_type: str


# The DMI fields this class judges; each is also the ctx key holding its expectation.
type IdentityField = Literal[
    "sys_vendor",
    "product_name",
    "product_version",
    "product_sku",
    "product_family",
    "chassis_type",
]


class Mpn(TestComponent[MpnIdentity]):
    """Judge one unit against the record of the MPN it was built as."""

    def __init__(self, *, spec: MpnSpec) -> None:
        """Build the judgement of one MPN from that MPN's record.

        The node is named after the part number, so there is no ``name`` to pass.

        Args:
            spec: The MPN's identity and, beside it, each component it calls for.
        """
        super().__init__(name=spec.mpn)
        self._spec = spec
        self._motherboard = Motherboard(name="motherboard", spec=spec.board)
        self._firmware = Firmware(name="firmware", spec=spec.firmware)
        self._cpu = Cpu(name="cpu", spec=spec.cpu)
        self._memory = Memory(name="memory", spec=spec.memory)
        self._drive = NvmeDrive(name="drive", spec=spec.drive)
        self._pci = PciBus(name="pci", devices=spec.pci)
        self._usb = Usb(name="usb", hubs=spec.usb_hubs, devices=spec.usb_devices)
        self._network = Network(name="network", interfaces=spec.networks)

    async def configure(self, utils: TestUtils[MpnIdentity]) -> MpnIdentity:
        """Provide the identity the unit's own claims judge against."""
        return {
            "sys_vendor": self._spec.vendor,
            "product_name": self._spec.product_name,
            "product_version": self._spec.version,
            # The unit reports its part number in the DMI SKU field.
            "product_sku": self._spec.mpn,
            "product_family": self._spec.family,
            # sysfs prints the SMBIOS chassis code as decimal text.
            "chassis_type": str(self._spec.chassis_type),
        }

    def build_suite(self) -> TestComponent:
        """Identity first: a unit of another MPN is not judged against this record.

        Within each step the children are independent, so each step runs in parallel:
        a wrong part number and a wrong version are both reported, and one failed
        component never hides another.
        """
        return self.sequence(
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
                self.parallel(
                    "components",
                    [
                        self._motherboard,
                        self._firmware,
                        self._cpu,
                        self._memory,
                        self._drive,
                        self._pci,
                        self._usb,
                        self._network,
                    ],
                ),
            ]
        )

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[MpnIdentity], *, field: IdentityField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, "(not read)")
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)
