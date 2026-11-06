"""The settings page's bounds and choices are PHP's markup (D80): each number
input of `settings.php` gives its field's `min`, `max` and `step`, and each
replay `<select>` of `coordinator.php` its choices. #48 took them from the
ticket and memory, and its review found a linger maximum PHP lacks, a
whole-number track length and the wrong replay choices (#48 retro)."""

import re
from decimal import Decimal
from pathlib import Path

from core import settings_page

WEBSITE = Path(__file__).resolve().parents[3] / "website"

# A PHP input whose name is not its field's key: the linger input shows
# seconds and `settings.js` posts them as `now-racing-linger-ms`
# (`settings.php:169-173`).
KEYS = {"now-racing-linger-sec": "now-racing-linger-ms"}
# A field whose step is not the browser's default 1 for an input with none:
# `settings.php:152` has no step and `settings.js` posts the number as typed,
# so a fractional length saves (D80).
STEP_ANY = {"track-length"}
# A minimum PHP's input lacks: `settings.php:171-173` has no `min`, and the
# owner chose to refuse a negative linger (D80).
OWNER_MINIMUMS = {"now-racing-linger-ms": Decimal(0)}
# coordinator.php's replay choices, by the field they bound.
SELECTS = {
    "replay-skipback": settings_page.REPLAY_SKIPBACKS,
    "replay-rate": settings_page.REPLAY_RATES,
}
RANGE_SELECTS = {"replay-num-showings"}


def markup(name: str) -> str:
    """A PHP page with its HTML comments and its PHP blocks taken out: a
    block such as `value="<?php echo sprintf("%0.1f", ...) ?>"` holds quotes
    and a `>`."""
    text = re.sub(r"<!--.*?-->", "", (WEBSITE / name).read_text(), flags=re.S)
    return re.sub(r"<\?php.*?\?>", "", text, flags=re.S)


def attributes(tag: str) -> dict[str, str]:
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))


def number_inputs(text: str) -> dict[str, dict[str, str]]:
    tags = (attributes(t) for t in re.findall(r"<input\b[^>]*>", text, flags=re.S))
    return {a["name"]: a for a in tags if a.get("type") == "number"}


def select_values(text: str, name: str) -> list[str]:
    found = re.search(rf'<select\b[^>]*name="{name}"[^>]*>(.*?)</select>', text, re.S)
    assert found, f"no <select name={name!r}>"
    return re.findall(r'<option value="([^"]*)"', found[1])


def bound(value: str | None) -> Decimal | None:
    return None if value is None else Decimal(value)


def test_the_reader_skips_commented_options():
    text = '<select name="x"><!-- <option value="1">1</option> -->'
    text += '<option value="2">2</option></select>'
    assert select_values(re.sub(r"<!--.*?-->", "", text), "x") == ["2"]


def test_each_number_input_bounds_its_field_as_in_settings_php():
    inputs = number_inputs(markup("settings.php"))
    assert inputs, "settings.php has no number inputs"
    wrong = []
    for name, attrs in inputs.items():
        key = KEYS.get(name, name)
        field = settings_page.BY_KEY.get(key)
        if field is None:
            wrong.append(f"{name}: no field {key!r}")
            continue
        step = None if key in STEP_ANY else bound(attrs.get("step", "1"))
        minimum = OWNER_MINIMUMS.get(key, bound(attrs.get("min")))
        want = (minimum, bound(attrs.get("max")), step)
        got = (field.minimum, field.maximum, field.step)
        if got != want:
            wrong.append(f"{key}: (min, max, step) {got} where PHP has {want}")
    assert wrong == []


def test_each_replay_choice_is_coordinator_php_s():
    text = markup("coordinator.php")
    wrong = []
    for name, choices in SELECTS.items():
        php = select_values(text, name)
        if [value for value, _ in choices] != php:
            wrong.append(f"{name}: {[v for v, _ in choices]} where PHP has {php}")
    for name in RANGE_SELECTS:
        php = [Decimal(v) for v in select_values(text, name)]
        field = settings_page.BY_KEY[name]
        want = (min(php), max(php), Decimal(1))
        got = (field.minimum, field.maximum, field.step)
        if got != want or php != [want[0] + i for i in range(len(php))]:
            wrong.append(f"{name}: (min, max, step) {got} where PHP offers {php}")
    assert wrong == []
