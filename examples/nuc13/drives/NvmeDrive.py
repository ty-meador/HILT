"""The NVMe drive: one controller, its namespace, and how the namespace is partitioned.

The drive answers for its controller and namespace. How the disk is laid out is the
system part number's to say, not the drive's, so the layout is handed in. The drive
builds a ``PartitionTable`` from it on its own namespace, and the table builds a
``Volume`` for each partition:

    drive
    ├─ state               the controller is present and live
    └─ details
       ├─ bom-check           the unit's bill of materials lists this drive
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
controller, namespace, and PCI address are handed to it by the product, which builds
it in the board's slot.

Every reading is one an ordinary user can take: sysfs, the kernel's mount table, and
``lsblk``, which answers from the udev database. SMART health and the drive's serial
number need root and are not judged here.

Nothing here writes to the device.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from typing import Protocol, TypedDict, Unpack

from nuc13.kernel import (
    NOT_READ,
    read_block_size_bytes,
    read_int,
    read_link_name,
    read_lsblk_column,
    read_text,
)

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

# ------------------------------------------------------------------------ volume


class VolumeLayout(TypedDict):
    """What one partition must hold, whichever disk it is laid out on.

    Attributes:
        filesystem: Filesystem type as ``lsblk`` names it (``vfat``, ``ext4``, ``swap``).
        mountpoint: Where it must be mounted, or ``None`` when it is not mounted
            directly.
        capacity_mib: Partition size as lshw prints it. lshw rounds down to a whole
            unit and moves up a unit only above 10240 of the smaller one, so a size
            is whole MiB up to 10240MiB and whole GiB beyond; the size claim allows
            for exactly that rounding.
    """

    filesystem: str
    mountpoint: str | None
    capacity_mib: int


class VolumeIdentity(VolumeLayout):
    """What one partition must be: its layout, on the device the kernel must show it as.

    Attributes:
        device: Kernel name, such as ``nvme0n1p1``.
    """

    device: str


class Volume(TestComponent[VolumeIdentity]):
    """Judge one partition: that it exists, its size, its filesystem, and its mount.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: What the partition must be.
    """

    _MIB = 1024 * 1024

    # The largest number lshw prints before it moves up to the next binary unit.
    _LSHW_UNIT_CEILING = 10240

    _OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")

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
        rounding_mib = self._lshw_rounding_mib(capacity_mib)
        size_bytes = await read_block_size_bytes(utils, device)
        # The capacity is lshw's figure, which lshw rounded down. The partition may be
        # larger by what that rounding dropped (under 1MiB, or under 1GiB for a size
        # lshw printed in GiB) and by nothing more; it may not be smaller at all.
        lowest = capacity_mib * self._MIB
        highest = (capacity_mib + rounding_mib) * self._MIB - 1
        size = utils.measure("partition_bytes", size_bytes, unit="B", limits=(lowest, highest))
        expected = f"{capacity_mib} MiB, plus under {rounding_mib} MiB lshw rounded off"
        if size_bytes is None:
            raise TestFailure(f"size of {device} could not be read", expected, NOT_READ)
        reading = {"partition_bytes": size_bytes}
        if size.in_limits:
            return PASS(reading)
        raise TestFailure(
            f"size of {device} does not match",
            expected,
            # Whole MiB rounded down, as the expected figure is: a size a few bytes short
            # of the next MiB must not read as that MiB.
            f"{size_bytes // self._MIB} MiB ({size_bytes} B)",
            evidence=reading,
        )

    @evidence("filesystem_found")
    async def _filesystem(self, utils: TestUtils[VolumeIdentity]) -> Verdict:
        device = utils.ctx["device"]
        expected = utils.ctx["filesystem"]
        found = await read_lsblk_column(utils, device, "FSTYPE")
        reading = {"filesystem_found": found}
        if found == expected:
            return PASS(reading)
        if found is None:
            raise TestFailure(f"filesystem of {device} could not be read", expected, NOT_READ)
        raise TestFailure(
            f"filesystem of {device} does not match",
            expected,
            found or "(none)",
            evidence=reading,
        )

    @evidence("mounted_at")
    async def _mounted(self, utils: TestUtils[VolumeIdentity], *, mountpoint: str) -> Verdict:
        device = utils.ctx["device"]
        mounts = await self._mounts_of(utils, device)
        if mounts is None:
            raise TestFailure("the mount table could not be read", mountpoint, NOT_READ)
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
        mounts = await self._mounts_of(utils, device)
        if mounts is None:
            raise TestFailure("the mount table could not be read", "rw", NOT_READ)
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

    @staticmethod
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
        return 1024 if capacity_mib >= Volume._LSHW_UNIT_CEILING else 1

    @staticmethod
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
                target = Volume._OCTAL_ESCAPE.sub(
                    lambda match: chr(int(match.group(1), 8)), fields[1]
                )
                mounts[target] = fields[3]
        return mounts


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

    The partition count is read off the layout it is handed.

    Args:
        name: Node name used by logs, artifacts, and failures.
        namespace: Kernel block device of the disk, such as ``nvme0n1``.
        partition_table: Partition table type (``gpt``).
        volumes: Every partition's layout by node name, in table order.
    """

    def __init__(
        self,
        *,
        name: str,
        namespace: str,
        partition_table: str,
        volumes: Mapping[str, VolumeLayout],
    ) -> None:
        super().__init__(name=name)
        self._volumes = dict(volumes)
        self._identity: PartitionTableIdentity = {
            "namespace": namespace,
            "partition_table": partition_table,
            "partition_count": len(self._volumes),
        }

    async def configure(self, utils: TestUtils[PartitionTableIdentity]) -> PartitionTableIdentity:
        """Provide the layout the table's own claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Independent claims: a wrong table type never hides a missing partition.

        The kernel names an NVMe namespace's partitions by their number in the table,
        after a ``p``, so each volume's device follows from where its layout is listed.
        """
        return self.parallel(
            self.name,
            [
                self.test("partition-table", self._partition_table),
                self.test("partition-count", self._partition_count),
                *[
                    Volume(name=name, device=f"{self._identity['namespace']}p{number}", **layout)
                    for number, (name, layout) in enumerate(self._volumes.items(), start=1)
                ],
            ],
        )

    @evidence("table_type")
    async def _partition_table(self, utils: TestUtils[PartitionTableIdentity]) -> Verdict:
        namespace = utils.ctx["namespace"]
        expected = utils.ctx["partition_table"]
        table = await read_lsblk_column(utils, namespace, "PTTYPE")
        reading = {"table_type": table}
        if table == expected:
            return PASS(reading)
        if table is None:
            raise TestFailure(
                f"partition table of {namespace} could not be read", expected, NOT_READ
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
        partitions = await self._partitions_of(utils, namespace)
        if partitions is None:
            raise TestFailure(f"partitions of {namespace} could not be listed", expected, NOT_READ)
        reading = {"partitions": partitions}
        if len(partitions) == expected:
            return PASS(reading)
        raise TestFailure(
            f"{namespace} does not hold the expected number of partitions",
            expected,
            len(partitions),
            evidence=reading,
        )

    @staticmethod
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


# ------------------------------------------------------------------------- drive


class DriveIdentity(TypedDict):
    """What the drive must be, and where the kernel must show it.

    Attributes:
        part_number: The drive as the bill of materials names it.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
        controller: Kernel name of the drive's controller, such as ``nvme0``.
        namespace: Kernel block device of its namespace, such as ``nvme0n1``.
        pci_address: The PCI function the controller sits on.
        model: Model string.
        firmware_revision: Firmware revision.
        capacity_gb: Namespace size in decimal gigabytes, as the label states it.
        logical_sector_bytes: Logical sector size.
    """

    part_number: str
    bom_mpns: list[str]
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
        partition_table: Partition table type the namespace must carry (``gpt``).
        volumes: Every partition's layout by node name, in table order.
        **identity: Where the kernel must show the drive, and the drive that must be
            there.
    """

    _GB = 10**9

    # The one state in which the kernel is doing I/O through an NVMe controller.
    _LIVE = "live"

    def __init__(
        self,
        *,
        name: str,
        partition_table: str,
        volumes: Mapping[str, VolumeLayout],
        **identity: Unpack[DriveIdentity],
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._partition_table = partition_table
        self._volumes = dict(volumes)

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
                        self.test("bom-check", self._bom_check),
                        self.test("model", self._model),
                        self.test("firmware", self._firmware),
                        self.test("pci-address", self._pci_address),
                        self.test("capacity", self._capacity),
                        self.test("sector-size", self._sector_size),
                        PartitionTable(
                            name="partitions",
                            namespace=self._identity["namespace"],
                            partition_table=self._partition_table,
                            volumes=self._volumes,
                        ),
                    ],
                ),
            ]
        )

    @evidence("controller_state")
    async def _state(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        controller = utils.ctx["controller"]
        state = await read_text(utils, f"/sys/class/nvme/{controller}/state")
        reading = {"controller_state": state}
        if state == self._LIVE:
            return PASS(reading)
        if state is None:
            raise TestFailure(f"NVMe controller {controller} is not present", self._LIVE, NOT_READ)
        raise TestFailure(
            f"NVMe controller {controller} is not {self._LIVE}", self._LIVE, state, evidence=reading
        )

    async def _bom_check(self, utils: TestUtils[DriveIdentity]) -> Verdict:
        expected = [utils.ctx["part_number"]]
        listed = utils.ctx["bom_mpns"]
        if listed == expected:
            return PASS
        raise TestFailure("the bill of materials does not list this drive", expected, listed)

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
            raise TestFailure(f"model of {controller} could not be read", expected, NOT_READ)
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
                f"firmware revision of {controller} could not be read", expected, NOT_READ
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
            raise TestFailure(f"PCI function of {controller} could not be read", expected, NOT_READ)
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
        size_bytes = await read_block_size_bytes(utils, namespace)
        # A label states decimal gigabytes rounded down: a drive sold as N GB holds at
        # least N GB and less than N + 1.
        lowest = labelled_gb * self._GB
        highest = (labelled_gb + 1) * self._GB - 1
        size = utils.measure("namespace_bytes", size_bytes, unit="B", limits=(lowest, highest))
        expected = f"{labelled_gb} GB, and under {labelled_gb + 1} GB"
        if size_bytes is None:
            raise TestFailure(f"size of {namespace} could not be read", expected, NOT_READ)
        reading = {"namespace_bytes": size_bytes}
        if size.in_limits:
            return PASS(reading)
        raise TestFailure(
            f"capacity of {namespace} does not match the label",
            expected,
            f"{size_bytes / self._GB:.2f} GB",
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
                NOT_READ,
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


# ------------------------------------------------------------- drive part number


class DrivePartNumber(Protocol):
    """A drive part number's class: what the product hands it to build the drive.

    The class says what the drive is. Everything else comes from whoever owns it: the
    bill of materials its line, the board the slot, the system part number the layout.
    """

    def __call__(
        self,
        *,
        name: str,
        bom_mpns: list[str],
        controller: str,
        namespace: str,
        pci_address: str,
        partition_table: str,
        volumes: Mapping[str, VolumeLayout],
    ) -> NvmeDrive:
        """Build the drive.

        Args:
            name: Node name used by logs, artifacts, and failures.
            bom_mpns: The part numbers the unit's bill of materials lists for this
                component.
            controller: Kernel name of the controller the drive is fitted as.
            namespace: Kernel block device of its namespace.
            pci_address: The PCI function the controller sits on.
            partition_table: Partition table type the namespace must carry.
            volumes: Every partition's layout by node name, in table order.
        """
        ...
