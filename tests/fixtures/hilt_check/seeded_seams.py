"""A second seeded suite, one violation per seam rule; ``tests/test_check_*`` pin the lines.

Kept apart from ``seeded_suite.py`` so its pinned line numbers stay where they are. The
same discipline applies here: one seed per rule, line numbers stable, and nothing in
this file trips a rule seeded in the other file.
"""

import os
from typing import Any

from hilt import PASS, TestComponent, TestFailure, TestUtils, Verdict


class SeededSeams(TestComponent):
    """One departure from each seam idiom, deliberately."""

    def __init__(self, *, name: str, ip: str | None = None) -> None:
        super().__init__(name=name)
        self._ip = ip

    async def configure(self, utils: TestUtils) -> dict[str, Any]:
        arp = utils.get_arp_resolver(mac=os.environ["DUT_MAC"])  # seed: hilt/env-via-utils
        ip = self._ip or (await arp.resolve())["ip"]  # seeds: no-input-fallback, resolution
        print(ip)  # seed: hilt/logger-not-print
        return {"ip": ip}

    def build_suite(self) -> TestComponent:
        return self.test("reachable", self._reachable)

    async def _reachable(self, utils: TestUtils) -> Verdict:
        try:
            result = await utils.exec("true")
        except BaseException:  # seed: hilt/no-swallowed-cancel
            raise TestFailure("exec interrupted", "exit 0", "cancelled") from None
        if result.exit_code == 0:
            return PASS
        raise TestFailure("unreachable", "exit 0", str(result.exit_code))
