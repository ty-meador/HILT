"""Processor part number ``i5-1340P``: what that processor is, down to each core's caches."""

from __future__ import annotations

from nuc13.cpus.Cpu import CacheIdentity, Cpu, CpuCore


class CoreI51340P(Cpu):
    """Processor part number ``i5-1340P``: 4 performance and 8 efficient cores, 16 threads.

    lshw prints each cache as a total over the core class it serves. The figures here
    are one cache each: that total divided over 4 performance cores, or over 8
    efficient cores in clusters of 4.

    Args:
        name: Node name used by logs, artifacts, and failures.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
    """

    def __init__(self, *, name: str, bom_mpns: list[str]) -> None:
        # The one 12MiB L3 of the package, which every core uses.
        l3: CacheIdentity = {
            "level": 3,
            "kind": "Unified",
            "size_kib": 12288,
            "shared_cpus": list(range(16)),
        }
        super().__init__(
            name=name,
            bom_mpns=bom_mpns,
            part_number="i5-1340P",
            model="13th Gen Intel(R) Core(TM) i5-1340P",
            vendor_id="GenuineIntel",
            signature="6.186.2",
            sockets=1,
            core_count=12,
            thread_count=16,
            # 64-bit, virtualization, and the vector and crypto extensions a workload
            # on this processor would be built for.
            flags=["lm", "ht", "vmx", "sse4_2", "avx2", "avx_vnni", "aes", "sha_ni", "rdrand"],
            minimum_khz=400_000,
            maximum_khz=4_600_000,
            # Four hyper-threaded performance cores, one per pair of CPUs 0-7, with
            # core ids 0, 4, 8, 12. A core's L1 pair and its L2 are its own, shared by
            # its two threads and no one else.
            p_cores=[
                CpuCore(
                    name=f"core-{2 * threads[0]}",
                    core_id=2 * threads[0],
                    kind="performance",
                    cpus=threads,
                    maximum_khz=4_600_000,
                    caches={
                        "l1d": {"level": 1, "kind": "Data", "size_kib": 48, "shared_cpus": threads},
                        "l1i": {
                            "level": 1,
                            "kind": "Instruction",
                            "size_kib": 32,
                            "shared_cpus": threads,
                        },
                        "l2": {
                            "level": 2,
                            "kind": "Unified",
                            "size_kib": 1280,
                            "shared_cpus": threads,
                        },
                        "l3": l3,
                    },
                )
                for threads in ([0, 1], [2, 3], [4, 5], [6, 7])
            ],
            # Eight single-threaded efficient cores on CPUs 8-15, with core ids 16-23.
            # A core's L1 pair is its own. Its L2 is one of two, each shared by a
            # cluster of four cores.
            e_cores=[
                CpuCore(
                    name=f"core-{cpu + 8}",
                    core_id=cpu + 8,
                    kind="efficient",
                    cpus=[cpu],
                    maximum_khz=3_400_000,
                    caches={
                        "l1d": {"level": 1, "kind": "Data", "size_kib": 32, "shared_cpus": [cpu]},
                        "l1i": {
                            "level": 1,
                            "kind": "Instruction",
                            "size_kib": 64,
                            "shared_cpus": [cpu],
                        },
                        "l2": {
                            "level": 2,
                            "kind": "Unified",
                            "size_kib": 2048,
                            "shared_cpus": cluster,
                        },
                        "l3": l3,
                    },
                )
                for cluster in ([8, 9, 10, 11], [12, 13, 14, 15])
                for cpu in cluster
            ],
        )
