"""nginx's sizes, shared by the settings and the upload health check (#48)."""


def nginx_bytes(value: str) -> int | None:
    """An nginx size (`16M`, `512k`, `1g`, or bytes) as bytes, as nginx reads
    `client_max_body_size`; None for anything else."""
    units = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}
    number, unit = value[:-1], value[-1:].lower()
    if unit.isdigit():
        number, unit = value, ""
    if not number.isdigit() or unit not in units:
        return None
    return int(number) * units[unit]
