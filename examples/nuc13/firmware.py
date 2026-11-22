"""The system firmware: which BIOS is flashed, and how it started the machine.

    firmware
    ├─ vendor
    ├─ version
    ├─ release-date
    └─ uefi-boot       only where the record says the machine boots through UEFI

The identity comes from the DMI fields the kernel publishes to every user. The boot
path is read from the kernel too: it exposes ``/sys/firmware/efi`` only when the
firmware handed over through UEFI.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from nuc13.probe import read_text
from nuc13.spec import FirmwareSpec

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

type BiosField = Literal["bios_vendor", "bios_version", "bios_date"]


class FirmwareIdentity(TypedDict):
    """What the firmware must report, keyed by the DMI field that reports it."""

    bios_vendor: str
    bios_version: str
    bios_date: str


class Firmware(TestComponent[FirmwareIdentity]):
    """Judge the flashed BIOS against the record, and the way it booted."""

    def __init__(self, *, name: str, spec: FirmwareSpec) -> None:
        """Hold the firmware's identity.

        Args:
            name: Node name used by logs, artifacts, and failures.
            spec: The BIOS the machine must be running.
        """
        super().__init__(name=name)
        self._spec = spec
        # None or one: a machine that need not boot through UEFI has no boot-path
        # claim, instead of one that could not fail.
        self._boot_path = (self.test("uefi-boot", self._uefi_boot),) if spec.uefi else ()

    async def configure(self, utils: TestUtils[FirmwareIdentity]) -> FirmwareIdentity:
        """Provide the identity the firmware's claims judge against."""
        return {
            "bios_vendor": self._spec.vendor,
            "bios_version": self._spec.version,
            "bios_date": self._spec.release_date,
        }

    def build_suite(self) -> TestComponent:
        """Independent claims: a stale version and a legacy boot are both reported."""
        return self.parallel(
            self.name,
            [
                self.test("vendor", self._dmi_field, field="bios_vendor"),
                self.test("version", self._dmi_field, field="bios_version"),
                self.test("release-date", self._dmi_field, field="bios_date"),
                *self._boot_path,
            ],
        )

    @evidence("dmi_value")
    async def _dmi_field(self, utils: TestUtils[FirmwareIdentity], *, field: BiosField) -> Verdict:
        expected = utils.ctx[field]
        value = await read_text(utils, f"/sys/class/dmi/id/{field}")
        reading = {"dmi_value": value}
        if value == expected:
            return PASS(reading)
        if value is None:
            raise TestFailure(f"DMI {field} could not be read", expected, "(not read)")
        raise TestFailure(f"DMI {field} does not match", expected, value, evidence=reading)

    @evidence("efi_booted")
    async def _uefi_boot(self, utils: TestUtils[FirmwareIdentity]) -> Verdict:
        result = await utils.exec("test -d /sys/firmware/efi")
        # test answers 0 or 1; anything else means the question was never put.
        booted = {0: True, 1: False}.get(result.exit_code)
        reading = {"efi_booted": booted}
        if booted is None:
            raise TestFailure("boot path could not be read", "UEFI", "(not read)")
        if booted:
            return PASS(reading)
        raise TestFailure(
            "the machine did not boot through UEFI", "UEFI", "legacy BIOS", evidence=reading
        )
