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
or a GET path such as `action.php?query=poll`, and is followed by a blank
line. The blank line before it is missing when the body before ended without
a newline, and that is fine. `expect_photo_shape`
(`testing/test-photo-manipulations.sh`) writes `photo <tail> <shape>` with no
blank lines at all, sometimes right after the previous body on the same line;
such a line is an exchange of its own whose body is the shape. A line inside
an HTML page that looks like a request line and is followed by a blank line
would be misread as a new exchange; pages are not compared, so it is unlikely
to matter.

A run whose request differs from the other's at the same position (another
endpoint or other arguments, after normalizing) diverged: the tool reports
where and stops comparing, because every later pair would differ too.

How a body is compared:

- An `action.php` reply is read in the endpoint's format, from
  `legacy_api.formats.format_of`, which the dispatcher uses too: XML for
  `timer-message`, `login`, `roles` and `snapshot.*`, JSON otherwise. A body
  that does not parse in that format is compared as text. A JSON path is
  `$.racers[3].firstname`; replies that `import-csv-roster.sh` appends with
  no request line of their own are numbered `[2]$.outcome`. An XML path is
  `/action-response/heat-ready/@heat`. JSON object keys are compared as a set,
  since JSON objects are unordered. Types are strict: `1`, `"1"`, `1.0` and
  `true` all differ.
- A `photo.php` body (the digest or shape the script printed) is text.
- Other pages are not compared, because each server renders HTML its own way
  (`curl_page` in `common.sh`); they count as skipped.

What is normalized, on both runs alike:

- Database ids, by first appearance per kind: the first racer id a run shows
  becomes racer 1, the next new one racer 2, and so on, in the requests and
  the responses. The keys that hold ids, and the standings, award and chart
  ids inside strings, are those of `legacy_api/tests/php_http.py`
  (`rewrite_ids`, `translate`), which the golden replays use. On top: the
  `who` of `racer.bulk` (`c<classid>`, `r<rankid>`, `d<partitionid>`), the
  `PWDid<racerid>` barcode, the `classid_<position>=<classid>` and
  `rankid_<position>=<rankid>` fields of `class.order` and `rank.order`
  (`class.add`'s `rankid_<rankid>=1` names the id in the key, as `translate`
  reads it), a racer id in a `photo.php/<repo>/racer/<id>` URL, and
  `roundid N` in XML failure text.
- Volatile keys (`VOLATILE`): `timecheck`; a kiosk's `age` in a `kiosks`
  list; `last_contact` and `last-contact`; `cache-breaker`; the `seq` of
  `racer.list`; the `duration` of `award.current`. Both runs' values must
  have the same JSON type, and for the keys where 0 means "never" or "no
  file", be zero in both or in neither. The `timing` of `schedule.reschedule`
  is masked as the golden replays mask it.
- Timestamps in a string (`YYYY-MM-DD HH:MM:SS`, also with a `T`, fraction
  and zone) within `TIMESTAMP_WINDOW` of now, which is when the run was
  made. A fixed date, such as an award's or the roster's, is compared.
- `CONNECTED (N sec.)`.
- Cache breakers. In a request, the last part of a photo URL is the script's
  `$RANDOM`, masked whatever its value. In a reply, the last part of
  `photo.php/<repo>/file/<render>/<name>/N` or of a racer photo URL is the
  file's modification time, masked unless 0, which means the file is
  missing. Also `unrec-<time>`.
- Session ids: the value of `PHPSESSID=`, `sessionid=` and `csrftoken=`.
- The port of a kiosk address `<ip>+<port>` (PHP's REMOTE_PORT).
- A kiosk address the script made with `mktemp kiosk1-XXXXXX`
  (`testing/test-scenes.sh`): the six random characters.
- An event's folder inside the data folder, before `racers`, `cars`,
  `videos`, `logs`, `imagery` or `slides`: PHP's `<year>/<name>` and
  Django's `events/<slug>` (owner decision D57) are both
  `<event:<slug>>`, with PHP's name slugified as Django slugs a new event's
  name, so two events' folders still differ.
  The data folder in front is compared, so the gate runs both servers on
  `/var/lib/derbynet`. The reset scripts post these paths back, so without
  this every reset would part the runs.
- File arguments of `snapshot.put` and `photo-upload`, by base name, since
  each run's copy of `testing/` sits in its own directory.

`GOLDEN_RULES` says where each rule of the golden capture's
`capture/normalize.mjs` is handled here; a test keeps the two lists equal.

Ids are numbered afresh after each database reset (`database.execute`
with `script=schema`), since PHP then hands out ids from 1 again and an id
from before the reset names another row. First-appearance numbering works
only if both servers create rows in the same order. Where they
legitimately differ, record a divergence (#37) rather than loosening this.

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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import PurePosixPath
from urllib.parse import parse_qsl

from django.utils.text import slugify

from legacy_api.formats import Format, format_of
from legacy_api.missing import MISSING, _Missing
from legacy_api.tests.php_http import (
    FIELD_KINDS,
    ID_KEYS,
    mask_reschedule_timing,
    rewrite_ids,
    translate,
)

# Where each rule of server/tests/golden/capture/normalize.mjs is handled.
GOLDEN_RULES = {
    "timecheck": "VOLATILE, any endpoint",
    "last-contact": "VOLATILE, last_contact and last-contact",
    "timer-contact-age": "_SECONDS, and VOLATILE for a kiosk's age",
    "export-results-order": "not compared: export-json.php is a page",
    "completed-timestamp": "_TIMESTAMP, within TIMESTAMP_WINDOW of now",
    "checkin-time": "VOLATILE, racer.list seq",
    "award-duration": "VOLATILE, award.current duration",
    "set-cookie-value": "_SESSION; output.curl records no headers",
    "reschedule-timing": "mask_reschedule_timing from php_http",
}

TIMESTAMP_WINDOW = timedelta(days=2)

_REQUEST = re.compile(
    r"(?:post|login|photo|photo-upload|snapshot\.put) \S.*"
    r"|[\w.~-]+(?:/[\w.~-]+)*\.php(?:[?/]\S*)?"
)
# A line of expect_photo_shape: `photo <tail> <W>x<H> <tag> <quarters>`.
_SHAPE = re.compile(r"photo (\S+) (\d+x\d+ \S+(?: [RGBW]+)?)")
_GLUED_SHAPE = re.compile(r"(.*[}\]>])(photo \S+ \d+x\d+ .*)")
_TIMESTAMP = re.compile(
    r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?"
)
_SECONDS = re.compile(r"\(\d+ sec\.\)")
_PHOTO_RACER = re.compile(r'(photo\.php/[^/\s"]+/racer/)(\d+)')
_PHOTO_PREFIX = (
    r'(photo\.php/[^/\s"]+/(?:file/[^/\s"]+/[^/\s"]+|racer/\d+(?:/[a-z-]+)?)/)'
)
# A server's breaker, a modification time: 0 means the file is missing.
_REPLY_BREAKER = re.compile(_PHOTO_PREFIX + r'[1-9]\d*(?=$|[\s"?])')
# A script's breaker, `$RANDOM`: any value.
_REQUEST_BREAKER = re.compile(_PHOTO_PREFIX + r"\d+$")
_UNREC = re.compile(r"unrec-\d+")
_SESSION = re.compile(r"\b(PHPSESSID|sessionid|csrftoken)=[^;&\s\"]+")
_ADDRESS = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\+\d+\b")
_MKTEMP_KIOSK = re.compile(r"\b(kiosk\d+-)[A-Za-z0-9]{6}\b")
_EVENT_FOLDER = re.compile(
    r"/(?:\d{4}|events)/([^/\s\"<>]+)(?=/(?:racers|cars|videos|logs|imagery|slides)\b)"
)


def _event_folder(found: re.Match) -> str:
    return f"/<event:{slugify(found[1])}>"


_ROUND_IN_TEXT = re.compile(r"(roundid )(\d+)")
_BARCODE = re.compile(r"(PWDid)(\d+)")
_WHO = re.compile(r"([cdr])(\d+)")
_WHO_KINDS = {"c": "class", "r": "rank", "d": "partition"}
_PLAIN_KEY = re.compile(r"[A-Za-z_][\w-]*")
# class.order, rank.order and partition.order post
# `classid_<position>=<classid>`; class.add posts `rankid_<rankid>=1`, the
# form php_http's PREFIX_KINDS reads.
_ORDER_ACTIONS = frozenset({"class.order", "rank.order", "partition.order"})
_ORDER_FIELD = re.compile(r"(class|rank|partition)id_\d+")


@dataclass(frozen=True)
class Volatile:
    """A key whose value changes from run to run. Both runs' values must
    still have one JSON type, and with `zero_means` be 0 in both or neither."""

    key: str
    endpoints: frozenset[str] | None = None  # None: any endpoint
    within: str | None = None  # the key of the list or object holding it
    zero_means: str | None = None

    def applies(self, endpoint: str, key: str, within: str | None) -> bool:
        return (
            key == self.key
            and (self.endpoints is None or endpoint in self.endpoints)
            and (self.within is None or within == self.within)
        )

    def same(self, a, b) -> bool:
        if _type(a) != _type(b):
            return False
        return self.zero_means is None or (a == 0) == (b == 0)


VOLATILE = (
    Volatile("timecheck"),
    # `age` is the seconds since a kiosk was heard from, in the kiosk list of
    # poll.kiosk.all and the kiosk actions (website/inc/kiosks.inc); an `age`
    # anywhere else is data.
    Volatile("age", within="kiosks"),
    Volatile("last_contact", zero_means="never"),
    Volatile("last-contact", zero_means="never"),
    Volatile("cache-breaker", zero_means="no file"),
    Volatile("seq", frozenset({"query racer.list"}), zero_means="not checked in"),
    Volatile("duration", frozenset({"query award.current"})),
)


def _volatile(endpoint: str, key: str, within: str | None) -> Volatile | None:
    return next((v for v in VOLATILE if v.applies(endpoint, key, within)), None)


def _pairs(a: Sequence, b: Sequence) -> Iterator[tuple[int, object, object]]:
    """`(i, a[i], b[i])` over the longer of the two, with MISSING past the
    end of the shorter."""
    for i in range(max(len(a), len(b))):
        yield (
            i,
            a[i] if i < len(a) else MISSING,
            b[i] if i < len(b) else MISSING,
        )


@dataclass(frozen=True)
class Exchange:
    """One request and its response, numbered from 1 in file order."""

    index: int
    request: str
    body: str
    line: int


def _unglue(lines: list[str]) -> list[str]:
    """Put a photo shape written right after a body on a line of its own."""
    out = []
    for line in lines:
        glued = _GLUED_SHAPE.fullmatch(line)
        out.extend([glued[1], glued[2]] if glued else [line])
    return out


def parse(text: str) -> list[Exchange]:
    """Split an `output.curl` into its exchanges."""
    lines = _unglue(text.split("\n"))
    heads = [
        i
        for i, line in enumerate(lines)
        if _SHAPE.fullmatch(line)
        or (_REQUEST.fullmatch(line) and (i + 1 == len(lines) or lines[i + 1] == ""))
    ]
    exchanges = []
    for n, head in enumerate(heads):
        end = heads[n + 1] if n + 1 < len(heads) else len(lines)
        shape = _SHAPE.fullmatch(lines[head])
        if shape:
            request = f"photo {shape[1]}"
            body = "\n".join([shape[2], *lines[head + 1 : end]])
        else:
            request, body = lines[head], "\n".join(lines[head + 2 : end])
        exchanges.append(Exchange(n + 1, request, body, head + 1))
    return exchanges


class FirstAppearance:
    """Numbers each kind's ids by first appearance in one run. Called as an
    id map, `ids(kind, value)`, by `rewrite_ids` and `translate`. An int stays
    an int and a digit string a string, so a change of type still shows."""

    def __init__(self):
        self.order: dict[str, dict[int, int]] = defaultdict(dict)

    def number(self, kind: str, raw: int) -> int:
        seen = self.order[kind]
        return seen.setdefault(raw, len(seen) + 1)

    def __call__(self, kind: str, value):
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return self.number(kind, value) if value > 0 else value
        if isinstance(value, str) and value.isdigit() and value == str(int(value)):
            return str(self.number(kind, int(value))) if int(value) > 0 else value
        return value


@dataclass
class Run:
    """What normalizing one run needs: its id numbering and when it ran."""

    ids: FirstAppearance
    now: datetime

    def _renumber(self, pattern: re.Pattern, kind: str, text: str) -> str:
        """`pattern`'s second group, an id of `kind`, renumbered."""
        return pattern.sub(lambda m: f"{m[1]}{self.ids.number(kind, int(m[2]))}", text)

    def _recent(self, found: re.Match) -> str:
        try:
            stamp = datetime.fromisoformat(found[0]).replace(tzinfo=None)
        except ValueError:
            return found[0]
        return "<timestamp>" if abs(stamp - self.now) <= TIMESTAMP_WINDOW else found[0]

    def mask(self, text: str) -> str:
        text = _TIMESTAMP.sub(self._recent, text)
        text = _SECONDS.sub("(<n> sec.)", text)
        text = self._renumber(_PHOTO_RACER, "racer", text)
        text = _REPLY_BREAKER.sub(r"\1<breaker>", text)
        text = _UNREC.sub("unrec-<time>", text)
        text = _SESSION.sub(r"\1=<value>", text)
        text = _EVENT_FOLDER.sub(_event_folder, text)
        text = _MKTEMP_KIOSK.sub(r"\1<mktemp>", text)
        return _ADDRESS.sub(r"\1+<port>", text)

    def who(self, value):
        found = _WHO.fullmatch(value) if isinstance(value, str) else None
        if found is None:
            return value
        return f"{found[1]}{self.ids.number(_WHO_KINDS[found[1]], int(found[2]))}"

    def echoed(self, fields: dict) -> dict:
        """The request fields a reply echoes under `action`, with their ids
        renumbered as the request's are: a text field as `fields` reads it,
        so a `rankid_<id>` key and a `class_and_rank` value are renumbered
        too."""
        action = fields.get("action")
        out = {}
        for key, value in fields.items():
            # PHP echoes a number field as a number: `"classid_1": 2`.
            number = isinstance(value, int) and not isinstance(value, bool)
            if (isinstance(value, str) or number) and key not in FIELD_KINDS:
                [_, (key, text)] = self._ids_in([("action", action), (key, str(value))])
                value = int(text) if number and text.lstrip("-").isdigit() else text
                value = self.who(value) if key == "who" else value
            else:
                value = self.map_echoed_field(action, key, value)
            out[key] = value
        return out

    def map_echoed_field(self, action, key: str, value):
        """A field of the request as the reply echoes it under `action`."""
        if key == "who":
            return self.who(value)
        order = _ORDER_FIELD.fullmatch(key) if action in _ORDER_ACTIONS else None
        if order:
            return self.ids(order[1], value)
        if key in FIELD_KINDS:
            return self.ids(FIELD_KINDS[key], value)
        return value

    def _ids_in(self, fields: list[tuple[str, str]]) -> list[tuple[str, str]]:
        names = dict(fields)
        if names.get("action") not in _ORDER_ACTIONS:
            return translate(fields, self.ids)
        # `<kind>id_<position>=<id>`: the id is the value, not the key.
        out = []
        for key, value in fields:
            order = _ORDER_FIELD.fullmatch(key)
            if order:
                out.append((key, str(self.ids(order[1], value))))
            else:
                out.extend(translate([(key, value)], self.ids))
        return out

    def fields(self, data: str) -> tuple[tuple[str, str], ...]:
        out = []
        for key, value in self._ids_in(parse_qsl(data, keep_blank_values=True)):
            if key == "who":
                value = self.who(value)
            elif key == "barcode":
                value = self._renumber(_BARCODE, "racer", value)
            out.append((key, self.mask(value)))
        return tuple(out)

    def photo_tail(self, tail: str) -> str:
        # The breaker first, so a non-zero one isn't masked as a server's.
        url = _REQUEST_BREAKER.sub(r"\1<random>", "photo.php/" + tail)
        return self.mask(url).removeprefix("photo.php/")

    def round_in_text(self, text: str) -> str:
        return self._renumber(_ROUND_IN_TEXT, "round", text)


# Requests


class Comparison(StrEnum):
    STRUCTURED = "structured"
    TEXT = "text"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Request:
    endpoint: str
    # What must be equal in both runs: the path and the normalized arguments.
    key: tuple

    @property
    def comparison(self) -> Comparison:
        if self.endpoint.startswith(("action ", "query ")):
            return Comparison.STRUCTURED
        if self.endpoint == "page photo.php":
            return Comparison.TEXT
        return Comparison.SKIPPED

    @property
    def format(self) -> Format:
        return format_of(self.endpoint.split(" ", 1)[1])


def _endpoint(path: str, fields: tuple[tuple[str, str], ...]) -> str:
    names = dict(fields)
    if path.rsplit("/", 1)[-1] != "action.php":
        return f"page {path}"
    if "query" in names:
        return f"query {names['query']}"
    if "action" in names:
        return f"action {names['action']}"
    return f"page {path}"


def parse_request(line: str, run: Run) -> Request:
    verb, _, rest = line.partition(" ")
    if verb == "post":
        path, _, data = rest.partition(" ")
        fields = run.fields(data)
        return Request(_endpoint(path, fields), ("POST", path, fields))
    if verb == "login":
        return Request("action role.login", ("login", rest))
    if verb == "snapshot.put":
        return Request("action snapshot.put", ("upload", PurePosixPath(rest).name))
    if verb == "photo-upload":
        upload, _, field_text = rest.partition(" with ")
        return Request(
            "action photo.upload",
            ("upload", PurePosixPath(upload).name, run.fields(field_text)),
        )
    if verb == "photo":
        tail, _, expected = rest.partition(" ")
        return Request("page photo.php", ("photo", run.photo_tail(tail), expected))
    path, _, query = line.partition("?")
    fields = run.fields(query)
    return Request(_endpoint(path, fields), ("GET", path, fields))


# Responses


class BodyKind(StrEnum):
    EMPTY = "empty"
    JSON = "json"
    XML = "xml"
    TEXT = "text"


@dataclass
class Body:
    kind: BodyKind
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


def read_body(text: str, request: Request, run: Run) -> Body:
    stripped = text.strip()
    if not stripped:
        return Body(BodyKind.EMPTY)
    as_text = Body(BodyKind.TEXT, trailing=run.mask(stripped))
    if request.comparison is not Comparison.STRUCTURED:
        return as_text
    if request.format is Format.XML:
        try:
            root = ElementTree.fromstring(stripped.encode())
        except ElementTree.ParseError:
            return as_text
        return Body(BodyKind.XML, [_xml(root, run)])
    values, trailing = _json_values(stripped)
    if not values:
        return as_text
    return Body(
        BodyKind.JSON,
        [_json(v, request.endpoint, run) for v in values],
        run.mask(trailing.strip()),
    )


def _json(value, endpoint: str, run: Run):
    echo = None
    if isinstance(value, dict) and isinstance(value.get("action"), dict):
        echo = run.echoed(value["action"])
        value = {k: v for k, v in value.items() if k != "action"}
    value = rewrite_ids(value, run.ids)
    if echo is not None:
        value["action"] = echo
    if endpoint == "action schedule.reschedule" and isinstance(value, dict):
        value = mask_reschedule_timing(value)
    return _mask_strings(value, run)


def _mask_strings(value, run: Run):
    if isinstance(value, dict):
        return {k: _mask_strings(v, run) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_strings(v, run) for v in value]
    if isinstance(value, str):
        return run.mask(value)
    return value


@dataclass
class Element:
    tag: str
    attributes: dict[str, str]
    text: str
    children: list[Element]


def _xml(element, run: Run) -> Element:
    attributes = {}
    for name, value in element.attrib.items():
        if name in ID_KEYS:
            value = run.ids(ID_KEYS[name], value)
        attributes[name] = run.mask(value)
    text = run.round_in_text((element.text or "").strip())
    return Element(
        element.tag,
        attributes,
        run.mask(text),
        [_xml(child, run) for child in element],
    )


# Comparing


@dataclass(frozen=True)
class Found:
    """A difference inside one body: where, and each run's value."""

    path: str
    a: object
    b: object


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
    one run ended. `a` or `b` is MISSING for a run with no exchange there."""

    index: int
    a: str | _Missing
    b: str | _Missing


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

    def render(self, labels: tuple[str, str] = ("a", "b"), limit=None) -> str:
        """`limit` caps the differences and the allowed ones listed, each."""
        a, b = labels
        out = []
        shown = self.differences[:limit] + self.allowed[:limit]
        for found in shown:
            note = f"  allowed: {found.allowed_by}" if found.allowed_by else ""
            out.append(f"#{found.index} {found.endpoint}  {found.request}")
            out.append(
                f"    {found.path}: {a}={_show(found.a)} {b}={_show(found.b)}{note}"
            )
        unlisted = len(self.differences) + len(self.allowed) - len(shown)
        if unlisted:
            out.append(f"... and {unlisted} more")
        if self.diverged is not None:
            out.append(f"#{self.diverged.index} the runs diverge:")
            out.append(f"    {a}: {self.diverged.a}")
            out.append(f"    {b}: {self.diverged.b}")
        out.append(
            f"{self.compared} exchanges compared, {self.skipped} pages not compared,"
            f" {len(self.differences)} differences, {len(self.allowed)} allowed"
            + (", runs diverged" if self.diverged else "")
            + " (ids are numbered by first appearance)"
        )
        return "\n".join(out)


# A value longer than this, such as a whole page, is cut in a report.
SHOW_LIMIT = 300


def _show(value) -> str:
    if value is MISSING:
        return str(MISSING)
    text = json.dumps(value, ensure_ascii=False)
    if len(text) > SHOW_LIMIT:
        return f"{text[:SHOW_LIMIT]}... ({len(text)} characters)"
    return text


def _key_path(key) -> str:
    if isinstance(key, str) and _PLAIN_KEY.fullmatch(key):
        return f".{key}"
    return f"[{json.dumps(key, ensure_ascii=False)}]"


def _type(value) -> str:
    return type(value).__name__


def diff_json(a, b, path: str, endpoint: str, out: list[Found], within=None):
    """`within` is the key of the list or object that holds `a` and `b`."""
    if _type(a) != _type(b):
        out.append(Found(path, a, b))
    elif isinstance(a, dict):
        for key in list(a) + [k for k in b if k not in a]:
            left, right = a.get(key, MISSING), b.get(key, MISSING)
            step = path + _key_path(key)
            rule = _volatile(endpoint, key, within)
            if left is MISSING or right is MISSING:
                out.append(Found(step, left, right))
            elif rule is not None:
                if not rule.same(left, right):
                    out.append(Found(step, left, right))
            else:
                diff_json(left, right, step, endpoint, out, within=key)
    elif isinstance(a, list):
        for i, left, right in _pairs(a, b):
            if left is MISSING or right is MISSING:
                out.append(Found(f"{path}[{i}]", left, right))
            else:
                diff_json(left, right, f"{path}[{i}]", endpoint, out, within)
    elif a != b:
        out.append(Found(path, a, b))


def _summary(element: Element | _Missing):
    if element is MISSING:
        return MISSING
    return {"tag": element.tag, **element.attributes}


def diff_xml(a: Element, b: Element, path: str, out: list[Found]) -> None:
    if a.tag != b.tag:
        out.append(Found(path, _summary(a), _summary(b)))
        return
    for name in list(a.attributes) + [n for n in b.attributes if n not in a.attributes]:
        left = a.attributes.get(name, MISSING)
        right = b.attributes.get(name, MISSING)
        if left != right:
            out.append(Found(f"{path}/@{name}", left, right))
    if a.text != b.text:
        out.append(Found(f"{path}/text()", a.text, b.text))
    tags = [c.tag for c in a.children] + [c.tag for c in b.children]
    seen: dict[str, int] = defaultdict(int)
    for _, left, right in _pairs(a.children, b.children):
        tag = (right if left is MISSING else left).tag
        seen[tag] += 1
        step = f"{path}/{tag}"
        if tags.count(tag) > 2 or (left is MISSING) != (right is MISSING):
            step += f"[{seen[tag]}]"
        if left is MISSING or right is MISSING:
            out.append(Found(step, _summary(left), _summary(right)))
        else:
            diff_xml(left, right, step, out)


def diff_bodies(a: Body, b: Body, endpoint: str) -> list[Found]:
    out: list[Found] = []
    if a.kind != b.kind:
        return [Found("$", f"<{a.kind}>", f"<{b.kind}>")]
    if a.kind is BodyKind.XML:
        root_a, root_b = a.values[0], b.values[0]
        diff_xml(root_a, root_b, f"/{root_a.tag}", out)
        return out
    several_values = len(a.values) > 1 or len(b.values) > 1
    for i, left, right in _pairs(a.values, b.values):
        prefix = f"[{i}]$" if several_values else "$"
        if left is MISSING or right is MISSING:
            out.append(Found(prefix, left, right))
        else:
            diff_json(left, right, prefix, endpoint, out)
    if a.trailing != b.trailing:
        out.append(Found("text", a.trailing, b.trailing))
    return out


def new_runs(now: datetime | None = None) -> tuple[Run, Run]:
    """The two runs' normalizing state, to pass to `diff` for each part of
    the runs in turn, so that ids keep their numbers from part to part."""
    now = now or datetime.now()
    return Run(FirstAppearance(), now), Run(FirstAppearance(), now)


def absorb(exchanges: Sequence[Exchange], run: Run) -> None:
    """Number the ids in exchanges only one run compared, so that the run's
    numbering goes on as if they had been compared.

    The other run never sees these rows, so until the next reset its
    numbering can run behind: a row first seen here and a different row the
    other run sees first later can get the same number, and compare equal.
    That happens only after an excused stop or a parted step, whose tails
    today are mostly pages, which are not numbered."""
    for exchange in exchanges:
        request = parse_request(exchange.request, run)
        if request.comparison is not Comparison.SKIPPED:
            read_body(exchange.body, request, run)
        if _resets_database(request):
            run.ids = FirstAppearance()


def diff(
    a_text: str,
    b_text: str,
    allow: Allow | None = None,
    now: datetime | None = None,
    runs: tuple[Run, Run] | None = None,
) -> Report:
    """Compare two `output.curl` runs exchange by exchange. `now` is when
    they ran, for telling volatile timestamps from fixed dates. `runs`, from
    `new_runs`, carries the id numbering over from an earlier part of the
    same two runs; it is updated in place, past any divergence too."""
    run_a, run_b = runs or new_runs(now)
    report = Report()
    exchanges_a, exchanges_b = parse(a_text), parse(b_text)
    for i, ex_a, ex_b in _pairs(exchanges_a, exchanges_b):
        if ex_a is MISSING or ex_b is MISSING:
            index = (ex_b if ex_a is MISSING else ex_a).index
            report.diverged = Divergence(
                index,
                MISSING if ex_a is MISSING else ex_a.request,
                MISSING if ex_b is MISSING else ex_b.request,
            )
            absorb(exchanges_a[i:], run_a)
            absorb(exchanges_b[i:], run_b)
            break
        req_a = parse_request(ex_a.request, run_a)
        req_b = parse_request(ex_b.request, run_b)
        if req_a != req_b:
            report.diverged = Divergence(ex_a.index, ex_a.request, ex_b.request)
            # Numbering is by first appearance, so the requests numbered
            # already number the same again.
            absorb(exchanges_a[i:], run_a)
            absorb(exchanges_b[i:], run_b)
            break
        if req_a.comparison is Comparison.SKIPPED:
            report.skipped += 1
            continue
        report.compared += 1
        found = diff_bodies(
            read_body(ex_a.body, req_a, run_a),
            read_body(ex_b.body, req_b, run_b),
            req_a.endpoint,
        )
        for item in found:
            difference = Difference(
                ex_a.index, req_a.endpoint, ex_a.request, item.path, item.a, item.b
            )
            difference.allowed_by = allow(difference) if allow else None
            if difference.allowed_by:
                report.allowed.append(difference)
            else:
                report.differences.append(difference)
        if _resets_database(req_a):
            run_a.ids, run_b.ids = FirstAppearance(), FirstAppearance()
    return report


def _resets_database(request: Request) -> bool:
    """`database.execute&script=schema`, which `testing/reset-database.sh`
    posts: every row is gone, and PHP hands out its ids from 1 again."""
    return (
        request.endpoint == "action database.execute"
        and ("script", "schema") in request.key[-1]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("a", help="the first run's output.curl, e.g. PHP's")
    parser.add_argument("b", help="the second run's output.curl, e.g. Django's")
    parser.add_argument(
        "--labels", default="a,b", help="names for the two runs, e.g. php,django"
    )
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="when the runs were made, e.g. 2026-11-01T09:30 (default: now)",
    )
    args = parser.parse_args(argv)
    labels = tuple(args.labels.split(",", 1))
    if len(labels) != 2:
        parser.error("--labels takes two names separated by a comma")
    with open(args.a, encoding="utf-8", errors="replace") as a_file:
        a_text = a_file.read()
    with open(args.b, encoding="utf-8", errors="replace") as b_file:
        b_text = b_file.read()
    report = diff(a_text, b_text, now=args.now)
    print(f"{labels[0]}: {args.a}\n{labels[1]}: {args.b}")
    print(report.render(labels))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
