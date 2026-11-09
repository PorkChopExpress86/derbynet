"""Long racer names on the kiosk pages (#250): each name shows whole.

PHP lets a name wrap in its table cell (`td.name`, now-racing.css:36-38,
has no white-space rule), and its `FontAdjuster`
(now-racing-adjust-fonts.js:42-66) shrinks every cell's font
by 0.9 until the table fits the window. Django's rows page wraps a name too
and steps the board's one `--row-font` down by 0.9 until every lane's names
fit their row (kiosk-now-racing.js `fit()`), within kiosk.css's `.k-lane`
clamp, whose bounds server/README.md states and
`test_the_lane_font_stays_within_the_clamp` checks (D10, ADR-10). The
columnar pages and the on-deck chart wrap a name in its column.

Four long names from testing/import-roster.sh (:12, :36, :44, :30) race
Lions' heat, with headshots, so each lane's photo narrows the name's column
when it loads. The desktop size runs at 1920x1080, the size #250 names.
`tools/kiosk_screens.py` reports a racer's name cut with an ellipsis, which
its clipped-text check skips for other text."""

import math
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import Error, expect

from core.models import RaceSettings
from racing.models import TimerStatus
from racing.services import heats
from roster.models import Racer
from tools import kiosk_screens
from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_pages import open_kiosk, settled
from ui.tests.race import race_setup, scheduled

HEADSHOTS = Path(__file__).resolve().parents[4] / "testing" / "data" / "headshots"
TV = {"width": 1920, "height": 1080}
# Lions' four racers, by car number, as testing/import-roster.sh names them.
LONG = {
    101: ('Adolfo "Dolf"', "Asher"),
    102: ("Darrell & Darrell", "Delaughter"),
    103: ("Earnest", "Evangelista"),
    104: ("Christoper", "Chauncey"),
}
CAR_NAME = "The Lightning Bolt Special"
NOTE = "Anywhere"
# kiosk.css's `.k-lane` clamp as server/README.md states it, [floor, cap]
# by window width; test_the_lane_font_stays_within_the_clamp checks it.
BOUNDS = "(innerWidth <= 640 ? [14.4, 32] : [16, 96])"

# What keeps each lane's names, car name and note from fitting their row:
# a line wider than its box, or the names box ending past the lane's foot.
MISFITS = """() => [...document.querySelectorAll('[data-now-racing] [data-lane]')]
  .flatMap(lane => {
    const names = lane.querySelector('.k-names');
    const n = lane.dataset.lane;
    const wide = [...names.children].filter(c => c.scrollWidth > c.clientWidth)
      .map(c => `${n} wide ${c.textContent}: ${c.scrollWidth} > ${c.clientWidth}`);
    const below = names.offsetTop + names.offsetHeight - lane.clientHeight;
    return below > 0 ? [...wide, `${n} ${below} px below its row`] : wide;
  })"""


def assert_fits(page):
    """Wait for every lane to fit; on a timeout, fail with what does not."""
    try:
        page.wait_for_function(f"({MISFITS})().length === 0", timeout=5000)
    except Error:
        pass
    # One read: the wait above has either seen every lane fit or given up,
    # and this read names what does not.
    assert page.evaluate(MISFITS) == []


# The lane font `fit()` starts from: the row height and the window, within
# the floor and the cap.
START_PX = (
    """() => {
  const board = document.querySelector('[data-now-racing]');
  const rows = board.querySelectorAll('[data-lane]').length;
  const height = board.getBoundingClientRect().height / (rows + 0.6);
  const [floor, cap] = """
    + BOUNDS
    + """;
  return Math.max(floor, Math.min(height * 0.42, innerWidth * 0.04, cap));
}"""
)
LANE_PX = "parseFloat(getComputedStyle(document.querySelector('.k-lane')).fontSize)"
IMAGES_LOADED = (
    "[...document.querySelectorAll('[data-lane] img')]"
    ".every(i => i.complete && i.naturalWidth > 0)"
)


# Fails a `fit()` loop that never ends, which would hang the tab and every
# Playwright call after it: `--row-font` set more than RUNAWAY times in one
# task throws, which ends the loop, and records the count in
# `window.runawayFit`, which the fixture asserts unset after the test. The
# throw alone fails nothing: kiosk.js's `run()` ends each poll with
# `.then(done, done)`, which swallows it. One fit sets it once to start and
# once a step, and from the highest cap to the lowest floor takes FIT_SETS
# sets at most; the fixture asserts RUNAWAY leaves room for two fits.
FIT_SETS = 1 + math.ceil(math.log(14.4 / 96) / math.log(0.9))
RUNAWAY = 50
RUNAWAY_GUARD = """(limit) => {
  const set = CSSStyleDeclaration.prototype.setProperty;
  let count = 0;
  window.rowFontSets = 0;
  CSSStyleDeclaration.prototype.setProperty = function (name, ...rest) {
    if (name === '--row-font') {
      window.rowFontSets += 1;
      if (count === 0) setTimeout(() => { count = 0; });
      count += 1;
      if (count > limit) {
        window.runawayFit = count;
        throw new Error(`fit() set --row-font ${count} times`);
      }
    }
    return set.call(this, name, ...rest);
  };
}"""


@pytest.fixture(autouse=True)
def runaway_guard(page):
    assert 2 * FIT_SETS < RUNAWAY
    page.context.add_init_script(f"({RUNAWAY_GUARD})({RUNAWAY})")
    yield
    # One read: the test has ended, and the guard records a runaway loop
    # once, when it stops it.
    assert page.evaluate("window.runawayFit || null") is None


def full_name(car: int) -> str:
    return " ".join(LONG[car])


@pytest.fixture
def long_names(settings, tmp_path, event):
    """Lions' heat 1 races the four long names, each with a headshot; car
    101 also has a car name and a note. Tigers' round is scheduled, with
    the factory's short names."""
    settings.DATA_DIR = tmp_path / "data"
    (tmp_path / "racers").mkdir()
    rounds = race_setup(event, groups=(("Lions", 4), ("Tigers", 4)))
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers")
    )
    shots = sorted(HEADSHOTS.iterdir())
    for (car, (first, last)), shot in zip(LONG.items(), shots, strict=False):
        shutil.copy(shot, tmp_path / "racers")
        Racer.objects.filter(event=event, car_number=car).update(
            first_name=first, last_name=last, headshot=shot.name
        )
    Racer.objects.filter(event=event, car_number=101).update(
        car_name=CAR_NAME, note=NOTE
    )
    scheduled(rounds["Tigers"], race=False)
    scheduled(rounds["Lions"])
    # A connected timer, so no "Check timer" overlay covers the names.
    TimerStatus.objects.filter(event=event).update(
        state=TimerStatus.State.STAGING, assume_connected_for_test=True
    )
    return rounds


def open_rows(page, event):
    if page.size == "desktop":
        page.set_viewport_size(TV)
    open_kiosk(page, event, "kiosks/now-racing.kiosk")
    for car in LONG:
        expect(page.locator("[data-now-racing]")).to_contain_text(full_name(car))
    page.wait_for_function(IMAGES_LOADED, timeout=10_000)


def test_long_names_show_whole(page, event, long_names):
    """Each name, the car name and the note show whole, on as many lines as
    they need, at 1920x1080 and 390x844. At 1920x1080 the board's font ends
    below where `fit()` starts, so it shrank for them, and every word is
    whole. At 390x844 each photo takes at most 40% of the racer's column
    (kiosk.css `.k-lane .k-who [data-photo]`, at 640 px or less), and the
    names box at least
    half of it; a word such as "Delaughter" is wider than the box even at
    the floor, 14.4 px, so the font stops there and the word breaks."""
    open_rows(page, event)
    assert_fits(page)
    if page.size == "desktop":
        page.wait_for_function(f"({START_PX})() - {LANE_PX} >= 1", timeout=5000)
        page.wait_for_function(f"({TOO_WIDE})().length === 0", timeout=5000)
    else:
        page.wait_for_function(f"{LANE_PX} === 14.4", timeout=5000)
        page.wait_for_function(f"({PHONE_ROOM})().length === 0", timeout=5000)
        page.wait_for_function(f"({TOO_WIDE})().length > 0", timeout=5000)
    expect(page.locator("[data-lane] [data-carname]").first).to_have_text(CAR_NAME)
    expect(page.locator("[data-lane] [data-note]").first).to_have_text(NOTE)
    settled(page)
    shoot(page, "kiosk-now-racing-long-names")


# The lines of names that hold a word wider than their box: each measured
# with words kept whole, set here rather than through kiosk.css's
# `.k-fitting`, which this checks.
TOO_WIDE = """() => [...document.querySelectorAll('[data-now-racing] .k-names > *')]
  .filter(line => {
    line.style.overflowWrap = 'normal';
    const wide = line.scrollWidth > line.clientWidth;
    line.style.overflowWrap = '';
    return wide;
  }).map(line => line.textContent)"""
WORD = "Wolfeschlegelsteinhausenberger"


def test_a_word_too_wide_for_its_box_shrinks_the_font(page, event, long_names):
    """At 1920x1080 a racer with a 30-letter word in the name: the font
    ends below the starting size with the word whole, as PHP shrinks a
    table too wide (now-racing-adjust-fonts.js:53-61), rather than break
    it."""
    if page.size == "phone":
        pytest.skip("the phone breaks a long word at the floor")
    Racer.objects.filter(event=event, car_number=103).update(last_name=WORD)
    page.set_viewport_size(TV)
    open_kiosk(page, event, "kiosks/now-racing.kiosk")
    expect(page.locator("[data-now-racing]")).to_contain_text(WORD)
    page.wait_for_function(IMAGES_LOADED, timeout=10_000)
    assert_fits(page)
    page.wait_for_function(f"({TOO_WIDE})().length === 0", timeout=5000)
    page.wait_for_function(f"({START_PX})() - {LANE_PX} >= 1", timeout=5000)


# The lanes whose photo takes more than 40% of the racer's column, or whose
# names box has less than half of it.
PHONE_ROOM = """() => [...document.querySelectorAll('[data-now-racing] [data-lane]')]
  .filter(lane => {
    const who = lane.querySelector('.k-who').clientWidth;
    const photo = lane.querySelector('[data-photo]').getBoundingClientRect().width;
    const names = lane.querySelector('.k-names').clientWidth;
    return photo > who * 0.4 + 0.5 || names < who * 0.5;
  }).map(lane => lane.dataset.lane)"""
# Each lane's photo: its box, the image's own size, and the row height.
PHOTOS = """() => [...document.querySelectorAll('[data-now-racing] [data-lane] img')]
  .map(img => ({
    width: img.getBoundingClientRect().width,
    height: img.getBoundingClientRect().height,
    natural: img.naturalWidth / img.naturalHeight,
    row: parseFloat(document.querySelector('[data-now-racing]')
      .style.getPropertyValue('--row-h')),
  }))"""


WIDE_WINDOWS = [
    TV,
    {"width": 1280, "height": 800},
    {"width": 1024, "height": 768},
    {"width": 800, "height": 600},
]


@pytest.mark.parametrize("window", WIDE_WINDOWS)
def test_on_a_wide_window_each_photo_keeps_its_full_size(
    page, event, long_names, window
):
    """The 40% cap applies at 640 px or less, so at TV and projector sizes,
    1920x1080, 1280x800, 1024x768 and 800x600, each photo is the row height
    x 0.8 tall (kiosk.css `.k-lane .k-who img`), as on master, and as wide
    as its shape makes it."""
    if page.size == "phone":
        pytest.skip("the phone's photos are capped (test_long_names_show_whole)")
    page.set_viewport_size(window)
    open_kiosk(page, event, "kiosks/now-racing.kiosk")
    expect(page.locator("[data-lane] img")).to_have_count(4)
    page.wait_for_function(IMAGES_LOADED, timeout=10_000)
    page.wait_for_function(
        f"({PHOTOS})().every(p => Math.abs(p.height - p.row * 0.8) < 0.5"
        " && Math.abs(p.width - p.height * p.natural) < 1)",
        timeout=5000,
    )


def test_the_next_lineup_starts_again_from_the_full_size(page, event, long_names):
    """Short names after long ones get the full starting size back, as
    PHP's `reset()` goes back to the original size for a new heat
    (now-racing.js:132, now-racing-adjust-fonts.js:13-17). At 1920x1080,
    where the long names shrink the font (test_long_names_show_whole)."""
    if page.size == "phone":
        pytest.skip("the phone's long names fit at the starting size")
    open_rows(page, event)
    page.wait_for_function(f"({START_PX})() - {LANE_PX} >= 1", timeout=5000)

    heats.select_heat(event, round_id=long_names["Tigers"].pk, heat=1, now_racing=True)

    expect(page.locator("[data-now-racing]")).to_contain_text("Tigers1 Racer")
    page.wait_for_function(f"Math.abs(({START_PX})() - {LANE_PX}) < 0.5", timeout=5000)
    assert_fits(page)


def test_the_font_shrinks_again_when_the_window_narrows(page, event, long_names):
    """A resize from 1920x1080 to 1440x1080 fits the names again from the
    new starting size, below it, as PHP sizes its table again on a resize
    (now-racing.js:359)."""
    if page.size == "phone":
        pytest.skip("the phone's long names fit at the starting size")
    open_rows(page, event)
    assert_fits(page)

    page.set_viewport_size({"width": 1440, "height": 1080})

    assert_fits(page)
    page.wait_for_function(
        f"innerWidth === 1440 && ({START_PX})() - {LANE_PX} >= 1", timeout=5000
    )


def test_at_the_floor_a_name_taller_than_its_row_is_cut_at_its_foot(page, event):
    """The limit of the floor (D10, ADR-10). With eight lanes at 390x844, a name of
    four 30-letter words needs more lines than its row holds at 14.4 px. The
    font stops at the floor, the name still wraps so no line is cut
    sideways, and the lines past the row's foot are hidden by the lane
    (kiosk.css `.k-lane { overflow: hidden }`). The other seven lanes fit."""
    if page.size != "phone":
        pytest.skip("eight lanes cut a name only on the phone")
    rounds = race_setup(event, groups=(("Lions", 8),), lanes=8)
    word = "Delaughter" * 3
    Racer.objects.filter(event=event, car_number=101).update(
        first_name=" ".join([word] * 3), last_name=word
    )
    scheduled(rounds["Lions"])
    open_kiosk(page, event, "kiosks/now-racing.kiosk")
    expect(page.locator("[data-now-racing]")).to_contain_text(word)

    try:
        page.wait_for_function(f"({FLOOR_STATE})().done", arg=None, timeout=5000)
    except Error:
        pass
    # One read: the wait above has either seen the state or given up, and
    # this read names what differs.
    state = page.evaluate(FLOOR_STATE)
    assert state == {
        "font": 14.4,
        "lanes": 8,
        "sideways": 0,
        "cut": 1,
        "hidden": "hidden",
        "done": True,
    }


# The floor test's state: the lane font, how many lanes have a line wider
# than its box, how many have names past the row's foot, and the lane's
# overflow.
FLOOR_STATE = """() => {
  const lanes = [...document.querySelectorAll('[data-now-racing] [data-lane]')];
  let sideways = 0, cut = 0;
  for (const lane of lanes) {
    const names = lane.querySelector('.k-names');
    if ([...names.children].some(n => n.scrollWidth > n.clientWidth)) sideways += 1;
    if (names.offsetTop + names.offsetHeight > lane.clientHeight) cut += 1;
  }
  const font = parseFloat(getComputedStyle(lanes[0]).fontSize);
  const hidden = getComputedStyle(lanes[0]).overflowY;
  const done = font === 14.4 && sideways === 0 && cut === 1 && hidden === 'hidden';
  return {font, lanes: lanes.length, sideways, cut, hidden, done};
}"""


@pytest.mark.parametrize(
    "name", ["now-racing-columnar", "now-racing-columnar-reversed", "ondeck"]
)
def test_columnar_and_ondeck_show_each_long_name_whole(page, event, long_names, name):
    """Each `.k-racer` holds the whole name, with no line wider than its
    box, and the box inside its column or its cell: in a column the box
    grows with a name that does not wrap, and the column cuts it. Car 103's
    30-letter word wraps only by `overflow-wrap`, where the other names
    wrap at their spaces. PHP's columnar page clips a name to one line
    (now-racing-columnar.css:122-131), where Django's wraps it (D10,
    ADR-10); PHP's on-deck chart wraps it in its cell (ondeck.css has no
    white-space rule)."""
    Racer.objects.filter(event=event, car_number=103).update(last_name=WORD)
    shown = {car: full_name(car) for car in LONG} | {103: f"{LONG[103][0]} {WORD}"}
    if page.size == "desktop":
        page.set_viewport_size(TV)
    open_kiosk(page, event, f"kiosks/{name}.kiosk")
    for text in shown.values():
        expect(page.locator(".k-racer").filter(has_text=text).first).to_have_text(text)
    page.wait_for_function(
        """() => [...document.querySelectorAll('.k-racer')].every(n => {
          const box = n.closest('.k-column, td').getBoundingClientRect();
          const r = n.getBoundingClientRect();
          return n.scrollWidth <= n.clientWidth && r.left >= box.left - 0.5
            && r.right <= box.right + 0.5 && r.bottom <= box.bottom + 0.5;
        })""",
        timeout=5000,
    )


def test_the_lane_font_stays_within_the_clamp(page, event, long_names):
    """kiosk.css's `.k-lane` clamp holds the bounds server/README.md states:
    16 to 96 px above 640 px wide, 14.4 to 32 px at 640 px or less. A
    `--row-font` far below and far above them computes to the floor and
    the cap."""
    open_rows(page, event)
    assert_fits(page)
    # One read: both values are read in one task, so no fit() runs between
    # them.
    found = page.evaluate(
        """() => {
          const board = document.querySelector('[data-now-racing]');
          const lane = board.querySelector('.k-lane');
          const at = (px) => {
            board.style.setProperty('--row-font', px + 'px');
            return parseFloat(getComputedStyle(lane).fontSize);
          };
          return [at(1), at(1000)];
        }"""
    )
    assert found == page.evaluate(BOUNDS)


def test_steady_polls_do_not_fit_again(page, event, long_names):
    """`fit()` runs for a new lineup, a photo that loads or a resize, never
    on PHP's 75 ms timer (now-racing-adjust-fonts.js:9-10) or at each poll:
    six polls of an unchanged lineup set `--row-font` no more times. Two
    polls pass first, so the photos' load events have run."""
    open_rows(page, event)
    assert_fits(page)
    for _ in range(2):
        with page.expect_request(lambda r: "query=poll&" in r.url, timeout=5000):
            pass
    # One read: the count before the polls, compared with the count after.
    before = page.evaluate("window.rowFontSets")
    for _ in range(6):
        with page.expect_request(lambda r: "query=poll&" in r.url, timeout=5000):
            pass
    # One read: the count after six polls, each sent once the reply before
    # it was drawn (kiosk.js `run()`).
    assert page.evaluate("window.rowFontSets") == before


ELLIPSES = """<!doctype html><html><body>
<div class="k-racer" style="width:60px;overflow:hidden;white-space:nowrap;
  text-overflow:ellipsis">Darrell &amp; Darrell Delaughter</div>
<div class="k-name" style="width:60px;overflow:hidden;white-space:nowrap;
  text-overflow:ellipsis">The banner's kiosk name</div>
<div class="k-racer" style="width:300px;overflow:hidden;white-space:nowrap;
  text-overflow:ellipsis">Kelvin Kinman</div>
</body></html>"""


def test_a_racer_name_cut_with_an_ellipsis_is_reported(page):
    """Of three boxes that cut text with an ellipsis, the check reports
    only the racer's name that does not fit (#250): another text cut with
    an ellipsis is cut on purpose, and a name that fits is not cut."""
    page.set_content(ELLIPSES)
    # One read: the stand-in page has no script, so nothing changes it.
    clipped = page.evaluate(kiosk_screens.CLIPPED)
    assert len(clipped) == 1
    assert clipped[0].startswith('<div class="k-racer" style="width:60px;')
