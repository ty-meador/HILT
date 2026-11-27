"""The NVMe drive: one controller, its namespace, and how the namespace is partitioned.

The drive answers for its controller and namespace. How the disk is laid out is the
system part number's to say, not the drive's, so it is composed in as a
``PartitionTable``, which answers for the table and hands each partition to a
``Volume``:

    drive
    ├─ state               the controller is present and live
    └─ details
       ├─ model, firmware, pci-address
       ├─ capacity, sector-size
       └─ partitions
          ├─ partition-table, partition-count
          └─ one node per volume
             ├─ partition     the kernel knows the partition
             └─ details
                ├─ size, filesystem
                └─ mount      only for a volume that must be mounted
                   ├─ mountpoint
                   └─ read-write

A drive part number is one class, in a module of its own beside this one; it says
what that drive is. Where the drive is fitted is not the drive's to know: its
controller, namespace, and PCI address are handed to it, and it is the board that
knows them. The package's ``mpns`` lists the part numbers.

Every reading is one an ordinary user can take: sysfs, the kernel's mount table, and
``lsblk``, which answers from the udev database. SMART health and the drive's serial
number need root and are not judged here.

Nothing here writes to the device.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Sequence
from typing import TypedDict, Unpack

from nuc13.kernel import read_int, read_link_name, read_text

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

# sysfs counts a block device's `size` in 512-byte units whatever its sector size.
_SYSFS_SECTOR_BYTES = 512
_MIB = 1024 * 1024
_GB = 10**9

# The largest number lshw prints before it moves up to the next binary unit.
_LSHW_UNIT_CEILING = 10240

_NOT_READ = "(not read)"

# The one state in which the kernel is doing I/O through an NVMe controller.
_LIVE = "live"

_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def _lshw_rounding_mib(capacity_mib: int) -> int:
    """Return how much of a partition's size lshw dropped when it printed ``capacity_mib``.

    lshw rounds a size down to a whole unit, and moves up a unit only once the number
    passes 10240. A volume is therefore printed in whole MiB up to 10240MiB (``4095MiB``
    is exact to the MiB) and in whole GiB beyond (``467GiB`` may hide up to 1023MiB).

    Args:
        capacity_mib: A size transcribed from lshw, in MiB.

    Returns:
        The width in MiB of the range of real sizes lshw prints as ``capacity_mib``.
    """
    return 1024 if capacity_mib >= _LSHW_UNIT_CEILING else 1


async def _size_bytes(utils: TestUtils, device: str) -> int | None:
    """Read a block device's size; ``None`` when the kernel does not report one."""
    sectors = await read_int(utils, f"/sys/class/block/{device}/size")
    if sectors is None:
        return None
    return sectors * _SYSFS_SECTOR_BYTES


async def _lsblk_column(utils: TestUtils, device: str, column: str) -> str | None:
    """Read one ``lsblk`` column for one device; ``None`` when lsblk cannot answer.

    An empty string is a reading: the device is there and has no such property.
    """
    path = shlex.quote(f"/dev/{device}")
    result = await utils.exec(f"lsblk --nodeps --noheadings --output {column} {path}")
    if result.exit_code != 0:
        return None
    return result.stdout.strip()


async def _partitions_of(utils: TestUtils, namespace: str) -> list[str] | None:
    """List the partitions the kernel found on a disk; ``None`` when lsblk cannot answer."""
    path = shlex.quote(f"/dev/{namespace}")
    result = await utils.exec(f"lsblk --noheadings --raw --output NAME,TYPE {path}")
    if result.exit_code != 0:
        return None
    rows = [line.split() for line in result.stdout.splitlines()]
    # The listing also holds the disk itself and whatever is stacked on a partition
    # (a dm-crypt mapping, for one); only rows typed `part` are partitions.
    return [row[0] for row in rows if len(row) == 2 and row[1] == "part"]


async def _mounts_of(utils: TestUtils, device: str) -> dict[str, str] | None:
    """Map each place a device is mounted to its mount options.

    The kernel's own mount table is the source, so the options are the ones in force
    now, not the ones ``fstab`` asked for. ``None`` when the table cannot be read; an
    empty mapping when the device is mounted nowhere.
    """
    table = await read_text(utils, "/proc/self/mounts")
    if table is None:
        return None
    mounts: dict[str, str] = {}
    for line in table.splitlines():
        fields = line.split()
        if len(fields) >= 4 and fields[0] == f"/dev/{device}":
            # The table writes a space in a path as \040; a mountpoint writes the space.
            target = _OCTAL_ESCAPE.sub(lambda match: chr(int(match.group(1), 8)), fields[1])
            mounts[target] = fields[3]
    return mounts


# ------------------------------------------------------------------------ volume


class VolumeIdentity(TypedDict):
    """What one partition must be.

    Attributes:
        device: Kernel name, such as ``nvme0n1p1``.
        filesystem: Filesystem type as ``lsblk`` names it (``vfat``, ``ext4``, ``swap``).
        mountpoint: Where it must be mounted, or ``None`` when it is not mounted
            directly.
        capacity_mib: Partition size as lshw prints it. lshw rounds down to a whole
            unit and moves up a unit only above 10240 of the smaller one, so a size
            is whole MiB up to 10240MiB and whole GiB beyond; the size claim allows
            for exactly that rounding.
    """

    device: str
    filesystem: str
    mountpoint: str | None
    capacity_mib: int


class Volume(TestComponent[VolumeIdentity]):
    """Judge one partition: that it exists, its size, its filesystem, and its mount.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: What the partition must be.
    """

    def __init__(self, *, name: str, **identity: Unpack[VolumeIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity
        # The places the volume must be mounted: none or one. The mount claims are
        # enumerated over this, so an unmounted volume simply has none.
        mountpoint = identity["mountpoint"]
        self._mountpoints = () if mountpoint is None else (mountpoint,)

    async def configure(self, utils: TestUtils[VolumeIdentity]) -> VolumeIdentity:
        """Provide the identity the claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Existence first: a partition that is not there has no size, type, or mount.

        The details are independent of each other and run in parallel. A mount is a
        sequence because options are only read off a filesystem that is mounted.
        """
        return self.sequence(
            self.name,
            [
                self.test("partition", self._partition),
                self.parallel(
                    "details",
                    [
                        self.test("size", self._size),
                        self.test("filesystem", self._filesystem),
                        *[
                            self.sequence(
                                "mount",
                                [
                                    self.test("mountpoint", self._mounted, mountpoint=mountpoint),
                                    self.test(
                                        "read-write", self._read_write, mountpoint=mountpoint
                                    ),
                                ],
                                discrete_log_file=False,
                            )
                            for mountpoint in self._mountpoints
                        ],
                    ],
                    discrete_log_file=False,
                ),
            ],
        )

    @evidence("partition_number")
    async def _partition(self, utils: TestUtils[VolumeIdentity]) -> Verdict:
        device = utils.ctx["device"]
        # Only a partition has this attribute; a whole disk or a missing device does not.
        number = await read_int(utils, f"/sys/class/block/{device}/partition")
        if number is not None:
            return PASS({"partition_number": number})
        raise TestFailure(f"{device} is not a partition on this machine", "present", "absent")

    @evidence("partition_bytes")
    async def _size(self, utils: TestUtils[VolumeIdentity]) -> Verdict:
        device = utils.ctx["device"]
        capacity_mib = utils.ctx["capacity_mib"]
        rounding_mib = _lshw_rounding_mib(capacity_mib)
        size_bytes = await _size_bytes(utils, device)
        # The capacity is lshw's figure, which lshw rounded down. The partition may be
        # larger by what that rounding dropped (under 1MiB, or under 1GiB for a size
        # lshw printed in GiB) and by nothing more; it may not be smaller at all.
        lowest = capacity_mib * _MIB
        highest = (capacity_mib + rounding_mib) * _MIB - 1
        size = utils.measure("partition_bytes", size_bytes, unit="B", limits=(lowest, highest))
        expected = f"{capacity_mib} MiB, plus under {rounding_mib} MiB lshw rounded off"
        if size_bytes is None:
            raise TestFailure(f"size of {device} could not be read", expected, _NOT_READ)
        reading = {"partition_bytes": size_bytes}
        if size.in_limits:
            return PASS(reading)
        raise TestFailure(
            f"size of {device} does not match",
            expected,
            # Whole MiB rounded down, as the expected figure is: a size a few bytes short
            # of the next MiB must not read as that MiB.
            f"{size_bytes // _MIB} MiB ({size_bytes} B)",
            evidence=reading,
        )

    @evidence("filesystem_found")
    async def _filesystem(self, utils: TestUtils[VolumeIdentity]) -> Verdict:
        device = utils.ctx["device"]
        expected = utils.ctx["filesystem"]
        found = await _lsblk_column(utils, device, "FSTYPE")
        reading = {"filesystem_found": found}
        if found == expected:
            return PASS(reading)
        if found is None:
            raise TestFailure(f"filesystem of {device} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"filesystem of {device} does not match",
            expected,
            found or "(none)",
            evidence=reading,
        )

    @evidence("mounted_at")
    async def _mounted(self, utils: TestUtils[VolumeIdentity], *, mountpoint: str) -> Verdict:
        device = utils.ctx["device"]
        mounts = await _mounts_of(utils, device)
        if mounts is None:
            raise TestFailure("the mount table could not be read", mountpoint, _NOT_READ)
        reading = {"mounted_at": sorted(mounts)}
        if mountpoint in mounts:
            return PASS(reading)
        raise TestFailure(
            f"{device} is not mounted at {mountpoint}",
            mountpoint,
            ", ".join(sorted(mounts)) or "(not mounted)",
            evidence=reading,
        )

    @evidence("mount_options")
    async def _read_write(self, utils: TestUtils[VolumeIdentity], *, mountpoint: str) -> Verdict:
        device = utils.ctx["device"]
        mounts = await _mounts_of(utils, device)
        if mounts is None:
            raise TestFailure("the mount table could not be read", "rw", _NOT_READ)
        options = mounts.get(mountpoint)
        if options is None:
            raise TestFailure(f"{device} is not mounted at {mountpoint}", "rw", "(not mounted)")
        reading = {"mount_options": options}
        # These filesystems are mounted errors=remount-ro: the kernel flips one to
        # `ro` when it meets an I/O or consistency error, so `rw` is the health signal.
        if "rw" in options.split(","):
            return PASS(reading)
        raise TestFailure(
            f"{mountpoint} is mounted read-only; the kernel does that after a filesystem error",
            "rw",
            options,
            evidence=reading,
        )


# --------------------------------------------------------------- partition table


class PartitionTableIdentity(TypedDict):
    """How a disk must be partitioned.

    Attributes:
        namespace: Kernel block device of the disk, such as ``nvme0n1``.
        partition_table: Partition table type (``gpt``).
        partition_count: Partitions in the table.
    """

    namespace: str
    partition_table: str
    partition_count: int


class PartitionTable(TestComponent[PartitionTableIdentity]):
    """Judge how a disk is partitioned, then every volume on it.

    The partition count is read off the volumes it is composed with.

    Args:
        name: Node name used by logs, artifacts, and failures.
        namespace: Kernel block device of the disk, such as ``nvme0n1``.
        partition_table: Partition table type (``gpt``).
        volumes: Every partition, in table order.
    """

    def __init__(
        self, *, name: str, namespace: str, partition_table: str, volumes: Sequence[Volume]
    ) -> None:
        super().__init__(name=name)
        self._volumes = tuple(volumes)
        self._identity: PartitionTableIdentity = {
            "namespace": namespace,
            "partition_table": partition_table,
            "partition_count": len(self._volumes),
        }

    async def configure(self, utils: TestUtils[PartitionTableIdentity]) -> PartitionTableIdentity:
        """Provide the layout the table's own claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Independent claims: a wrong table type never hides a missing partition."""
        return self.parallel(
            self.name,
            [
                self.test("partition-table", self._partition_table),
                self.test("partition-count", self._partition_count),
                *self._volumes,
            ],
        )

    @evidence("table_type")
    async def _partition_table(self, utils: TestUtils[PartitionTableIdentity]) -> Verdict:
        namespace = utils.ctx["namespace"]
        expected = utils.ctx["partition_table"]
        table = await _lsblk_column(utils, namespace, "PTTYPE")
        reading = {"table_type": table}
        if table == expected:
            return PASS(reading)
        if table is None:
            raise TestFailure(
                f"partition table of {namespace} could not be read", expected, _NOT_READ
            )
        raise TestFailure(
            f"partition table of {namespace} does not match",
            expected,
            table or "(none)",
            evidence=reading,
        )

    @evidence("partitions")
    async def _partition_count(self, utils: TestUtils[PartitionTableIdentity]) -> Verdict:
        namespace = utils.ctx["namespace"]
        expected = utils.ctx["partition_count"]
        partitions = await _partitions_of(utils, namespace)
        if partitions is None:
            raise TestFailure(f"partitions of {namespace} could not be listed", expected, _NOT_READ)
        reading = {"partitions": partitions}
        if len(partitions) == expected:
            return PASS(reading)
        raise TestFailure(
            f"{namespace} does not hold the expected number of partitions",
            expected,
            len(partitions),
            evidence=reading,
        )


# ------------------------------------------------------------------------- drive


class DriveIdentity(TypedDict):
    """What the drive must be, and where the kernel must show it.

    Attributes:
        controller: Kernel name of the drive's controller, such as ``nvme0``.
        namespace: Kernel block device of its namespace, such as ``nvme0n1``.
        pci_address: The PCI function the controller sits on.
        model: Model string.
        firmware_revision: Firmware revision.
        capacity_gb: Namespace size in decimal gigabytes, as the label states it.
        logical_sector_bytes: Logical sector size.
    """

    controller: str
    namespace: str
    pci_address: str
    model: str
    firmware_revision: str
    capacity_gb: int
    logical_sector_bytes: int


class NvmeDrive(TestComponent[DriveIdentity]):
    """Judge the controller and its namespace, then how the namespace is partitioned.

    Args:
        name: Node name used by logs, artifacts, and failures.
        partitions: How the drive's namespace must be laid out.
        **identity: Where the kernel must show the drive, and the drive that must be
            there.
    """

    def __init__(
        self, *, name: str, partitions: PartitionTable, **identity: Unpack[DriveIdentity]
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._partitions = partitions

    async def configure(self, utils: TestUtils[DriveIdentity]) -> DriveIdentity:
        """Provide the identity the drive's own claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """State first: nothing read through a controller that is not live can be trusted.

        Everything after it is independent, so the details and the partitions run in
        parallel and a wrong firmware revision never hides a missing partition.
        """
        return self.sequence(
            [
                self.test("state", self._state),
                self.parallel(
                    "details",
                    [
                        self.test("model", self._model),
                        self.test("firmware", self._firmware),
                        self.test("pci-address", self._pci_address),
                        self.test("capacity", self._capacity),
                        self.test("sector-size", self._sector_size),
                        self._partitions,
                    ],
                ),
            ]
        )

    @evidence("controller_state")
    async def _state(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        controller = utils.ctx["controller"]
        state = await read_text(utils, f"/sys/class/nvme/{controller}/state")
        reading = {"controller_state": state}
        if state == _LIVE:
            return PASS(reading)
        if state is None:
            raise TestFailure(f"NVMe controller {controller} is not present", _LIVE, _NOT_READ)
        raise TestFailure(
            f"NVMe controller {controller} is not {_LIVE}", _LIVE, state, evidence=reading
        )

    @evidence("model_reported")
    async def _model(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        controller = utils.ctx["controller"]
        expected = utils.ctx["model"]
        # read_text strips the padding the controller puts after a short model string.
        model = await read_text(utils, f"/sys/class/nvme/{controller}/model")
        reading = {"model_reported": model}
        if model == expected:
            return PASS(reading)
        if model is None:
            raise TestFailure(f"model of {controller} could not be read", expected, _NOT_READ)
        raise TestFailure(
            f"model of {controller} does not match", expected, model, evidence=reading
        )

    @evidence("firmware_reported")
    async def _firmware(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        controller = utils.ctx["controller"]
        expected = utils.ctx["firmware_revision"]
        revision = await read_text(utils, f"/sys/class/nvme/{controller}/firmware_rev")
        reading = {"firmware_reported": revision}
        if revision == expected:
            return PASS(reading)
        if revision is None:
            raise TestFailure(
                f"firmware revision of {controller} could not be read", expected, _NOT_READ
            )
        raise TestFailure(
            f"firmware revision of {controller} does not match",
            expected,
            revision,
            evidence=reading,
        )

    @evidence("pci_function")
    async def _pci_address(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        controller = utils.ctx["controller"]
        expected = utils.ctx["pci_address"]
        # The controller's `device` link points at the PCI function it sits on.
        function = await read_link_name(utils, f"/sys/class/nvme/{controller}/device")
        reading = {"pci_function": function}
        if function == expected:
            return PASS(reading)
        if function is None:
            raise TestFailure(
                f"PCI function of {controller} could not be read", expected, _NOT_READ
            )
        raise TestFailure(
            f"{controller} is not at the expected PCI address",
            expected,
            function,
            evidence=reading,
        )

    @evidence("namespace_bytes")
    async def _capacity(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        namespace = utils.ctx["namespace"]
        labelled_gb = utils.ctx["capacity_gb"]
        size_bytes = await _size_bytes(utils, namespace)
        # A label states decimal gigabytes rounded down: a drive sold as N GB holds at
        # least N GB and less than N + 1.
        lowest = labelled_gb * _GB
        highest = (labelled_gb + 1) * _GB - 1
        size = utils.measure("namespace_bytes", size_bytes, unit="B", limits=(lowest, highest))
        expected = f"{labelled_gb} GB, and under {labelled_gb + 1} GB"
        if size_bytes is None:
            raise TestFailure(f"size of {namespace} could not be read", expected, _NOT_READ)
        reading = {"namespace_bytes": size_bytes}
        if size.in_limits:
            return PASS(reading)
        raise TestFailure(
            f"capacity of {namespace} does not match the label",
            expected,
            f"{size_bytes / _GB:.2f} GB",
            evidence=reading,
        )

    @evidence("logical_block_bytes")
    async def _sector_size(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        namespace = utils.ctx["namespace"]
        expected = utils.ctx["logical_sector_bytes"]
        block_bytes = await read_int(utils, f"/sys/block/{namespace}/queue/logical_block_size")
        size = utils.measure(
            "logical_block_bytes", block_bytes, unit="B", limits=(expected, expected)
        )
        if block_bytes is None:
            raise TestFailure(
                f"logical sector size of {namespace} could not be read",
                f"{expected} B",
                _NOT_READ,
            )
        reading = {"logical_block_bytes": block_bytes}
        if size.in_limits:
            return PASS(reading)
        raise TestFailure(
            f"logical sector size of {namespace} does not match",
            f"{expected} B",
            f"{block_bytes} B",
            evidence=reading,
        )
