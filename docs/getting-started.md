# Getting started

This page takes you from a fresh clone to a saved run open in the Inspector, then
through the examples in the order they teach. For why HILT is shaped the way it is,
read the [overview](overview.md); for the rules your suite is held to, the
[contracts](contracts.md).

## Install

HILT needs Python 3.14 and [uv](https://docs.astral.sh/uv/).

```sh
make install   # uv sync + git hooks
```

## Your first run

```sh
uv run hilt run examples/hello_world.py
uv run hilt inspect
```

`hilt run` loads a suite file or module and runs the one `TestComponent` subclass it
defines, or the one named after `::`. Its exit status is the verdict:

| Exit | Meaning |
|---|---|
| 0 | Pass |
| 1 | Fail |
| 2 | Aborted |
| 3 | The suite could not be run at all |

`hilt inspect [PATH]` opens a saved run in the [Inspector](inspector.md), the Floor
Technician's terminal surface. Without `PATH` it opens the latest run under
`artifacts/`.

Three flags shape what a run leaves behind ([hilt run](hilt-run.md)):

- `--artifact-root DIR` puts the run's evidence somewhere specific.
- `--json` prints the `format_version` 1 result record to stdout
  ([Inspector JSON contract](inspector-json.md)).
- `--junit PATH` writes the [JUnit XML](junit-xml.md) projection beside it, for CI.

## Write your own suite

```sh
uv run hilt new WidgetSuite
```

`hilt new` writes a gated-`PASS` suite and a matching self-test, so a new suite starts
in the right shape instead of as a copy of an example ([hilt new](hilt-new.md)).
`hilt-check` is the optional tier-3 linter over authored suites
([hilt-check](hilt-check.md)); [idioms](idioms.md) collects the authoring patterns.

## Walk the examples

The locally executable examples build from one explicit claim, through composition, to
polling, lifecycle hooks, and the evidence surface:

```sh
uv run hilt run examples/hello_world.py
uv run hilt run examples/test_composition.py::SequencedDevice
uv run hilt run examples/test_composition.py::ParallelDevice   # exits 1 by design
uv run hilt run examples/wait_for_patterns.py::ConfigureGatedDevice
uv run hilt run examples/wait_for_patterns.py::MainAssertionDevice
uv run hilt run examples/hardware_device_lifecycle.py
uv run hilt run examples/run_data_evidence.py
```

- `wait_for_patterns.py` shows readiness polling in `configure()` and verdict polling
  in a test method.
- `hardware_device_lifecycle.py` registers survey and audit hooks around a rail that
  comes up, is watched, and is dropped in cleanup.
- `run_data_evidence.py` shows limits flowing in through ctx and readings travelling
  out with the verdict.

Every example carries comments explaining the reasoning behind its pattern, not only
what the code does. Read them as the authoring guide.

### Examples against real devices

The SSH-backed examples take their target from `utils.env` in `configure()`. A missing
variable Fails the suite (exit 1): the device did not reach a testable state, and the
message names the variable to set. Optional password and port variables
(`SSH_PASSWORD` / `SSH_PORT`, `JUMP_*` / `TARGET_*`, `GATEWAY_*`) default the port to 22
when unset.

```sh
SSH_HOST=dut.lab SSH_USER=root uv run hilt run examples/remote_device_ssh.py
JUMP_HOST=jump.lab JUMP_USER=ops TARGET_HOST=dut.lab TARGET_USER=root uv run hilt run examples/ssh_tunneling.py
DUT_MAC=aa:bb:cc:dd:ee:ff uv run hilt run examples/networked_device.py::DutDevice
TARGET_ADDRESS=pihole.lab TARGET_USER=root uv run hilt run examples/pi_hole_server.py
```

`networked_device.py` defines `Gateway` and `DutDevice`; pick the station suite with
`::DutDevice`. `GATEWAY_HOST` and `GATEWAY_USER` (both required together) resolve ARP
through a hop instead of the test host's table.

## Deploying to a station

**Every test host must synchronize its system clock with NTP before HILT runs.** The
scoped log is the authoritative chronology for a run, including structured pointers to
captured artifacts, and may be used to establish event order in customer disputes. A
station with an unsynchronized clock cannot produce trustworthy evidence and must not
execute tests.

Set `HILT_ARTIFACT_ROOT` to the run-specific evidence directory. Point-in-time evidence
is an immutable `Artifact` value with no public file-writing method; persist it only with
`await utils.capture_artifact(artifact)`, which writes the file and then appends its
pointer to the scoped log timeline.

The [Floor Technician guide](floor-technician.md) is the station page: how to read a
result without reading source. Its rendered page,
<https://gitlab.com/Tyler-Meador/HILT/-/blob/main/docs/floor-technician.md>, is
the URL that belongs on a station label.

## Contributing

Read `STANDARDS.md` before committing.

```sh
make fix           # auto-format + auto-fix lint
make check         # the gate (there is no CI; the bench run is it): format check, lint, pyright strict, import cycles, pytest under the coverage floors, hilt-check, wheel smoke install, docs, pip-audit
make docs          # documentation site (strict: a broken cross-reference is red)
make docs-release  # the same build into docs/site/, committed on a release branch only
```

[API stability](api-stability.md) defines the public surface, semantic versioning from
1.0.0, and the deprecation procedure. `CHANGELOG.md` at the repository root records every
change to that surface. For a release MR, select the Release template
(`.gitlab/merge_request_templates/Release.md`): it records the candidate's checks,
changelog cut, and the handoff to Zavala for review.

Nothing hosts the docs site. The tree is the documentation: GitLab renders every page
under <https://gitlab.com/Tyler-Meador/HILT/-/tree/main/docs>, and each release
branch (`release/MAJOR.MINOR.PATCH`) also carries the built site under `docs/site/`
(`make docs-release`; open `docs/site/index.html` from a clone, no network needed).

The port design lives in the workspace plan (`../PLAN.md`); the TypeScript original is
the `../hilt-test-legacy/` design reference, and its language server is
`../hilt-language-server-legacy/`. Both are read-only — parity with them is explicitly
not a goal. `PLAN.md` section 0 maps the workspace.
