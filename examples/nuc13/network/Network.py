"""Networking: each interface, and its link where one is required.

The subsystem composes one component per expected interface. An interface that must
have a link is composed with that link as a part of its own; one that need not has
no link node at all, so nothing is claimed about its carrier:

    network
    └─ <interface>            one per expected interface, such as ``ethernet``
       ├─ present             the kernel has the interface
       └─ details
          ├─ pci-address      the PCI function behind it
          ├─ driver           the driver bound to that function
          ├─ kind             wired or wireless
          └─ link             only where a link is required
             ├─ carrier       the link is up
             └─ negotiation
                ├─ speed      negotiated speed
                └─ duplex     negotiated duplex

Everything here is a read of sysfs. Run against a machine reached over one of its
own interfaces, the link claims read a link that is carrying the session, and they
leave it exactly as they found it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, TypedDict, Unpack

from nuc13.kernel import read_int, read_link_name, read_text

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

type LinkKind = Literal["ethernet", "wireless"]

_INTERFACES = "/sys/class/net"
_NOT_READ = "(not read)"


class LinkIdentity(TypedDict):
    """What a required link must have negotiated.

    Attributes:
        interface: Kernel name of the interface whose link this is.
        speed_mbps: Negotiated speed.
        duplex: Negotiated duplex, ``full`` or ``half``.
    """

    interface: str
    speed_mbps: int
    duplex: str


class NetworkLink(TestComponent[LinkIdentity]):
    """Judge the link of an interface that must have one.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: The interface, and what its link must have negotiated.
    """

    def __init__(self, *, name: str, **identity: Unpack[LinkIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity

    async def configure(self, utils: TestUtils[LinkIdentity]) -> LinkIdentity:
        """Provide the identity the claims judge against."""
        return self._identity

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
    async def _carrier(self, utils: TestUtils[LinkIdentity]) -> Verdict:
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
    async def _speed(self, utils: TestUtils[LinkIdentity]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["speed_mbps"]
        # Measured without a window: a link speed is one of a few fixed rates, and
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
    async def _duplex(self, utils: TestUtils[LinkIdentity]) -> Verdict:
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


class InterfaceIdentity(TypedDict):
    """What one interface must show.

    Attributes:
        interface: Kernel interface name.
        pci_address: PCI function behind the interface.
        driver: Kernel driver bound to that function.
        kind: Wired or wireless.
    """

    interface: str
    pci_address: str
    driver: str
    kind: LinkKind


class NetworkInterface(TestComponent[InterfaceIdentity]):
    """Judge one network interface and, where it must have one, its link.

    Args:
        name: Node name used by logs, artifacts, and failures.
        link: The link the interface must have, or ``None`` where none is required.
            An interface composed without one gets no carrier claim that could pass
            or fail on whatever network happens to be in range.
        **identity: What the interface must show.
    """

    def __init__(
        self, *, name: str, link: NetworkLink | None, **identity: Unpack[InterfaceIdentity]
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._links = () if link is None else (link,)

    async def configure(self, utils: TestUtils[InterfaceIdentity]) -> InterfaceIdentity:
        """Provide the identity the claims judge against."""
        return self._identity

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
    async def _present(self, utils: TestUtils[InterfaceIdentity]) -> Verdict:
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
    async def _pci_address(self, utils: TestUtils[InterfaceIdentity]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["pci_address"]
        device = await read_link_name(utils, f"{_INTERFACES}/{interface}/device")
        reading = {"backing_device": device}
        if device == expected:
            return PASS(reading)
        raise TestFailure(
            f"{interface} is not on the expected PCI function",
            expected,
            device or "(no device behind it)",
            evidence=reading,
        )

    @evidence("bound_driver")
    async def _driver(self, utils: TestUtils[InterfaceIdentity]) -> Verdict:
        interface = utils.ctx["interface"]
        expected = utils.ctx["driver"]
        bound = await read_link_name(utils, f"{_INTERFACES}/{interface}/device/driver")
        reading = {"bound_driver": bound}
        if bound == expected:
            return PASS(reading)
        raise TestFailure(
            f"driver behind {interface} is not the expected one",
            expected,
            bound or "(no driver bound)",
            evidence=reading,
        )

    @evidence("wireless_radio", "observed_kind")
    async def _kind(self, utils: TestUtils[InterfaceIdentity]) -> Verdict:
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
            f"{interface} is not the expected kind of interface",
            expected,
            observed,
            evidence=reading,
        )


class Network(TestComponent):
    """Judge every network interface the machine must have.

    Args:
        name: Node name used by logs, artifacts, and failures.
        interfaces: Every interface to judge.
    """

    def __init__(self, *, name: str, interfaces: Sequence[NetworkInterface]) -> None:
        super().__init__(name=name)
        self._interfaces = tuple(interfaces)

    def build_suite(self) -> TestComponent:
        """Independent interfaces, so independent verdicts."""
        return self.parallel(self.name, self._interfaces)
