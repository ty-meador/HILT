"""The system firmware, and every firmware release the suite can judge."""

from __future__ import annotations

from nuc13.firmwares.Anrpl357Release0036 import Anrpl357Release0036

# Every firmware release part number the suite can judge, each with the class that judges it.
mpns = {"ANRPL357.0036.2024.1225.1724": Anrpl357Release0036}
