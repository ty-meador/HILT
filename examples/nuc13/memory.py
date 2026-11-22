"""System memory: the totals the kernel accounts for, then each module in its slot.

    memory
    ├─ installed-size             every module the kernel lists, summed
    ├─ usable-size                MemTotal, between the floor and what is installed
    ├─ populated-banks            how many modules the kernel lists
    ├─ correctable-errors         every controller's count since boot
    ├─ uncorrectable-errors
    └─ bank-N                     one per bank of the spec
       ├─ position                a module is listed at the bank's channel and slot
       └─ module
          ├─ size
          ├─ technology
          ├─ correctable-errors
          ├─ uncorrectable-errors
          └─ identity-eeprom      only where the kernel binds the module's EEPROM
             ├─ technology
             └─ identity
                ├─ part-number
                ├─ vendor
                └─ speed

Every reading is one the run's user can take: the EDAC driver's sysfs tree,
``/proc/meminfo``, and a module's identity EEPROM where the kernel leaves it
world-readable. SMBIOS, where lshw found the part numbers, is root's alone, so a
module's part number, vendor, and speed are judged only where its EEPROM can be read.
A bank without one has no such claims, instead of claims that could never take their
reading.
"""

from __future__ import annotations

import shlex
from typing import TypedDict

from nuc13.probe import parse_int, read_files, read_int, read_text
from nuc13.spec import MemoryBankSpec, MemorySpec

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

_EDAC = "/sys/devices/system/edac/mc"

# The DDR4 layout of the EEPROM (JEDEC Standard 21-C, Annex L), as (offset, length).
# The module's serial number is in there too; no range below reaches it, so it is
# never read, logged, or recorded.
_DEVICE_TYPE = (2, 1)
# The minimum cycle time is two bytes far apart: whole units of 125 ps at offset 18
# and a signed correction in picoseconds at offset 125. One read spans both, and
# everything between them is timing data.
_CYCLE_TIME = (18, 108)
_MANUFACTURER = (320, 2)
_PART_NUMBER = (329, 20)

# The one generation whose layout is decoded here, and its device-type code.
_DECODED_TECHNOLOGY = "DDR4"
_DECODED_DEVICE_TYPE = 0x0C


def _counts(files: dict[str, str]) -> dict[str, int] | None:
    """Parse each file's text as an integer.

    Returns:
        The integers under the same paths, or ``None`` when there are no files or
        any one of them does not hold an integer.
    """
    counts: dict[str, int] = {}
    for path, text in files.items():
        count = parse_int(text)
        if count is None:
            return None
        counts[path] = count
    return counts or None


async def _memory_total_kib(utils: TestUtils) -> int | None:
    """Read ``MemTotal`` from ``/proc/meminfo`` in KiB (the file's ``kB``)."""
    result = await utils.exec("grep -m 1 '^MemTotal:' /proc/meminfo")
    if result.exit_code != 0:
        return None
    return parse_int(result.stdout.strip().removeprefix("MemTotal:").removesuffix("kB"))


async def _read_eeprom(utils: TestUtils, eeprom: str, field: tuple[int, int]) -> bytes | None:
    """Read one field of a module's identity EEPROM.

    Args:
        utils: The claim's utilities.
        eeprom: Shell pattern that resolves to the EEPROM's sysfs file.
        field: The field's offset and length in bytes.

    Returns:
        The field's bytes, or ``None`` unless exactly that many came back.
    """
    offset, length = field
    result = await utils.exec(f"od -An -v -tx1 -j {offset} -N {length} {eeprom}")
    if result.exit_code != 0:
        return None
    try:
        content = bytes.fromhex(result.stdout)
    except ValueError:
        return None
    return content if len(content) == length else None


def _rated_speed_mhz(cycle_time: bytes) -> int | None:
    """The speed a module is rated for, from its cycle-time field; ``None`` if blank."""
    correction_ps = int.from_bytes(cycle_time[-1:], signed=True)
    cycle_ps = cycle_time[0] * 125 + correction_ps
    if cycle_ps <= 0:
        return None
    # Two transfers per clock cycle, which is the figure a module is sold by and the
    # one SMBIOS and lshw print: a 625 ps cycle is "3200 MHz".
    return round(2_000_000 / cycle_ps)


class EepromIdentity(TypedDict):
    """What a module's EEPROM must say, and where the EEPROM is."""

    eeprom: str
    technology: str
    part_number: str
    vendor: str
    manufacturer_id: str
    speed_mhz: int


class IdentityEeprom(TestComponent[EepromIdentity]):
    """The small EEPROM on a memory module that states the module's own identity.

    JEDEC calls it Serial Presence Detect (SPD). It holds the module's generation,
    part number, maker, and rated speed, and each is judged against the bank's
    record. Only the DDR4 layout is decoded.
    """

    def __init__(self, *, name: str, spec: MemoryBankSpec) -> None:
        """Hold the bank whose module this EEPROM describes.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The bank's identity. Its ``identity_eeprom`` says where the EEPROM
                answers.

        Raises:
            ValueError: The bank declares no EEPROM, so there is nothing to read.
        """
        super().__init__(name=name)
        if spec.identity_eeprom is None:
            raise ValueError(f"{spec.slot} declares no identity EEPROM")
        self._spec = spec
        self._location = spec.identity_eeprom

    async def configure(self, utils: TestUtils[EepromIdentity]) -> EepromIdentity:
        """Provide the module identity the EEPROM is judged against."""
        # The I2C bus number is handed out at boot, so the EEPROM is found from the
        # PCI function of its SMBus controller, which does not move.
        controller = shlex.quote(f"/sys/bus/pci/devices/{self._location.smbus}")
        return {
            "eeprom": f"{controller}/i2c-*/*-{self._location.address:04x}/eeprom",
            "technology": self._spec.technology,
            "part_number": self._spec.part_number,
            "vendor": self._spec.vendor,
            "manufacturer_id": self._location.manufacturer_id,
            "speed_mhz": self._spec.speed_mhz,
        }

    def build_suite(self) -> TestComponent:
        """Technology first: on another generation the other fields are at other offsets.

        Only DDR4 is recognised, so no other layout is ever read as if it were DDR4.
        The identity fields are independent: a wrong maker does not hide a wrong
        speed.
        """
        return self.sequence(
            self.name,
            [
                self.test("technology", self._technology),
                self.parallel(
                    "identity",
                    [
                        self.test("part-number", self._part_number),
                        self.test("vendor", self._vendor),
                        self.test("speed", self._speed),
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("eeprom_technology")
    async def _technology(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = utils.ctx["technology"]
        device_type = await _read_eeprom(utils, utils.ctx["eeprom"], _DEVICE_TYPE)
        if device_type is None:
            raise TestFailure("module EEPROM could not be read", expected, "(not read)")
        code = device_type[0]
        decoded = code == _DECODED_DEVICE_TYPE
        technology = _DECODED_TECHNOLOGY if decoded else f"device type {code:#04x}"
        reading = {"eeprom_technology": technology}
        if technology == expected:
            return PASS(reading)
        raise TestFailure(
            "module EEPROM does not state the recorded generation",
            expected,
            technology,
            evidence=reading,
        )

    @evidence("eeprom_part_number")
    async def _part_number(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = utils.ctx["part_number"]
        field = await _read_eeprom(utils, utils.ctx["eeprom"], _PART_NUMBER)
        # The field is ASCII, padded with spaces to its full width.
        part_number = None if field is None else field.decode("ascii", "replace").strip()
        reading = {"eeprom_part_number": part_number}
        if part_number == expected:
            return PASS(reading)
        if part_number is None:
            raise TestFailure("module EEPROM could not be read", expected, "(not read)")
        raise TestFailure(
            "module is not the recorded part", expected, part_number, evidence=reading
        )

    @evidence("eeprom_manufacturer_id")
    async def _vendor(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = f"{utils.ctx['vendor']} (JEDEC {utils.ctx['manufacturer_id']})"
        field = await _read_eeprom(utils, utils.ctx["eeprom"], _MANUFACTURER)
        manufacturer = None if field is None else field.hex()
        reading = {"eeprom_manufacturer_id": manufacturer}
        if manufacturer == utils.ctx["manufacturer_id"]:
            return PASS(reading)
        if manufacturer is None:
            raise TestFailure("module EEPROM could not be read", expected, "(not read)")
        raise TestFailure(
            "module is from another maker", expected, f"JEDEC {manufacturer}", evidence=reading
        )

    @evidence("eeprom_speed_mhz")
    async def _speed(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = utils.ctx["speed_mhz"]
        cycle_time = await _read_eeprom(utils, utils.ctx["eeprom"], _CYCLE_TIME)
        speed = None if cycle_time is None else _rated_speed_mhz(cycle_time)
        utils.measure("eeprom_speed_mhz", speed, unit="MHz", limits=(expected, expected))
        reading = {"eeprom_speed_mhz": speed}
        if speed == expected:
            return PASS(reading)
        if speed is None:
            raise TestFailure("module EEPROM gave no rated speed", f"{expected} MHz", "(not read)")
        raise TestFailure(
            "module is rated for another speed",
            f"{expected} MHz",
            f"{speed} MHz",
            evidence=reading,
        )


class BankIdentity(TypedDict):
    """What one bank must hold, and the kernel's directory for it."""

    slot: str
    edac_directory: str
    location: str
    size_mib: int
    technology: str


class MemoryBank(TestComponent[BankIdentity]):
    """Judge one populated slot: the module is there, and it is the module recorded."""

    def __init__(self, *, name: str, spec: MemoryBankSpec) -> None:
        """Build the bank, and its EEPROM where the spec says one can be read.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The bank's identity.
        """
        super().__init__(name=name)
        self._spec = spec
        # None or one: the population is identity, so it is settled here and
        # build_suite only lists it.
        self._identity_eeprom = (
            ()
            if spec.identity_eeprom is None
            else (IdentityEeprom(name="identity-eeprom", spec=spec),)
        )

    async def configure(self, utils: TestUtils[BankIdentity]) -> BankIdentity:
        """Provide the module identity the bank's claims judge against."""
        directory = f"{_EDAC}/mc{self._spec.controller}/dimm{self._spec.edac_dimm}"
        return {
            "slot": self._spec.slot,
            "edac_directory": directory,
            "location": f"channel {self._spec.channel} slot {self._spec.dimm}",
            "size_mib": self._spec.size_mib,
            "technology": self._spec.technology,
        }

    def build_suite(self) -> TestComponent:
        """Position first: with no module there, the rest would be readings of nothing.

        What follows is independent, so a module of the wrong size still has its
        error counts and its EEPROM reported.
        """
        return self.sequence(
            self.name,
            [
                self.test("position", self._position),
                self.parallel(
                    "module",
                    [
                        self.test("size", self._size),
                        self.test("technology", self._technology),
                        self.test(
                            "correctable-errors",
                            self._error_count,
                            counter="dimm_ce_count",
                            kind="correctable",
                        ),
                        self.test(
                            "uncorrectable-errors",
                            self._error_count,
                            counter="dimm_ue_count",
                            kind="uncorrectable",
                        ),
                        *self._identity_eeprom,
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("dimm_location")
    async def _position(self, utils: TestUtils[BankIdentity]) -> Verdict:
        expected = utils.ctx["location"]
        location = await read_text(utils, f"{utils.ctx['edac_directory']}/dimm_location")
        reading = {"dimm_location": location}
        if location == expected:
            return PASS(reading)
        if location is None:
            # EDAC creates a directory only for a populated position.
            raise TestFailure(
                f"the kernel lists no module for {utils.ctx['slot']}", expected, "(not read)"
            )
        raise TestFailure(
            f"the module for {utils.ctx['slot']} is somewhere else",
            expected,
            location,
            evidence=reading,
        )

    @evidence("dimm_size_mib")
    async def _size(self, utils: TestUtils[BankIdentity]) -> Verdict:
        expected = utils.ctx["size_mib"]
        size = await read_int(utils, f"{utils.ctx['edac_directory']}/size")
        utils.measure("dimm_size_mib", size, unit="MiB", limits=(expected, expected))
        reading = {"dimm_size_mib": size}
        if size == expected:
            return PASS(reading)
        if size is None:
            raise TestFailure("module size could not be read", f"{expected} MiB", "(not read)")
        raise TestFailure(
            "module is the wrong size", f"{expected} MiB", f"{size} MiB", evidence=reading
        )

    @evidence("dimm_memory_type")
    async def _technology(self, utils: TestUtils[BankIdentity]) -> Verdict:
        expected = utils.ctx["technology"]
        memory_type = await read_text(utils, f"{utils.ctx['edac_directory']}/dimm_mem_type")
        reading = {"dimm_memory_type": memory_type}
        # EDAC puts the module's buffering in front of the generation
        # ("Unbuffered-DDR4"); the claim is about the generation alone.
        if memory_type is not None and expected in memory_type.split("-"):
            return PASS(reading)
        if memory_type is None:
            raise TestFailure("module type could not be read", expected, "(not read)")
        raise TestFailure(
            "module is the wrong memory generation", expected, memory_type, evidence=reading
        )

    @evidence("dimm_errors")
    async def _error_count(
        self, utils: TestUtils[BankIdentity], *, counter: str, kind: str
    ) -> Verdict:
        errors = await read_int(utils, f"{utils.ctx['edac_directory']}/{counter}")
        utils.measure(f"{kind}_errors", errors, limits=(0, 0))
        reading = {"dimm_errors": errors}
        if errors == 0:
            return PASS(reading)
        if errors is None:
            raise TestFailure(f"{kind} error count could not be read", 0, "(not read)")
        raise TestFailure(
            f"{utils.ctx['slot']} has logged {kind} errors since boot",
            0,
            errors,
            evidence=reading,
        )


class MemoryTotals(TypedDict):
    """What the system's memory must add up to."""

    installed_mib: int
    usable_floor_mib: int
    bank_count: int


class Memory(TestComponent[MemoryTotals]):
    """Judge system memory as a whole, then every bank that makes it up."""

    def __init__(self, *, name: str, spec: MemorySpec) -> None:
        """Build system memory and one bank per populated slot of the spec.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The totals and, nested in them, each bank's identity.
        """
        super().__init__(name=name)
        self._spec = spec
        self._banks = tuple(
            MemoryBank(name=f"bank-{index}", spec=bank) for index, bank in enumerate(spec.banks)
        )

    async def configure(self, utils: TestUtils[MemoryTotals]) -> MemoryTotals:
        """Provide the totals the system-wide claims judge against."""
        return {
            "installed_mib": self._spec.installed_mib,
            "usable_floor_mib": self._spec.usable_floor_mib,
            "bank_count": len(self._spec.banks),
        }

    def build_suite(self) -> TestComponent:
        """Independent totals and independent banks: one bad module never hides another."""
        return self.parallel(
            self.name,
            [
                self.test("installed-size", self._installed_size),
                self.test("usable-size", self._usable_size),
                self.test("populated-banks", self._populated_banks),
                self.test(
                    "correctable-errors",
                    self._error_count,
                    counter="ce_count",
                    kind="correctable",
                ),
                self.test(
                    "uncorrectable-errors",
                    self._error_count,
                    counter="ue_count",
                    kind="uncorrectable",
                ),
                *self._banks,
            ],
        )

    # The kernel, not the spec, decides how many controllers and modules there are,
    # so the system-wide claims below ask for every one it lists.

    @evidence("dimm_sizes_mib")
    async def _installed_size(self, utils: TestUtils[MemoryTotals]) -> Verdict:
        expected = utils.ctx["installed_mib"]
        sizes = _counts(await read_files(utils, f"{_EDAC}/mc*/dimm*/size"))
        total = None if sizes is None else sum(sizes.values())
        utils.measure("installed_size_mib", total, unit="MiB", limits=(expected, expected))
        reading = {"dimm_sizes_mib": sizes}
        if total == expected:
            return PASS(reading)
        if total is None:
            raise TestFailure("module sizes could not be read", f"{expected} MiB", "(not read)")
        raise TestFailure(
            "the modules do not add up to the installed memory",
            f"{expected} MiB",
            f"{total} MiB",
            evidence=reading,
        )

    @evidence("memory_total_kib")
    async def _usable_size(self, utils: TestUtils[MemoryTotals]) -> Verdict:
        floor, installed = utils.ctx["usable_floor_mib"], utils.ctx["installed_mib"]
        window = f"{floor} to {installed} MiB"
        total_kib = await _memory_total_kib(utils)
        usable_mib = None if total_kib is None else total_kib / 1024
        usable = utils.measure("usable_size_mib", usable_mib, unit="MiB", limits=(floor, installed))
        reading = {"memory_total_kib": total_kib}
        if usable.value is None:
            raise TestFailure("MemTotal could not be read from /proc/meminfo", window, "(not read)")
        if usable.in_limits:
            return PASS(reading)
        raise TestFailure(
            "usable memory is outside what the installed modules allow",
            window,
            f"{usable.value:.0f} MiB",
            evidence=reading,
        )

    @evidence("dimm_locations")
    async def _populated_banks(self, utils: TestUtils[MemoryTotals]) -> Verdict:
        expected = utils.ctx["bank_count"]
        # EDAC creates a module directory only for a populated position, so the
        # directories it lists are the modules it found.
        locations = await read_files(utils, f"{_EDAC}/mc*/dimm*/dimm_location")
        populated = len(locations) if locations else None
        utils.measure("populated_banks", populated, limits=(expected, expected))
        reading = {"dimm_locations": locations}
        if populated == expected:
            return PASS(reading)
        if populated is None:
            raise TestFailure("the kernel lists no memory modules", expected, "(not read)")
        raise TestFailure(
            "the kernel sees a different number of modules", expected, populated, evidence=reading
        )

    @evidence("controller_errors")
    async def _error_count(
        self, utils: TestUtils[MemoryTotals], *, counter: str, kind: str
    ) -> Verdict:
        # The controller's own count includes errors it could not attribute to a
        # module, which no bank's counter would show.
        counts = _counts(await read_files(utils, f"{_EDAC}/mc*/{counter}"))
        errors = None if counts is None else sum(counts.values())
        utils.measure(f"{kind}_errors", errors, limits=(0, 0))
        reading = {"controller_errors": counts}
        if errors == 0:
            return PASS(reading)
        if errors is None:
            raise TestFailure(f"{kind} error counts could not be read", 0, "(not read)")
        raise TestFailure(
            f"memory has logged {kind} errors since boot", 0, errors, evidence=reading
        )
