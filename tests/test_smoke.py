"""Scaffold smoke test — proves the package imports and the async test rig runs."""

import asyncio

import hilt


class TestScaffold:
    def test_package_imports(self) -> None:
        assert hilt.__name__ == "hilt"

    async def test_async_rig_runs(self) -> None:
        await asyncio.sleep(0)
