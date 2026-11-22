"""The runnable suite: the nuc13 product, one unit of it reached over SSH.

This file is the whole wiring, and it reads the way the line works, top down:

- The product is what the line builds. ``Nuc13`` is its suite and the root of every
  run; it is also the station's reach to the unit in front of it.
- An MPN is one orderable configuration of the product. Its record says what a unit
  of that part number must contain, and ``Mpn`` judges the unit against it.
- A unit is the one device at the station. Nothing in the tree names one: which
  unit was reached is the station's to say, in the run's environment.

Nothing below the root knows the unit is remote.

Run it from the directory that holds the ``nuc13`` package:

    PYTHONPATH=. DUT_HOST=<address> DUT_USER=<login> hilt run nuc13/suite.py

The suite only reads: it runs as an ordinary user and changes nothing on the unit.
"""

from __future__ import annotations

from nuc13.mpn import Mpn, nuc13anki50wc
from nuc13.remote import OverSsh


class Nuc13(OverSsh):
    """The product: reach the unit under test, then judge it as its MPN."""

    def __init__(self, *, name: str = "nuc13") -> None:
        """Compose the MPN's judgement behind the hop.

        Args:
            name: Root node name used by logs, artifacts, and failures.
        """
        super().__init__(name=name, child=Mpn(spec=nuc13anki50wc.RECORD))


# The module imports several components; this names the one `hilt run` should pick.
__suite__ = Nuc13
