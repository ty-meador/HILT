"""The identity types of the product: what one MPN of it must contain.

A spec is identity in HILT's sense: fixed at composition time and handed to a
component's constructor. Every component in this package takes exactly one of these
types as ``spec=`` and judges the unit against it; none of them discovers what it
should have found.

The types mirror the composition, so the tree reads off the spec: ``MpnSpec`` holds
the part number's own identity and one spec per component it calls for, side by
side: the ``BoardSpec``, the ``FirmwareSpec``, the ``CpuSpec`` (cores, each with its
caches), the ``MemorySpec`` (banks), the drive, the PCI functions, the USB root
hubs, and the network interfaces. There is one ``MpnSpec`` per MPN, each in its own
module under ``nuc13/mpn/``. Nothing here describes a single unit: a serial number
or a hostname is the unit's, not the part number's.

All types are frozen. Sizes carry their unit in the field name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type CoreKind = Literal["performance", "efficient"]
type CacheKind = Literal["Data", "Instruction", "Unified"]
type LinkKind = Literal["ethernet", "wireless"]


# --------------------------------------------------------------------------- CPU


@dataclass(frozen=True, slots=True, kw_only=True)
class CacheSpec:
    """One cache as the core that uses it sees it.

    A cache belongs to the cores it serves, so it is part of each of those cores'
    identity and there is no list of caches beside the cores. A cache that several
    cores share (an efficient cluster's L2, the package's L3) appears in each of
    their specs with the same ``shared_cpus``.

    Attributes:
        name: Claim prefix within the core, such as ``l1d`` or ``l3``.
        level: Cache level, 1 to 3.
        kind: The kernel's ``cache/indexN/type``.
        size_kib: Size of this one cache.
        shared_cpus: Every logical CPU that uses this cache, the kernel's
            ``shared_cpu_list``. It includes the core's own threads.
    """

    name: str
    level: int
    kind: CacheKind
    size_kib: int
    shared_cpus: tuple[int, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CoreSpec:
    """One physical core.

    Attributes:
        core_id: The kernel's ``topology/core_id`` for this core.
        kind: Performance (hyper-threaded) or efficient core.
        cpus: Logical CPUs that are this core's hardware threads.
        maximum_khz: ``cpufreq/cpuinfo_max_freq`` every thread of the core reports.
        caches: Every cache this core uses, its own and the ones it shares.
    """

    core_id: int
    kind: CoreKind
    cpus: tuple[int, ...]
    maximum_khz: int
    caches: tuple[CacheSpec, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CpuSpec:
    """The processor package.

    Attributes:
        model: ``Model name`` as the kernel reports it.
        vendor_id: CPUID vendor string.
        family: CPUID family (``cpu family``), the first part of lshw's ``version``.
        model_number: CPUID model (``model``), the second part.
        stepping: Silicon revision (``stepping``), the third part.
        sockets: Populated sockets.
        minimum_khz: Lowest frequency the package scales to (lshw ``size``).
        maximum_khz: Highest frequency of any core (lshw ``capacity``).
        flags: Capabilities the package must advertise in ``/proc/cpuinfo``.
        p_cores: Every performance core, each with its caches.
        e_cores: Every efficient core, each with its caches.
    """

    model: str
    vendor_id: str
    family: int
    model_number: int
    stepping: int
    sockets: int
    minimum_khz: int
    maximum_khz: int
    flags: tuple[str, ...]
    p_cores: tuple[CoreSpec, ...]
    e_cores: tuple[CoreSpec, ...]

    @property
    def cores(self) -> tuple[CoreSpec, ...]:
        """Tuple of all cores."""
        return (*self.p_cores, *self.e_cores)

    @property
    def core_count(self) -> int:
        """Physical cores (lshw ``cores=``)."""
        return len(self.cores)

    @property
    def thread_count(self) -> int:
        """Logical CPUs (lshw ``threads=``)."""
        return sum(len(core.cpus) for core in self.cores)


# ------------------------------------------------------------------------ memory


@dataclass(frozen=True, slots=True, kw_only=True)
class IdentityEepromSpec:
    """Where a memory module's identity EEPROM answers.

    The small EEPROM on the module is its own statement of who made it and what it
    is rated for. Unlike SMBIOS it can be readable without root, but only for a
    module whose EEPROM the kernel binds.

    Attributes:
        smbus: PCI address of the SMBus controller the EEPROM sits behind. The I2C
            bus number is assigned at boot; this address is not.
        address: The EEPROM's I2C address on that bus.
        manufacturer_id: The JEDEC JEP106 code of the bank's ``vendor`` as the EEPROM
            stores it: two bytes in hex, continuation count (with its parity bit)
            then code.
    """

    smbus: str
    address: int
    manufacturer_id: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryBankSpec:
    """One populated SODIMM slot.

    lshw took ``part_number``, ``vendor``, and ``speed_mhz`` from SMBIOS, which only
    root can read on the device. They are recorded here as the bank's identity for
    every bank, but a claim may only judge what the run's user can actually read.
    For these three that is the module's identity EEPROM, so they are judged on a
    bank that declares one and recorded without a claim on a bank whose
    ``identity_eeprom`` is ``None``.

    Attributes:
        slot: lshw's slot label, such as ``Controller0-ChannelA-DIMM0``.
        controller: Memory controller index (EDAC ``mcN``).
        channel: Channel on that controller.
        dimm: DIMM position on that channel.
        edac_dimm: The kernel's index for that position (EDAC ``mcN/dimmM``). EDAC
            numbers a controller's positions channel by channel and lists only the
            populated ones.
        size_mib: Module capacity.
        technology: Memory generation, such as ``DDR4``.
        speed_mhz: Rated clock.
        vendor: Module manufacturer.
        part_number: Module part number.
        identity_eeprom: Where the module's identity EEPROM answers, or ``None``
            when the kernel exposes none for this slot to an ordinary user.
    """

    slot: str
    controller: int
    channel: int
    dimm: int
    edac_dimm: int
    size_mib: int
    technology: str
    speed_mhz: int
    vendor: str
    part_number: str
    identity_eeprom: IdentityEepromSpec | None


@dataclass(frozen=True, slots=True, kw_only=True)
class MemorySpec:
    """System memory as a whole.

    Attributes:
        installed_mib: Total the board reports installed (lshw ``size``).
        usable_floor_mib: Least ``MemTotal`` an intact system shows; firmware and the
            integrated GPU reserve the rest.
        banks: Every populated slot.
    """

    installed_mib: int
    usable_floor_mib: int
    banks: tuple[MemoryBankSpec, ...]


# ---------------------------------------------------------------------- firmware


@dataclass(frozen=True, slots=True, kw_only=True)
class FirmwareSpec:
    """The BIOS.

    Attributes:
        vendor: DMI ``bios_vendor``.
        version: DMI ``bios_version``.
        release_date: DMI ``bios_date``, ``MM/DD/YYYY``.
        uefi: Whether the system must have booted through UEFI.
    """

    vendor: str
    version: str
    release_date: str
    uefi: bool


# ----------------------------------------------------------------------- storage


@dataclass(frozen=True, slots=True, kw_only=True)
class VolumeSpec:
    """One partition of the drive.

    Attributes:
        name: Node name, such as ``efi`` or ``root``.
        device: Kernel name, such as ``nvme0n1p1``.
        filesystem: Filesystem type as ``lsblk`` names it (``vfat``, ``ext4``, ``swap``).
        mountpoint: Where it must be mounted, or ``None`` when it is not mounted
            directly (the swap partition is used through dm-crypt).
        capacity_mib: Partition size as lshw prints it. lshw rounds down to a whole
            unit and moves up a unit only above 10240 of the smaller one, so a size
            is whole MiB up to 10240MiB and whole GiB beyond; a claim must allow
            for exactly that rounding.
    """

    name: str
    device: str
    filesystem: str
    mountpoint: str | None
    capacity_mib: int


@dataclass(frozen=True, slots=True, kw_only=True)
class NvmeSpec:
    """The NVMe drive: controller, its one namespace, and the partitions on it.

    Attributes:
        controller: Kernel controller name, such as ``nvme0``.
        pci_address: The controller's PCI function.
        model: Model string.
        firmware: Firmware revision.
        namespace: Kernel block device of the namespace, such as ``nvme0n1``.
        capacity_gb: Namespace size in decimal gigabytes, as the label states it.
        logical_sector_bytes: Logical sector size.
        partition_table: Partition table type (``gpt``).
        volumes: Every partition, in table order.
    """

    controller: str
    pci_address: str
    model: str
    firmware: str
    namespace: str
    capacity_gb: int
    logical_sector_bytes: int
    partition_table: str
    volumes: tuple[VolumeSpec, ...]


# ----------------------------------------------------------------------- PCI/USB


@dataclass(frozen=True, slots=True, kw_only=True)
class PciDeviceSpec:
    """One PCI function.

    The numeric IDs are what the function itself reports and what a claim judges.
    ``description`` and ``product`` are the pci.ids names lshw printed for those
    numbers: a label for the reader, never a reading.

    Attributes:
        name: Node name, such as ``display`` or ``usb-xhci-pch``.
        address: Domain-qualified address, such as ``0000:00:02.0``.
        class_code: Base class and subclass, the ``[0300]`` of ``lspci -nn``.
        vendor_id: Vendor ID, the ``8086`` of ``[8086:a7a0]``.
        device_id: Device ID, the ``a7a0`` of ``[8086:a7a0]``.
        description: lshw's class description, such as ``VGA compatible controller``.
        product: Product string.
        driver: Kernel driver that must be bound, or ``None`` where the log shows
            the function ``UNCLAIMED``.
    """

    name: str
    address: str
    class_code: int
    vendor_id: int
    device_id: int
    description: str
    product: str
    driver: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class UsbRootHubSpec:
    """One USB root hub of an xHCI controller.

    Attributes:
        bus: USB bus number (``usbN``).
        controller_address: PCI function of the controller that owns the bus.
        usb_version: Version the hub advertises, such as ``2.00`` or ``3.10``.
        ports: Downstream ports (lshw ``slots=``).
        speed_mbps: Bus speed.
    """

    bus: int
    controller_address: str
    usb_version: str
    ports: int
    speed_mbps: int


@dataclass(frozen=True, slots=True, kw_only=True)
class UsbDeviceSpec:
    """One device permanently attached to a USB bus.

    The numeric IDs are what the device reports and what a claim judges. ``product``
    is the usb.ids name lshw printed for them: a label for the reader, since a
    device need not carry a product string of its own.

    Attributes:
        name: Node name, such as ``bluetooth``.
        bus: USB bus number it hangs off.
        port: Root-hub port it is wired to (the ``a`` of lshw's ``usb@3:a``).
        vendor_id: ``idVendor``, the ``8087`` of lsusb's ``8087:0033``.
        product_id: ``idProduct``, the ``0033`` of lsusb's ``8087:0033``.
        product: Product string.
        driver: Kernel driver bound to its interface.
    """

    name: str
    bus: int
    port: int
    vendor_id: int
    product_id: int
    product: str
    driver: str


# ----------------------------------------------------------------------- network


@dataclass(frozen=True, slots=True, kw_only=True)
class LinkSpec:
    """The link an interface must have up, and what it must have negotiated.

    Attributes:
        speed_mbps: Negotiated speed.
        duplex: Negotiated duplex, ``full`` or ``half``.
    """

    speed_mbps: int
    duplex: str


@dataclass(frozen=True, slots=True, kw_only=True)
class NetworkSpec:
    """One network interface.

    Attributes:
        name: Node name, such as ``ethernet``.
        interface: Kernel interface name.
        pci_address: PCI function behind the interface.
        kind: Wired or wireless.
        driver: Kernel driver.
        link: The link the interface must have, or ``None`` where none is required
            and nothing is claimed about its carrier. The wireless interface is
            unassociated in the log, so it has none.
    """

    name: str
    interface: str
    pci_address: str
    kind: LinkKind
    driver: str
    link: LinkSpec | None


# ------------------------------------------------------------------- board / MPN


@dataclass(frozen=True, slots=True, kw_only=True)
class BoardSpec:
    """The motherboard itself: what the board reports about its own identity.

    The parts mounted on the board are not here. They are components of the MPN,
    each with its own spec on ``MpnSpec``.

    Attributes:
        vendor: DMI ``board_vendor``.
        product: DMI ``board_name``.
        version: DMI ``board_version``.
    """

    vendor: str
    product: str
    version: str


@dataclass(frozen=True, slots=True, kw_only=True)
class MpnSpec:
    """One MPN of the product: its identity, and one spec per component it calls for.

    Every unit built as this part number must match all of it.

    Attributes:
        mpn: The manufacturer part number. A unit reports it as DMI ``product_sku``.
        vendor: DMI ``sys_vendor``.
        product_name: DMI ``product_name``, the model the part number belongs to.
        version: DMI ``product_version``.
        family: DMI ``product_family``.
        chassis_type: SMBIOS chassis type code.
        board: The motherboard.
        firmware: The BIOS.
        cpu: The processor.
        memory: System memory.
        drive: The NVMe drive.
        pci: Every PCI function the log lists.
        usb_hubs: Every USB root hub.
        usb_devices: Devices permanently attached over USB.
        networks: Every network interface.
    """

    mpn: str
    vendor: str
    product_name: str
    version: str
    family: str
    chassis_type: int
    board: BoardSpec
    firmware: FirmwareSpec
    cpu: CpuSpec
    memory: MemorySpec
    drive: NvmeSpec
    pci: tuple[PciDeviceSpec, ...]
    usb_hubs: tuple[UsbRootHubSpec, ...]
    usb_devices: tuple[UsbDeviceSpec, ...]
    networks: tuple[NetworkSpec, ...]
