"""The nuc13 product, and every system part number the suite can judge."""

from __future__ import annotations

from nuc13.Nuc13AnKi50Wc import Nuc13AnKi50Wc

# Every system part number the suite can judge, each with the class that judges it.
# A bill of materials names its system as the shopfloor writes it, which is not how a
# class is spelled, so this is where a harness looks that line up.
mpns = {"NUC13ANKi50WC": Nuc13AnKi50Wc}
