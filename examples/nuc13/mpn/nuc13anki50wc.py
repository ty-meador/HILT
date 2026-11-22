"""The golden record for MPN NUC13ANKi50WC, transcribed from ``lshw.log``.

One part number, one value: ``RECORD``. The log was taken from one unit built as
this MPN; the record keeps what every unit of the MPN must match and leaves out what
belongs to that unit alone. Each block names the log node it came from. Three kinds
of value are not literal copies of a log line, and each is marked where it appears:

- lshw prints a cache as a per-class total. Each core's spec carries the one cache
  it uses: that total divided over the core class it serves (4 performance cores,
  8 efficient cores in clusters of 4), and the CPUs the cache is shared with.
- lshw prints ``cores=12 threads=16`` and the host bridge names the ``4p+8e`` split;
  the per-core rows spell that population out.
- Serial numbers, UUIDs, MAC addresses, and the hostname are left out on purpose.
  They identify one unit, not the part number, and they do not belong in a file
  that gets shared. The serial and the UUID are also readable only by root.
"""

from __future__ import annotations

from nuc13.spec import (
    BoardSpec,
    CacheSpec,
    CoreSpec,
    CpuSpec,
    FirmwareSpec,
    IdentityEepromSpec,
    LinkSpec,
    MemoryBankSpec,
    MemorySpec,
    MpnSpec,
    NetworkSpec,
    NvmeSpec,
    PciDeviceSpec,
    UsbDeviceSpec,
    UsbRootHubSpec,
    VolumeSpec,
)

# --------------------------------------------------------------------------- CPU
# *-cpu: 13th Gen Intel(R) Core(TM) i5-1340P, size 400MHz, capacity 4600MHz,
# cores=12 enabledcores=12 threads=16. Host bridge: "Raptor Lake-P/U 4p+8e cores".

_ALL_CPUS = tuple(range(16))

# *-cache:3 and *-cache:7: one 12MiB L3 for the whole package, which the log lists
# once under each core type's handle. Every core's spec carries this same entry.
_L3 = CacheSpec(name="l3", level=3, kind="Unified", size_kib=12288, shared_cpus=_ALL_CPUS)


def _performance_caches(cpus: tuple[int, ...]) -> tuple[CacheSpec, ...]:
    """The caches of one performance core: all its own but the L3.

    *-cache:0..2 print the four cores' sum, 192KiB, 128KiB, 5MiB: 48KiB, 32KiB, and
    1280KiB a core. Each is shared by the core's two hardware threads and no one else.
    """
    return (
        CacheSpec(name="l1d", level=1, kind="Data", size_kib=48, shared_cpus=cpus),
        CacheSpec(name="l1i", level=1, kind="Instruction", size_kib=32, shared_cpus=cpus),
        CacheSpec(name="l2", level=2, kind="Unified", size_kib=1280, shared_cpus=cpus),
        _L3,
    )


def _efficient_caches(cpu: int) -> tuple[CacheSpec, ...]:
    """The caches of one efficient core: its own L1 pair, its cluster's L2, the L3.

    *-cache:4..6 print the eight cores' sum, 256KiB, 512KiB, 4MiB: 32KiB and 64KiB a
    core, and two 2MiB L2 caches, one for each cluster of four (CPUs 8-11, 12-15).
    """
    cluster_start = cpu - (cpu - 8) % 4
    cluster = tuple(range(cluster_start, cluster_start + 4))
    return (
        CacheSpec(name="l1d", level=1, kind="Data", size_kib=32, shared_cpus=(cpu,)),
        CacheSpec(name="l1i", level=1, kind="Instruction", size_kib=64, shared_cpus=(cpu,)),
        CacheSpec(name="l2", level=2, kind="Unified", size_kib=2048, shared_cpus=cluster),
        _L3,
    )


# Four hyper-threaded performance cores: core_id 0, 4, 8, 12 on CPUs 0-7.
_PERFORMANCE_CORES = tuple(
    CoreSpec(
        core_id=4 * index,
        kind="performance",
        cpus=(2 * index, 2 * index + 1),
        maximum_khz=4_600_000,
        caches=_performance_caches((2 * index, 2 * index + 1)),
    )
    for index in range(4)
)

# Eight single-threaded efficient cores: core_id 16-23 on CPUs 8-15.
_EFFICIENT_CORES = tuple(
    CoreSpec(
        core_id=16 + index,
        kind="efficient",
        cpus=(8 + index,),
        maximum_khz=3_400_000,
        caches=_efficient_caches(8 + index),
    )
    for index in range(8)
)

_CPU = CpuSpec(
    model="13th Gen Intel(R) Core(TM) i5-1340P",
    vendor_id="GenuineIntel",
    # The log's "version: 6.186.2" is family.model.stepping.
    family=6,
    model_number=186,
    stepping=2,
    sockets=1,
    minimum_khz=400_000,
    maximum_khz=4_600_000,
    # A subset of the log's capabilities line: 64-bit, virtualization, and the
    # vector and crypto extensions a workload on this product would be built for.
    flags=("lm", "ht", "vmx", "sse4_2", "avx2", "avx_vnni", "aes", "sha_ni", "rdrand"),
    p_cores=_PERFORMANCE_CORES,
    e_cores=_EFFICIENT_CORES,
)

# ------------------------------------------------------------------------ memory
# *-memory: System Memory 8GiB; *-bank:0 and *-bank:1, one SODIMM per controller.

# Not in the log: where each module's identity EEPROM answers, keyed by memory
# controller. The kernel binds one for the module on controller 0 (ee1004 at 0x50
# behind the PCH SMBus function, *-serial:2 in the log); it was matched to *-bank:0
# by the one field that tells the two modules apart, which is left out here like
# every serial. 80 2c is JEDEC bank 1, code 0x2c: Micron Technology.
#
# Bank 1 has no entry. Its identity EEPROM is not exposed to an ordinary user, so
# its part number, vendor, and speed are recorded below but not judged.
_IDENTITY_EEPROMS = {
    0: IdentityEepromSpec(smbus="0000:00:1f.4", address=0x50, manufacturer_id="802c"),
}

_BANKS = tuple(
    MemoryBankSpec(
        slot=f"Controller{controller}-ChannelA-DIMM0",
        controller=controller,
        channel=0,
        dimm=0,
        # Not in the log: channel 0, slot 0 is the first position EDAC numbers.
        edac_dimm=0,
        size_mib=4096,
        technology="DDR4",
        speed_mhz=3200,
        vendor="Micron Technology",
        part_number="4ATF51264HZ-3G2R1",
        identity_eeprom=_IDENTITY_EEPROMS.get(controller),
    )
    for controller in range(2)
)

# usable_floor_mib is not in the log: it is the installed total less a 1GiB
# allowance for firmware and integrated-graphics reservations.
_MEMORY = MemorySpec(installed_mib=8192, usable_floor_mib=7168, banks=_BANKS)

# ---------------------------------------------------------------------- firmware
# *-firmware: BIOS, Intel Corp., capabilities include "uefi".

_FIRMWARE = FirmwareSpec(
    vendor="Intel Corp.",
    version="ANRPL357.0036.2024.1225.1724",
    release_date="12/25/2024",
    uefi=True,
)

# ----------------------------------------------------------------------- storage
# *-nvme: SAMSUNG MZVL2512HCJQ-00B00 at pci@0000:01:00.0; *-namespace:2 is
# /dev/nvme0n1, 476GiB (512GB), gpt, logicalsectorsize=512; *-volume:0..3.

_DRIVE = NvmeSpec(
    controller="nvme0",
    pci_address="0000:01:00.0",
    model="SAMSUNG MZVL2512HCJQ-00B00",
    firmware="GXA7801Q",
    namespace="nvme0n1",
    capacity_gb=512,
    logical_sector_bytes=512,
    partition_table="gpt",
    volumes=(
        VolumeSpec(
            name="efi",
            device="nvme0n1p1",
            filesystem="vfat",
            mountpoint="/boot/efi",
            capacity_mib=1021,
        ),
        VolumeSpec(
            name="recovery",
            device="nvme0n1p2",
            filesystem="vfat",
            mountpoint="/recovery",
            capacity_mib=4095,
        ),
        # The log prints 467GiB: above 10240MiB lshw rounds down to whole GiB.
        VolumeSpec(
            name="root",
            device="nvme0n1p3",
            filesystem="ext4",
            mountpoint="/",
            capacity_mib=467 * 1024,
        ),
        # The partition carries a swap signature but is not swapped on directly:
        # the active swap is the dm-crypt device (cryptswap) layered over it.
        VolumeSpec(
            name="swap",
            device="nvme0n1p4",
            filesystem="swap",
            mountpoint=None,
            capacity_mib=4095,
        ),
    ),
)

# --------------------------------------------------------------------------- PCI
# Every function under *-pci, in log order. driver=None is "UNCLAIMED" in the log,
# or a function the log shows with no driver= at all (the ISA bridge).
#
# The class, vendor, and device IDs are not in the log, which carries only the
# pci.ids names for them. They are the numbers `lspci -nn` prints beside those same
# names on a unit of this MPN, so each ID is the one the log's description and product
# were looked up from.

_PCI = (
    PciDeviceSpec(
        name="host-bridge",
        address="0000:00:00.0",
        class_code=0x0600,
        vendor_id=0x8086,
        device_id=0xA707,
        description="Host bridge",
        product="Raptor Lake-P/U 4p+8e cores Host Bridge/DRAM Controller",
        driver="igen6_edac",
    ),
    PciDeviceSpec(
        name="display",
        address="0000:00:02.0",
        class_code=0x0300,
        vendor_id=0x8086,
        device_id=0xA7A0,
        description="VGA compatible controller",
        product="Raptor Lake-P [Iris Xe Graphics]",
        driver="i915",
    ),
    PciDeviceSpec(
        name="pcie-graphics-port",
        address="0000:00:06.0",
        class_code=0x0604,
        vendor_id=0x8086,
        device_id=0xA74D,
        description="PCI bridge",
        product="Raptor Lake PCIe 4.0 Graphics Port",
        driver="pcieport",
    ),
    PciDeviceSpec(
        name="nvme-controller",
        address="0000:01:00.0",
        class_code=0x0108,
        vendor_id=0x144D,
        device_id=0xA80A,
        description="NVMe device",
        product="SAMSUNG MZVL2512HCJQ-00B00",
        driver="nvme",
    ),
    PciDeviceSpec(
        name="thunderbolt-root-port-0",
        address="0000:00:07.0",
        class_code=0x0604,
        vendor_id=0x8086,
        device_id=0xA76E,
        description="PCI bridge",
        product="Raptor Lake-P Thunderbolt 4 PCI Express Root Port #0",
        driver="pcieport",
    ),
    PciDeviceSpec(
        name="thunderbolt-root-port-2",
        address="0000:00:07.2",
        class_code=0x0604,
        vendor_id=0x8086,
        device_id=0xA72F,
        description="PCI bridge",
        product="Raptor Lake-P Thunderbolt 4 PCI Express Root Port #2",
        driver="pcieport",
    ),
    PciDeviceSpec(
        name="gna",
        address="0000:00:08.0",
        class_code=0x0880,
        vendor_id=0x8086,
        device_id=0xA74F,
        description="System peripheral",
        product="GNA Scoring Accelerator module",
        driver=None,
    ),
    PciDeviceSpec(
        name="crashlog-telemetry",
        address="0000:00:0a.0",
        class_code=0x1180,
        vendor_id=0x8086,
        device_id=0xA77D,
        description="Signal processing controller",
        product="Raptor Lake Crashlog and Telemetry",
        driver="intel_vsec",
    ),
    PciDeviceSpec(
        name="usb-xhci-thunderbolt",
        address="0000:00:0d.0",
        class_code=0x0C03,
        vendor_id=0x8086,
        device_id=0xA71E,
        description="USB controller",
        product="Raptor Lake-P Thunderbolt 4 USB Controller",
        driver="xhci_hcd",
    ),
    PciDeviceSpec(
        name="thunderbolt-nhi-0",
        address="0000:00:0d.2",
        class_code=0x0C03,
        vendor_id=0x8086,
        device_id=0xA73E,
        description="USB controller",
        product="Raptor Lake-P Thunderbolt 4 NHI #0",
        driver="thunderbolt",
    ),
    PciDeviceSpec(
        name="thunderbolt-nhi-1",
        address="0000:00:0d.3",
        class_code=0x0C03,
        vendor_id=0x8086,
        device_id=0xA76D,
        description="USB controller",
        product="Raptor Lake-P Thunderbolt 4 NHI #1",
        driver="thunderbolt",
    ),
    PciDeviceSpec(
        name="usb-xhci-pch",
        address="0000:00:14.0",
        class_code=0x0C03,
        vendor_id=0x8086,
        device_id=0x51ED,
        description="USB controller",
        product="Alder Lake PCH USB 3.2 xHCI Host Controller",
        driver="xhci_hcd",
    ),
    PciDeviceSpec(
        name="shared-sram",
        address="0000:00:14.2",
        class_code=0x0500,
        vendor_id=0x8086,
        device_id=0x51EF,
        description="RAM memory",
        product="Alder Lake PCH Shared SRAM",
        driver=None,
    ),
    PciDeviceSpec(
        name="wifi",
        address="0000:00:14.3",
        class_code=0x0280,
        vendor_id=0x8086,
        device_id=0x51F1,
        description="Wireless interface",
        product="Raptor Lake PCH CNVi WiFi",
        driver="iwlwifi",
    ),
    PciDeviceSpec(
        name="i2c-0",
        address="0000:00:15.0",
        class_code=0x0C80,
        vendor_id=0x8086,
        device_id=0x51E8,
        description="Serial bus controller",
        product="Alder Lake PCH Serial IO I2C Controller #0",
        driver="intel-lpss",
    ),
    PciDeviceSpec(
        name="i2c-1",
        address="0000:00:15.1",
        class_code=0x0C80,
        vendor_id=0x8086,
        device_id=0x51E9,
        description="Serial bus controller",
        product="Alder Lake PCH Serial IO I2C Controller #1",
        driver="intel-lpss",
    ),
    PciDeviceSpec(
        name="heci",
        address="0000:00:16.0",
        class_code=0x0780,
        vendor_id=0x8086,
        device_id=0x51E0,
        description="Communication controller",
        product="Alder Lake PCH HECI Controller",
        driver="mei_me",
    ),
    PciDeviceSpec(
        name="sata",
        address="0000:00:17.0",
        class_code=0x0106,
        vendor_id=0x8086,
        device_id=0x51D3,
        description="SATA controller",
        product="Alder Lake-P SATA AHCI Controller",
        driver="ahci",
    ),
    PciDeviceSpec(
        name="pcie-root-port-9",
        address="0000:00:1d.0",
        class_code=0x0604,
        vendor_id=0x8086,
        device_id=0x51B0,
        description="PCI bridge",
        product="Alder Lake PCI Express Root Port #9",
        driver="pcieport",
    ),
    PciDeviceSpec(
        name="ethernet-controller",
        address="0000:56:00.0",
        class_code=0x0200,
        vendor_id=0x8086,
        device_id=0x125C,
        description="Ethernet interface",
        product="Ethernet Controller I226-V",
        driver="igc",
    ),
    PciDeviceSpec(
        name="isa-bridge",
        address="0000:00:1f.0",
        class_code=0x0601,
        vendor_id=0x8086,
        device_id=0x519D,
        description="ISA bridge",
        product="Raptor Lake LPC/eSPI Controller",
        driver=None,
    ),
    PciDeviceSpec(
        name="audio",
        address="0000:00:1f.3",
        class_code=0x0401,
        vendor_id=0x8086,
        device_id=0x51CA,
        description="Multimedia audio controller",
        product="Raptor Lake-P/U/H cAVS",
        driver="snd_hda_intel",
    ),
    PciDeviceSpec(
        name="smbus",
        address="0000:00:1f.4",
        class_code=0x0C05,
        vendor_id=0x8086,
        device_id=0x51A3,
        description="SMBus",
        product="Alder Lake PCH-P SMBus Host Controller",
        driver="i801_smbus",
    ),
    PciDeviceSpec(
        name="spi",
        address="0000:00:1f.5",
        class_code=0x0C80,
        vendor_id=0x8086,
        device_id=0x51A4,
        description="Serial bus controller",
        product="Alder Lake-P PCH SPI Controller",
        driver="intel-spi",
    ),
)

# --------------------------------------------------------------------------- USB
# *-usbhost under *-usb:0 (Thunderbolt xHCI) and *-usb:3 (PCH xHCI).

_USB_HUBS = (
    UsbRootHubSpec(
        bus=1,
        controller_address="0000:00:0d.0",
        usb_version="2.00",
        ports=1,
        speed_mbps=480,
    ),
    # The log prints speed=10000Mbit/s for this hub. The kernel's own figure, in
    # /sys/bus/usb/devices/usb2/speed, is 20000, and the spec carries the value the
    # claim can read.
    UsbRootHubSpec(
        bus=2,
        controller_address="0000:00:0d.0",
        usb_version="3.10",
        ports=3,
        speed_mbps=20_000,
    ),
    UsbRootHubSpec(
        bus=3,
        controller_address="0000:00:14.0",
        usb_version="2.00",
        ports=12,
        speed_mbps=480,
    ),
    UsbRootHubSpec(
        bus=4,
        controller_address="0000:00:14.0",
        usb_version="3.10",
        ports=4,
        speed_mbps=10_000,
    ),
)

# *-usb under usb3 at usb@3:a: the Bluetooth half of the wireless module. The IDs
# are the `8087:0033` lsusb prints beside the log's product name.
_USB_DEVICES = (
    UsbDeviceSpec(
        name="bluetooth",
        bus=3,
        port=10,
        vendor_id=0x8087,
        product_id=0x0033,
        product="AX211 Bluetooth",
        driver="btusb",
    ),
)

# ----------------------------------------------------------------------- network
# *-network under *-pci:3 (wired, link=yes 1Gbit/s full) and *-network at 14.3
# (wireless, link=no).

_NETWORKS = (
    NetworkSpec(
        name="ethernet",
        interface="enp86s0",
        pci_address="0000:56:00.0",
        kind="ethernet",
        driver="igc",
        link=LinkSpec(speed_mbps=1000, duplex="full"),
    ),
    NetworkSpec(
        name="wifi",
        interface="wlo1",
        pci_address="0000:00:14.3",
        kind="wireless",
        driver="iwlwifi",
        # Unassociated in the log (link=no), so no link is required of it.
        link=None,
    ),
)

# ------------------------------------------------------------------- board / MPN
# *-core: Motherboard NUC13ANBi5. Root node: product NUC13ANKi5 (NUC13ANKi50WC),
# configuration chassis=mini family=AN sku=NUC13ANKi50WC. The log's sku= is the MPN.

_BOARD = BoardSpec(vendor="Intel Corporation", product="NUC13ANBi5", version="N13061-202")

RECORD = MpnSpec(
    mpn="NUC13ANKi50WC",
    vendor="Intel(R) Client Systems",
    product_name="NUC13ANKi5",
    version="N13119-202",
    family="AN",
    chassis_type=35,
    board=_BOARD,
    firmware=_FIRMWARE,
    cpu=_CPU,
    memory=_MEMORY,
    drive=_DRIVE,
    pci=_PCI,
    usb_hubs=_USB_HUBS,
    usb_devices=_USB_DEVICES,
    networks=_NETWORKS,
)
