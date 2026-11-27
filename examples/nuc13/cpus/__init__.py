"""The processor, and every processor part number the suite can judge."""

from __future__ import annotations

from nuc13.cpus.CoreI51340P import CoreI51340P

# Every processor part number the suite can judge, each with the class that judges it.
mpns = {"i5-1340P": CoreI51340P}
