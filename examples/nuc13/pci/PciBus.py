"""The PCI bus: what the kernel enumerates on it, then each function.

The bus makes one claim of its own (the functions enumerated are exactly the ones
it was composed with) and composes one component per function, each with its own
claims:

    pci
    ├─ enumeration            nothing missing, nothing unexpected
    └─ <function>             one per expected function, such as ``display``
       ├─ present             the kernel enumerates a function at the address
       └─ details
          ├─ kind             class and vendor:device IDs
          └─ driver           the bound driver, or none where none is expected

Kind is judged on the numbers the function itself reports. The names beside them
in ``lspci`` and ``lshw`` are looked up in the pci.ids database on whichever machine
ran the tool, so they travel in the identity as a label and are never compared.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypedDict, Unpack

from nuc13.kernel import NOT_READ, PCI_SYSFS, list_dir, read_files, read_link_name

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence


class PciDeviceIdentity(TypedDict):
    """What one function must show, in the notation ``lspci -nn`` prints.

    Attributes:
        address: Domain-qualified address, such as ``0000:00:02.0``.
        label: The pci.ids names for the IDs below, such as ``VGA compatible
            controller: Raptor Lake-P [Iris Xe Graphics]``. For the reader only.
        class_code: Base class and subclass, the ``0300`` of ``[0300]``.
        vendor_device: Vendor and device IDs, the ``8086:a7a0`` of ``[8086:a7a0]``.
        driver: Kernel driver that must be bound, or ``None`` where none binds.
    """

    address: str
    label: str
    class_code: str
    vendor_device: str
    driver: str | None


class PciDevice(TestComponent[PciDeviceIdentity]):
    """Judge one PCI function against its identity.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: What the function must show.
    """

    _NO_DRIVER = "(no driver bound)"

    def __init__(self, *, name: str, **identity: Unpack[PciDeviceIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity

    @property
    def address(self) -> str:
        """Where the function sits, which the bus counts in its population."""
        return self._identity["address"]

    async def configure(self, utils: TestUtils[PciDeviceIdentity]) -> PciDeviceIdentity:
        """Provide the identity the claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Presence first: with no function at the address, kind and driver say nothing.

        Kind and driver are then independent readings, so a function of the wrong
        kind still reports what is bound to it.
        """
        return self.sequence(
            self.name,
            [
                self.test("present", self._present),
                self.parallel(
                    "details",
                    [
                        self.test("kind", self._kind),
                        self.test("driver", self._driver),
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("enumerated_as")
    async def _present(self, utils: TestUtils[PciDeviceIdentity]) -> Verdict:
        address = utils.ctx["address"]
        # The bus directory holds one link per enumerated function, named for it.
        entry = await read_link_name(utils, f"{PCI_SYSFS}/{address}")
        reading = {"enumerated_as": entry}
        if entry == address:
            return PASS(reading)
        raise TestFailure(
            f"no PCI function at {address} ({utils.ctx['label']})",
            address,
            entry or "(not enumerated)",
            evidence=reading,
        )

    @evidence("reported_class", "reported_vendor_device")
    async def _kind(self, utils: TestUtils[PciDeviceIdentity]) -> Verdict:
        address = utils.ctx["address"]
        node = f"{PCI_SYSFS}/{address}"
        files = await read_files(utils, f"{node}/class", f"{node}/vendor", f"{node}/device")
        reading = {
            "reported_class": self._class_code(files.get(f"{node}/class")),
            "reported_vendor_device": self._vendor_device(
                files.get(f"{node}/vendor"), files.get(f"{node}/device")
            ),
        }
        expected = f"[{utils.ctx['class_code']}] [{utils.ctx['vendor_device']}]"
        if (
            reading["reported_class"] == utils.ctx["class_code"]
            and reading["reported_vendor_device"] == utils.ctx["vendor_device"]
        ):
            return PASS(reading)
        if None in reading.values():
            raise TestFailure(
                f"class and IDs of {address} could not be read",
                expected,
                NOT_READ,
                evidence=reading,
            )
        raise TestFailure(
            f"{address} is not the expected device ({utils.ctx['label']})",
            expected,
            f"[{reading['reported_class']}] [{reading['reported_vendor_device']}]",
            evidence=reading,
        )

    @evidence("bound_driver")
    async def _driver(self, utils: TestUtils[PciDeviceIdentity]) -> Verdict:
        address = utils.ctx["address"]
        expected = utils.ctx["driver"]
        # No link is the kernel's way of saying nothing is bound, which is what some
        # functions expect. It can be read as that, and not as a missing function,
        # only because this claim runs after `present` passed.
        bound = await read_link_name(utils, f"{PCI_SYSFS}/{address}/driver")
        reading = {"bound_driver": bound}
        if bound == expected:
            return PASS(reading)
        raise TestFailure(
            f"driver bound to {address} ({utils.ctx['label']}) is not the expected one",
            expected or self._NO_DRIVER,
            bound or self._NO_DRIVER,
            evidence=reading,
        )

    @staticmethod
    def _class_code(text: str | None) -> str | None:
        """Reduce sysfs ``class`` (``0x030000``) to base class and subclass (``0300``).

        The last two digits are the programming interface, which ``lspci -nn`` leaves
        out of its brackets and the identity does not carry.
        """
        if text is None:
            return None
        return text.removeprefix("0x")[:4]

    @staticmethod
    def _vendor_device(vendor: str | None, device: str | None) -> str | None:
        """Join sysfs ``vendor`` and ``device`` (``0x8086``, ``0xa7a0``) as ``8086:a7a0``."""
        if vendor is None or device is None:
            return None
        return f"{vendor.removeprefix('0x')}:{device.removeprefix('0x')}"


class PciBusIdentity(TypedDict):
    """Every address the bus must enumerate, sorted."""

    addresses: list[str]


class PciBus(TestComponent[PciBusIdentity]):
    """Judge the bus's population, then every function on it.

    The bus's identity is its population, so it is read off the functions it is
    composed with.

    Args:
        name: Node name used by logs, artifacts, and failures.
        devices: Every function the bus must enumerate, and no others.
    """

    def __init__(self, *, name: str, devices: Sequence[PciDevice]) -> None:
        super().__init__(name=name)
        self._devices = tuple(devices)
        self._identity: PciBusIdentity = {
            "addresses": sorted(device.address for device in self._devices)
        }

    async def configure(self, utils: TestUtils[PciBusIdentity]) -> PciBusIdentity:
        """Provide the population the enumeration claim judges against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Independent claims: the population and each function are judged on their own."""
        return self.parallel(
            self.name,
            [self.test("enumeration", self._enumeration), *self._devices],
        )

    @evidence("enumerated")
    async def _enumeration(self, utils: TestUtils[PciBusIdentity]) -> Verdict:
        expected = set(utils.ctx["addresses"])
        listing = await list_dir(utils, PCI_SYSFS)
        if listing is None:
            raise TestFailure(
                f"{PCI_SYSFS} could not be listed", f"{len(expected)} functions", NOT_READ
            )
        enumerated = set(listing)
        missing = sorted(expected - enumerated)
        unexpected = sorted(enumerated - expected)
        reading = {"enumerated": list(listing), "missing": missing, "unexpected": unexpected}
        if enumerated == expected:
            return PASS(reading)
        raise TestFailure(
            "PCI functions enumerated differ from the expected population",
            f"{len(expected)} functions, none missing, none unexpected",
            f"{len(enumerated)} functions, missing {missing or 'none'}, "
            f"unexpected {unexpected or 'none'}",
            evidence=reading,
        )
