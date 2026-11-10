"""The kiosk banner's title shows whole on a phone (#272).

At 640 px wide or less, kiosk.css lets `.k-banner h1` wrap, at 0.36 of
the banner's height instead of 0.48, and the banner's height becomes a
minimum, so a title of three or more lines grows the banner where it was
cut with an ellipsis. This is option B on #272, the lead's ruling under
D10 and ADR-10, not an owner decision. Wider windows keep the one-line
title at 0.48 of the banner, cut with an ellipsis when it runs past its
box.

PHP at 390x844 wraps the title on every page (global.css:25-34, an
absolute box with no height). Only now racing also shrinks it, by 0.9
while its box is taller than 60 px (now-racing-adjust-fonts.js:34-40).
The columnar pages and scale-MPH do not, and their wrapped lines fall
below the banner. #272's PR quotes the run.

On master each phone test here failed (#272's PR). On this machine's Noto
Sans the title's glyphs stand outside a 1.2 line, so the tests of a title
of three or more lines fail without the banner's padding, and every phone
test fails without the title's `overflow: visible`."""

import pytest
from playwright.sync_api import Error, expect

from racing.services import heats
from roster.models import Group
from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_names import (  # noqa: F401
    assert_fits,
    long_names,
    open_rows,
)
from ui.tests.browser.test_kiosk_pages import open_kiosk
from ui.tests.race import race_setup, scheduled

LONG = "White's Wolves"
# Three or more lines at the phone's font (asserted where it is used).
LONGER = "White's Wolves of the Arrow of Light Den Seven"
# One word wider than the title's box at the phone's font, which only
# overflow-wrap breaks.
UNBREAKABLE = "Supercalifragilisticexpialidocious"
HEAT, HEATS = 12, 17
# kiosk.css's `--k-banner`, clamp(2.75rem, 8vh, 6rem), in px.
K_BANNER = "Math.min(96, Math.max(44, innerHeight * 0.08))"

# The banner, its title and logo, and the title's lines: each line's box is
# one of the title's text rects, by its top. `problems` is what keeps the
# title from showing whole: a line past the title's sides, a line past the
# banner's top or foot (off the screen, or over the page), a line past the
# title's top or foot where the title clips (overflow not visible). A line's
# rect is its glyphs' height, which can be taller than the line.
MEASURE = f"""() => {{
  const banner = document.querySelector('.k-banner');
  const h1 = banner.querySelector('h1');
  const logo = banner.querySelector('img');
  const box = r => ({{left: r.left, top: r.top, right: r.right, bottom: r.bottom}});
  const range = document.createRange();
  range.selectNodeContents(h1);
  const rects = [...range.getClientRects()].map(box);
  const style = getComputedStyle(h1);
  const m = {{
    kBanner: {K_BANNER},
    banner: box(banner.getBoundingClientRect()),
    h1: box(h1.getBoundingClientRect()),
    rects,
    lines: new Set(rects.map(r => Math.round(r.top))).size,
    fontSize: parseFloat(style.fontSize),
    whiteSpace: style.whiteSpace,
    textOverflow: style.textOverflow,
    overflow: style.overflow,
    logoHeight: logo.getBoundingClientRect().height,
    border: parseFloat(getComputedStyle(banner).borderBottomWidth),
  }};
  const past = (r, o, sides) => sides.some(([a, sign]) => sign * (r[a] - o[a]) > 0.5);
  const across = [['left', -1], ['right', 1]], down = [['top', -1], ['bottom', 1]];
  m.problems = [];
  for (const r of rects) {{
    const line = `line ${{JSON.stringify(r)}}`;
    if (past(r, m.h1, across)) m.problems.push(`${{line}} past the title's sides`);
    if (past(r, m.banner, down)) m.problems.push(`${{line}} past the banner`);
    if (m.overflow !== 'visible' && past(r, m.h1, down))
      m.problems.push(`${{line}} clipped by the title`);
  }}
  return m;
}}"""


def logo(m: dict) -> float:
    """The logo's height as kiosk.css sets it on a TV: 70% of the banner's
    `--k-banner` height less its bottom border (box-sizing: border-box)."""
    return 0.7 * (m["kBanner"] - m["border"])


def height(m: dict) -> float:
    return m["banner"]["bottom"] - m["banner"]["top"]


def measured(page, until: str) -> dict:
    """Wait until `until`, a JavaScript condition on MEASURE's `m`, holds,
    or give up after 5 s, then read MEASURE once for the asserts that
    follow to name what differs."""
    try:
        page.wait_for_function(
            f"() => {{ const m = ({MEASURE})(); return {until}; }}", timeout=5000
        )
    except Error:
        pass
    # One read: the wait above has either seen the condition or given up,
    # and this read names what differs.
    return page.evaluate(MEASURE)


def whole_title(page) -> dict:
    """Wait for the title to show whole; fail with what does not."""
    m = measured(page, "m.problems.length === 0")
    assert m["problems"] == [], m
    return m


def name(key: str) -> str:
    """kiosks/now-racing.kiosk -> now-racing"""
    return key.removeprefix("kiosks/").removesuffix(".kiosk")


def racing(event, group: str, heat: int = HEAT):
    """`group`'s 17 racers on 4 lanes, 17 heats, with `heat` racing, so the
    title reads "<group>, Heat 12 of 17". The poll names the class only
    when the event has more than one group (`polls.current_heat`), so
    Tigers is a second."""
    rounds = race_setup(event, groups=((group, HEATS), ("Tigers", 4)))
    scheduled(rounds[group])
    failure = heats.select_heat(
        event, round_id=rounds[group].pk, heat=heat, now_racing=True
    )
    assert failure is None, failure


def write_title(page, title: str):
    page.evaluate(
        "t => { document.querySelector('[data-banner-title]').textContent = t; }",
        title,
    )


def open_titled(page, event, key: str, group: str) -> str:
    """Open `key` with the title "<group>, Heat 12 of 17": the racing pages
    take it from the poll; scale-MPH's own title is fixed, so the test
    writes it there, and no script of that page writes the banner."""
    racing(event, group)
    title = f"{group}, Heat {HEAT} of {HEATS}"
    open_kiosk(page, event, key)
    if key == "kiosks/scale-MPH.kiosk":
        write_title(page, title)
    expect(page.locator("[data-banner-title]")).to_have_text(title)
    return title


PAGES = (
    "kiosks/now-racing.kiosk",
    "kiosks/now-racing-columnar.kiosk",
    "kiosks/scale-MPH.kiosk",
)


def phone_only(page):
    if page.size != "phone":
        pytest.skip("test_wider_windows_keep_the_one_line_title covers the desktop")


@pytest.mark.parametrize("key", PAGES)
def test_a_long_title_shows_whole_in_the_banner_on_a_phone(page, event, key):
    """The long title wraps to two lines at 0.36 of the banner inside the
    banner, which keeps its height."""
    phone_only(page)
    open_titled(page, event, key, LONG)

    m = whole_title(page)

    assert m["lines"] == 2, m
    assert m["fontSize"] == pytest.approx(0.36 * m["kBanner"], abs=0.1), m
    assert height(m) == pytest.approx(m["kBanner"], abs=0.5), m
    shoot(page, f"kiosk-banner-{name(key)}")


def test_scale_mph_shows_its_own_title_whole_on_a_phone(page, event):
    """ "Scale Miles Per Hour", which the master branch cut to "Scale Miles
    Per H..." (#256)."""
    phone_only(page)
    open_kiosk(page, event, "kiosks/scale-MPH.kiosk")
    expect(page.locator("[data-banner-title]")).to_have_text("Scale Miles Per Hour")

    whole_title(page)


def test_a_one_line_title_keeps_the_banner_height_on_a_phone(page, event):
    """A short title leaves the banner at `--k-banner`, a minimum on a
    phone, as on a TV."""
    phone_only(page)
    open_kiosk(page, event, "kiosks/scale-MPH.kiosk")
    write_title(page, "Racing")

    m = whole_title(page)

    assert m["lines"] == 1, m
    assert height(m) == pytest.approx(m["kBanner"], abs=0.5), m


@pytest.mark.parametrize(
    "key, group",
    (
        ("kiosks/now-racing.kiosk", LONGER),
        ("kiosks/now-racing.kiosk", UNBREAKABLE),
        ("kiosks/now-racing-columnar.kiosk", LONGER),
    ),
)
def test_a_title_of_three_lines_grows_the_banner_on_a_phone(page, event, key, group):
    """A title that needs three or more lines grows the banner, the logo
    keeps its size (`logo()`), and the lanes below, rows or columns, still
    end inside the page. The unbreakable name breaks inside the title's
    box. The columnar layouts share columnar.html and kiosk.css's
    `.k-column`, so the reversed one is not run again."""
    phone_only(page)
    open_titled(page, event, key, group)

    m = whole_title(page)

    assert m["lines"] >= 3, m
    assert height(m) > m["kBanner"] + 1, m
    assert m["logoHeight"] == pytest.approx(logo(m), abs=0.5), m
    page.wait_for_function(
        """() => {
          const foot = document.querySelector('.k-main').getBoundingClientRect().bottom;
          return [...document.querySelectorAll('[data-now-racing] [data-lane]')]
            .every(lane => lane.getBoundingClientRect().bottom <= foot + 0.5);
        }""",
        timeout=5000,
    )
    shoot(page, f"kiosk-banner-grows-{name(key)}-{len(group)}")


@pytest.mark.parametrize(
    "window",
    (
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
        {"width": 1024, "height": 768},
        {"width": 641, "height": 844},
    ),
    ids=("tv", "laptop", "projector", "641px"),
)
def test_wider_windows_keep_the_one_line_title(page, event, window):
    """At TV, laptop and projector sizes, and 1 px past the phone's
    640 px, the banner is as before #272: its height is `--k-banner`, the
    title one line at 0.48 of it, cut with an ellipsis past its box, and
    the logo `logo()` high."""
    if page.size != "desktop":
        pytest.skip("the phone wraps (test_a_long_title_shows_whole_...)")
    page.set_viewport_size(window)
    open_titled(page, event, "kiosks/now-racing.kiosk", LONGER)

    m = measured(
        page,
        "m.whiteSpace === 'nowrap' && m.lines === 1"
        " && Math.abs(m.banner.bottom - m.banner.top - m.kBanner) <= 0.5",
    )

    assert m["whiteSpace"] == "nowrap", m
    assert m["textOverflow"] == "ellipsis", m
    assert m["lines"] == 1, m
    assert m["fontSize"] == pytest.approx(0.48 * m["kBanner"], abs=0.1), m
    assert height(m) == pytest.approx(m["kBanner"], abs=0.5), m
    assert m["logoHeight"] == pytest.approx(logo(m), abs=0.5), m


# Each lane's photo and names box, against the lane's own box.
LANE_FIT = """() => [...document.querySelectorAll('[data-now-racing] [data-lane]')]
  .map(lane => {
    const l = lane.getBoundingClientRect();
    const img = lane.querySelector('.k-who img');
    const p = img ? img.getBoundingClientRect() : null;
    const n = lane.querySelector('.k-names').getBoundingClientRect();
    return {lane: l.height, photo: p ? [p.top - l.top, l.bottom - p.bottom] : null,
            names: [n.top - l.top, l.bottom - n.bottom]};
  })"""


def test_a_title_that_grows_with_the_same_lineup_keeps_the_lanes_fitted(
    page,
    event,
    long_names,  # noqa: F811
):
    """Lions' name grows while its heat races test_kiosk_names' four long
    names with headshots, so the title goes from 1 line to 3 or more with
    no new lineup, and `fit()`, which runs only for a new lineup, a photo
    or a resize, does not run. The rows are `minmax(0, 1fr)` and shrink
    with the board, and each lane's names (test_kiosk_names.MISFITS) and
    photo still fit inside it."""
    phone_only(page)
    open_rows(page, event)
    assert_fits(page)
    before = whole_title(page)
    assert before["lines"] == 1, before

    Group.objects.filter(event=event, name="Lions").update(name=LONGER)

    expect(page.locator("[data-banner-title]")).to_have_text(
        f"{LONGER}, Heat 1 of 4", timeout=8000
    )
    after = whole_title(page)
    assert after["lines"] >= 3, after
    assert height(after) - height(before) > 20, (before, after)
    assert_fits(page)
    # One read: the title is in (expect above) and assert_fits waited for
    # the names; this reads each lane's photo and names box to report.
    lanes = page.evaluate(LANE_FIT)
    for lane in lanes:
        assert lane["photo"] is not None and min(lane["photo"]) >= -0.5, lanes
        assert min(lane["names"]) >= -0.5, lanes
    shoot(page, "kiosk-banner-grows-in-place")


def test_the_phone_rules_hold_at_640_px(page, event):
    """640 px wide is the phone's widest (kiosk.css's `max-width: 640px`):
    the title wraps there at 0.36 of the banner. 641 px is in
    test_wider_windows_keep_the_one_line_title."""
    phone_only(page)
    page.set_viewport_size({"width": 640, "height": 844})
    open_titled(page, event, "kiosks/now-racing.kiosk", LONG)

    m = whole_title(page)

    assert m["whiteSpace"] == "normal", m
    assert m["fontSize"] == pytest.approx(0.36 * m["kBanner"], abs=0.1), m
