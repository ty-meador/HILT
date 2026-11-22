"""The PCI bus: what the kernel enumerates on it, then each function.

The bus makes one claim of its own (the functions enumerated are exactly the ones
on record) and composes one component per function, each with its own claims:

    pci
    ├─ enumeration            nothing missing, nothing unexpected
    └─ <function>             one per recorded function, such as ``display``
       ├─ present             the kernel enumerates a function at the address
       └─ details
          ├─ kind             class and vendor:device IDs
          └─ driver           the bound driver, or none where none is recorded

Kind is judged on the numbers the function itself reports. The names beside them
in ``lspci`` and ``lshw`` are looked up in the pci.ids database on whichever machine
ran the tool, so they travel on the record as a label and are never compared.
"""

from __future__ import annotations

from typing import TypedDict

from nuc13.probe import list_dir, read_files, read_link_name
from nuc13.spec import PciDeviceSpec

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

_DEVICES = "/sys/bus/pci/devices"
_NOT_READ = "(not read)"
_NO_DRIVER = "(no driver bound)"


class PciFunction(TypedDict):
    """What one function must show, in the notation ``lspci -nn`` prints."""

    address: str
    label: str
    class_code: str
    vendor_device: str
    driver: str | None


class PciDevice(TestComponent[PciFunction]):
    """Judge one PCI function against its record."""

    def __init__(self, *, name: str, spec: PciDeviceSpec) -> None:
        """Hold the function's record.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The function's address, IDs, and driver.
        """
        super().__init__(name=name)
        self._spec = spec

    async def configure(self, utils: TestUtils[PciFunction]) -> PciFunction:
        """Provide the record the claims judge against."""
        spec = self._spec
        return {
            "address": spec.address,
            "label": f"{spec.description}: {spec.product}",
            "class_code": f"{spec.class_code:04x}",
            "vendor_device": f"{spec.vendor_id:04x}:{spec.device_id:04x}",
            "driver": spec.driver,
        }

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
    async def _present(self, utils: TestUtils[PciFunction]) -> Verdict:
        address = utils.ctx["address"]
        # The bus directory holds one link per enumerated function, named for it.
        entry = await read_link_name(utils, f"{_DEVICES}/{address}")
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
    async def _kind(self, utils: TestUtils[PciFunction]) -> Verdict:
        address = utils.ctx["address"]
        node = f"{_DEVICES}/{address}"
        files = await read_files(utils, f"{node}/class", f"{node}/vendor", f"{node}/device")
        reading = {
            "reported_class": _class_code(files.get(f"{node}/class")),
            "reported_vendor_device": _vendor_device(
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
                _NOT_READ,
                evidence=reading,
            )
        raise TestFailure(
            f"{address} is not the recorded device ({utils.ctx['label']})",
            expected,
            f"[{reading['reported_class']}] [{reading['reported_vendor_device']}]",
            evidence=reading,
        )

    @evidence("bound_driver")
    async def _driver(self, utils: TestUtils[PciFunction]) -> Verdict:
        address = utils.ctx["address"]
        expected = utils.ctx["driver"]
        # No link is the kernel's way of saying nothing is bound, and three functions
        # are recorded that way. It can be read as that, and not as a missing
        # function, only because this claim runs after `present` passed.
        bound = await read_link_name(utils, f"{_DEVICES}/{address}/driver")
        reading = {"bound_driver": bound}
        if bound == expected:
            return PASS(reading)
        raise TestFailure(
            f"driver bound to {address} ({utils.ctx['label']}) is not the recorded one",
            expected or _NO_DRIVER,
            bound or _NO_DRIVER,
            evidence=reading,
        )


class PciEnumeration(TypedDict):
    """Every address the bus must enumerate, sorted."""

    addresses: list[str]


class PciBus(TestComponent[PciEnumeration]):
    """Judge the bus's population, then every function on it."""

    def __init__(self, *, name: str, devices: tuple[PciDeviceSpec, ...]) -> None:
        """Build one component per recorded function.

        Args:
            name: Node name used by logs, artifacts, and failures.
            devices: Every function the bus must enumerate, and no others.
        """
        super().__init__(name=name)
        self._addresses = tuple(sorted(spec.address for spec in devices))
        self._devices = tuple(PciDevice(name=spec.name, spec=spec) for spec in devices)

    async def configure(self, utils: TestUtils[PciEnumeration]) -> PciEnumeration:
        """Provide the population the enumeration claim judges against."""
        return {"addresses": list(self._addresses)}

    def build_suite(self) -> TestComponent:
        """Independent claims: the population and each function are judged on their own."""
        return self.parallel(
            self.name,
            [self.test("enumeration", self._enumeration), *self._devices],
        )

    @evidence("enumerated")
    async def _enumeration(self, utils: TestUtils[PciEnumeration]) -> Verdict:
        recorded = set(utils.ctx["addresses"])
        listing = await list_dir(utils, _DEVICES)
        if listing is None:
            raise TestFailure(
                f"{_DEVICES} could not be listed", f"{len(recorded)} functions", _NOT_READ
            )
        enumerated = set(listing)
        missing = sorted(recorded - enumerated)
        unexpected = sorted(enumerated - recorded)
        reading = {"enumerated": list(listing), "missing": missing, "unexpected": unexpected}
        if enumerated == recorded:
            return PASS(reading)
        raise TestFailure(
            "PCI functions enumerated differ from the record",
            f"{len(recorded)} functions, none missing, none unexpected",
            f"{len(enumerated)} functions, missing {missing or 'none'}, "
            f"unexpected {unexpected or 'none'}",
            evidence=reading,
        )


def _class_code(text: str | None) -> str | None:
    """Reduce sysfs ``class`` (``0x030000``) to base class and subclass (``0300``).

    The last two digits are the programming interface, which ``lspci -nn`` leaves
    out of its brackets and the record does not carry.
    """
    if text is None:
        return None
    return text.removeprefix("0x")[:4]


def _vendor_device(vendor: str | None, device: str | None) -> str | None:
    """Join sysfs ``vendor`` and ``device`` (``0x8086``, ``0xa7a0``) as ``8086:a7a0``."""
    if vendor is None or device is None:
        return None
    return f"{vendor.removeprefix('0x')}:{device.removeprefix('0x')}"
