"""USB: the buses the kernel registers, each root hub, and what is wired to them.

The subsystem makes one claim of its own (how many buses there are) and composes
one component per root hub and one per permanently attached device:

    usb
    ├─ bus-count              as many buses as root hubs on record
    ├─ bus-<n>                one per recorded root hub
    │  ├─ present             the kernel registers the bus
    │  └─ details
    │     ├─ controller       the PCI function that owns the bus
    │     ├─ version          the USB version the hub advertises
    │     ├─ ports            downstream ports
    │     └─ speed            bus speed
    └─ <device>               one per recorded device, such as ``bluetooth``
       ├─ present             something is attached at the recorded port
       └─ details
          ├─ kind             vendor:product IDs
          └─ driver           the driver bound to its first interface

A device's kind is judged on the IDs it reports. Its product name in ``lsusb`` and
``lshw`` is looked up in the usb.ids database (a device need not carry a product
string, and the one on record does not), so the name is a label on the record and
is never compared.
"""

from __future__ import annotations

from typing import TypedDict

from nuc13.probe import list_dir, read_files, read_int, read_link, read_link_name, read_text
from nuc13.spec import UsbDeviceSpec, UsbRootHubSpec

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

_DEVICES = "/sys/bus/usb/devices"
_NOT_READ = "(not read)"


class RootHub(TypedDict):
    """What one root hub must show."""

    bus: str
    controller: str
    usb_version: str
    ports: int
    speed_mbps: int


class UsbRootHub(TestComponent[RootHub]):
    """Judge one USB bus through its root hub."""

    def __init__(self, *, name: str, spec: UsbRootHubSpec) -> None:
        """Hold the root hub's record.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The bus number, its controller, and what the hub advertises.
        """
        super().__init__(name=name)
        self._spec = spec

    async def configure(self, utils: TestUtils[RootHub]) -> RootHub:
        """Provide the record the claims judge against."""
        spec = self._spec
        return {
            "bus": f"usb{spec.bus}",
            "controller": spec.controller_address,
            "usb_version": spec.usb_version,
            "ports": spec.ports,
            "speed_mbps": spec.speed_mbps,
        }

    def build_suite(self) -> TestComponent:
        """Presence first: a bus that is not there has no owner, version, ports, or speed.

        The four attributes are independent readings, so each is reported.
        """
        return self.sequence(
            self.name,
            [
                self.test("present", self._present),
                self.parallel(
                    "details",
                    [
                        self.test("controller", self._controller),
                        self.test("version", self._version),
                        self.test("ports", self._ports),
                        self.test("speed", self._speed),
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("enumerated_as")
    async def _present(self, utils: TestUtils[RootHub]) -> Verdict:
        bus = utils.ctx["bus"]
        entry = await read_link_name(utils, f"{_DEVICES}/{bus}")
        reading = {"enumerated_as": entry}
        if entry == bus:
            return PASS(reading)
        raise TestFailure(f"no USB bus {bus}", bus, entry or "(not registered)", evidence=reading)

    @evidence("owning_controller")
    async def _controller(self, utils: TestUtils[RootHub]) -> Verdict:
        bus = utils.ctx["bus"]
        expected = utils.ctx["controller"]
        # A root hub hangs directly off its controller, so the bus links to
        # .../<controller's PCI address>/usbN.
        owner = _parent_name(await read_link(utils, f"{_DEVICES}/{bus}"))
        reading = {"owning_controller": owner}
        if owner == expected:
            return PASS(reading)
        if owner is None:
            raise TestFailure(f"owner of {bus} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"{bus} belongs to a different controller", expected, owner, evidence=reading
        )

    @evidence("advertised_version")
    async def _version(self, utils: TestUtils[RootHub]) -> Verdict:
        bus = utils.ctx["bus"]
        expected = utils.ctx["usb_version"]
        version = await read_text(utils, f"{_DEVICES}/{bus}/version")
        reading = {"advertised_version": version}
        if version == expected:
            return PASS(reading)
        if version is None:
            raise TestFailure(f"USB version of {bus} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"{bus} advertises a different USB version", expected, version, evidence=reading
        )

    @evidence("downstream_ports")
    async def _ports(self, utils: TestUtils[RootHub]) -> Verdict:
        bus = utils.ctx["bus"]
        expected = utils.ctx["ports"]
        ports = await read_int(utils, f"{_DEVICES}/{bus}/maxchild")
        reading = {"downstream_ports": ports}
        if ports == expected:
            return PASS(reading)
        if ports is None:
            raise TestFailure(f"port count of {bus} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"{bus} has a different number of ports", expected, ports, evidence=reading
        )

    @evidence("bus_speed_mbps")
    async def _speed(self, utils: TestUtils[RootHub]) -> Verdict:
        bus = utils.ctx["bus"]
        expected = utils.ctx["speed_mbps"]
        # Recorded without a window: a bus speed is one of a few fixed rates, and
        # the claim is that it is this one.
        speed = utils.measure(
            "bus_speed_mbps", await read_int(utils, f"{_DEVICES}/{bus}/speed"), unit="Mbit/s"
        ).value
        reading = {"bus_speed_mbps": speed}
        if speed == expected:
            return PASS(reading)
        if speed is None:
            raise TestFailure(f"speed of {bus} could not be read", f"{expected} Mbit/s", _NOT_READ)
        raise TestFailure(
            f"{bus} runs at a different speed",
            f"{expected} Mbit/s",
            f"{speed:g} Mbit/s",
            evidence=reading,
        )


class AttachedDevice(TypedDict):
    """What one attached device must show, in the notation ``lsusb`` prints."""

    device: str
    interface: str
    label: str
    vendor_product: str
    driver: str


class UsbDevice(TestComponent[AttachedDevice]):
    """Judge one device wired to a root-hub port."""

    def __init__(self, *, name: str, spec: UsbDeviceSpec) -> None:
        """Hold the device's record.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The bus and port, the device's IDs, and its driver.
        """
        super().__init__(name=name)
        self._spec = spec

    async def configure(self, utils: TestUtils[AttachedDevice]) -> AttachedDevice:
        """Provide the record the claims judge against."""
        spec = self._spec
        device = f"{spec.bus}-{spec.port}"
        return {
            "device": device,
            # USB drivers bind to interfaces, not to the device, which the kernel's
            # generic `usb` driver always holds. Every configured device has
            # interface 0 of configuration 1.
            "interface": f"{device}:1.0",
            "label": spec.product,
            "vendor_product": f"{spec.vendor_id:04x}:{spec.product_id:04x}",
            "driver": spec.driver,
        }

    def build_suite(self) -> TestComponent:
        """Presence first: with nothing at the port, kind and driver say nothing.

        Kind and driver are then independent readings, so both are reported.
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
    async def _present(self, utils: TestUtils[AttachedDevice]) -> Verdict:
        device = utils.ctx["device"]
        entry = await read_link_name(utils, f"{_DEVICES}/{device}")
        reading = {"enumerated_as": entry}
        if entry == device:
            return PASS(reading)
        raise TestFailure(
            f"no USB device at {device} ({utils.ctx['label']})",
            device,
            entry or "(nothing attached)",
            evidence=reading,
        )

    @evidence("reported_vendor_product")
    async def _kind(self, utils: TestUtils[AttachedDevice]) -> Verdict:
        device = utils.ctx["device"]
        expected = utils.ctx["vendor_product"]
        node = f"{_DEVICES}/{device}"
        files = await read_files(utils, f"{node}/idVendor", f"{node}/idProduct")
        vendor, product = files.get(f"{node}/idVendor"), files.get(f"{node}/idProduct")
        reported = None if vendor is None or product is None else f"{vendor}:{product}"
        reading = {"reported_vendor_product": reported}
        if reported == expected:
            return PASS(reading)
        if reported is None:
            raise TestFailure(f"IDs of USB device {device} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"USB device {device} is not the recorded one ({utils.ctx['label']})",
            expected,
            reported,
            evidence=reading,
        )

    @evidence("bound_driver")
    async def _driver(self, utils: TestUtils[AttachedDevice]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["driver"]
        bound = await read_link_name(utils, f"{_DEVICES}/{interface}/driver")
        reading = {"bound_driver": bound}
        if bound == expected:
            return PASS(reading)
        raise TestFailure(
            f"driver bound to USB interface {interface} ({utils.ctx['label']}) "
            "is not the recorded one",
            expected,
            bound or "(no driver bound)",
            evidence=reading,
        )


class UsbBuses(TypedDict):
    """How many buses the machine must register."""

    bus_count: int


class Usb(TestComponent[UsbBuses]):
    """Judge the number of USB buses, then every root hub and attached device."""

    def __init__(
        self,
        *,
        name: str,
        hubs: tuple[UsbRootHubSpec, ...],
        devices: tuple[UsbDeviceSpec, ...],
    ) -> None:
        """Build one component per recorded root hub and attached device.

        Args:
            name: Node name used by logs, artifacts, and failures.
            hubs: Every root hub, one per bus.
            devices: Every device permanently wired to one of those buses.
        """
        super().__init__(name=name)
        self._bus_count = len(hubs)
        self._hubs = tuple(UsbRootHub(name=f"bus-{spec.bus}", spec=spec) for spec in hubs)
        self._devices = tuple(UsbDevice(name=spec.name, spec=spec) for spec in devices)

    async def configure(self, utils: TestUtils[UsbBuses]) -> UsbBuses:
        """Provide the count the bus claim judges against."""
        return {"bus_count": self._bus_count}

    def build_suite(self) -> TestComponent:
        """Independent claims: the count, each bus, and each device are judged on their own."""
        return self.parallel(
            self.name,
            [self.test("bus-count", self._bus_count_matches), *self._hubs, *self._devices],
        )

    @evidence("root_hubs")
    async def _bus_count_matches(self, utils: TestUtils[UsbBuses]) -> Verdict:
        expected = utils.ctx["bus_count"]
        listing = await list_dir(utils, _DEVICES)
        if listing is None:
            raise TestFailure(f"{_DEVICES} could not be listed", f"{expected} buses", _NOT_READ)
        # The directory also holds devices (3-10) and interfaces (3-10:1.0); a bus
        # is the entry for its root hub, usbN.
        root_hubs = [entry for entry in listing if entry.startswith("usb")]
        reading = {"root_hubs": root_hubs}
        if len(root_hubs) == expected:
            return PASS(reading)
        raise TestFailure(
            "number of USB buses differs from the record",
            f"{expected} buses",
            f"{len(root_hubs)} buses: {', '.join(root_hubs) or 'none'}",
            evidence=reading,
        )


def _parent_name(target: str | None) -> str | None:
    """Name the directory a link target sits in: ``.../0000:00:14.0/usb3`` gives the address."""
    if target is None:
        return None
    parts = target.rsplit("/", 2)
    return parts[-2] if len(parts) >= 2 else None
