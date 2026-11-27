"""Drive part number ``MZVL2512HCJQ-00B00``: what that drive is."""

from __future__ import annotations

from nuc13.drives.NvmeDrive import NvmeDrive, PartitionTable


class SamsungMzvl2512Hcjq00B00(NvmeDrive):
    """Drive part number ``MZVL2512HCJQ-00B00``: a 512GB NVMe drive from Samsung.

    Args:
        name: Node name used by logs, artifacts, and failures.
        partitions: How the drive's namespace must be laid out.
        controller: Kernel name of the controller the drive is fitted as.
        namespace: Kernel block device of its namespace.
        pci_address: The PCI function the controller sits on.
    """

    def __init__(
        self,
        *,
        name: str,
        partitions: PartitionTable,
        controller: str,
        namespace: str,
        pci_address: str,
    ) -> None:
        super().__init__(
            name=name,
            partitions=partitions,
            controller=controller,
            namespace=namespace,
            pci_address=pci_address,
            model="SAMSUNG MZVL2512HCJQ-00B00",
            firmware_revision="GXA7801Q",
            capacity_gb=512,
            logical_sector_bytes=512,
        )
