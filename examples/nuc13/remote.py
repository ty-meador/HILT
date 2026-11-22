"""The SSH hop to the unit under test.

The hardware components in this package never say where their commands run: run on
their own they read the test host, and composed under ``OverSsh`` they read the
unit. That is HILT's "the parent owns the route" idiom, and it is why this is the
only file in the package that knows the unit is remote.

Which unit is the station's to say, never the suite's: the target comes from the
run's environment, so it is published on the run's record:

- ``DUT_HOST``: address or name of the device (required)
- ``DUT_USER``: login on the device (required)
- ``DUT_PORT``: SSH port (default 22)

Authentication is the transport's own: the agent, then the user's default keys.
"""

from __future__ import annotations

from typing import Any

from hilt import TestComponent, TestFailure, TestUtils

# The suite fans out wide under this one hop: a claim per core, per cache, per PCI
# function. The device's sshd grants 10 sessions per connection (its default) and
# refuses the rest, so the hop runs at most this many commands at once and the
# others take turns. Two sessions are left free for whatever else rides the
# connection.
_MAX_SESSIONS = 8


class OverSsh(TestComponent):
    """Open one SSH hop and run ``child`` behind it."""

    def __init__(self, *, name: str, child: TestComponent) -> None:
        """Hold the component to run on the far side of the hop.

        Args:
            name: Node name used by logs, artifacts, and failures.
            child: The component whose commands should land on the device.
        """
        super().__init__(name=name)
        self._child = child

    async def configure(self, utils: TestUtils) -> dict[str, Any]:
        """Declare the route. An unreachable device is a Fail here, before any claim."""
        host = utils.env.require("DUT_HOST")
        username = utils.env.require("DUT_USER")
        port = utils.env.port("DUT_PORT")
        try:
            await utils.use_shell(
                host=host, username=username, port=port, max_sessions=_MAX_SESSIONS
            )
        except Exception as error:
            raise TestFailure(f"SSH connect to {username}@{host}:{port} failed: {error}") from error
        return {}

    def build_suite(self) -> TestComponent:
        """Everything composed here inherits the hop."""
        return self._child
