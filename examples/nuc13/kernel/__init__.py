"""Shared readers for the kernel's text interfaces.

Every component in the suite takes its readings the same way: one read-only
command through ``utils.exec``, parsed here into a value or ``None``. ``None`` means
the reading was not taken (the file is absent, unreadable, or not in the expected
form), which a claim reports as such instead of guessing.

Nothing here writes to the device.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence

from hilt import TestUtils

# What a claim reports in place of a reading that was not taken.
NOT_READ = "(not read)"

# Where the kernel keeps one directory per logical CPU, PCI function, USB device,
# and network interface.
CPU_SYSFS = "/sys/devices/system/cpu"
PCI_SYSFS = "/sys/bus/pci/devices"
USB_SYSFS = "/sys/bus/usb/devices"
NET_SYSFS = "/sys/class/net"

# sysfs counts a block device's `size` in 512-byte units whatever its sector size.
_SYSFS_SECTOR_BYTES = 512


async def read_text(utils: TestUtils, path: str) -> str | None:
    """Read one file on the device, stripped; ``None`` when it cannot be read."""
    result = await utils.exec(f"cat {shlex.quote(path)}")
    if result.exit_code != 0:
        return None
    return result.stdout.strip()


async def read_files(utils: TestUtils, *patterns: str) -> dict[str, str]:
    """Read every file the patterns match, in one command, as path to stripped text.

    For a claim that needs a set of attributes at once (every cache index of a CPU,
    every attribute of a device): one command instead of one per file. A file that
    is absent or unreadable is simply not in the result, so a claim looks its path
    up with ``.get`` and treats ``None`` as a reading not taken.

    The patterns are expanded by the device's shell, so they are not quoted: they
    must come from identity, never from a reading.
    """
    # `tail -v` heads each file with "==> path <=="; unlike `grep -H`, that survives
    # the colons in PCI paths. Its exit status is not judged: it is non-zero when
    # any one pattern matched nothing, and the files that did match are still here.
    result = await utils.exec("tail -v -n +1 -- " + " ".join(patterns))
    files: dict[str, str] = {}
    path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("==> ") and line.endswith(" <=="):
            path = line[4:-4]
            files[path] = ""
        elif path is not None:
            files[path] += line + "\n"
    return {name: text.strip() for name, text in files.items()}


async def read_per_cpu(
    utils: TestUtils, cpus: Sequence[int], attribute: str
) -> dict[str, str | None]:
    """Read one per-CPU sysfs attribute for each of ``cpus`` in a single command.

    Keyed by CPU number as text, so the mapping can travel as evidence. A CPU whose
    attribute could not be read maps to ``None``.
    """
    paths = {str(cpu): f"{CPU_SYSFS}/cpu{cpu}/{attribute}" for cpu in cpus}
    files = await read_files(utils, *paths.values())
    return {cpu: files.get(path) for cpu, path in paths.items()}


async def read_int(utils: TestUtils, path: str) -> int | None:
    """Read one file holding a decimal integer; ``None`` when it does not."""
    return parse_int(await read_text(utils, path))


async def read_block_size_bytes(utils: TestUtils, device: str) -> int | None:
    """Read a block device's size; ``None`` when the kernel does not report one."""
    sectors = await read_int(utils, f"/sys/class/block/{device}/size")
    if sectors is None:
        return None
    return sectors * _SYSFS_SECTOR_BYTES


async def read_lsblk_column(utils: TestUtils, device: str, column: str) -> str | None:
    """Read one ``lsblk`` column for one device; ``None`` when lsblk cannot answer.

    An empty string is a reading: the device is there and has no such property.
    """
    path = shlex.quote(f"/dev/{device}")
    result = await utils.exec(f"lsblk --nodeps --noheadings --output {column} {path}")
    if result.exit_code != 0:
        return None
    return result.stdout.strip()


async def read_link_name(utils: TestUtils, path: str) -> str | None:
    """Read the last component of a symlink's target; ``None`` when there is no link.

    This is how sysfs names a bound driver: ``<device>/driver`` links to
    ``.../drivers/<name>``, and is absent when nothing is bound.
    """
    target = await read_link(utils, path)
    if target is None:
        return None
    return target.rsplit("/", 1)[-1]


async def read_link(utils: TestUtils, path: str) -> str | None:
    """Read a symlink's whole target; ``None`` when there is no link.

    sysfs states ownership through link targets: a USB bus links to a path that
    runs through the PCI function of the controller that owns it.
    """
    result = await utils.exec(f"readlink {shlex.quote(path)}")
    target = result.stdout.strip()
    if result.exit_code != 0 or not target:
        return None
    return target


async def list_dir(utils: TestUtils, path: str) -> tuple[str, ...] | None:
    """List a directory's entry names, sorted; ``None`` when it cannot be listed."""
    result = await utils.exec(f"ls -1 {shlex.quote(path)}")
    if result.exit_code != 0:
        return None
    return tuple(sorted(result.stdout.split()))


def parse_int(text: str | None) -> int | None:
    """Parse a decimal integer; ``None`` for anything else."""
    if text is None:
        return None
    try:
        return int(text.strip())
    except ValueError:
        return None


def parse_cpu_list(text: str | None) -> tuple[int, ...] | None:
    """Parse a kernel CPU list such as ``0-3,8,10-11`` into sorted CPU numbers."""
    if text is None or not text.strip():
        return None
    cpus: set[int] = set()
    for part in text.strip().split(","):
        low, _, high = part.partition("-")
        first, last = parse_int(low), parse_int(high or low)
        if first is None or last is None or last < first:
            return None
        cpus.update(range(first, last + 1))
    return tuple(sorted(cpus))


def parse_size_kib(text: str | None) -> int | None:
    """Parse a sysfs cache size such as ``48K`` or ``12288K`` (or ``2M``) into KiB."""
    if text is None:
        return None
    value = text.strip().upper()
    scale = {"K": 1, "M": 1024, "G": 1024 * 1024}.get(value[-1:])
    number = parse_int(value[:-1]) if scale is not None else None
    if scale is None or number is None:
        return None
    return number * scale
