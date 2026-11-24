"""nginx's sizes, shared by the settings and the upload health check (#48)."""

import re

_SIZE = re.compile(r"([0-9]+)([kmg]?)", re.IGNORECASE)
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def nginx_bytes(value: str) -> int | None:
    """An nginx size (`16M`, `512k`, `1g`, or bytes) as bytes, as nginx reads
    `client_max_body_size`; None for anything else."""
    found = _SIZE.fullmatch(value)
    return int(found[1]) * _UNITS[found[2].lower()] if found else None
