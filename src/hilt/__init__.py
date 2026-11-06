"""hilt — Python HIL testing with a pit of success.

Composable test components, guardrails that make a bad test hard to write, and an
engine that won't let an unhandled failure pass silently.

`__all__` below is the public surface, and `docs/api-stability.md` is the policy that
governs it: from 1.0.0 every name listed here is a promise, and nothing outside the
list is. Import public names from `hilt` itself; the module a name is defined in is
an implementation detail and may move.

Suite authors need `TestComponent`, `TestUtils`, `Shell`, `DeviceApi`, `DeviceRequest`,
`DeviceResponse`, `CoapCode`, `ExecResult`, `Artifact`,
`JsonObject`, `PASS`, `Verdict`, `evidence`, `measure`, `Measurement`, `TestFailure`,
and `ArpResolver`. The
rest of the failure taxonomy is exported because it appears in artifacts and logs,
where a reader will follow a name back to its meaning (`ExternalIntegrationFailure`,
`OperatorInterrupt`, `ArpResolutionFailure`, `DeviceApiOperationFailure`,
`EvidenceViolation`). The record types (`RunResult`,
`SummaryTreeNode`, `State`, `NodeType`, `Slot`, `TimeoutEvidence`, `RunSignal`,
`ScopedLogger`) are exported because a public object has no attribute of a private
type. `Phase` names the survey dispatch points for `use_survey(phases=)`. Harness
integrators import the narrow `BeforeEachContext` and `AfterEachContext` surfaces.

The lifecycle machinery (`hilt.lifecycle`) is deliberately absent. The child
boundary, its slot policies, and the run-abort signal are the framework's internal
organs — a suite author never holds an instance of any of them, so they do not belong
in the outward API. The transports (`RemoteInterface`, `SshInterface`,
`SerialInterface`) and the JUnit reporter stay off the surface for the same
reason: a suite author never constructs one; `use_shell` and `get_shell` hand
back a `Shell` for SSH, `use_serial` and `get_serial` do the same for a console
by device path, and the runner (#73) will own the reporter. The framework and
its self-tests import all of these from their modules directly.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version

from hilt.arp import ArpResolver
from hilt.artifacts import Artifact
from hilt.base import TestComponent
from hilt.device_api import CoapCode, DeviceApi, DeviceRequest, DeviceResponse
from hilt.errors import (
    ArpResolutionFailure,
    ContractViolation,
    DeviceApiOperationFailure,
    EvidenceViolation,
    ExternalIntegrationFailure,
    Failure,
    FrameworkContractViolation,
    HiltError,
    MissingVerdict,
    OperatorInterrupt,
    TestFailure,
)
from hilt.evidence import evidence
from hilt.executor import ExecResult
from hilt.hooks import AfterEachContext, AfterEachHook, BeforeEachContext, BeforeEachHook
from hilt.lifecycle import Phase, Slot, State, TimeoutEvidence
from hilt.measurement import Measurement, measure
from hilt.results import NodeType, RunResult, SummaryTreeNode
from hilt.routing import Shell
from hilt.run_data import JsonObject
from hilt.utils import RunEnv, RunSignal, ScopedLogger, TestUtils
from hilt.verdict import PASS, Verdict

try:
    __version__ = _distribution_version("hilt")
except PackageNotFoundError:  # pragma: no cover - a source checkout that was never installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "PASS",
    "AfterEachContext",
    "AfterEachHook",
    "ArpResolutionFailure",
    "ArpResolver",
    "Artifact",
    "BeforeEachContext",
    "BeforeEachHook",
    "CoapCode",
    "ContractViolation",
    "DeviceApi",
    "DeviceApiOperationFailure",
    "DeviceRequest",
    "DeviceResponse",
    "EvidenceViolation",
    "ExecResult",
    "ExternalIntegrationFailure",
    "Failure",
    "FrameworkContractViolation",
    "HiltError",
    "JsonObject",
    "Measurement",
    "MissingVerdict",
    "NodeType",
    "OperatorInterrupt",
    "Phase",
    "RunEnv",
    "RunResult",
    "RunSignal",
    "ScopedLogger",
    "Shell",
    "Slot",
    "State",
    "SummaryTreeNode",
    "TestComponent",
    "TestFailure",
    "TestUtils",
    "TimeoutEvidence",
    "Verdict",
    "evidence",
    "measure",
]
