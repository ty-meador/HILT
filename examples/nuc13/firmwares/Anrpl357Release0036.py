"""BIOS release ``ANRPL357.0036.2024.1225.1724``: what that release reports."""

from __future__ import annotations

from nuc13.firmwares.Firmware import Firmware


class Anrpl357Release0036(Firmware):
    """BIOS release ``ANRPL357.0036.2024.1225.1724``, dated 12/25/2024.

    Args:
        name: Node name used by logs, artifacts, and failures.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
    """

    def __init__(self, *, name: str, bom_mpns: list[str]) -> None:
        super().__init__(
            name=name,
            bom_mpns=bom_mpns,
            part_number="ANRPL357.0036.2024.1225.1724",
            bios_vendor="Intel Corp.",
            bios_version="ANRPL357.0036.2024.1225.1724",
            bios_date="12/25/2024",
            uefi=True,
        )
