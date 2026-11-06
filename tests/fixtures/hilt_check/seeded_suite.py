"""A suite with one seeded violation per rule; ``tests/test_check_*`` pin the lines.

Keep the seeds one per rule and keep the line numbers stable: each rule's CLI test names
the exact ``line:col`` it expects, which is what proves the fixture is caught end to
end rather than merely somewhere.
"""

from typing import Any

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict, evidence, measure


class SeededBlade(TestComponent):
    """One departure from each idiom, deliberately."""

    def __init__(self, *, name: str, limit_c: float) -> None:
        super().__init__(name=name)
        self._limit_c = limit_c

    async def configure(self, utils: TestUtils) -> dict[str, Any]:
        self._reading_c = 40.0  # seed: hilt/immutable-self
        self.use_survey(self._snapshot)  # seed: hilt/constructor-hooks
        return {}

    def build_suite(self) -> TestComponent:
        claims = [self.test("temperature", lambda u: self._temperature(u))]  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]  # two seeds
        return self.group(self.name, claims)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]  # seed: hilt/retired-names

    async def _temperature(self, _utils: TestUtils) -> Verdict:
        if self._limit_c < 0:
            raise TestFailure("negative limit", ">= 0", str(self._limit_c))
        return PASS  # seed: hilt/gated-pass

    async def _snapshot(self, _utils: TestUtils) -> object:
        return {"reading_c": self._reading_c}

    def _annotation_seed(self, value) -> None:  # pyright: ignore[reportMissingParameterType, reportUnknownParameterType]  # noqa: ANN001
        del value  # seed: hilt/unannotated-interface

    async def _plain_error(self, _utils: TestUtils) -> Verdict:
        raise ValueError("out of range")  # seed: hilt/raise-test-failure

    async def cleanup(self, utils: TestUtils) -> None:
        if self._limit_c:
            return PASS  # pyright: ignore[reportReturnType]  # seed: hilt/no-verdict-in-cleanup
        return None

    async def _unprovided(self, utils: TestUtils) -> Verdict:
        if utils.ctx["rail_v"] > 0:  # seed: hilt/ctx-provenance
            return PASS
        raise TestFailure("rail down", "> 0 V", "0 V")

    @evidence("reading_c")
    async def _unfulfilled(self, utils: TestUtils) -> Verdict:
        if self._limit_c:
            return PASS  # seed: hilt/evidence-fulfilled
        raise TestFailure("no limit", "> 0", "0")

    async def _retarget(self, utils: TestUtils) -> object:
        await utils.use_shell(inherit=False)  # seed: hilt/shell-in-configure
        return None

    async def _twice(self, utils: TestUtils) -> Verdict:
        utils.measure("rail_v", 3.3)
        utils.measure("rail_v", 3.31)  # seed: hilt/measurement-name-unique
        if self._limit_c:
            return PASS
        raise TestFailure("no limit", "> 0", "0")


async def before_each(_context: object) -> None:
    measure("hook_ms", 1.0)  # seed: hilt/measurement-in-node


class SeededGateway(TestComponent):
    """The one seed that needs a constructor argument beside a discovery call."""

    def __init__(self, *, name: str, mac: str, ip: str | None = None) -> None:
        super().__init__(name=name)
        self._mac = mac
        self._ip = ip

    async def configure(self, utils: TestUtils) -> dict[str, Any]:
        resolution = utils.get_arp_resolver(mac=self._mac).resolve()
        ip = self._ip or (await resolution)["ip"]  # seed: hilt/no-input-fallback
        return {"ip": ip}

    def build_suite(self) -> TestComponent:
        return self.test("reachable", self._reachable)

    async def _reachable(self, utils: TestUtils) -> Verdict:
        if utils.ctx["ip"]:
            return PASS
        raise TestFailure("no address", "an IP", "none")


class SeededReadings(TestComponent):
    """The one seed whose two halves are a ``configure()`` key and an ``@evidence`` key."""

    def __init__(self, *, name: str) -> None:
        super().__init__(name=name)

    async def configure(self, utils: TestUtils) -> dict[str, Any]:
        return {"rail_v": 3.3}

    def build_suite(self) -> TestComponent:
        return self.test("rail", self._rail)

    @evidence("rail_v")  # seed: hilt/evidence-shadows-ctx
    async def _rail(self, utils: TestUtils) -> Verdict:
        if utils.ctx["rail_v"] > 3.0:
            return PASS({"rail_v": utils.ctx["rail_v"]})
        raise TestFailure("rail low", "> 3.0 V", str(utils.ctx["rail_v"]))
