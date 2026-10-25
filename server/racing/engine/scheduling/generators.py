"""Lookup into the generated PHP schedule generator tables."""

from typing import Any


def scheduler_data(nlanes: int, ncars: int) -> dict[str, Any] | None:
    """Return {"generators": [...], "permutation_sets": [...]} or None.

    None means no table covers this lane and car count, which the caller
    reports as cant-fulfill. The tables load on the first call, so importing
    the engine does not read them. The result is shared; do not mutate it.
    """
    from . import generator_tables

    return generator_tables.GENERATOR_TABLES.get(nlanes, {}).get(ncars)
