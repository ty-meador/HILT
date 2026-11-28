"""The processor: the package's own claims, then one node per core.

    cpu
    ├─ package            bom-check, model, vendor, signature, socket-count,
    │                     core-count, thread-count, online, flags, frequency-range
    │                                                         (this class's claims)
    ├─ p_cores
    │  └─ core-<id>       online, then siblings, core-id, kind, per thread
    │     │               cpu-<n>-maximum-frequency and cpu-<n>-compute, and
    │     └─ caches       <cache>-size and <cache>-sharing for l1d, l1i, l2, l3
    └─ e_cores
       └─ core-<id>       the same claims, for an efficient core

The package reads what every logical CPU reports about the whole (``/proc/cpuinfo``,
the kernel's present and online lists). A core reads its own threads' topology,
proves each thread still computes, and judges the caches it uses. A cache has no
node of its own: it is part of the core it serves, so a cache several cores share
is judged from each of them.

Every reading is a file read or, for ``compute``, a hash of a fixed megabyte: nothing
here changes the device or loads it for longer than a few milliseconds per thread.

A processor part number is one class, in a module of its own beside this one; it says
what that processor is, down to each core and the caches it uses.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from typing import ClassVar, Literal, TypedDict, Unpack

from nuc13.kernel import (
    CPU_SYSFS,
    NOT_READ,
    parse_cpu_list,
    parse_int,
    parse_size_kib,
    read_files,
    read_int,
    read_per_cpu,
    read_text,
)

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence

type CoreKind = Literal["performance", "efficient"]
type CacheKind = Literal["Data", "Instruction", "Unified"]


# ------------------------------------------------------------------------- core


class CacheIdentity(TypedDict):
    """What one of a core's caches must look like from each of the core's threads.

    A cache belongs to the cores it serves, so it is part of each of those cores'
    identity. A cache that several cores share (an efficient cluster's L2, the
    package's L3) appears in each of them with the same ``shared_cpus``.

    Attributes:
        level: Cache level, 1 to 3.
        kind: The kernel's ``cache/indexN/type``.
        size_kib: Size of this one cache.
        shared_cpus: Every logical CPU that uses this cache, the kernel's
            ``shared_cpu_list``. It includes the core's own threads.
    """

    level: int
    kind: CacheKind
    size_kib: int
    shared_cpus: list[int]


class CoreIdentity(TypedDict):
    """What one physical core must report.

    Attributes:
        core_id: The kernel's ``topology/core_id`` for this core.
        kind: Performance (hyper-threaded) or efficient core.
        cpus: Logical CPUs that are this core's hardware threads.
        maximum_khz: ``cpufreq/cpuinfo_max_freq`` every thread of the core reports.
        caches: Every cache this core uses, its own and the ones it shares, keyed by
            the prefix of its claims, such as ``l1d`` or ``l3``.
    """

    core_id: int
    kind: CoreKind
    cpus: list[int]
    maximum_khz: int
    caches: dict[str, CacheIdentity]


class CpuCore(TestComponent[CoreIdentity]):
    """Judge one physical core: its threads, its topology, its arithmetic, its caches.

    Args:
        name: Node name used by logs, artifacts, and failures.
        **identity: What the core must report.
    """

    # A hybrid processor registers one PMU per core type, and each lists its CPUs.
    _KIND_CPUS: ClassVar[dict[CoreKind, str]] = {
        "performance": "/sys/devices/cpu_core/cpus",
        "efficient": "/sys/devices/cpu_atom/cpus",
    }

    # The work each hardware thread must get right: SHA-256 over this many zero bytes.
    _WORK_BYTES = 1024 * 1024
    _WORK_SHA256 = "30e14955ebf1352266dc2ff8067e68104607e750abb9d3b36582b8af909fcb58"

    def __init__(self, *, name: str, **identity: Unpack[CoreIdentity]) -> None:
        super().__init__(name=name)
        self._identity = identity

    async def configure(self, utils: TestUtils[CoreIdentity]) -> CoreIdentity:
        """Provide the identity the core's claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """Online first: an offline thread has no topology, no cpufreq, no caches, no work.

        Every later claim would fail for that one reason and bury it. Once the
        threads are online the remaining claims are independent and run in parallel,
        the core's caches among them: a wrong cache size never hides a wrong core id.
        """
        return self.sequence(
            [
                self.test("online", self._online),
                self.parallel(
                    "checks",
                    [
                        self.test("siblings", self._siblings),
                        self.test("core-id", self._core_id),
                        self.test("kind", self._kind),
                        *[
                            self.test(
                                f"cpu-{cpu}-maximum-frequency", self._maximum_frequency, cpu=cpu
                            )
                            for cpu in self._identity["cpus"]
                        ],
                        *[
                            self.test(f"cpu-{cpu}-compute", self._compute, cpu=cpu)
                            for cpu in self._identity["cpus"]
                        ],
                        self.parallel(
                            "caches",
                            [
                                *[
                                    self.test(f"{cache}-size", self._cache_size, cache=cache)
                                    for cache in self._identity["caches"]
                                ],
                                *[
                                    self.test(f"{cache}-sharing", self._cache_sharing, cache=cache)
                                    for cache in self._identity["caches"]
                                ],
                            ],
                            discrete_log_file=False,
                        ),
                    ],
                    discrete_log_file=False,
                ),
            ]
        )

    @evidence("cpus_online")
    async def _online(self, utils: TestUtils[CoreIdentity]) -> Verdict:
        threads = utils.ctx["cpus"]
        online = parse_cpu_list(await read_text(utils, f"{CPU_SYSFS}/online"))
        if online is None:
            raise TestFailure("online CPU list could not be read", threads, NOT_READ)
        reading = {"cpus_online": list(online)}
        if set(threads) <= set(online):
            return PASS(reading)
        offline = sorted(set(threads) - set(online))
        raise TestFailure(f"threads {offline} are offline", threads, list(online), evidence=reading)

    @evidence("siblings")
    async def _siblings(self, utils: TestUtils[CoreIdentity]) -> Verdict:
        threads = utils.ctx["cpus"]
        lists = await read_per_cpu(utils, threads, "topology/core_cpus_list")
        siblings = {
            cpu: None if (listed := parse_cpu_list(text)) is None else list(listed)
            for cpu, text in lists.items()
        }
        reading = {"siblings": siblings}
        # Each thread must name exactly this core's threads: no stranger, none missing.
        if all(listed == threads for listed in siblings.values()):
            return PASS(reading)
        found = self._per_cpu(siblings)
        if None in siblings.values():
            raise TestFailure("thread siblings could not be read", threads, found, evidence=reading)
        raise TestFailure("thread siblings do not match", threads, found, evidence=reading)

    @evidence("core_ids")
    async def _core_id(self, utils: TestUtils[CoreIdentity]) -> Verdict:
        expected = utils.ctx["core_id"]
        texts = await read_per_cpu(utils, utils.ctx["cpus"], "topology/core_id")
        core_ids = {cpu: parse_int(text) for cpu, text in texts.items()}
        reading = {"core_ids": core_ids}
        if all(core_id == expected for core_id in core_ids.values()):
            return PASS(reading)
        found = self._per_cpu(core_ids)
        if None in core_ids.values():
            raise TestFailure("core id could not be read", expected, found, evidence=reading)
        raise TestFailure("core id does not match", expected, found, evidence=reading)

    @evidence("kinds")
    async def _kind(self, utils: TestUtils[CoreIdentity]) -> Verdict:
        expected = utils.ctx["kind"]
        files = await read_files(utils, *self._KIND_CPUS.values())
        members: dict[CoreKind, tuple[int, ...] | None] = {
            kind: parse_cpu_list(files.get(path)) for kind, path in self._KIND_CPUS.items()
        }
        kinds = {str(cpu): self._kind_of(cpu, members) for cpu in utils.ctx["cpus"]}
        reading = {"kinds": kinds}
        if all(kind == expected for kind in kinds.values()):
            return PASS(reading)
        found = self._per_cpu(kinds)
        if None in kinds.values():
            raise TestFailure("core type could not be read", expected, found, evidence=reading)
        raise TestFailure("core type does not match", expected, found, evidence=reading)

    @evidence("maximum_frequency_khz")
    async def _maximum_frequency(self, utils: TestUtils[CoreIdentity], *, cpu: int) -> Verdict:
        expected = utils.ctx["maximum_khz"]
        khz = await read_int(utils, f"{CPU_SYSFS}/cpu{cpu}/cpufreq/cpuinfo_max_freq")
        ceiling = utils.measure(
            "maximum_frequency_khz", khz, unit="kHz", limits=(expected, expected)
        )
        reading = {"maximum_frequency_khz": khz}
        if ceiling.in_limits:
            return PASS(reading)
        if khz is None:
            raise TestFailure(
                f"cpu{cpu} maximum frequency could not be read", f"{expected} kHz", NOT_READ
            )
        raise TestFailure(
            f"cpu{cpu} maximum frequency does not match",
            f"{expected} kHz",
            f"{khz} kHz",
            evidence=reading,
        )

    @evidence("sha256")
    async def _compute(self, utils: TestUtils[CoreIdentity], *, cpu: int) -> Verdict:
        # taskset wraps the whole pipeline, so the thread that hashes the input is
        # also the one that produced it and no other CPU can lend a hand.
        result = await utils.exec(
            f"taskset -c {cpu} sh -c 'head -c {self._WORK_BYTES} /dev/zero | sha256sum'"
        )
        words = result.stdout.split()
        digest = words[0] if result.exit_code == 0 and words else None
        reading = {"sha256": digest}
        if digest == self._WORK_SHA256:
            return PASS(reading)
        if digest is None:
            raise TestFailure(
                f"work pinned to cpu{cpu} did not run",
                self._WORK_SHA256,
                result.stderr.strip() or f"exit {result.exit_code}",
            )
        raise TestFailure(
            f"cpu{cpu} computed a wrong digest", self._WORK_SHA256, digest, evidence=reading
        )

    async def _read_cache(
        self, utils: TestUtils[CoreIdentity], cache: str, attribute: str
    ) -> dict[str, str | None]:
        """Read one attribute of the named cache as each of the core's threads reports it.

        Keyed by CPU number as text, so the mapping can travel as evidence. A thread
        maps to ``None`` when it does not show exactly one cache of that level and
        type, or the attribute could not be read.
        """
        expected = utils.ctx["caches"][cache]
        cpus = utils.ctx["cpus"]
        files = await read_files(
            utils,
            *(
                f"{CPU_SYSFS}/cpu{cpu}/cache/index[0-9]*/{name}"
                for cpu in cpus
                for name in ("level", "type", attribute)
            ),
        )
        found: dict[str, str | None] = {}
        for cpu in cpus:
            # The kernel numbers a CPU's caches in the order it discovers them, so
            # the index holding this level and type is found, not assumed.
            indexes = {
                path.rsplit("/", 1)[0]
                for path in files
                if path.startswith(f"{CPU_SYSFS}/cpu{cpu}/cache/")
            }
            matches = [
                index
                for index in indexes
                if parse_int(files.get(f"{index}/level")) == expected["level"]
                and files.get(f"{index}/type") == expected["kind"]
            ]
            found[str(cpu)] = files.get(f"{matches[0]}/{attribute}") if len(matches) == 1 else None
        return found

    @evidence("sizes_kib")
    async def _cache_size(self, utils: TestUtils[CoreIdentity], *, cache: str) -> Verdict:
        expected = utils.ctx["caches"][cache]["size_kib"]
        texts = await self._read_cache(utils, cache, "size")
        sizes = {cpu: parse_size_kib(text) for cpu, text in texts.items()}
        reading = {"sizes_kib": sizes}
        # Every thread of the core must see the cache, and see it at this size.
        if all(size == expected for size in sizes.values()):
            return PASS(reading)
        found = self._per_cpu(sizes)
        if None in sizes.values():
            raise TestFailure(
                f"{cache} cache size could not be read", f"{expected} KiB", found, evidence=reading
            )
        raise TestFailure(
            f"{cache} cache size does not match", f"{expected} KiB", found, evidence=reading
        )

    @evidence("shared_cpus")
    async def _cache_sharing(self, utils: TestUtils[CoreIdentity], *, cache: str) -> Verdict:
        expected = utils.ctx["caches"][cache]["shared_cpus"]
        texts = await self._read_cache(utils, cache, "shared_cpu_list")
        shared = {
            cpu: None if (listed := parse_cpu_list(text)) is None else list(listed)
            for cpu, text in texts.items()
        }
        reading = {"shared_cpus": shared}
        # The sharing list is what tells one physical cache from another: a core
        # that names a stranger, or misses a neighbour, is on a different cache.
        if all(listed == expected for listed in shared.values()):
            return PASS(reading)
        found = self._per_cpu(shared)
        if None in shared.values():
            raise TestFailure(
                f"{cache} cache sharing could not be read", expected, found, evidence=reading
            )
        raise TestFailure(
            f"{cache} cache is shared with the wrong CPUs", expected, found, evidence=reading
        )

    @staticmethod
    def _kind_of(cpu: int, members: Mapping[CoreKind, tuple[int, ...] | None]) -> CoreKind | None:
        """Name the core type whose PMU lists ``cpu``; ``None`` when neither does."""
        for kind, cpus in members.items():
            if cpus is not None and cpu in cpus:
                return kind
        return None

    @staticmethod
    def _per_cpu(readings: Mapping[str, object]) -> str:
        """Spell per-CPU readings for a failure line: ``cpu0=performance, cpu1=None``."""
        return ", ".join(f"cpu{cpu}={value}" for cpu, value in readings.items())


# ---------------------------------------------------------------------- package


class CpuIdentity(TypedDict):
    """What the processor is, and what the package must report as a whole.

    Attributes:
        part_number: The processor as the bill of materials names it.
        bom_mpns: The part numbers the unit's bill of materials lists for this component.
        model: ``model name`` as the kernel reports it.
        vendor_id: CPUID vendor string.
        signature: CPUID family, model, and stepping, joined as ``6.186.2``.
        sockets: Populated sockets.
        core_count: Physical cores.
        thread_count: Logical CPUs.
        flags: Capabilities every core must advertise in ``/proc/cpuinfo``.
        minimum_khz: Lowest frequency the package scales to.
        maximum_khz: Highest frequency of any core.
    """

    part_number: str
    bom_mpns: list[str]
    model: str
    vendor_id: str
    signature: str
    sockets: int
    core_count: int
    thread_count: int
    flags: list[str]
    minimum_khz: int
    maximum_khz: int


class Cpu(TestComponent[CpuIdentity]):
    """Judge the processor package, then every core in it.

    Args:
        name: Node name used by logs, artifacts, and failures.
        p_cores: Every performance core, each with its caches.
        e_cores: Every efficient core, each with its caches.
        **identity: What the processor is, and what the package must report as a whole.
    """

    _CPUINFO = "/proc/cpuinfo"

    def __init__(
        self,
        *,
        name: str,
        p_cores: Sequence[CpuCore],
        e_cores: Sequence[CpuCore],
        **identity: Unpack[CpuIdentity],
    ) -> None:
        super().__init__(name=name)
        self._identity = identity
        self._p_cores = tuple(p_cores)
        self._e_cores = tuple(e_cores)

    async def configure(self, utils: TestUtils[CpuIdentity]) -> CpuIdentity:
        """Provide the identity the package's own claims judge against."""
        return self._identity

    def build_suite(self) -> TestComponent:
        """All independent: the package and each core are read separately.

        Nothing here gates anything else, so everything runs in parallel and a wrong
        model name never hides an offline core. A core builds its own cache claims.
        """
        return self.parallel(
            [
                self.parallel(
                    "package",
                    [
                        self.test("bom-check", self._bom_check),
                        self.test("model", self._model),
                        self.test("vendor", self._vendor),
                        self.test("signature", self._signature),
                        self.test("socket-count", self._socket_count),
                        self.test("core-count", self._core_count),
                        self.test("thread-count", self._thread_count),
                        self.test("online", self._online),
                        self.test("flags", self._flags),
                        self.test("frequency-range", self._frequency_range),
                    ],
                    discrete_log_file=False,
                ),
                self.parallel("p_cores", self._p_cores),
                self.parallel("e_cores", self._e_cores),
            ]
        )

    async def _bom_check(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = [utils.ctx["part_number"]]
        listed = utils.ctx["bom_mpns"]
        if listed == expected:
            return PASS
        raise TestFailure("the bill of materials does not list this processor", expected, listed)

    @evidence("model_names")
    async def _model(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = utils.ctx["model"]
        names = self._cpuinfo_values(await read_text(utils, self._CPUINFO), "model name")
        if names is None:
            raise TestFailure("model name could not be read", expected, NOT_READ)
        reading = {"model_names": sorted(set(names))}
        # One distinct name, the right one: every logical CPU is this processor.
        if reading["model_names"] == [expected]:
            return PASS(reading)
        raise TestFailure(
            "model name does not match",
            expected,
            ", ".join(reading["model_names"]),
            evidence=reading,
        )

    @evidence("vendor_ids")
    async def _vendor(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = utils.ctx["vendor_id"]
        vendors = self._cpuinfo_values(await read_text(utils, self._CPUINFO), "vendor_id")
        if vendors is None:
            raise TestFailure("vendor id could not be read", expected, NOT_READ)
        reading = {"vendor_ids": sorted(set(vendors))}
        if reading["vendor_ids"] == [expected]:
            return PASS(reading)
        raise TestFailure(
            "vendor id does not match",
            expected,
            ", ".join(reading["vendor_ids"]),
            evidence=reading,
        )

    @evidence("signatures")
    async def _signature(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = utils.ctx["signature"]
        cpuinfo = await read_text(utils, self._CPUINFO)
        families = self._cpuinfo_values(cpuinfo, "cpu family")
        models = self._cpuinfo_values(cpuinfo, "model")
        steppings = self._cpuinfo_values(cpuinfo, "stepping")
        if families is None or models is None or steppings is None:
            raise TestFailure("CPU signature could not be read", expected, NOT_READ)
        # Judged as one value, the way lshw prints it: family.model.stepping.
        triples = zip(families, models, steppings, strict=True)
        reading = {"signatures": sorted({".".join(triple) for triple in triples})}
        if reading["signatures"] == [expected]:
            return PASS(reading)
        raise TestFailure(
            "CPU signature does not match",
            expected,
            ", ".join(reading["signatures"]),
            evidence=reading,
        )

    @evidence("socket_ids")
    async def _socket_count(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = utils.ctx["sockets"]
        packages = self._cpuinfo_values(await read_text(utils, self._CPUINFO), "physical id")
        if packages is None:
            raise TestFailure("socket ids could not be read", expected, NOT_READ)
        reading = {"socket_ids": sorted(set(packages))}
        if len(reading["socket_ids"]) == expected:
            return PASS(reading)
        raise TestFailure(
            "socket count does not match", expected, len(reading["socket_ids"]), evidence=reading
        )

    @evidence("cores_found")
    async def _core_count(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = utils.ctx["core_count"]
        cpuinfo = await read_text(utils, self._CPUINFO)
        packages = self._cpuinfo_values(cpuinfo, "physical id")
        core_ids = self._cpuinfo_values(cpuinfo, "core id")
        if packages is None or core_ids is None:
            raise TestFailure("core ids could not be read", expected, NOT_READ)
        # Core ids repeat across sockets, so a physical core is the pair.
        cores = len(set(zip(packages, core_ids, strict=True)))
        reading = {"cores_found": cores}
        if cores == expected:
            return PASS(reading)
        raise TestFailure("physical core count does not match", expected, cores, evidence=reading)

    @evidence("cpus_present")
    async def _thread_count(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = utils.ctx["thread_count"]
        present = parse_cpu_list(await read_text(utils, f"{CPU_SYSFS}/present"))
        if present is None:
            raise TestFailure("present CPU list could not be read", expected, NOT_READ)
        reading = {"cpus_present": list(present)}
        if len(present) == expected:
            return PASS(reading)
        raise TestFailure(
            "logical CPU count does not match", expected, len(present), evidence=reading
        )

    @evidence("cpus_online")
    async def _online(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        expected = self._logical_cpus(utils)
        online = parse_cpu_list(await read_text(utils, f"{CPU_SYSFS}/online"))
        if online is None:
            raise TestFailure("online CPU list could not be read", expected, NOT_READ)
        reading = {"cpus_online": list(online)}
        if set(expected) <= set(online):
            return PASS(reading)
        offline = sorted(set(expected) - set(online))
        raise TestFailure(f"CPUs {offline} are offline", expected, list(online), evidence=reading)

    @evidence("flags_advertised")
    async def _flags(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        required = utils.ctx["flags"]
        per_cpu = self._cpuinfo_values(await read_text(utils, self._CPUINFO), "flags")
        if per_cpu is None:
            raise TestFailure("CPU flags could not be read", " ".join(required), NOT_READ)
        # Software can rely on a capability only if every core offers it, and on a
        # hybrid package the two core types need not agree.
        advertised = set[str].intersection(*(set(flags.split()) for flags in per_cpu))
        reading = {"flags_advertised": sorted(advertised)}
        if advertised.issuperset(required):
            return PASS(reading)
        missing = sorted(set(required) - advertised)
        raise TestFailure(
            "required CPU flags are missing",
            " ".join(required),
            f"missing: {' '.join(missing)}",
            evidence=reading,
        )

    @evidence("minimum_frequency_khz", "maximum_frequency_khz")
    async def _frequency_range(self, utils: TestUtils[CpuIdentity]) -> Verdict:
        low, high, cpus = (
            utils.ctx["minimum_khz"],
            utils.ctx["maximum_khz"],
            self._logical_cpus(utils),
        )
        floors = await read_per_cpu(utils, cpus, "cpufreq/cpuinfo_min_freq")
        ceilings = await read_per_cpu(utils, cpus, "cpufreq/cpuinfo_max_freq")
        # The package's range runs from its slowest core's floor to its fastest's top.
        floor = self._complete([parse_int(text) for text in floors.values()])
        ceiling = self._complete([parse_int(text) for text in ceilings.values()])
        lowest = utils.measure(
            "minimum_frequency_khz",
            None if floor is None else min(floor),
            unit="kHz",
            limits=(low, low),
        )
        highest = utils.measure(
            "maximum_frequency_khz",
            None if ceiling is None else max(ceiling),
            unit="kHz",
            limits=(high, high),
        )
        reading = {"minimum_frequency_khz": lowest.value, "maximum_frequency_khz": highest.value}
        if lowest.in_limits and highest.in_limits:
            return PASS(reading)
        if lowest.value is None or highest.value is None:
            raise TestFailure("frequency range could not be read", f"{low}-{high} kHz", NOT_READ)
        raise TestFailure(
            "frequency range does not match",
            f"{low}-{high} kHz",
            f"{lowest.value}-{highest.value} kHz",
            evidence=reading,
        )

    @staticmethod
    def _cpuinfo_values(text: str | None, field: str) -> list[str] | None:
        """Return ``field`` as each logical CPU reports it in ``/proc/cpuinfo``.

        ``None`` when the file was not read or any CPU's record lacks the field: a
        partial answer would let a claim pass on the CPUs that happened to report.
        """
        if text is None:
            return None
        values: list[str] = []
        for record in text.split("\n\n"):
            lines = (line.partition(":") for line in record.splitlines())
            found = [value.strip() for key, _, value in lines if key.strip() == field]
            if len(found) != 1:
                return None
            values.append(found[0])
        return values

    @staticmethod
    def _complete(values: Collection[int | None]) -> list[int] | None:
        """Return the readings when every one was taken, else ``None``.

        An extreme or a sum over a partial set would be a guess presented as a reading.
        """
        taken = [value for value in values if value is not None]
        return taken if taken and len(taken) == len(values) else None

    @staticmethod
    def _logical_cpus(utils: TestUtils[CpuIdentity]) -> list[int]:
        """Every logical CPU of the package: the kernel numbers them from 0, no gaps."""
        return list(range(utils.ctx["thread_count"]))
