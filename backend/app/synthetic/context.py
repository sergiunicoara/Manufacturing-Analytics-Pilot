"""Shared generation context: deterministic RNG, ID sequences, and the
in-memory table registry that every generator module writes into.

Design: all tables use explicit, Python-assigned surrogate integer IDs
(1..N) so referential integrity can be wired up purely in-memory across
generator modules with no DB round-trips. The loader inserts these explicit
IDs using `SET IDENTITY_INSERT ... ON`, so the DB's IDENTITY columns end up
holding exactly the values generated here. This keeps the whole generation
pipeline pure-Python/pandas and reproducible from the seed alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from faker import Faker


class IdSequence:
    def __init__(self, start: int = 1) -> None:
        self._next = start

    def next(self) -> int:
        v = self._next
        self._next += 1
        return v

    def take(self, n: int) -> list[int]:
        ids = list(range(self._next, self._next + n))
        self._next += n
        return ids


@dataclass
class GenContext:
    seed: int
    rng: np.random.Generator = field(init=False)
    faker: Faker = field(init=False)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    ids: dict[str, IdSequence] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)
        self.faker = Faker()
        Faker.seed(self.seed)

    def id_seq(self, table: str) -> IdSequence:
        if table not in self.ids:
            self.ids[table] = IdSequence()
        return self.ids[table]

    def add_table(self, name: str, df: pd.DataFrame) -> None:
        self.tables[name] = df


# --- Shared reference vocabularies, used across generator modules ---

EUROPEAN_COUNTRIES = [
    "DE", "FR", "IT", "PL", "NL", "ES", "BE", "AT", "CZ", "SE",
    "DK", "PT", "RO", "HU", "CH",
]

RAL_POWDER_COAT_COLORS = ["RAL7035", "RAL7016", "RAL9005", "RAL9010", "RAL5010"]

INVALID_UOM_VARIANTS = ["PCE", "pc ", "Pcs.", "EA "]
VALID_UOM = ["PC", "KG", "M", "L", "SET"]
