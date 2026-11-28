"""What the product needs of a part number's class before it builds the part.

A system part number names the class of each part it is built from and builds none of
them. The product builds each one where it lists it, because the product is what holds
the unit's bill of materials, and a part is handed its own line when it is built.
"""

from __future__ import annotations

from typing import Protocol


class PartNumber[Part](Protocol):
    """A part number's class: built from a node name and its line of the bill of materials.

    A part that is also fitted somewhere takes more than this, and the component it is
    a part number of says what, beside that component.
    """

    def __call__(self, *, name: str, bom_mpns: list[str]) -> Part:
        """Build the part.

        Args:
            name: Node name used by logs, artifacts, and failures.
            bom_mpns: The part numbers the unit's bill of materials lists for this
                component.
        """
        ...
