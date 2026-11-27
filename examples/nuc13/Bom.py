"""A unit's bill of materials, as the shopfloor gives it.

On a line, the station asks the shopfloor what the unit in front of it was built
from. Here a JSON file stands in for that answer. It is a bill of materials and
nothing more: the system's part number, then the part numbers under each component
type, a list where the unit has several:

    {
      "system": "NUC13ANKi50WC",
      "cpu": "i5-1340P",
      "memory": ["4ATF51264HZ-3G2R1", "4ATF51264HZ-3G2R1"],
      ...
    }

Two things are not the shopfloor's to say, and so are not read here. What a part
number means is the suite's: one class per part number, beside the component it is a
part number of. How many of each component a unit is built from is the system part
number's, so its class says it and the product holds the bill of materials to it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TypeIs, cast


@dataclass(frozen=True)
class Bom:
    """One unit's bill of materials: the part numbers it was built from.

    Attributes:
        system: The finished assembly's own part number.
        parts: Every other line: component type to its part numbers, one per part
            fitted, in the order the shopfloor lists them.
    """

    _SYSTEM_LINE = "system"

    system: str
    parts: Mapping[str, tuple[str, ...]]

    @classmethod
    def read(cls, path: Path) -> Bom:
        """Read one unit's bill of materials from the shopfloor's JSON.

        The file comes from outside the suite, so its shape is checked here and
        nothing past this method has to doubt it: a system part number, and part
        numbers on every other line. Which component types a unit has, and how many
        of each, is not checked: the system part number says that, not the file's
        reader.

        Args:
            path: The JSON file.

        Returns:
            The bill of materials.

        Raises:
            ValueError: The file cannot be read, or is not a bill of materials. The
                message says what is wrong with it.
        """
        try:
            document: object = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"{path}: the bill of materials could not be read: {error}") from error
        if not isinstance(document, dict):
            raise ValueError(
                f"{path}: a bill of materials is a JSON object, component type to part"
            )
        lines = dict(cast("dict[str, object]", document))

        system = lines.pop(cls._SYSTEM_LINE, None)
        if not cls._is_part_number(system):
            raise ValueError(f"{path}: {cls._SYSTEM_LINE} must be one part number")
        return cls(
            system=system,
            parts={
                component: cls._part_numbers(path, component, line)
                for component, line in lines.items()
            },
        )

    @classmethod
    def _part_numbers(cls, path: Path, component: str, line: object) -> tuple[str, ...]:
        """Return the part numbers on one line: a lone part number is a list of one."""
        entries = cast("list[object]", line) if isinstance(line, list) else [line]
        part_numbers: list[str] = []
        for entry in entries:
            if not cls._is_part_number(entry):
                raise ValueError(
                    f"{path}: {component} must be a part number or a list of part numbers"
                )
            part_numbers.append(entry)
        return tuple(part_numbers)

    @staticmethod
    def _is_part_number(value: object) -> TypeIs[str]:
        """Whether ``value`` could be a part number: text, and not blank."""
        return isinstance(value, str) and bool(value.strip())
