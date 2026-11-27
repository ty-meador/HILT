"""Processor part number ``i5-1340P``: what that processor is, down to each core's caches."""

from __future__ import annotations

from nuc13.cpus.Cpu import CacheIdentity, Cpu, CpuCore

# lshw prints each cache as a total over the core class it serves. The figures below
# are one cache each: that total divided over 4 performance cores, or over 8 efficient
# cores in clusters of 4.


def _i5_1340p_l3() -> CacheIdentity:
    """The one 12MiB L3 of the package, which every core uses."""
    return {"level": 3, "kind": "Unified", "size_kib": 12288, "shared_cpus": list(range(16))}


def _i5_1340p_performance_core(index: int) -> CpuCore:
    """One of four hyper-threaded performance cores: core ids 0, 4, 8, 12 on CPUs 0-7.

    Its L1 pair and its L2 are its own, shared by its two threads and no one else.
    """
    core_id = 4 * index
    cpus = [2 * index, 2 * index + 1]
    return CpuCore(
        name=f"core-{core_id}",
        core_id=core_id,
        kind="performance",
        cpus=cpus,
        maximum_khz=4_600_000,
        caches={
            "l1d": {"level": 1, "kind": "Data", "size_kib": 48, "shared_cpus": cpus},
            "l1i": {"level": 1, "kind": "Instruction", "size_kib": 32, "shared_cpus": cpus},
            "l2": {"level": 2, "kind": "Unified", "size_kib": 1280, "shared_cpus": cpus},
            "l3": _i5_1340p_l3(),
        },
    )


def _i5_1340p_efficient_core(index: int) -> CpuCore:
    """One of eight single-threaded efficient cores: core ids 16-23 on CPUs 8-15.

    Its L1 pair is its own. Its L2 is one of two, each shared by a cluster of four
    cores (CPUs 8-11 and 12-15).
    """
    core_id = 16 + index
    cpu = 8 + index
    cluster_start = 8 + 4 * (index // 4)
    cluster = list(range(cluster_start, cluster_start + 4))
    return CpuCore(
        name=f"core-{core_id}",
        core_id=core_id,
        kind="efficient",
        cpus=[cpu],
        maximum_khz=3_400_000,
        caches={
            "l1d": {"level": 1, "kind": "Data", "size_kib": 32, "shared_cpus": [cpu]},
            "l1i": {"level": 1, "kind": "Instruction", "size_kib": 64, "shared_cpus": [cpu]},
            "l2": {"level": 2, "kind": "Unified", "size_kib": 2048, "shared_cpus": cluster},
            "l3": _i5_1340p_l3(),
        },
    )


class CoreI51340P(Cpu):
    """Processor part number ``i5-1340P``: 4 performance and 8 efficient cores, 16 threads."""

    def __init__(self, *, name: str) -> None:
        super().__init__(
            name=name,
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
            p_cores=[_i5_1340p_performance_core(index) for index in range(4)],
            e_cores=[_i5_1340p_efficient_core(index) for index in range(8)],
        )
