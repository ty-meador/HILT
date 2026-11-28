"""The system firmware: which BIOS is flashed, and how it started the machine.

    firmware
    ├─ bom-check           the unit's bill of materials lists this release
    ├─ vendor
    ├─ version
    ├─ release-date
    └─ uefi-boot           only for firmware that must boot the machine through UEFI

The identity comes from the DMI fields the kernel publishes to every user. The boot
path is read from the kernel too: it exposes ``/sys/firmware/efi`` only when the
firmware handed over through UEFI.

A firmware release is a part like any other: the shopfloor names it on the bill of
materials, and one class, in a module of its own beside this one, says what that
release reports.
"""

from __future__ import annotations

from typing import Literal, TypedDict, Unpack

from nuc13.kernel import NOT_READ, read_text

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

type BiosField = Literal["bios_vendor", "bios_version", "bios_date"]


class FirmwareIdentity(TypedDict):
    """What the firmware is, and what it must report, keyed by the DMI field that reports it.

    Attributes:
        part_number: The release as the bill of materials names it.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
        bios_vendor: Who released the BIOS.
        bios_version: The release.
        bios_date: Its release date, ``MM/DD/YYYY``.
    """

    part_number: str
    bom_mpns: list[str]
    bios_vendor: str
    bios_version: str
    bios_date: str


class Firmware(TestComponent[FirmwareIdentity]):
    """Judge which BIOS is flashed, and the way it booted the machine.

    Args:
        name: Node name used by logs, artifacts, and failures.
        uefi: Whether the machine must have booted through UEFI. Firmware that need
            not has no boot-path claim, instead of one that could not fail.
        **identity: What the firmware is, and what it must report.
    """

    def __init__(self, *, name: str, uefi: bool, **identity: Unpack[FirmwareIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._boot_path = (self.test("uefi-boot", self._uefi_boot),) if uefi else ()

    async def configure(self, utils: TestUtils[FirmwareIdentity]) -> FirmwareIdentity:
        """Provide the identity the firmware's claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Independent claims: a stale version and a legacy boot are both reported."""
        return self.parallel(
            self.name,
            [
                self.test("bom-check", self._bom_check),
                self.test("vendor", self._dmi_field, field="bios_vendor"),
                self.test("version", self._dmi_field, field="bios_version"),
                self.test("release-date", self._dmi_field, field="bios_date"),
                *self._boot_path,
            ],
        )

    async def _bom_check(self, utils: TestUtils[FirmwareIdentity]) -> Verdict:
        expected = [utils.ctx["part_number"]]
        listed = utils.ctx["bom_mpns"]
        if listed == expected:
            return PASS
        raise TestFailure(
            "the bill of materials does not list this firmware release", expected, listed
        )

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[FirmwareIdentity], *, field: BiosField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, NOT_READ)
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)

    @evidence("efi_booted")
    async def _uefi_boot(self, utils: TestUtils[FirmwareIdentity]) -> Verdict:
        result = await utils.exec("test -d /sys/firmware/efi")
        # test answers 0 or 1; anything else means the question was never put.
        booted = {0: True, 1: False}.get(result.exit_code)
        reading = {"efi_booted": booted}
        if booted is None:
            raise TestFailure("boot path could not be read", "UEFI", NOT_READ)
        if booted:
            return PASS(reading)
        raise TestFailure(
            "the machine did not boot through UEFI", "UEFI", "legacy BIOS", evidence=reading
        )
