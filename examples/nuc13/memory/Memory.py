"""System memory: the totals the kernel accounts for, then each module in its slot.

    memory
    ├─ bom-check                  the unit's bill of materials lists these modules
    ├─ installed-size             every module the kernel lists, summed
    ├─ usable-size                MemTotal, between the floor and what is installed
    ├─ populated-banks            how many modules the kernel lists
    ├─ correctable-errors         every controller's count since boot
    ├─ uncorrectable-errors
    └─ bank-N                     one per module on the bill of materials
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
A module without one has no such claims, instead of claims that could never take their
reading.

A module part number is one class, in a module of its own beside this one; it says
what that module is. Where the module is fitted is not the module's to know: its
slot label, EDAC position, and EEPROM path are handed to it by ``Memory``, which fits
each module the system part number names into one of the board's slots.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypedDict, Unpack

from nuc13.kernel import NOT_READ, parse_int, read_files, read_int, read_text

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence


class EepromIdentity(TypedDict):
    """What a module's EEPROM must say, and where the EEPROM is.

    Attributes:
        eeprom: Shell pattern that resolves to the EEPROM's sysfs file.
        technology: Memory generation, such as ``DDR4``.
        part_number: Module part number.
        vendor: Module manufacturer, by name.
        manufacturer_id: That manufacturer's JEDEC JEP106 code as the EEPROM stores
            it: two bytes in hex, continuation count (with its parity bit) then code.
        speed_mhz: Rated clock.
    """

    eeprom: str
    technology: str
    part_number: str
    vendor: str
    manufacturer_id: str
    speed_mhz: int


class IdentityEeprom(TestComponent[EepromIdentity]):
    """The small EEPROM on a memory module that states the module's own identity.

    JEDEC calls it Serial Presence Detect (SPD). It holds the module's generation,
    part number, maker, and rated speed, and each is judged against the module's
    identity. Only the DDR4 layout is decoded.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: Where the EEPROM is, and what it must say.
    """

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

    def __init__(self, *, name: str, **identity: Unpack[EepromIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity

    async def configure(self, utils: TestUtils[EepromIdentity]) -> EepromIdentity:
        """Provide the module identity the EEPROM is judged against."""
        return self._identity

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
        device_type = await self._read_eeprom(utils, utils.ctx["eeprom"], self._DEVICE_TYPE)
        if device_type is None:
            raise TestFailure("module EEPROM could not be read", expected, NOT_READ)
        code = device_type[0]
        decoded = code == self._DECODED_DEVICE_TYPE
        technology = self._DECODED_TECHNOLOGY if decoded else f"device type {code:#04x}"
        reading = {"eeprom_technology": technology}
        if technology == expected:
            return PASS(reading)
        raise TestFailure(
            "module EEPROM does not state the expected generation",
            expected,
            technology,
            evidence=reading,
        )

    @evidence("eeprom_part_number")
    async def _part_number(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = utils.ctx["part_number"]
        field = await self._read_eeprom(utils, utils.ctx["eeprom"], self._PART_NUMBER)
        # The field is ASCII, padded with spaces to its full width.
        part_number = None if field is None else field.decode("ascii", "replace").strip()
        reading = {"eeprom_part_number": part_number}
        if part_number == expected:
            return PASS(reading)
        if part_number is None:
            raise TestFailure("module EEPROM could not be read", expected, NOT_READ)
        raise TestFailure(
            "module is not the expected part", expected, part_number, evidence=reading
        )

    @evidence("eeprom_manufacturer_id")
    async def _vendor(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = f"{utils.ctx['vendor']} (JEDEC {utils.ctx['manufacturer_id']})"
        field = await self._read_eeprom(utils, utils.ctx["eeprom"], self._MANUFACTURER)
        manufacturer = None if field is None else field.hex()
        reading = {"eeprom_manufacturer_id": manufacturer}
        if manufacturer == utils.ctx["manufacturer_id"]:
            return PASS(reading)
        if manufacturer is None:
            raise TestFailure("module EEPROM could not be read", expected, NOT_READ)
        raise TestFailure(
            "module is from another maker", expected, f"JEDEC {manufacturer}", evidence=reading
        )

    @evidence("eeprom_speed_mhz")
    async def _speed(self, utils: TestUtils[EepromIdentity]) -> Verdict:
        expected = utils.ctx["speed_mhz"]
        cycle_time = await self._read_eeprom(utils, utils.ctx["eeprom"], self._CYCLE_TIME)
        speed = None if cycle_time is None else self._rated_speed_mhz(cycle_time)
        utils.measure("eeprom_speed_mhz", speed, unit="MHz", limits=(expected, expected))
        reading = {"eeprom_speed_mhz": speed}
        if speed == expected:
            return PASS(reading)
        if speed is None:
            raise TestFailure("module EEPROM gave no rated speed", f"{expected} MHz", NOT_READ)
        raise TestFailure(
            "module is rated for another speed",
            f"{expected} MHz",
            f"{speed} MHz",
            evidence=reading,
        )

    @staticmethod
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

    @staticmethod
    def _rated_speed_mhz(cycle_time: bytes) -> int | None:
        """The speed a module is rated for, from its cycle-time field; ``None`` if blank."""
        correction_ps = int.from_bytes(cycle_time[-1:], signed=True)
        cycle_ps = cycle_time[0] * 125 + correction_ps
        if cycle_ps <= 0:
            return None
        # Two transfers per clock cycle, which is the figure a module is sold by and the
        # one SMBIOS and lshw print: a 625 ps cycle is "3200 MHz".
        return round(2_000_000 / cycle_ps)


class SodimmLocation(TypedDict):
    """Where one module is fitted, and so where the kernel must show it.

    Attributes:
        slot: The slot's label, such as ``Controller0-ChannelA-DIMM0``.
        edac_directory: The kernel's directory for the position, such as
            ``/sys/devices/system/edac/mc/mc0/dimm0``.
        location: The position as that directory words it, such as
            ``channel 0 slot 0``.
        eeprom: Shell pattern that resolves to the sysfs file of the module's
            identity EEPROM, or ``None`` where the kernel exposes none to an
            ordinary user.
    """

    slot: str
    edac_directory: str
    location: str
    eeprom: str | None


class SodimmIdentity(SodimmLocation):
    """What one module must be, in the slot the kernel must show it in.

    A module's part number, vendor, and speed can only be read from its identity
    EEPROM. They are judged in a slot that has one and carried without a claim in a
    slot whose ``eeprom`` is ``None``.

    Attributes:
        size_mib: Module capacity.
        technology: Memory generation, such as ``DDR4``.
        speed_mhz: Rated clock.
        vendor: Module manufacturer, by name.
        manufacturer_id: That manufacturer's JEDEC JEP106 code as the EEPROM stores
            it: two bytes in hex, continuation count (with its parity bit) then code.
        part_number: Module part number.
    """

    size_mib: int
    technology: str
    speed_mhz: int
    vendor: str
    manufacturer_id: str
    part_number: str


class Sodimm(TestComponent[SodimmIdentity]):
    """Judge one populated slot: the module is there, and it is the module expected.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: Where the kernel must show the module, and the module that must
            be there.
    """

    def __init__(self, *, name: str, **identity: Unpack[SodimmIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity
        # The identity EEPROMs the module's claims can read: none or one. The EEPROM
        # node is enumerated over this, so a module in a slot without one simply has none.
        self._identity_eeproms = () if identity["eeprom"] is None else (identity["eeprom"],)

    @property
    def size_mib(self) -> int:
        """The module's capacity, which system memory adds up over its modules."""
        return self._identity["size_mib"]

    @property
    def part_number(self) -> str:
        """The module's part number, which system memory holds the bill of materials to."""
        return self._identity["part_number"]

    async def configure(self, utils: TestUtils[SodimmIdentity]) -> SodimmIdentity:
        """Provide the identity the module's claims judge against."""
        return self._identity

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
                        *[
                            IdentityEeprom(
                                name="identity-eeprom",
                                eeprom=eeprom,
                                technology=self._identity["technology"],
                                part_number=self._identity["part_number"],
                                vendor=self._identity["vendor"],
                                manufacturer_id=self._identity["manufacturer_id"],
                                speed_mhz=self._identity["speed_mhz"],
                            )
                            for eeprom in self._identity_eeproms
                        ],
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("dimm_location")
    async def _position(self, utils: TestUtils[SodimmIdentity]) -> Verdict:
        expected = utils.ctx["location"]
        location = await read_text(utils, f"{utils.ctx['edac_directory']}/dimm_location")
        reading = {"dimm_location": location}
        if location == expected:
            return PASS(reading)
        if location is None:
            # EDAC creates a directory only for a populated position.
            raise TestFailure(
                f"the kernel lists no module for {utils.ctx['slot']}", expected, NOT_READ
            )
        raise TestFailure(
            f"the module for {utils.ctx['slot']} is somewhere else",
            expected,
            location,
            evidence=reading,
        )

    @evidence("dimm_size_mib")
    async def _size(self, utils: TestUtils[SodimmIdentity]) -> Verdict:
        expected = utils.ctx["size_mib"]
        size = await read_int(utils, f"{utils.ctx['edac_directory']}/size")
        utils.measure("dimm_size_mib", size, unit="MiB", limits=(expected, expected))
        reading = {"dimm_size_mib": size}
        if size == expected:
            return PASS(reading)
        if size is None:
            raise TestFailure("module size could not be read", f"{expected} MiB", NOT_READ)
        raise TestFailure(
            "module is the wrong size", f"{expected} MiB", f"{size} MiB", evidence=reading
        )

    @evidence("dimm_memory_type")
    async def _technology(self, utils: TestUtils[SodimmIdentity]) -> Verdict:
        expected = utils.ctx["technology"]
        memory_type = await read_text(utils, f"{utils.ctx['edac_directory']}/dimm_mem_type")
        reading = {"dimm_memory_type": memory_type}
        # EDAC puts the module's buffering in front of the generation
        # ("Unbuffered-DDR4"); the claim is about the generation alone.
        if memory_type is not None and expected in memory_type.split("-"):
            return PASS(reading)
        if memory_type is None:
            raise TestFailure("module type could not be read", expected, NOT_READ)
        raise TestFailure(
            "module is the wrong memory generation", expected, memory_type, evidence=reading
        )

    @evidence("dimm_errors")
    async def _error_count(
        self, utils: TestUtils[SodimmIdentity], *, counter: str, kind: str
    ) -> Verdict:
        errors = await read_int(utils, f"{utils.ctx['edac_directory']}/{counter}")
        utils.measure(f"{kind}_errors", errors, limits=(0, 0))
        reading = {"dimm_errors": errors}
        if errors == 0:
            return PASS(reading)
        if errors is None:
            raise TestFailure(f"{kind} error count could not be read", 0, NOT_READ)
        raise TestFailure(
            f"{utils.ctx['slot']} has logged {kind} errors since boot",
            0,
            errors,
            evidence=reading,
        )


class SodimmPartNumber(Protocol):
    """A module part number's class: built from a node name and the slot it is fitted in."""

    def __call__(
        self, *, name: str, slot: str, edac_directory: str, location: str, eeprom: str | None
    ) -> Sodimm:
        """Build the module in one slot.

        Args:
            name: Node name used by logs, artifacts, and failures.
            slot: Label of the slot the module is fitted in.
            edac_directory: The kernel's directory for that position.
            location: The position as that directory words it.
            eeprom: Shell pattern for the module's identity EEPROM, or ``None`` where
                the kernel exposes none to an ordinary user.
        """
        ...


class MemoryIdentity(TypedDict):
    """What the system's memory is made of, and what it must add up to.

    Attributes:
        part_numbers: Every module's part number, sorted.
        bom_mpns: The part numbers the unit's bill of materials lists for memory, sorted.
        installed_mib: Every module's capacity, summed.
        usable_floor_mib: Least ``MemTotal`` an intact system shows.
        bank_count: Populated slots.
    """

    part_numbers: list[str]
    bom_mpns: list[str]
    installed_mib: int
    usable_floor_mib: int
    bank_count: int


class Memory(TestComponent[MemoryIdentity]):
    """Judge system memory as a whole, then every module that makes it up.

    The totals are read off the modules it fits.

    Args:
        name: Node name used by logs, artifacts, and failures.
        bom_mpns: The part numbers the unit's bill of materials lists for memory, one
            per module, in any order.
        sodimms: The part number of the module in each slot, in slot order.
        slots: The slots the modules are fitted in, one per module.

    Raises:
        ValueError: There is not exactly one module for each slot.
    """

    _EDAC = "/sys/devices/system/edac/mc"

    # What an intact system may hold back from ``MemTotal``: firmware and the integrated
    # graphics reserve some of what is installed.
    _RESERVED_ALLOWANCE_MIB = 1024

    def __init__(
        self,
        *,
        name: str,
        bom_mpns: Sequence[str],
        sodimms: Sequence[SodimmPartNumber],
        slots: Sequence[SodimmLocation],
    ) -> None:
        super().__init__(name=name)
        if len(sodimms) != len(slots):
            raise ValueError(
                f"{name}: {len(sodimms)} modules for {len(slots)} slots; "
                "name one module part number for each slot"
            )
        # The totals below are read off the modules, so the modules are fitted here and
        # build_suite only lists them.
        self._sodimms = tuple(
            sodimm(name=f"bank-{index}", **slot)
            for index, (sodimm, slot) in enumerate(zip(sodimms, slots, strict=True))
        )
        installed_mib = sum(sodimm.size_mib for sodimm in self._sodimms)
        self._identity: MemoryIdentity = {
            "part_numbers": sorted(sodimm.part_number for sodimm in self._sodimms),
            "bom_mpns": sorted(bom_mpns),
            "installed_mib": installed_mib,
            "usable_floor_mib": installed_mib - self._RESERVED_ALLOWANCE_MIB,
            "bank_count": len(self._sodimms),
        }

    async def configure(self, utils: TestUtils[MemoryIdentity]) -> MemoryIdentity:
        """Provide the totals the system-wide claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Independent totals and independent modules: one bad module never hides another."""
        return self.parallel(
            self.name,
            [
                self.test("bom-check", self._bom_check),
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
                *self._sodimms,
            ],
        )

    async def _bom_check(self, utils: TestUtils[MemoryIdentity]) -> Verdict:
        # A module per entry, in no order the suite relies on: the same part numbers,
        # each as many times, is the same set of modules. Both lists are sorted.
        expected = utils.ctx["part_numbers"]
        listed = utils.ctx["bom_mpns"]
        if listed == expected:
            return PASS
        raise TestFailure(
            "the bill of materials does not list these memory modules", expected, listed
        )

    # The kernel, not the suite, decides how many controllers and modules there are,
    # so the system-wide claims below ask for every one it lists.

    @evidence("dimm_sizes_mib")
    async def _installed_size(self, utils: TestUtils[MemoryIdentity]) -> Verdict:
        expected = utils.ctx["installed_mib"]
        sizes = self._counts(await read_files(utils, f"{self._EDAC}/mc*/dimm*/size"))
        total = None if sizes is None else sum(sizes.values())
        utils.measure("installed_size_mib", total, unit="MiB", limits=(expected, expected))
        reading = {"dimm_sizes_mib": sizes}
        if total == expected:
            return PASS(reading)
        if total is None:
            raise TestFailure("module sizes could not be read", f"{expected} MiB", NOT_READ)
        raise TestFailure(
            "the modules do not add up to the installed memory",
            f"{expected} MiB",
            f"{total} MiB",
            evidence=reading,
        )

    @evidence("memory_total_kib")
    async def _usable_size(self, utils: TestUtils[MemoryIdentity]) -> Verdict:
        floor, installed = utils.ctx["usable_floor_mib"], utils.ctx["installed_mib"]
        window = f"{floor} to {installed} MiB"
        total_kib = await self._memory_total_kib(utils)
        usable_mib = None if total_kib is None else total_kib / 1024
        usable = utils.measure("usable_size_mib", usable_mib, unit="MiB", limits=(floor, installed))
        reading = {"memory_total_kib": total_kib}
        if usable.value is None:
            raise TestFailure("MemTotal could not be read from /proc/meminfo", window, NOT_READ)
        if usable.in_limits:
            return PASS(reading)
        raise TestFailure(
            "usable memory is outside what the installed modules allow",
            window,
            f"{usable.value:.0f} MiB",
            evidence=reading,
        )

    @evidence("dimm_locations")
    async def _populated_banks(self, utils: TestUtils[MemoryIdentity]) -> Verdict:
        expected = utils.ctx["bank_count"]
        # EDAC creates a module directory only for a populated position, so the
        # directories it lists are the modules it found.
        locations = await read_files(utils, f"{self._EDAC}/mc*/dimm*/dimm_location")
        populated = len(locations) if locations else None
        utils.measure("populated_banks", populated, limits=(expected, expected))
        reading = {"dimm_locations": locations}
        if populated == expected:
            return PASS(reading)
        if populated is None:
            raise TestFailure("the kernel lists no memory modules", expected, NOT_READ)
        raise TestFailure(
            "the kernel sees a different number of modules", expected, populated, evidence=reading
        )

    @evidence("controller_errors")
    async def _error_count(
        self, utils: TestUtils[MemoryIdentity], *, counter: str, kind: str
    ) -> Verdict:
        # The controller's own count includes errors it could not attribute to a
        # module, which no module's counter would show.
        counts = self._counts(await read_files(utils, f"{self._EDAC}/mc*/{counter}"))
        errors = None if counts is None else sum(counts.values())
        utils.measure(f"{kind}_errors", errors, limits=(0, 0))
        reading = {"controller_errors": counts}
        if errors == 0:
            return PASS(reading)
        if errors is None:
            raise TestFailure(f"{kind} error counts could not be read", 0, NOT_READ)
        raise TestFailure(
            f"memory has logged {kind} errors since boot", 0, errors, evidence=reading
        )

    @staticmethod
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

    @staticmethod
    async def _memory_total_kib(utils: TestUtils) -> int | None:
        """Read ``MemTotal`` from ``/proc/meminfo`` in KiB (the file's ``kB``)."""
        result = await utils.exec("grep -m 1 '^MemTotal:' /proc/meminfo")
        if result.exit_code != 0:
            return None
        return parse_int(result.stdout.strip().removeprefix("MemTotal:").removesuffix("kB"))
