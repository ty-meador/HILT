"""Networking: each interface, and its link where the record requires one.

The subsystem composes one component per recorded interface. An interface whose
record requires a link composes that link as a part of its own; one whose record
does not has no link node at all, so nothing is claimed about its carrier:

    network
    └─ <interface>            one per recorded interface, such as ``ethernet``
       ├─ present             the kernel has the interface
       └─ details
          ├─ pci-address      the PCI function behind it
          ├─ driver           the driver bound to that function
          ├─ kind             wired or wireless
          └─ link             only where the record requires a link
             ├─ carrier       the link is up
             └─ negotiation
                ├─ speed      negotiated speed
                └─ duplex     negotiated duplex

Everything here is a read of sysfs. Run against a machine reached over one of its
own interfaces, the link claims read a link that is carrying the session, and they
leave it exactly as they found it.
"""

from __future__ import annotations

from typing import TypedDict

from nuc13.probe import read_int, read_link_name, read_text
from nuc13.spec import LinkKind, LinkSpec, NetworkSpec

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

_INTERFACES = "/sys/class/net"
_NOT_READ = "(not read)"


class RequiredLink(TypedDict):
    """What a required link must have negotiated."""

    interface: str
    speed_mbps: int
    duplex: str


class NetworkLink(TestComponent[RequiredLink]):
    """Judge the link of an interface whose record requires one."""

    def __init__(self, *, name: str, interface: str, spec: LinkSpec) -> None:
        """Hold the link's record and the interface it belongs to.

        Args:
            name: Node name used by logs, artifacts, and failures.
            interface: Kernel name of the interface whose link this is.
            spec: The speed and duplex the link must have negotiated.
        """
        super().__init__(name=name)
        self._interface = interface
        self._spec = spec

    async def configure(self, utils: TestUtils[RequiredLink]) -> RequiredLink:
        """Provide the record the claims judge against."""
        return {
            "interface": self._interface,
            "speed_mbps": self._spec.speed_mbps,
            "duplex": self._spec.duplex,
        }

    def build_suite(self) -> TestComponent:
        """Carrier first: without it nothing was negotiated, so speed and duplex are noise.

        Speed and duplex are then independent readings, so both are reported.
        """
        return self.sequence(
            [
                self.test("carrier", self._carrier),
                self.parallel(
                    "negotiation",
                    [
                        self.test("speed", self._speed),
                        self.test("duplex", self._duplex),
                    ],
                    discrete_log_file=False,
                ),
            ]
        )

    @evidence("carrier")
    async def _carrier(self, utils: TestUtils[RequiredLink]) -> Verdict:
        interface = utils.ctx["interface"]
        carrier = await read_int(utils, f"{_INTERFACES}/{interface}/carrier")
        reading = {"carrier": carrier}
        if carrier == 1:
            return PASS(reading)
        if carrier is None:
            # The kernel refuses this read while the interface is administratively down.
            raise TestFailure(f"carrier of {interface} could not be read", "1 (up)", _NOT_READ)
        raise TestFailure(
            f"{interface} has no carrier", "1 (up)", f"{carrier} (down)", evidence=reading
        )

    @evidence("negotiated_speed_mbps")
    async def _speed(self, utils: TestUtils[RequiredLink]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["speed_mbps"]
        # Recorded without a window: a link speed is one of a few fixed rates, and
        # the claim is that it is this one.
        speed = utils.measure(
            "negotiated_speed_mbps",
            await read_int(utils, f"{_INTERFACES}/{interface}/speed"),
            unit="Mbit/s",
        ).value
        reading = {"negotiated_speed_mbps": speed}
        if speed == expected:
            return PASS(reading)
        if speed is None:
            raise TestFailure(
                f"speed of {interface} could not be read", f"{expected} Mbit/s", _NOT_READ
            )
        raise TestFailure(
            f"{interface} negotiated a different speed",
            f"{expected} Mbit/s",
            f"{speed:g} Mbit/s",
            evidence=reading,
        )

    @evidence("negotiated_duplex")
    async def _duplex(self, utils: TestUtils[RequiredLink]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["duplex"]
        duplex = await read_text(utils, f"{_INTERFACES}/{interface}/duplex")
        reading = {"negotiated_duplex": duplex}
        if duplex == expected:
            return PASS(reading)
        if duplex is None:
            raise TestFailure(f"duplex of {interface} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"{interface} negotiated a different duplex", expected, duplex, evidence=reading
        )


class Interface(TypedDict):
    """What one interface must show."""

    interface: str
    pci_address: str
    driver: str
    kind: LinkKind


class NetworkInterface(TestComponent[Interface]):
    """Judge one network interface and, where its record requires one, its link."""

    def __init__(self, *, name: str, spec: NetworkSpec) -> None:
        """Hold the interface's record and build its link where one is required.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The interface's name, PCI function, driver, kind, and link.
        """
        super().__init__(name=name)
        self._spec = spec
        # Whether there is a link to judge is identity, so it is decided here: an
        # interface recorded without a link gets no link node, and so no carrier
        # claim that could pass or fail on whatever network happens to be in range.
        self._links = (
            ()
            if spec.link is None
            else (NetworkLink(name="link", interface=spec.interface, spec=spec.link),)
        )

    async def configure(self, utils: TestUtils[Interface]) -> Interface:
        """Provide the record the claims judge against."""
        spec = self._spec
        return {
            "interface": spec.interface,
            "pci_address": spec.pci_address,
            "driver": spec.driver,
            "kind": spec.kind,
        }

    def build_suite(self) -> TestComponent:
        """Presence first: an interface that is not there has nothing behind it to judge.

        Its attributes and its link are then independent, so each is reported.
        """
        return self.sequence(
            self.name,
            [
                self.test("present", self._present),
                self.parallel(
                    "details",
                    [
                        self.test("pci-address", self._pci_address),
                        self.test("driver", self._driver),
                        self.test("kind", self._kind),
                        *self._links,
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("enumerated_as")
    async def _present(self, utils: TestUtils[Interface]) -> Verdict:
        interface = utils.ctx["interface"]
        entry = await read_link_name(utils, f"{_INTERFACES}/{interface}")
        reading = {"enumerated_as": entry}
        if entry == interface:
            return PASS(reading)
        raise TestFailure(
            f"no network interface {interface}",
            interface,
            entry or "(no such interface)",
            evidence=reading,
        )

    @evidence("backing_device")
    async def _pci_address(self, utils: TestUtils[Interface]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["pci_address"]
        device = await read_link_name(utils, f"{_INTERFACES}/{interface}/device")
        reading = {"backing_device": device}
        if device == expected:
            return PASS(reading)
        raise TestFailure(
            f"{interface} is not on the recorded PCI function",
            expected,
            device or "(no device behind it)",
            evidence=reading,
        )

    @evidence("bound_driver")
    async def _driver(self, utils: TestUtils[Interface]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["driver"]
        bound = await read_link_name(utils, f"{_INTERFACES}/{interface}/device/driver")
        reading = {"bound_driver": bound}
        if bound == expected:
            return PASS(reading)
        raise TestFailure(
            f"driver behind {interface} is not the recorded one",
            expected,
            bound or "(no driver bound)",
            evidence=reading,
        )

    @evidence("wireless_radio", "observed_kind")
    async def _kind(self, utils: TestUtils[Interface]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["kind"]
        # A wireless interface links to its 802.11 radio; a wired one has no such
        # link. Its absence can be read as "wired" only because this claim runs
        # after `present` passed.
        radio = await read_link_name(utils, f"{_INTERFACES}/{interface}/phy80211")
        observed: LinkKind = "ethernet" if radio is None else "wireless"
        reading = {"wireless_radio": radio, "observed_kind": observed}
        if observed == expected:
            return PASS(reading)
        raise TestFailure(
            f"{interface} is not the recorded kind of interface",
            expected,
            observed,
            evidence=reading,
        )


class Network(TestComponent):
    """Judge every network interface on record."""

    def __init__(self, *, name: str, interfaces: tuple[NetworkSpec, ...]) -> None:
        """Build one component per recorded interface.

        Args:
            name: Node name used by logs, artifacts, and failures.
            interfaces: Every interface to judge.
        """
        super().__init__(name=name)
        self._interfaces = tuple(NetworkInterface(name=spec.name, spec=spec) for spec in interfaces)

    def build_suite(self) -> TestComponent:
        """Independent interfaces, so independent verdicts."""
        return self.parallel(self.name, self._interfaces)
