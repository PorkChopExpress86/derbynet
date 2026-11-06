"""Where in a reply the parity gate (#37) lets Django differ from PHP.

`legacy_api/registry.py` lists these per endpoint in `PARITY_PATHS`, and
`tools/parity_gate.py` per step. Like `legacy_api.missing`, this module
imports nothing of Django, so the gate can build its own before Django loads.
"""

import re
from dataclasses import dataclass


class _Any:
    # A fixed repr, not object()'s address, so the test ids built from a
    # ParityPath are the same in every pytest-xdist worker.
    def __repr__(self):
        return "ANY"


ANY = _Any()  # A ParityPath value that matches any value.


@dataclass(frozen=True)
class ParityPath:
    """Where in an endpoint's reply the parity gate (#37) lets Django differ
    from PHP, under one of the endpoint's `divergences`.

    `endpoint` is `action <name>` or `query <name>`. `path` is a reply path as
    `tools/transcript_diff.py` prints it (`$.current-heat.classid`,
    `/action-response/heat-ready/@heat`), where `*` stands for any run of
    characters and `[*]` for any list index. `php` and `django` are the
    values the divergence gives each server there, after the differ's
    normalizing: a value, `MISSING` (`legacy_api.missing`) for a key one
    reply lacks, a type such as `int` for any value of it, or `ANY`.
    `divergence` is the id that starts one of the endpoint's `divergences`
    (`R3`); the guard in legacy_api/tests/test_registry.py checks it does.
    `roles`, when set, are the roles the divergence covers: the role the
    session was logged in as when it sent the request (`""` for none), as the
    transcript's last login shows it. A difference with no known role is
    not covered then."""

    endpoint: str
    path: str
    divergence: str
    php: object = ANY
    django: object = ANY
    # A sorted tuple, not a set, so the test ids built from a ParityPath are
    # the same in every pytest-xdist worker.
    roles: tuple[str, ...] | None = None

    def covers(
        self, endpoint: str, path: str, php=ANY, django=ANY, role: str | None = None
    ) -> bool:
        return (
            endpoint == self.endpoint
            and (self.roles is None or role in self.roles)
            and _path_pattern(self.path).fullmatch(path) is not None
            and _matches(self.php, php)
            and _matches(self.django, django)
        )


def _matches(expected, value) -> bool:
    if expected is ANY or value is ANY:
        return True
    # 0 == False and 1 == True in Python; JSON tells them apart.
    if isinstance(expected, type):
        return type(value) is expected
    return type(expected) is type(value) and expected == value


def _path_pattern(pattern: str) -> re.Pattern:
    parts = re.split(r"(\[\*\]|\*)", pattern)
    wild = {"[*]": r"\[\d+\]", "*": ".*"}
    return re.compile("".join(wild.get(part, re.escape(part)) for part in parts))
