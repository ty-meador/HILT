"""Module part number ``4ATF51264HZ-3G2R1``: what that module is."""

from __future__ import annotations

from nuc13.memory.Memory import Sodimm


class Micron4Atf51264Hz3G2R1(Sodimm):
    """Module part number ``4ATF51264HZ-3G2R1``: a 4GiB DDR4-3200 SODIMM from Micron.

    Args:
        name: Node name used by logs, artifacts, and failures.
        slot: Label of the slot the module is fitted in.
        edac_directory: The kernel's directory for that position.
        location: The position as that directory words it.
        eeprom: Shell pattern for the module's identity EEPROM, or ``None`` where the
            kernel exposes none to an ordinary user.
    """

    def __init__(
        self, *, name: str, slot: str, edac_directory: str, location: str, eeprom: str | None
    ) -> None:
        super().__init__(
            name=name,
            slot=slot,
            edac_directory=edac_directory,
            location=location,
            eeprom=eeprom,
            size_mib=4096,
            technology="DDR4",
            speed_mhz=3200,
            vendor="Micron Technology",
            # JEDEC bank 1, code 0x2c.
            manufacturer_id="802c",
            part_number="4ATF51264HZ-3G2R1",
        )
