"""Read a PHP `config-roles.inc` into roles (#15).

The file assigns `$roles`, an array literal of role name to `password`,
`permissions` and `interactive`, and `$post_setup_role`. `parse()` reads the
literal subset these files use: strings with simple escapes, decimal
integers, `true` and `false`, the `*_PERMISSION` constants, `|`, and keyed
`array(...)` or `[...]`. Anything else, an octal mask included, raises
`ConfigRolesError`, and `read()` then asks `php` when it is installed.
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from accounts.permissions import Perm


class ConfigRolesError(ValueError):
    pass


@dataclass(frozen=True)
class RoleConfig:
    name: str
    password: str
    permissions: int
    interactive: bool


CONSTANTS = {f"{p.name}_PERMISSION": p.value for p in Perm}
BOOLEANS = {"true": True, "false": False}

_TOKEN = re.compile(
    r"""
    (?P<skip>\s+|<\?php|\?>|//[^\n]*|\#[^\n]*|/\*.*?\*/)
    | (?P<single>'(?:[^'\\]|\\.)*')
    | (?P<double>"(?:[^"\\$]|\\[ntr\\"$])*")
    | (?P<int>-?(?:0|[1-9]\d*)(?![\w.]))
    | (?P<var>\$[A-Za-z_]\w*)
    | (?P<name>[A-Za-z_]\w*)
    | (?P<punct>=>|[=()\[\],;|])
    """,
    re.VERBOSE | re.DOTALL,
)
_DOUBLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "$": "$"}


def _tokens(text: str):
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise ConfigRolesError(f"cannot read {text[pos : pos + 20]!r}")
        pos = match.end()
        kind = match.lastgroup
        value = match.group()
        if kind == "skip":
            continue
        if kind == "single":
            value = re.sub(r"\\([\\'])", r"\1", value[1:-1])
            kind = "str"
        elif kind == "double":
            value = re.sub(r"\\(.)", lambda m: _DOUBLE_ESCAPES[m[1]], value[1:-1])
            kind = "str"
        elif kind == "int":
            value = int(value)
        yield kind, value
    yield "end", None


class _Parser:
    def __init__(self, text: str):
        self._tokens = _tokens(text)
        self._next()

    def _next(self):
        self.kind, self.value = next(self._tokens)

    def _expect(self, value):
        if self.value != value or self.kind not in ("punct", "name"):
            raise ConfigRolesError(f"expected {value!r}, found {self.value!r}")
        self._next()

    def statements(self) -> dict:
        found = {}
        while self.kind != "end":
            if self.kind != "var":
                raise ConfigRolesError(f"expected an assignment, found {self.value!r}")
            name = self.value
            self._next()
            self._expect("=")
            found[name] = self._expression()
            self._expect(";")
        return found

    def _expression(self):
        value = self._term()
        while self.value == "|" and self.kind == "punct":
            self._next()
            right = self._term()
            if not (isinstance(value, int) and isinstance(right, int)):
                raise ConfigRolesError("| joins integers only")
            value |= right
        return value

    def _term(self):
        kind, value = self.kind, self.value
        if kind in ("str", "int"):
            self._next()
            return value
        if kind == "name" and value.lower() == "array":
            self._next()
            return self._items(open_="(", close=")")
        if kind == "punct" and value == "[":
            return self._items(open_="[", close="]")
        if kind == "name" and value in CONSTANTS:
            self._next()
            return CONSTANTS[value]
        if kind == "name" and value.lower() in BOOLEANS:
            self._next()
            return BOOLEANS[value.lower()]
        raise ConfigRolesError(f"cannot read the value {value!r}")

    def _items(self, open_: str, close: str) -> dict:
        self._expect(open_)
        items = {}
        while not (self.kind == "punct" and self.value == close):
            key = self._expression()
            if self.value == "=>" and self.kind == "punct":
                self._next()
                items[key] = self._expression()
            else:
                raise ConfigRolesError("an array item has no key")
            if self.value != ",":
                break
            self._next()
        self._expect(close)
        return items


def parse(text: str) -> tuple[dict, str | None]:
    """`$roles` and `$post_setup_role` as PHP's `json_encode()` gives them."""
    found = _Parser(text).statements()
    if not isinstance(found.get("$roles"), dict):
        raise ConfigRolesError("the file sets no $roles array")
    return found["$roles"], found.get("$post_setup_role")


def _php(path: Path) -> tuple[dict, str | None]:
    defines = "".join(
        f"define('{name}', {value});" for name, value in CONSTANTS.items()
    )
    script = (
        defines + "include $argv[1];"
        'echo json_encode(["roles" => $roles,'
        ' "post_setup_role" => $post_setup_role ?? null]);'
    )
    result = subprocess.run(
        ["php", "-r", script, str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    found = json.loads(result.stdout)
    return found["roles"], found["post_setup_role"]


def read(path: Path) -> tuple[list[RoleConfig], str | None]:
    """The roles in file order, and the post-setup role's name."""
    try:
        roles, post_setup_role = parse(path.read_text())
    except ConfigRolesError:
        if shutil.which("php") is None:
            raise
        roles, post_setup_role = _php(path)
    return [
        RoleConfig(
            name=str(name),
            password=str(details.get("password", "")),
            # PHP reads a missing mask as 0, and anything not false as interactive.
            permissions=int(details.get("permissions", 0)),
            interactive=bool(details.get("interactive", True)),
        )
        for name, details in roles.items()
    ], post_setup_role
