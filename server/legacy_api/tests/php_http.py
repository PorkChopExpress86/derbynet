"""Replaying PHP transcripts through `/action.php` with the ids normalized.

A request names rows by PHP's ids; each is translated to the Django row PHP's
id stood for, learned from the responses before it. A response matches when,
with Django's ids written as PHP's and its volatile values masked as
`capture/normalize.mjs` masks PHP's, it encodes to PHP's body byte for byte.
"""

import json
import re
from urllib.parse import parse_qsl, quote, urlencode

from legacy_api.encoding import encode_php_json

# Keys whose value is a row id, and the kind of row.
ID_KEYS = {
    "racerid": "racer",
    "partitionid": "partition",
    "classid": "class",
    "rankid": "rank",
    "roundid": "round",
    "resultid": "result",
    "awardid": "award",
}
# Keys whose value is a list of ids of one kind, or for `schedule` a list of
# heats of racer ids.
LIST_KEYS = {
    "classids": "class",
    "rankids": "rank",
    "eligible-classids": "class",
    "eligible-rankids": "rank",
}
# Ids the scenarios choose to name no row, and where Django's stand-ins start.
UNKNOWN_ID = 99
NO_ROW = 10**9

_TIMECHECK = re.compile(r'("timecheck": )"[^"]*"')
_EPOCH = re.compile(r'("last[-_]contact": )[1-9]\d*')
_SECONDS = re.compile(r"\(\d+ sec\.\)")
_DURATION = re.compile(r'("duration": )[0-9.eE+-]+')
_AGE = re.compile(r'("age": )\d+')
_TIMESTAMP = re.compile(r'"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"')
_ROUND_IN_TEXT = re.compile(r"roundid (\d+)")
_ROUND_IN_XML = re.compile(r'(roundid="|roundid )(\d+)')
_SIGNATURE = re.compile(r"(\d+)-(\d+)")
# An award key: `award-<awardid>`, or `speed-<place>[-<classid>[-<rankid>]]`.
_AWARD_KEY = re.compile(r"award-(\d+)|speed-(\w+?)(?:-(\d+)(?:-(\d+))?)?")


def award_key(key: str, to_id) -> str:
    """An award key with its ids passed through `to_id(kind, id)`."""
    found = _AWARD_KEY.fullmatch(key)
    if found is None:
        return key
    if found[1]:
        return f"award-{to_id('award', int(found[1]))}"
    key = f"speed-{found[2]}"
    for kind, part in (("class", found[3]), ("rank", found[4])):
        if part:
            key += f"-{to_id(kind, int(part))}"
    return key


class Ids:
    """PHP's id of each Django row, per kind, as the responses show it."""

    def __init__(self):
        self.to_php: dict[str, dict[int, int]] = {k: {} for k in ID_KEYS.values()}

    def _pair(self, kind, php, django):
        if isinstance(php, int) and isinstance(django, int) and php > 0:
            self.to_php[kind].setdefault(django, php)

    def learn(self, php, django, key=None):
        if isinstance(php, dict) and isinstance(django, dict):
            for k in php.keys() & django.keys():
                if k == "tbodyid" and not php.get("use_master_sched"):
                    self._pair("round", php[k], django[k])
                elif k in ID_KEYS:
                    self._pair(ID_KEYS[k], php[k], django[k])
                elif k in LIST_KEYS and isinstance(php[k], list):
                    for a, b in zip(php[k], django[k] or [], strict=False):
                        self._pair(LIST_KEYS[k], a, b)
                elif k == "schedule":
                    for heat_a, heat_b in zip(php[k], django[k], strict=False):
                        for a, b in zip(heat_a, heat_b, strict=False):
                            self._pair("racer", a, b)
                elif k == "schedule-signature":
                    self._learn_signature(php[k], django[k])
                else:
                    self.learn(php[k], django[k], k)
        elif isinstance(php, list) and isinstance(django, list):
            for a, b in zip(php, django, strict=False):
                self.learn(a, b, key)

    def _learn_signature(self, php, django):
        if not isinstance(php, dict) or not isinstance(django, dict):
            return
        for (round_a, sig_a), (round_b, sig_b) in zip(
            php.items(), django.items(), strict=False
        ):
            self._pair("round", int(round_a), int(round_b))
            for a, b in zip(
                _SIGNATURE.findall(sig_a), _SIGNATURE.findall(sig_b), strict=False
            ):
                self._pair("result", int(a[0]), int(b[0]))
                self._pair("racer", int(a[1]), int(b[1]))

    def django(self, kind, php_id: int) -> int:
        for django_id, known in self.to_php[kind].items():
            if known == php_id:
                return django_id
        if php_id <= 0:
            return php_id
        if php_id >= UNKNOWN_ID:
            # An id the scenario chose to name no row, past any Django row.
            return NO_ROW + php_id
        raise KeyError(f"{kind} {php_id} has not appeared yet")

    def php(self, kind, django_id):
        if isinstance(django_id, int):
            return self.to_php[kind].get(django_id, django_id)
        return django_id

    def as_php(self, value, key=None):
        if isinstance(value, dict):
            out = {}
            for k, v in value.items():
                if k in ID_KEYS:
                    out[k] = self.php(ID_KEYS[k], v)
                elif k in LIST_KEYS and isinstance(v, list):
                    out[k] = [self.php(LIST_KEYS[k], i) for i in v]
                elif k == "schedule" and isinstance(v, list):
                    out[k] = [[self.php("racer", r) for r in heat] for heat in v]
                elif k == "schedule-signature" and isinstance(v, dict):
                    out[k] = {
                        str(self.php("round", int(r))): _SIGNATURE.sub(
                            lambda m: (
                                f"{self.php('result', int(m[1]))}"
                                f"-{self.php('racer', int(m[2]))}"
                            ),
                            sig,
                        )
                        for r, sig in v.items()
                    }
                elif k == "tbodyid" and not value.get("use_master_sched"):
                    out[k] = self.php("round", v)
                elif k == "key" and isinstance(v, str):
                    out[k] = award_key(v, self.php)
                elif k == "chart" and isinstance(v, list) and key is None:
                    out[k] = [
                        None
                        if heat is None
                        else " ".join(
                            str(self.php("racer", int(r))) if r.isdigit() else r
                            for r in heat.split(" ")
                        )
                        for heat in v
                    ]
                else:
                    out[k] = self.as_php(v, k)
            return out
        if isinstance(value, list):
            return [self.as_php(v, key) for v in value]
        return value


# A request field that names a row, and the kind of row.
FIELD_KINDS = {
    "racer": "racer",
    "racerid": "racer",
    "roundid": "round",
    "classid": "class",
    "rankid": "rank",
    "partitionid": "partition",
    "awardid": "award",
}
# Fields `<prefix><n>` whose value names a row.
VALUE_PREFIX_KINDS = {"awardid_": "award"}
PREFIX_KINDS = {"classid_": "class", "rankid_": "rank", "constituent_": "class"}


def translate(fields, ids: Ids):
    out = []
    for key, value in fields:
        if key in FIELD_KINDS and value.lstrip("-").isdigit():
            value = str(ids.django(FIELD_KINDS[key], int(value)))
        for prefix, kind in VALUE_PREFIX_KINDS.items():
            if key.startswith(prefix) and value.isdigit():
                value = str(ids.django(kind, int(value)))
        if key == "key":
            value = award_key(value, ids.django)
        if key == "class_and_rank":
            # `classid[,rankid]`, where 0 is none.
            value = ",".join(
                str(ids.django(kind, int(part))) if part.isdigit() else part
                for kind, part in zip(("class", "rank"), value.split(","), strict=False)
            )
        for prefix, kind in PREFIX_KINDS.items():
            if key.startswith(prefix) and key[len(prefix) :].isdigit():
                key = prefix + str(ids.django(kind, int(key[len(prefix) :])))
        out.append((key, value))
    return out


def send(client, request: str, body: str | None, ids: Ids):
    method, target = request.split(" ", 1)
    if method == "GET":
        path, _, query = target.partition("?")
        fields = translate(parse_qsl(query, keep_blank_values=True), ids)
        return client.get(f"/{path}?{urlencode(fields, quote_via=quote)}")
    fields = translate(parse_qsl(body or "", keep_blank_values=True), ids)
    return client.post(
        "/" + target,
        urlencode(fields, quote_via=quote),
        content_type="application/x-www-form-urlencoded",
    )


def mask(body: str) -> str:
    """`normalize.mjs`'s masks for the values that change from run to run."""
    body = _TIMECHECK.sub(r'\1"<timecheck>"', body)
    body = _EPOCH.sub(r'\1"<epoch>"', body)
    body = _SECONDS.sub("(<n> sec.)", body)
    body = _AGE.sub(r'\1"<seconds>"', body)
    body = _DURATION.sub(r'\1"<seconds>"', body)
    return _TIMESTAMP.sub('"<timestamp>"', body)


def mask_reschedule_timing(django: dict) -> dict:
    timing = django.get("timing")
    if isinstance(timing, dict):

        def seconds(value):
            # Only the numbers: `run-N-timed-out` stays `true`, as in PHP's.
            if isinstance(value, dict):
                return {k: seconds(v) for k, v in value.items()}
            return value if isinstance(value, bool) else "<seconds>"

        django["timing"] = seconds(timing)
    return django


def as_php_body(response, php_body: bytes, ids: Ids) -> bytes:
    """Django's body with its ids written as PHP's, masked as PHP's is."""
    if response["Content-Type"].startswith("text/xml"):
        # A timer reply names rounds only, in attributes and failure text.
        return _ROUND_IN_XML.sub(
            lambda m: f"{m[1]}{ids.php('round', int(m[2]))}",
            response.content.decode(),
        ).encode()
    django = json.loads(response.content)
    php = json.loads(php_body) if php_body.strip() else None
    if isinstance(php, dict):
        ids.learn(php, django)
        if "action" in php and "action" in django:
            # The echo of the request, which named rows by Django's ids.
            assert list(django["action"]) == list(php["action"])
            django["action"] = php["action"]
    django = mask_reschedule_timing(ids.as_php(django))
    outcome = django.get("outcome")
    if isinstance(outcome, dict) and isinstance(outcome.get("description"), str):
        # `nosuchheat` names the round it looked in.
        outcome["description"] = _ROUND_IN_TEXT.sub(
            lambda m: f"roundid {ids.php('round', int(m[1]))}", outcome["description"]
        )
    return mask(encode_php_json(django).decode()).encode()
