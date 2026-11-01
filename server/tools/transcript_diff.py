"""Diff two runs of the `testing/` shell suite for the parity gate (#37).

    uv run python -m tools.transcript_diff php/output.curl django/output.curl
    uv run python -m tools.transcript_diff --labels php,django A B

`testing/common.sh` appends each exchange to `output.curl` as a blank line,
the request line, a blank line and the response body. This tool splits both
files into exchanges, pairs them by position, normalizes what changes from
run to run, and prints each remaining difference with its endpoint, its
request and its path. It exits 1 on a difference and 0 when the runs match.

A request line is one of `post <path> <data>`, `login <name>`,
`photo <tail> ...`, `photo-upload <file> with <field>`, `snapshot.put <file>`,
or a GET path such as `action.php?query=poll`. A run whose request differs
from the other's at the same position (another endpoint or other arguments,
after normalizing) diverged: the tool reports where and stops comparing,
because every later pair would differ too.

Bodies of `action.php` exchanges are compared as JSON or XML (`timer-message`,
`login`, `roles` and `snapshot.*` are XML, `registry.format_of`). A JSON path
is `$.racers[3].firstname`; replies that `import-csv-roster.sh` appends with
no request line of their own are numbered `[2]$.outcome`. An XML path is
`/action-response/heat-ready/@heat`. Object keys are compared as a set, since
JSON objects are unordered; types are strict, so `1` and `"1"` differ. A
`photo.php` body (a digest or shape the script printed) is compared as text.
Other pages are not compared, because each server renders HTML its own way
(`curl_page` in `common.sh`); they count as skipped.

What is normalized, on both runs alike:

- Database ids, by first appearance per kind: the first racer id a run shows
  becomes racer 1, the next new one racer 2, and so on, in the requests and
  the responses. The keys that hold ids, and the standings, award and chart
  ids inside strings, are those of `legacy_api/tests/php_http.py`, which the
  golden replays use; `FirstAppearance` reuses its `Ids` walk. On top: the
  `who` of `racer.bulk` (`c<classid>`, `r<rankid>`, `d<partitionid>`), the
  `PWDid<racerid>` barcode, a racer id in a `photo.php/<repo>/racer/<id>` URL,
  and `roundid N` in XML failure text.
- `timecheck`, a non-zero `last_contact` or `last-contact`, a kiosk `age`,
  `CONNECTED (N sec.)`, a non-zero `cache-breaker`, the `seq` of
  `racer.list` (check-in time), the `duration` of `award.current`, and the
  `timing` of `schedule.reschedule` (`capture/normalize.mjs` masks the same).
- Timestamps in any string: `YYYY-MM-DD HH:MM:SS`, also with a `T`,
  fraction and zone.
- Cache breakers: the last part of `photo.php/<repo>/file/<render>/<name>/N`
  and of a racer photo URL when non-zero (0 means the file is missing), and
  `unrec-<time>`.
- Session ids: the value of `PHPSESSID=`, `sessionid=` and `csrftoken=`.
- The port of a kiosk address `<ip>+<port>` (PHP's REMOTE_PORT).
- File arguments of `snapshot.put` and `photo-upload`, by base name, since
  each run's copy of `testing/` sits in its own directory.

First-appearance numbering works only if both servers create rows in the
same order. Where they legitimately differ, record a divergence (#37) rather
than loosening this.

Allowed differences: `diff(..., allow=f)` calls `f(difference)` for each one,
and a non-empty string it returns is the reason the difference is allowed. An
allowed difference is listed but does not fail the run. Mapping the
registry's `divergences` (#17) to paths is the next box of #37.
"""

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import parse_qsl

from legacy_api.tests.php_http import (
    FIELD_KINDS,
    ID_KEYS,
    Ids,
    mask_reschedule_timing,
    translate,
)


class _Missing:
    def __repr__(self):
        return "(missing)"


MISSING = _Missing()

_REQUEST = re.compile(
    r"(?:post|login|photo|photo-upload|snapshot\.put) \S.*"
    r"|[\w.~-]+(?:/[\w.~-]+)*\.php(?:[?/]\S*)?"
)
_TIMESTAMP = re.compile(
    r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?"
)
_SECONDS = re.compile(r"\(\d+ sec\.\)")
_PHOTO_RACER = re.compile(r'(photo\.php/[^/\s"]+/racer/)(\d+)')
_PHOTO_BREAKER = re.compile(
    r'(photo\.php/[^/\s"]+/(?:file/[^/\s"]+/[^/\s"]+|racer/\d+(?:/[a-z-]+)?)/)'
    r'[1-9]\d*(?=$|[\s"?])'
)
_UNREC = re.compile(r"unrec-\d+")
_SESSION = re.compile(r"\b(PHPSESSID|sessionid|csrftoken)=[^;&\s\"]+")
_ADDRESS = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\+\d+\b")
_ROUND_IN_TEXT = re.compile(r"(roundid )(\d+)")
_BARCODE = re.compile(r"(PWDid)(\d+)")
_WHO = re.compile(r"([cdr])(\d+)")
_WHO_KINDS = {"c": "class", "r": "rank", "d": "partition"}
_PLAIN_KEY = re.compile(r"[A-Za-z_][\w-]*")

# Keys whose value is volatile in every reply: the mask for any value, or for
# a non-zero number only.
_ALWAYS = {"timecheck": "<timecheck>", "age": "<seconds>"}
_NON_ZERO = {
    "last_contact": "<epoch>",
    "last-contact": "<epoch>",
    "cache-breaker": "<breaker>",
}
# Keys volatile in one endpoint's reply only.
_PER_ENDPOINT = {
    ("query racer.list", "seq"): "<epoch-ms>",
    ("query award.current", "duration"): "<seconds>",
}


@dataclass(frozen=True)
class Exchange:
    """One request and its response, numbered from 1 in file order."""

    index: int
    request: str
    body: str
    line: int


def parse(text: str) -> list[Exchange]:
    """Split an `output.curl` into its exchanges."""
    lines = text.split("\n")
    heads = [
        i
        for i, line in enumerate(lines)
        if (i == 0 or lines[i - 1] == "")
        and (i + 1 == len(lines) or lines[i + 1] == "")
        and _REQUEST.fullmatch(line)
    ]
    exchanges = []
    for n, head in enumerate(heads):
        end = heads[n + 1] - 1 if n + 1 < len(heads) else len(lines)
        body = "\n".join(lines[head + 2 : end])
        exchanges.append(Exchange(n + 1, lines[head], body, head + 1))
    return exchanges


class FirstAppearance(Ids):
    """`Ids` that numbers each kind's ids by first appearance in one run,
    instead of pairing them with another server's. An int stays an int and a
    digit string a string, so a change of type still shows."""

    def __init__(self):
        super().__init__()
        self.order: dict[str, dict[int, int]] = defaultdict(dict)

    def number(self, kind: str, raw: int) -> int:
        seen = self.order[kind]
        return seen.setdefault(raw, len(seen) + 1)

    def php(self, kind, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return self.number(kind, value) if value > 0 else value
        if isinstance(value, str) and value.isdigit() and value == str(int(value)):
            return str(self.number(kind, int(value))) if int(value) > 0 else value
        return value

    def django(self, kind, php_id):
        return self.php(kind, php_id)


def mask_text(text: str, ids: FirstAppearance) -> str:
    text = _TIMESTAMP.sub("<timestamp>", text)
    text = _SECONDS.sub("(<n> sec.)", text)
    text = _PHOTO_RACER.sub(lambda m: f"{m[1]}{ids.number('racer', int(m[2]))}", text)
    text = _PHOTO_BREAKER.sub(r"\1<breaker>", text)
    text = _UNREC.sub("unrec-<time>", text)
    text = _SESSION.sub(r"\1=<value>", text)
    return _ADDRESS.sub(r"\1+<port>", text)


def _who(value, ids: FirstAppearance):
    found = _WHO.fullmatch(value) if isinstance(value, str) else None
    if found is None:
        return value
    return f"{found[1]}{ids.number(_WHO_KINDS[found[1]], int(found[2]))}"


# Requests


@dataclass(frozen=True)
class Request:
    endpoint: str
    # What must be equal in both runs: the path and the normalized arguments.
    key: tuple

    @property
    def compared(self) -> str | None:
        """How the response is compared: structured, as text, or not at all."""
        if self.endpoint.startswith(("action ", "query ")):
            return "structured"
        return "text" if self.endpoint == "page photo.php" else None


def _fields(data: str, ids: FirstAppearance) -> list[tuple[str, str]]:
    out = []
    for key, value in translate(parse_qsl(data, keep_blank_values=True), ids):
        if key == "who":
            value = _who(value, ids)
        elif key == "barcode":
            value = _BARCODE.sub(
                lambda m: f"{m[1]}{ids.number('racer', int(m[2]))}", value
            )
        out.append((key, mask_text(value, ids)))
    return out


def _named(path: str, fields: list[tuple[str, str]]) -> str:
    names = dict(fields)
    if path.rsplit("/", 1)[-1] != "action.php":
        return f"page {path}"
    if "query" in names:
        return f"query {names['query']}"
    if "action" in names:
        return f"action {names['action']}"
    return f"page {path}"


def parse_request(line: str, ids: FirstAppearance) -> Request:
    verb, _, rest = line.partition(" ")
    if verb == "post":
        path, _, data = rest.partition(" ")
        fields = _fields(data, ids)
        return Request(_named(path, fields), ("POST", path, tuple(fields)))
    if verb == "login":
        return Request("action role.login", ("login", rest))
    if verb == "snapshot.put":
        return Request("action snapshot.put", ("upload", PurePosixPath(rest).name))
    if verb == "photo-upload":
        upload, _, field_text = rest.partition(" with ")
        fields = _fields(field_text, ids)
        return Request(
            "action photo.upload",
            ("upload", PurePosixPath(upload).name, tuple(fields)),
        )
    if verb == "photo":
        tail, _, expected = rest.partition(" ")
        tail = mask_text("photo.php/" + tail, ids).removeprefix("photo.php/")
        return Request("page photo.php", ("photo", tail, expected))
    path, _, query = line.partition("?")
    fields = _fields(query, ids)
    return Request(_named(path, fields), ("GET", path, tuple(fields)))


# Responses


@dataclass
class Body:
    kind: str  # "empty", "json", "xml" or "text"
    values: list = field(default_factory=list)
    trailing: str = ""


def _json_values(text: str) -> tuple[list, str]:
    decoder = json.JSONDecoder()
    values, at = [], 0
    while True:
        while at < len(text) and text[at].isspace():
            at += 1
        if at == len(text) or text[at] not in "{[":
            return values, text[at:]
        try:
            value, at = decoder.raw_decode(text, at)
        except json.JSONDecodeError:
            return values, text[at:]
        values.append(value)


def read_body(text: str, request: Request, ids: FirstAppearance) -> Body:
    stripped = text.strip()
    if not stripped:
        return Body("empty")
    if request.compared == "structured" and stripped.startswith("<"):
        try:
            root = ElementTree.fromstring(stripped.encode())
        except ElementTree.ParseError:
            return Body("text", trailing=mask_text(stripped, ids))
        return Body("xml", [_xml(root, ids)])
    if request.compared == "structured" and stripped[0] in "{[":
        values, trailing = _json_values(stripped)
        return Body(
            "json",
            [_json(v, request.endpoint, ids) for v in values],
            mask_text(trailing.strip(), ids),
        )
    return Body("text", trailing=mask_text(stripped, ids))


def _echoed(key: str, value, ids: FirstAppearance):
    """A field of the request's echo, which names rows by request field names."""
    if key == "who":
        return _who(value, ids)
    if key in FIELD_KINDS:
        return ids.php(FIELD_KINDS[key], value)
    return value


def _json(value, endpoint: str, ids: FirstAppearance):
    echo = None
    if isinstance(value, dict) and isinstance(value.get("action"), dict):
        echo = {k: _echoed(k, v, ids) for k, v in value["action"].items()}
        value = {k: v for k, v in value.items() if k != "action"}
    value = ids.as_php(value)
    if echo is not None:
        value["action"] = echo
    if endpoint == "action schedule.reschedule" and isinstance(value, dict):
        value = mask_reschedule_timing(value)
    return _mask(value, endpoint, ids)


def _mask(value, endpoint: str, ids: FirstAppearance):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            number = isinstance(v, int | float) and not isinstance(v, bool)
            if k in _ALWAYS:
                out[k] = _ALWAYS[k]
            elif k in _NON_ZERO and number and v != 0:
                out[k] = _NON_ZERO[k]
            elif (endpoint, k) in _PER_ENDPOINT and number and v != 0:
                out[k] = _PER_ENDPOINT[(endpoint, k)]
            else:
                out[k] = _mask(v, endpoint, ids)
        return out
    if isinstance(value, list):
        return [_mask(v, endpoint, ids) for v in value]
    if isinstance(value, str):
        return mask_text(value, ids)
    return value


@dataclass
class Element:
    tag: str
    attributes: dict[str, str]
    text: str
    children: list[Element]


def _xml(element, ids: FirstAppearance) -> Element:
    attributes = {}
    for name, value in element.attrib.items():
        if name in ID_KEYS:
            value = ids.php(ID_KEYS[name], value)
        attributes[name] = mask_text(value, ids)
    text = _ROUND_IN_TEXT.sub(
        lambda m: f"{m[1]}{ids.number('round', int(m[2]))}",
        (element.text or "").strip(),
    )
    return Element(
        element.tag,
        attributes,
        mask_text(text, ids),
        [_xml(child, ids) for child in element],
    )


# Comparing


@dataclass
class Difference:
    index: int
    endpoint: str
    request: str
    path: str
    a: object
    b: object
    allowed_by: str | None = None


@dataclass(frozen=True)
class Divergence:
    """The first position where the runs sent different requests, or where
    one run ended. `a` or `b` is None for a run with no exchange there."""

    index: int
    a: str | None
    b: str | None


Allow = Callable[[Difference], str | None]


@dataclass
class Report:
    differences: list[Difference] = field(default_factory=list)
    allowed: list[Difference] = field(default_factory=list)
    diverged: Divergence | None = None
    compared: int = 0
    skipped: int = 0

    @property
    def ok(self) -> bool:
        return not self.differences and self.diverged is None

    def render(self, labels: tuple[str, str] = ("a", "b")) -> str:
        a, b = labels
        out = []
        for found in self.differences + self.allowed:
            head = f"#{found.index} {found.endpoint}  {found.request}"
            note = f"  allowed: {found.allowed_by}" if found.allowed_by else ""
            out.append(head)
            out.append(
                f"    {found.path}: {a}={_show(found.a)} {b}={_show(found.b)}{note}"
            )
        if self.diverged is not None:
            d = self.diverged
            out.append(f"#{d.index} the runs diverge:")
            out.append(f"    {a}: {d.a if d.a is not None else '(no more exchanges)'}")
            out.append(f"    {b}: {d.b if d.b is not None else '(no more exchanges)'}")
        out.append(
            f"{self.compared} exchanges compared, {self.skipped} pages not compared,"
            f" {len(self.differences)} differences, {len(self.allowed)} allowed"
            + (", runs diverged" if self.diverged else "")
            + " (ids are numbered by first appearance)"
        )
        return "\n".join(out)


def _show(value) -> str:
    if value is MISSING:
        return "(missing)"
    return json.dumps(value, ensure_ascii=False)


def _key_path(key) -> str:
    if isinstance(key, str) and _PLAIN_KEY.fullmatch(key):
        return f".{key}"
    return f"[{json.dumps(key, ensure_ascii=False)}]"


def _type(value) -> str:
    return type(value).__name__


def diff_json(a, b, path: str, out: list) -> None:
    if _type(a) != _type(b):
        out.append((path, a, b))
    elif isinstance(a, dict):
        for key in list(a) + [k for k in b if k not in a]:
            if key not in b or key not in a:
                out.append(
                    (path + _key_path(key), a.get(key, MISSING), b.get(key, MISSING))
                )
            else:
                diff_json(a[key], b[key], path + _key_path(key), out)
    elif isinstance(a, list):
        for i in range(max(len(a), len(b))):
            if i >= len(a) or i >= len(b):
                out.append(
                    (
                        f"{path}[{i}]",
                        a[i] if i < len(a) else MISSING,
                        b[i] if i < len(b) else MISSING,
                    )
                )
            else:
                diff_json(a[i], b[i], f"{path}[{i}]", out)
    elif a != b:
        out.append((path, a, b))


def _summary(element: Element) -> dict:
    return {"tag": element.tag, **element.attributes}


def diff_xml(a: Element, b: Element, path: str, out: list) -> None:
    if a.tag != b.tag:
        out.append((path, _summary(a), _summary(b)))
        return
    for name in list(a.attributes) + [n for n in b.attributes if n not in a.attributes]:
        left = a.attributes.get(name, MISSING)
        right = b.attributes.get(name, MISSING)
        if left != right:
            out.append((f"{path}/@{name}", left, right))
    if a.text != b.text:
        out.append((f"{path}/text()", a.text, b.text))
    tags = [c.tag for c in a.children] + [c.tag for c in b.children]
    seen: dict[str, int] = defaultdict(int)
    for i in range(max(len(a.children), len(b.children))):
        left = a.children[i] if i < len(a.children) else None
        right = b.children[i] if i < len(b.children) else None
        tag = (left or right).tag
        seen[tag] += 1
        step = f"{path}/{tag}"
        if tags.count(tag) > 2 or (left is None) != (right is None):
            step += f"[{seen[tag]}]"
        if left is None or right is None:
            out.append(
                (
                    step,
                    _summary(left) if left else MISSING,
                    _summary(right) if right else MISSING,
                )
            )
        else:
            diff_xml(left, right, step, out)


def diff_bodies(a: Body, b: Body) -> list[tuple[str, object, object]]:
    out: list = []
    if a.kind != b.kind:
        return [("$", f"<{a.kind}>", f"<{b.kind}>")]
    if a.kind == "xml":
        root_a, root_b = a.values[0], b.values[0]
        diff_xml(root_a, root_b, f"/{root_a.tag}", out)
        return out
    if a.kind == "json":
        many = len(a.values) > 1 or len(b.values) > 1
        for i in range(max(len(a.values), len(b.values))):
            prefix = f"[{i}]$" if many else "$"
            if i >= len(a.values) or i >= len(b.values):
                out.append(
                    (
                        prefix,
                        MISSING if i >= len(a.values) else a.values[i],
                        MISSING if i >= len(b.values) else b.values[i],
                    )
                )
            else:
                diff_json(a.values[i], b.values[i], prefix, out)
    if a.trailing != b.trailing:
        out.append(("text", a.trailing, b.trailing))
    return out


def diff(a_text: str, b_text: str, allow: Allow | None = None) -> Report:
    """Compare two `output.curl` runs exchange by exchange."""
    report = Report()
    ids_a, ids_b = FirstAppearance(), FirstAppearance()
    runs = parse(a_text), parse(b_text)
    for i in range(max(len(runs[0]), len(runs[1]))):
        if i >= len(runs[0]) or i >= len(runs[1]):
            report.diverged = Divergence(
                i + 1,
                runs[0][i].request if i < len(runs[0]) else None,
                runs[1][i].request if i < len(runs[1]) else None,
            )
            break
        ex_a, ex_b = runs[0][i], runs[1][i]
        req_a = parse_request(ex_a.request, ids_a)
        req_b = parse_request(ex_b.request, ids_b)
        if req_a != req_b:
            report.diverged = Divergence(ex_a.index, ex_a.request, ex_b.request)
            break
        if req_a.compared is None:
            report.skipped += 1
            continue
        report.compared += 1
        found = diff_bodies(
            read_body(ex_a.body, req_a, ids_a), read_body(ex_b.body, req_b, ids_b)
        )
        for path, left, right in found:
            difference = Difference(
                ex_a.index, req_a.endpoint, ex_a.request, path, left, right
            )
            difference.allowed_by = allow(difference) if allow else None
            if difference.allowed_by:
                report.allowed.append(difference)
            else:
                report.differences.append(difference)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a", help="the first run's output.curl, e.g. PHP's")
    parser.add_argument("b", help="the second run's output.curl, e.g. Django's")
    parser.add_argument(
        "--labels", default="a,b", help="names for the two runs, e.g. php,django"
    )
    args = parser.parse_args(argv)
    labels = tuple(args.labels.split(",", 1))
    if len(labels) != 2:
        parser.error("--labels takes two names separated by a comma")
    with open(args.a, encoding="utf-8", errors="replace") as a_file:
        a_text = a_file.read()
    with open(args.b, encoding="utf-8", errors="replace") as b_file:
        b_text = b_file.read()
    report = diff(a_text, b_text)
    print(f"{labels[0]}: {args.a}\n{labels[1]}: {args.b}")
    print(report.render(labels))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
