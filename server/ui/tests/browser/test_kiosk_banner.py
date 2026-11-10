"""The kiosk banner's title shows whole on a phone (#272).

At 640 px wide or less, kiosk.css lets `.k-banner h1` wrap, at 0.36 of
the banner's height instead of 0.48, and the banner's height becomes a
minimum, so a title of three or more lines grows the banner where it was
cut with an ellipsis (D10, ADR-10, the lead's choice B on #272). Wider
windows keep the one-line title at 0.48 of the banner, cut with an
ellipsis when it runs past its box.

PHP at 390x844 (php-scratch, #272's PR) wraps the title on every page.
now-racing-adjust-fonts.js:34-40 also shrinks it by 0.9 while its box is
taller than 60 px, so "White's Wolves, Heat 12 of 17" ends on 2 lines at
22 px in the 64 px banner. The columnar pages and scale-MPH do not shrink
it, and their wrapped lines fall below the banner (global.css:25-34, an
absolute box with no height).

The long title, "White's Wolves, Heat 12 of 17", is 29 characters. At the
desktop font (0.48 x 67.52 px = 32.4 px) it ran past the about 266 px the
phone leaves it (390 px less the padding, the logo, the gaps and the
kiosk's name), and the master branch cut it: each phone test of a long
title here failed there (#272's PR).

Whether the title's glyphs stand taller than its line depends on the font.
Only on such a font (Noto Sans here) does
`test_a_title_of_three_lines_grows_the_banner_on_a_phone` fail without the
banner's padding, and every phone test without the title's
`overflow: visible`."""

import pytest
from playwright.sync_api import Error, expect

from racing.services import heats
from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_pages import open_kiosk
from ui.tests.race import race_setup, scheduled

LONG = "White's Wolves"
# Three or more lines at the phone's font: 46 characters in about 266 px.
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
# rect is its glyphs' height, which on some fonts is taller than the line
# (Noto Sans: about 1.36 em).
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


def whole_title(page) -> dict:
    """Wait for the title to show whole; on a timeout, fail with what does
    not."""
    try:
        page.wait_for_function(f"({MEASURE})().problems.length === 0", timeout=5000)
    except Error:
        pass
    # One read: the wait above has either seen the title whole or given up,
    # and this read names what is not.
    m = page.evaluate(MEASURE)
    assert m["problems"] == [], m
    return m


def racing(event, group: str):
    """`group`'s 17 racers on 4 lanes, 17 heats, with heat 12 racing, so the
    title reads "<group>, Heat 12 of 17". The poll names the class only
    when the event has more than one group (`polls.current_heat`), so
    Tigers is a second."""
    rounds = race_setup(event, groups=((group, HEATS), ("Tigers", 4)))
    scheduled(rounds[group])
    failure = heats.select_heat(
        event, round_id=rounds[group].pk, heat=HEAT, now_racing=True
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
    """The 29-character title wraps to two lines inside the banner, which
    keeps its height: at 0.36 x 67.52 = 24.3 px and line-height 1.2, two
    lines take 58.3 px of the 59.1 px the banner leaves inside its 0.3vh
    padding and 0.4vh border (67.52 - 2 x 2.53 - 3.38)."""
    phone_only(page)
    open_titled(page, event, key, LONG)

    m = whole_title(page)

    assert m["lines"] == 2, m
    assert m["fontSize"] == pytest.approx(0.36 * m["kBanner"], abs=0.1), m
    assert m["banner"]["bottom"] - m["banner"]["top"] == pytest.approx(
        m["kBanner"], abs=0.5
    ), m
    shoot(page, f"kiosk-banner-{key.split('/')[1].split('.')[0]}")


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
    assert m["banner"]["bottom"] - m["banner"]["top"] == pytest.approx(
        m["kBanner"], abs=0.5
    ), m


@pytest.mark.parametrize("group", (LONGER, UNBREAKABLE))
def test_a_title_of_three_lines_grows_the_banner_on_a_phone(page, event, group):
    """A title that needs three or more lines grows the banner, the logo
    keeps its size (`logo()`), and the lanes below still fit the
    page. The unbreakable name breaks inside the title's box."""
    phone_only(page)
    open_titled(page, event, "kiosks/now-racing.kiosk", group)

    m = whole_title(page)

    assert m["lines"] >= 3, m
    assert m["banner"]["bottom"] - m["banner"]["top"] > m["kBanner"] + 1, m
    assert m["logoHeight"] == pytest.approx(logo(m), abs=0.5), m
    page.wait_for_function(
        """() => {
          const foot = document.querySelector('.k-main').getBoundingClientRect().bottom;
          return [...document.querySelectorAll('[data-now-racing] [data-lane]')]
            .every(lane => lane.getBoundingClientRect().bottom <= foot + 0.5);
        }""",
        timeout=5000,
    )
    shoot(page, f"kiosk-banner-grows-{len(group)}")


@pytest.mark.parametrize(
    "window",
    (
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
        {"width": 1024, "height": 768},
    ),
    ids=("tv", "laptop", "projector"),
)
def test_wider_windows_keep_the_one_line_title(page, event, window):
    """At TV, laptop and projector sizes the banner is as before #272: its
    height is `--k-banner`, the title one line at 0.48 of it, cut with an
    ellipsis past its box, and the logo `logo()` high."""
    if page.size != "desktop":
        pytest.skip("the phone wraps (test_a_long_title_shows_whole_...)")
    page.set_viewport_size(window)
    open_titled(page, event, "kiosks/now-racing.kiosk", LONGER)

    try:
        page.wait_for_function(
            f"""() => {{
              const m = ({MEASURE})();
              return m.whiteSpace === 'nowrap' && m.lines === 1
                && Math.abs(m.banner.bottom - m.banner.top - m.kBanner) <= 0.5;
            }}""",
            timeout=5000,
        )
    except Error:
        pass
    # One read: the wait above has either seen the one-line banner or given
    # up, and the asserts below name what differs.
    m = page.evaluate(MEASURE)

    assert m["whiteSpace"] == "nowrap", m
    assert m["lines"] == 1, m
    assert m["fontSize"] == pytest.approx(0.48 * m["kBanner"], abs=0.1), m
    assert m["banner"]["bottom"] - m["banner"]["top"] == pytest.approx(
        m["kBanner"], abs=0.5
    ), m
    assert m["logoHeight"] == pytest.approx(logo(m), abs=0.5), m
    assert (
        page.evaluate(
            "getComputedStyle(document.querySelector('.k-banner h1')).textOverflow"
        )
        == "ellipsis"
    )
