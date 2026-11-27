"""System part number ``NUC13ANKi50WC``, and the suite to run for a unit of it.

Run it from the directory that holds the ``nuc13`` package:

    PYTHONPATH=. DUT_HOST=<address> DUT_USER=<login> hilt run nuc13/Nuc13AnKi50Wc.py
"""

from __future__ import annotations

from nuc13.drives.NvmeDrive import PartitionTable, Volume
from nuc13.Nuc13 import Nuc13


class Nuc13AnKi50Wc(Nuc13):
    """System part number ``NUC13ANKi50WC``, a ``NUC13ANKi5`` of the ``AN`` family."""

    def __init__(self, *, name: str) -> None:
        super().__init__(
            name=name,
            sys_vendor="Intel(R) Client Systems",
            product_name="NUC13ANKi5",
            product_version="N13119-202",
            product_sku="NUC13ANKi50WC",
            product_family="AN",
            # SMBIOS chassis code 35 is "Mini PC".
            chassis_type="35",
            part_counts={"motherboard": 1, "firmware": 1, "cpu": 1, "memory": 2, "drive": 1},
            # Sizes are the figures lshw prints.
            partitions=PartitionTable(
                name="partitions",
                namespace="nvme0n1",
                partition_table="gpt",
                volumes=[
                    Volume(
                        name="efi",
                        device="nvme0n1p1",
                        filesystem="vfat",
                        mountpoint="/boot/efi",
                        capacity_mib=1021,
                    ),
                    Volume(
                        name="recovery",
                        device="nvme0n1p2",
                        filesystem="vfat",
                        mountpoint="/recovery",
                        capacity_mib=4095,
                    ),
                    Volume(
                        name="root",
                        device="nvme0n1p3",
                        filesystem="ext4",
                        mountpoint="/",
                        capacity_mib=467 * 1024,
                    ),
                    # The partition carries a swap signature but is not swapped on
                    # directly: the active swap is the dm-crypt device layered over it.
                    Volume(
                        name="swap",
                        device="nvme0n1p4",
                        filesystem="swap",
                        mountpoint=None,
                        capacity_mib=4095,
                    ),
                ],
            ),
        )


__suite__ = Nuc13AnKi50Wc
