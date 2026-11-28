"""System part number ``NUC13ANKi50WC``, and the suite to run for a unit of it.

Run it from the directory that holds the ``nuc13`` package:

    PYTHONPATH=. DUT_HOST=<address> DUT_USER=<login> hilt run nuc13/Nuc13AnKi50Wc.py
"""

from __future__ import annotations

from nuc13.cpus.CoreI51340P import CoreI51340P
from nuc13.drives.SamsungMzvl2512Hcjq00B00 import SamsungMzvl2512Hcjq00B00
from nuc13.firmwares.Anrpl357Release0036 import Anrpl357Release0036
from nuc13.memory.Micron4Atf51264Hz3G2R1 import Micron4Atf51264Hz3G2R1
from nuc13.motherboards.Nuc13AnBi5 import Nuc13AnBi5
from nuc13.Nuc13 import Nuc13


class Nuc13AnKi50Wc(Nuc13):
    """System part number ``NUC13ANKi50WC``, a ``NUC13ANKi5`` of the ``AN`` family.

    It names the part number of every part it is built from and builds none of them:
    the product does, where it lists them. What is this system's alone, how its drive
    leaves the line partitioned, is said here too.
    """

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
            motherboard=Nuc13AnBi5,
            firmware=Anrpl357Release0036,
            cpu=CoreI51340P,
            # One module in each of the board's slots.
            sodimms=[Micron4Atf51264Hz3G2R1, Micron4Atf51264Hz3G2R1],
            drive=SamsungMzvl2512Hcjq00B00,
            partition_table="gpt",
            # In table order. Sizes are the figures lshw prints.
            volumes={
                "efi": {"filesystem": "vfat", "mountpoint": "/boot/efi", "capacity_mib": 1021},
                "recovery": {
                    "filesystem": "vfat",
                    "mountpoint": "/recovery",
                    "capacity_mib": 4095,
                },
                "root": {"filesystem": "ext4", "mountpoint": "/", "capacity_mib": 467 * 1024},
                # The partition carries a swap signature but is not swapped on directly:
                # the active swap is the dm-crypt device layered over it.
                "swap": {"filesystem": "swap", "mountpoint": None, "capacity_mib": 4095},
            },
        )


__suite__ = Nuc13AnKi50Wc
