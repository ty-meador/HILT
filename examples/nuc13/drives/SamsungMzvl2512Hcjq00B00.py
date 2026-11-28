"""Drive part number ``MZVL2512HCJQ-00B00``: what that drive is."""

from __future__ import annotations

from collections.abc import Mapping

from nuc13.drives.NvmeDrive import NvmeDrive, VolumeLayout


class SamsungMzvl2512Hcjq00B00(NvmeDrive):
    """Drive part number ``MZVL2512HCJQ-00B00``: a 512GB NVMe drive from Samsung.

    Args:
        name: Node name used by logs, artifacts, and failures.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
        controller: Kernel name of the controller the drive is fitted as.
        namespace: Kernel block device of its namespace.
        pci_address: The PCI function the controller sits on.
        partition_table: Partition table type the namespace must carry.
        volumes: Every partition's layout by node name, in table order.
    """

    def __init__(
        self,
        *,
        name: str,
        bom_mpns: list[str],
        controller: str,
        namespace: str,
        pci_address: str,
        partition_table: str,
        volumes: Mapping[str, VolumeLayout],
    ) -> None:
        super().__init__(
            name=name,
            bom_mpns=bom_mpns,
            part_number="MZVL2512HCJQ-00B00",
            controller=controller,
            namespace=namespace,
            pci_address=pci_address,
            partition_table=partition_table,
            volumes=volumes,
            model="SAMSUNG MZVL2512HCJQ-00B00",
            firmware_revision="GXA7801Q",
            capacity_gb=512,
            logical_sector_bytes=512,
        )
