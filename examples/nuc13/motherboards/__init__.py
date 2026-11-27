"""The motherboard, and every board part number the suite can judge."""

from __future__ import annotations

from nuc13.motherboards.Nuc13AnBi5 import Nuc13AnBi5

# Every board part number the suite can judge, each with the class that judges it.
mpns = {"NUC13ANBi5": Nuc13AnBi5}
