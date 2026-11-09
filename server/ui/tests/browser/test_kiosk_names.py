"""Long racer names on the kiosk pages (#250): each name shows whole.

PHP lets a name wrap in its table cell (`td.name`, now-racing.css:36-38,
has no white-space rule), and its `FontAdjuster`
(now-racing-adjust-fonts.js:42-66) shrinks every cell's font
by 0.9 until the table fits the window. Django's rows page wraps a name too
and steps the board's one `--row-font` down by 0.9 until every lane's names
fit their row (kiosk-now-racing.js `fit()`), within kiosk.css's clamp:
16 to 96 px above 640 px wide, 14.4 to 32 px at 640 px or less (D10). The
columnar pages and the on-deck chart wrap a name in its column.

The roster's longest names (testing/import-roster.sh:12, :36, :44, :30) race
Lions' heat, with headshots, so each lane's photo narrows the name's column
when it loads. The desktop size runs at 1920x1080, the size #239 saw names
cut at."""

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import Error, expect

from core.models import RaceSettings
from racing.services import heats
from roster.models import Racer
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
# kiosk.css's `.k-lane` clamp: 1rem to 6rem above 640 px wide, .9rem to
# 2rem at 640 px or less.
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
# `.then(done, done)`, which swallows it. One fit
# sets it once to start and once a step; from the cap to the floor is at
# most ceil(log(14.4 / 96) / log(0.9)) = 19 steps, so 50 leaves room for
# more than two fits in one task.
RUNAWAY = 50
RUNAWAY_GUARD = """(limit) => {
  const set = CSSStyleDeclaration.prototype.setProperty;
  let count = 0;
  CSSStyleDeclaration.prototype.setProperty = function (name, ...rest) {
    if (name === '--row-font') {
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
    page.context.add_init_script(f"({RUNAWAY_GUARD})({RUNAWAY})")
    yield
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
    below where `fit()` starts, so it shrank for them. At 390x844 the lane
    starts at 15.6 px (4% of the width), 1.2 px above the floor, and the
    names fit there; each photo takes at most 40% of the racer's column
    (kiosk.css `.k-lane .k-who [data-photo]`), and the names box at least
    half of it."""
    open_rows(page, event)
    assert_fits(page)
    if page.size == "desktop":
        page.wait_for_function(f"({START_PX})() - {LANE_PX} >= 1", timeout=5000)
    else:
        page.wait_for_function(f"Math.abs({LANE_PX} - 15.6) < 0.01", timeout=5000)
        page.wait_for_function(f"({PHONE_ROOM})().length === 0", timeout=5000)
    expect(page.locator("[data-lane] [data-carname]").first).to_have_text(CAR_NAME)
    expect(page.locator("[data-lane] [data-note]").first).to_have_text(NOTE)
    settled(page)
    shoot(page, "kiosk-now-racing-long-names")


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


@pytest.mark.parametrize("window", [TV, {"width": 1280, "height": 800}])
def test_on_a_wide_window_each_photo_keeps_its_full_size(
    page, event, long_names, window
):
    """The 40% cap changes no photo that already fits: at 1920x1080 and
    1280x800 each photo is the row height x 0.8 tall (kiosk.css
    `.k-lane .k-who img`), as on master, and as wide as its shape makes it."""
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
    """The limit of the floor (D10). With eight lanes at 390x844, a name of
    five 30-letter words needs more lines than its row holds at 14.4 px. The
    font stops at the floor, the name still wraps so no line is cut
    sideways, and the lines past the row's foot are hidden by the lane
    (kiosk.css `.k-lane { overflow: hidden }`). The other seven lanes fit."""
    if page.size != "phone":
        pytest.skip("eight lanes cut a name only on the phone")
    rounds = race_setup(event, groups=(("Lions", 8),), lanes=8)
    word = "Delaughter" * 3
    Racer.objects.filter(event=event, car_number=101).update(
        first_name=" ".join([word] * 4), last_name=word
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
    grows with a name that does not wrap, and the column cuts it. PHP's columnar page
    clips a name to one line (now-racing-columnar.css:122-131), where
    Django's wraps it (D10); PHP's on-deck chart wraps it in its cell
    (ondeck.css has no white-space rule)."""
    if page.size == "desktop":
        page.set_viewport_size(TV)
    open_kiosk(page, event, f"kiosks/{name}.kiosk")
    names = page.locator(".k-racer").filter(has_text=full_name(102))
    expect(names.first).to_have_text(full_name(102))
    for car in LONG:
        expect(
            page.locator(".k-racer").filter(has_text=full_name(car)).first
        ).to_be_visible()
    page.wait_for_function(
        """() => [...document.querySelectorAll('.k-racer')].every(n => {
          const box = n.closest('.k-column, td').getBoundingClientRect();
          const r = n.getBoundingClientRect();
          return n.scrollWidth <= n.clientWidth && r.left >= box.left - 0.5
            && r.right <= box.right + 0.5 && r.bottom <= box.bottom + 0.5;
        })""",
        timeout=5000,
    )
