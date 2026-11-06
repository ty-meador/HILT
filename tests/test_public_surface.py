"""`hilt.__all__` is the public surface and `docs/api-stability.md` is its record.

The two must name the same set, every name must resolve to a documented hilt object
of the kind the table claims, and the examples and documented code blocks must reach
the surface only through the package root. An MR that changes one without the others
is refused here, not discovered by a station.
"""

from __future__ import annotations

import ast
import importlib.metadata
import inspect
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TypeAliasType

import pytest

import hilt

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "api-stability.md"
EXAMPLES = ROOT / "examples"
DOCUMENTS = (*sorted((ROOT / "docs").glob("*.md")), ROOT / "README.md", ROOT / "STANDARDS.md")

_ROW = re.compile(r"^\| `(?P<name>[A-Za-z_][A-Za-z0-9_]*)` \| (?P<kind>[a-z ]+?) \|")
_FENCE = re.compile(r"^```(?P<lang>[\w-]*)\s*$")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.+-]+)?$")


def _reference_rows() -> dict[str, str]:
    """Name to declared kind, from the Reference table of the policy document."""
    rows: dict[str, str] = {}
    in_reference = False
    for line in POLICY.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_reference = line == "## Reference"
            continue
        if not in_reference:
            continue
        match = _ROW.match(line)
        if match is None:
            continue
        name = match.group("name")
        assert name not in rows, f"{name} has two reference rows"
        rows[name] = match.group("kind").strip()
    return rows


def _kind_of(obj: object) -> str:
    if inspect.isfunction(obj):
        return "function"
    if isinstance(obj, TypeAliasType):
        return "type alias"
    if inspect.isclass(obj):
        return "class"
    return "constant"


def _hilt_imports(tree: ast.AST) -> Iterator[tuple[str, str]]:
    """Every (module, name) an import statement pulls from the hilt package."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "hilt":
            for alias in node.names:
                yield node.module, alias.name
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "hilt":
                    yield alias.name, "<module>"


def _python_blocks(document: Path) -> Iterator[tuple[int, str]]:
    """Fenced python code blocks with the line each one starts on."""
    lines = document.read_text(encoding="utf-8").splitlines()
    start: int | None = None
    body: list[str] = []
    for number, line in enumerate(lines, start=1):
        fence = _FENCE.match(line)
        if fence is None:
            if start is not None:
                body.append(line)
            continue
        if start is None:
            if fence.group("lang") in {"python", "py"}:
                start, body = number, []
        else:
            yield start, "\n".join(body)
            start = None


def _import_lines(block: str) -> Iterator[tuple[str, str]]:
    """Imports from a documented block, tolerating fragments that do not parse whole."""
    statement: list[str] = []
    for line in block.splitlines():
        stripped = line.strip()
        if statement:
            statement.append(stripped)
            if ")" not in stripped:
                continue
        elif stripped.startswith(("from hilt", "import hilt")):
            statement.append(stripped)
            if stripped.endswith("("):
                continue
        else:
            continue
        yield from _hilt_imports(ast.parse(" ".join(statement)))
        statement = []


class TestDunderAll:
    def test_every_public_name_is_a_hilt_object(self) -> None:
        for name in hilt.__all__:
            obj = getattr(hilt, name)
            module = getattr(obj, "__module__", None) or type(obj).__module__
            assert module.split(".")[0] == "hilt", f"{name} resolves outside the package: {module}"

    def test_no_public_name_is_underscored(self) -> None:
        assert [name for name in hilt.__all__ if name.startswith("_")] == []

    def test_public_names_are_unique(self) -> None:
        assert len(set(hilt.__all__)) == len(hilt.__all__)

    def test_every_public_class_states_its_contract(self) -> None:
        undocumented = [
            name
            for name in hilt.__all__
            if inspect.isclass(getattr(hilt, name))
            and not (inspect.getdoc(getattr(hilt, name)) or "").strip()
        ]
        assert undocumented == []

    def test_version_is_the_installed_distribution(self) -> None:
        assert hilt.__version__ == importlib.metadata.version("hilt")
        assert _SEMVER.match(hilt.__version__), hilt.__version__


class TestReferenceTable:
    def test_table_and_dunder_all_name_the_same_set(self) -> None:
        rows = _reference_rows()
        assert set(rows) == set(hilt.__all__), {
            "in __all__ only": sorted(set(hilt.__all__) - set(rows)),
            "in table only": sorted(set(rows) - set(hilt.__all__)),
        }

    def test_table_rows_follow_dunder_all_order(self) -> None:
        assert list(_reference_rows()) == list(hilt.__all__)

    def test_declared_kind_matches_the_object(self) -> None:
        mismatched = {
            name: (kind, _kind_of(getattr(hilt, name)))
            for name, kind in _reference_rows().items()
            if kind != _kind_of(getattr(hilt, name))
        }
        assert mismatched == {}

    def test_the_test_utils_row_names_attributes_that_exist(self) -> None:
        """Every method the `TestUtils` row promises (`use_serial` among them) is real."""
        rows = [
            line
            for line in POLICY.read_text(encoding="utf-8").splitlines()
            if line.startswith("| `TestUtils` |")
        ]
        assert len(rows) == 1
        promised = re.findall(r"`([a-z_]+)`", rows[0].split("|")[3])
        assert {"use_shell", "get_shell", "use_serial", "get_serial"} <= set(promised)
        missing = [name for name in promised if not hasattr(hilt.TestUtils, name)]
        assert missing == []

    def test_every_row_points_at_a_document_that_exists(self) -> None:
        in_reference = False
        for line in POLICY.read_text(encoding="utf-8").splitlines():
            if line.startswith("## "):
                in_reference = line == "## Reference"
            if in_reference and _ROW.match(line):
                cited = re.findall(r"`(docs/[a-z-]+\.md)`", line)
                assert cited, f"no document cited: {line[:60]}"
                for path in cited:
                    assert (ROOT / path).is_file(), path


class TestSurfaceIsReachedThroughThePackageRoot:
    @pytest.mark.parametrize("example", sorted(EXAMPLES.glob("*.py")), ids=lambda p: p.name)
    def test_examples_import_only_public_names_from_hilt(self, example: Path) -> None:
        tree = ast.parse(example.read_text(encoding="utf-8"))
        offending = [
            (module, name)
            for module, name in _hilt_imports(tree)
            if module != "hilt" or name not in hilt.__all__
        ]
        assert offending == []

    @pytest.mark.parametrize("document", DOCUMENTS, ids=lambda p: p.name)
    def test_documented_code_imports_only_public_names_from_hilt(self, document: Path) -> None:
        offending = [
            (start, module, name)
            for start, block in _python_blocks(document)
            for module, name in _import_lines(block)
            if module != "hilt" or name not in hilt.__all__
        ]
        assert offending == []

    def test_documented_code_is_scanned(self) -> None:
        """A regression in the fence scanner would pass vacuously; prove it finds imports."""
        found = [
            (module, name)
            for document in DOCUMENTS
            for _, block in _python_blocks(document)
            for module, name in _import_lines(block)
        ]
        assert ("hilt", "JsonObject") in found
