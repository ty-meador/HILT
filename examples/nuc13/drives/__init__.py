"""The NVMe drive, and every drive part number the suite can judge."""

from __future__ import annotations

from nuc13.drives.SamsungMzvl2512Hcjq00B00 import SamsungMzvl2512Hcjq00B00

# Every drive part number the suite can judge, each with the class that judges it.
mpns = {"MZVL2512HCJQ-00B00": SamsungMzvl2512Hcjq00B00}
