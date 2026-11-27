"""Helpers the product's wiring shares, which belong to no one component."""

from __future__ import annotations

from collections.abc import Mapping


def mpn_to_class[ClassT](mpns: Mapping[str, ClassT], mpn: str) -> ClassT:
    """Return the class that judges one part number.

    Args:
        mpns: A component's ``mpns``: every part number of it the suite can judge,
            each with its class.
        mpn: The part number the bill of materials names.

    Returns:
        The class for ``mpn``.

    Raises:
        ValueError: The suite has no class for ``mpn``. The message names the part
            numbers it does have.
    """
    if mpn not in mpns:
        raise ValueError(
            f"the bill of materials names part number {mpn!r}, which this suite cannot "
            f"judge; it knows: {', '.join(mpns)}"
        )
    return mpns[mpn]
