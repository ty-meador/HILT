"""Board part number ``NUC13ANBi5``: what that board is made of."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from nuc13.motherboards.Motherboard import Motherboard, NvmeSlot, SodimmSlot
from nuc13.network.Network import Network, NetworkInterface, NetworkLink
from nuc13.pci.PciBus import PciBus, PciDevice
from nuc13.usb.Usb import Usb, UsbDevice, UsbRootHub


class Nuc13AnBi5(Motherboard):
    """Board part number ``NUC13ANBi5``: its PCI functions, USB buses, and network interfaces.

    Its slots are the class's own, so the product can fit a module or a drive into one
    before any board is built.

    Args:
        name: Node name used by logs, artifacts, and failures.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
    """

    # The board has one slot per memory controller. EDAC numbers a controller's
    # positions channel by channel, so the only slot of each is its dimm0.
    SODIMM_SLOTS: ClassVar[Sequence[SodimmSlot]] = (
        {
            "slot": "Controller0-ChannelA-DIMM0",
            "edac_directory": "/sys/devices/system/edac/mc/mc0/dimm0",
            "location": "channel 0 slot 0",
            # The kernel binds this slot's EEPROM at 0x50 behind the PCH SMBus function.
            # The I2C bus number is handed out at boot, so the EEPROM is found from that
            # function's PCI address, which does not move.
            "eeprom": "/sys/bus/pci/devices/0000:00:1f.4/i2c-*/*-0050/eeprom",
        },
        {
            "slot": "Controller1-ChannelA-DIMM0",
            "edac_directory": "/sys/devices/system/edac/mc/mc1/dimm0",
            "location": "channel 0 slot 0",
            # This slot's EEPROM is not exposed to an ordinary user.
            "eeprom": None,
        },
    )

    # The board's one M.2 slot.
    M2_SLOT: ClassVar[NvmeSlot] = {
        "controller": "nvme0",
        "namespace": "nvme0n1",
        "pci_address": "0000:01:00.0",
    }

    def __init__(self, *, name: str, bom_mpns: list[str]) -> None:
        super().__init__(
            name=name,
            bom_mpns=bom_mpns,
            part_number="NUC13ANBi5",
            board_vendor="Intel Corporation",
            board_name="NUC13ANBi5",
            board_version="N13061-202",
            network=Network(
                name="network",
                interfaces=[
                    NetworkInterface(
                        name="ethernet",
                        interface="enp86s0",
                        pci_address="0000:56:00.0",
                        driver="igc",
                        kind="ethernet",
                        link=NetworkLink(
                            name="link",
                            interface="enp86s0",
                            speed_mbps=1000,
                            duplex="full",
                        ),
                    ),
                    NetworkInterface(
                        name="wifi",
                        interface="wlo1",
                        pci_address="0000:00:14.3",
                        driver="iwlwifi",
                        kind="wireless",
                        # Nothing associates the radio at the station, so no link is required.
                        link=None,
                    ),
                ],
            ),
            pci=PciBus(
                name="pci",
                devices=[
                    PciDevice(
                        name="host-bridge",
                        address="0000:00:00.0",
                        label=(
                            "Host bridge: Raptor Lake-P/U 4p+8e cores Host Bridge/DRAM Controller"
                        ),
                        class_code="0600",
                        vendor_device="8086:a707",
                        driver="igen6_edac",
                    ),
                    PciDevice(
                        name="display",
                        address="0000:00:02.0",
                        label="VGA compatible controller: Raptor Lake-P [Iris Xe Graphics]",
                        class_code="0300",
                        vendor_device="8086:a7a0",
                        driver="i915",
                    ),
                    PciDevice(
                        name="pcie-graphics-port",
                        address="0000:00:06.0",
                        label="PCI bridge: Raptor Lake PCIe 4.0 Graphics Port",
                        class_code="0604",
                        vendor_device="8086:a74d",
                        driver="pcieport",
                    ),
                    PciDevice(
                        name="nvme-controller",
                        address="0000:01:00.0",
                        label="NVMe device: SAMSUNG MZVL2512HCJQ-00B00",
                        class_code="0108",
                        vendor_device="144d:a80a",
                        driver="nvme",
                    ),
                    PciDevice(
                        name="thunderbolt-root-port-0",
                        address="0000:00:07.0",
                        label="PCI bridge: Raptor Lake-P Thunderbolt 4 PCI Express Root Port #0",
                        class_code="0604",
                        vendor_device="8086:a76e",
                        driver="pcieport",
                    ),
                    PciDevice(
                        name="thunderbolt-root-port-2",
                        address="0000:00:07.2",
                        label="PCI bridge: Raptor Lake-P Thunderbolt 4 PCI Express Root Port #2",
                        class_code="0604",
                        vendor_device="8086:a72f",
                        driver="pcieport",
                    ),
                    PciDevice(
                        name="gna",
                        address="0000:00:08.0",
                        label="System peripheral: GNA Scoring Accelerator module",
                        class_code="0880",
                        vendor_device="8086:a74f",
                        driver=None,
                    ),
                    PciDevice(
                        name="crashlog-telemetry",
                        address="0000:00:0a.0",
                        label="Signal processing controller: Raptor Lake Crashlog and Telemetry",
                        class_code="1180",
                        vendor_device="8086:a77d",
                        driver="intel_vsec",
                    ),
                    PciDevice(
                        name="usb-xhci-thunderbolt",
                        address="0000:00:0d.0",
                        label="USB controller: Raptor Lake-P Thunderbolt 4 USB Controller",
                        class_code="0c03",
                        vendor_device="8086:a71e",
                        driver="xhci_hcd",
                    ),
                    PciDevice(
                        name="thunderbolt-nhi-0",
                        address="0000:00:0d.2",
                        label="USB controller: Raptor Lake-P Thunderbolt 4 NHI #0",
                        class_code="0c03",
                        vendor_device="8086:a73e",
                        driver="thunderbolt",
                    ),
                    PciDevice(
                        name="thunderbolt-nhi-1",
                        address="0000:00:0d.3",
                        label="USB controller: Raptor Lake-P Thunderbolt 4 NHI #1",
                        class_code="0c03",
                        vendor_device="8086:a76d",
                        driver="thunderbolt",
                    ),
                    PciDevice(
                        name="usb-xhci-pch",
                        address="0000:00:14.0",
                        label="USB controller: Alder Lake PCH USB 3.2 xHCI Host Controller",
                        class_code="0c03",
                        vendor_device="8086:51ed",
                        driver="xhci_hcd",
                    ),
                    PciDevice(
                        name="shared-sram",
                        address="0000:00:14.2",
                        label="RAM memory: Alder Lake PCH Shared SRAM",
                        class_code="0500",
                        vendor_device="8086:51ef",
                        driver=None,
                    ),
                    PciDevice(
                        name="wifi",
                        address="0000:00:14.3",
                        label="Wireless interface: Raptor Lake PCH CNVi WiFi",
                        class_code="0280",
                        vendor_device="8086:51f1",
                        driver="iwlwifi",
                    ),
                    PciDevice(
                        name="i2c-0",
                        address="0000:00:15.0",
                        label="Serial bus controller: Alder Lake PCH Serial IO I2C Controller #0",
                        class_code="0c80",
                        vendor_device="8086:51e8",
                        driver="intel-lpss",
                    ),
                    PciDevice(
                        name="i2c-1",
                        address="0000:00:15.1",
                        label="Serial bus controller: Alder Lake PCH Serial IO I2C Controller #1",
                        class_code="0c80",
                        vendor_device="8086:51e9",
                        driver="intel-lpss",
                    ),
                    PciDevice(
                        name="heci",
                        address="0000:00:16.0",
                        label="Communication controller: Alder Lake PCH HECI Controller",
                        class_code="0780",
                        vendor_device="8086:51e0",
                        driver="mei_me",
                    ),
                    PciDevice(
                        name="sata",
                        address="0000:00:17.0",
                        label="SATA controller: Alder Lake-P SATA AHCI Controller",
                        class_code="0106",
                        vendor_device="8086:51d3",
                        driver="ahci",
                    ),
                    PciDevice(
                        name="pcie-root-port-9",
                        address="0000:00:1d.0",
                        label="PCI bridge: Alder Lake PCI Express Root Port #9",
                        class_code="0604",
                        vendor_device="8086:51b0",
                        driver="pcieport",
                    ),
                    PciDevice(
                        name="ethernet-controller",
                        address="0000:56:00.0",
                        label="Ethernet interface: Ethernet Controller I226-V",
                        class_code="0200",
                        vendor_device="8086:125c",
                        driver="igc",
                    ),
                    PciDevice(
                        name="isa-bridge",
                        address="0000:00:1f.0",
                        label="ISA bridge: Raptor Lake LPC/eSPI Controller",
                        class_code="0601",
                        vendor_device="8086:519d",
                        driver=None,
                    ),
                    PciDevice(
                        name="audio",
                        address="0000:00:1f.3",
                        label="Multimedia audio controller: Raptor Lake-P/U/H cAVS",
                        class_code="0401",
                        vendor_device="8086:51ca",
                        driver="snd_hda_intel",
                    ),
                    PciDevice(
                        name="smbus",
                        address="0000:00:1f.4",
                        label="SMBus: Alder Lake PCH-P SMBus Host Controller",
                        class_code="0c05",
                        vendor_device="8086:51a3",
                        driver="i801_smbus",
                    ),
                    PciDevice(
                        name="spi",
                        address="0000:00:1f.5",
                        label="Serial bus controller: Alder Lake-P PCH SPI Controller",
                        class_code="0c80",
                        vendor_device="8086:51a4",
                        driver="intel-spi",
                    ),
                ],
            ),
            usb=Usb(
                name="usb",
                hubs=[
                    UsbRootHub(
                        name="bus-1",
                        bus="usb1",
                        controller="0000:00:0d.0",
                        usb_version="2.00",
                        ports=1,
                        speed_mbps=480,
                    ),
                    # lshw prints 10000Mbit/s for this hub; the kernel's own figure, which is
                    # the one the claim reads, is 20000.
                    UsbRootHub(
                        name="bus-2",
                        bus="usb2",
                        controller="0000:00:0d.0",
                        usb_version="3.10",
                        ports=3,
                        speed_mbps=20_000,
                    ),
                    UsbRootHub(
                        name="bus-3",
                        bus="usb3",
                        controller="0000:00:14.0",
                        usb_version="2.00",
                        ports=12,
                        speed_mbps=480,
                    ),
                    UsbRootHub(
                        name="bus-4",
                        bus="usb4",
                        controller="0000:00:14.0",
                        usb_version="3.10",
                        ports=4,
                        speed_mbps=10_000,
                    ),
                ],
                devices=[
                    # The Bluetooth half of the wireless module, wired to port 10 of bus 3.
                    UsbDevice(
                        name="bluetooth",
                        device="3-10",
                        label="AX211 Bluetooth",
                        vendor_product="8087:0033",
                        driver="btusb",
                    ),
                ],
            ),
        )
