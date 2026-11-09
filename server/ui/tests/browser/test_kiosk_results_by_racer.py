"""The results-by-racer kiosk (#247): its text reads on its cards and on the
kiosk's own background, and every round's cards come on screen.

The page holds #49's rounds, whose cards are app.css's `bg-surface`,
inside the kiosk, whose `body.kiosk-page` sets the light `--k-ink`
(kiosk.css). Text with no color of its own took that ink onto the white
cards of the light theme. The tests pause Playwright's clock before the
page loads, so the list's glide (kiosk-results.js, a pixel every 40 ms)
moves only when a test runs the clock."""

from datetime import datetime

import pytest
from playwright.sync_api import Error, expect

from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_pages import day, open_kiosk  # noqa: F401
from ui.tests.race import run_heat

PAUSED_AT = datetime(2026, 11, 9, 12, 0, 0)
WCAG_AA = 4.5

# The text of the page whose contrast is below WCAG AA (4.5:1) against what
# it is drawn on: the first ancestor with an opaque background, layers with
# alpha composited over it, or, through to the body, both ends of the
# kiosk's gradient (`--k-bg`, `--k-bg-2`). `checked` counts the racer
# headers (car and name) and the heat times among the elements read, so a
# selector that finds none of them fails instead of passing on nothing.
LOW_CONTRAST = """(minimum) => {
  const parse = (text) => {
    const m = /^rgba?\\(([^)]*)\\)$/.exec(text.trim());
    if (!m) return null;
    const p = m[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
    return {r: p[0], g: p[1], b: p[2], a: p.length > 3 ? p[3] : 1};
  };
  const hex = (text) => {
    const h = text.trim().replace('#', '');
    return {r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16),
            b: parseInt(h.slice(4, 6), 16), a: 1};
  };
  const over = (top, under) => ({
    r: top.r * top.a + under.r * (1 - top.a),
    g: top.g * top.a + under.g * (1 - top.a),
    b: top.b * top.a + under.b * (1 - top.a), a: 1});
  const lum = (c) => {
    const ch = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
  };
  const ratio = (a, b) => {
    const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
    return (hi + 0.05) / (lo + 0.05);
  };
  const root = getComputedStyle(document.documentElement);
  const kiosk = ['--k-bg', '--k-bg-2'].map((name) => hex(root.getPropertyValue(name)));
  const grounds = (el) => {
    const layers = [];
    for (let at = el; at && at !== document.body; at = at.parentElement) {
      const bg = parse(getComputedStyle(at).backgroundColor);
      if (bg === null) return null;
      if (bg.a === 0) continue;
      layers.push(bg);
      if (bg.a === 1) break;
    }
    const opaque = layers.length && layers[layers.length - 1].a === 1;
    const bases = opaque ? [layers.pop()] : kiosk;
    const flat = (base) => layers.reduceRight((under, top) => over(top, under), base);
    return bases.map(flat);
  };
  const low = [];
  let checked = 0;
  for (const el of document.querySelectorAll('[data-results] *')) {
    const text = (n) => n.nodeType === 3 && n.textContent.trim();
    const own = [...el.childNodes].some(text);
    if (!own || !el.getClientRects().length) continue;
    if (el.matches('[data-car], [data-name], [data-shown]')) checked += 1;
    const color = getComputedStyle(el).color;
    const ink = parse(color);
    const under = grounds(el);
    const said = el.textContent.trim().slice(0, 40);
    if (ink === null || under === null) {
      low.push({text: said, color, unparsed: true});
      continue;
    }
    for (const bg of under) {
      const r = ratio(over(ink, bg), bg);
      if (r < minimum) {
        const background = `rgb(${bg.r}, ${bg.g}, ${bg.b})`;
        low.push({text: said, color, background, ratio: Math.round(r * 100) / 100});
        break;
      }
    }
  }
  return {low, checked};
}"""


def open_paused(page, event):
    """The kiosk with two heats of Lions' results, Tigers' round with none,
    on a clock paused before the page loads."""
    run_heat(event)
    run_heat(event)
    page.clock.install(time=PAUSED_AT)
    page.clock.pause_at(PAUSED_AT)
    open_kiosk(page, event, "kiosks/results-by-racer.kiosk")
    expect(page.locator("[data-result] [data-shown]").first).not_to_be_empty()


def assert_readable(page):
    """Wait for no text below WCAG AA; on a timeout, fail with what is low."""
    try:
        page.wait_for_function(
            f"m => ({LOW_CONTRAST})(m).low.length === 0", arg=WCAG_AA, timeout=3000
        )
    except Error:
        pass
    # One read: the wait above has either seen it pass or given up, and this
    # read names what is low.
    found = page.evaluate(LOW_CONTRAST, WCAG_AA)
    shown = page.locator("[data-results] :is([data-car], [data-name], [data-shown])")
    assert found["checked"] == shown.count() > 0, found
    assert found["low"] == [], found["low"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_racer_headers_and_heat_times_read_on_their_cards(page, event, day, scheme):  # noqa: F811
    """Each racer header (car and name), heat time, heat label, round
    heading, "Now racing" pill and "No heats yet" reads at 4.5:1 or more
    against what it sits on, at both sizes and in both color schemes:
    app.css's cards follow the device setting (D20), the kiosk's background
    does not. The light shots are the page's evidence (D75)."""
    page.emulate_media(color_scheme=scheme)
    open_paused(page, event)
    expect(page.locator("[data-no-heats]")).to_have_text("No heats yet.")
    expect(page.locator("[data-round] h2 [data-state='ok']")).to_have_text("Now racing")
    expect(page.locator("[data-round] h2")).to_contain_text(
        ["Lions, Round 1", "Tigers, Round 1"]
    )
    expect(page.locator("[data-result] .text-ink-muted").first).to_have_text(
        "Heat 1 · Lane 1"
    )

    assert_readable(page)
    if scheme == "light":
        shoot(page, "kiosk-results-by-racer")


# Every round heading, racer card and "No heats yet" line inside the kiosk's
# results box now, keyed by round and item, added to what earlier calls saw.
SEEN = """() => {
  const box = document.querySelector('[data-results]').getBoundingClientRect();
  window.seen = window.seen || {};
  const all = [];
  for (const round of document.querySelectorAll('[data-round]')) {
    const items = round.querySelectorAll(':scope > h2, [data-racer], [data-no-heats]');
    items.forEach((el, i) => {
      const key = `${round.dataset.round}:${i}`;
      const r = el.getBoundingClientRect();
      all.push(key);
      if (r.top >= box.top - 1 && r.bottom <= box.bottom + 1) window.seen[key] = true;
    });
  }
  return {all, seen: Object.keys(window.seen)};
}"""


def test_every_rounds_cards_come_on_screen(page, event, day):  # noqa: F811
    """At 390x844 Lions' four cards and Tigers' round are taller than the
    screen, which doesn't scroll. The list glides up a pixel every 40 ms and
    starts over (kiosk-results.js), where PHP's kiosk moves it up a row every
    2 s (results-by-racer-scrolling.js:71), so in turn every heading and card
    is wholly on screen. The clock runs 400 ms (10 px) at a time, through
    the overflow and 2 s more."""
    open_paused(page, event)
    expect(page.locator("[data-round]")).to_have_count(2)
    # One read: the clock is paused, so the list stands still.
    overflow = page.evaluate(
        "(() => { const r = document.querySelector('[data-results]');"
        " return r.querySelector('[data-rounds]').scrollHeight - r.clientHeight; })()"
    )
    if page.size == "phone":
        assert overflow > 0, "the rounds fit the phone; the test checks no glide"
    found = page.evaluate(SEEN)
    for _ in range(max(overflow, 0) // 10 + 5):
        if set(found["seen"]) == set(found["all"]):
            break
        page.clock.run_for(400)
        # One read per step: the clock is paused between steps.
        found = page.evaluate(SEEN)
    assert sorted(found["seen"]) == sorted(found["all"])
    assert len(found["all"]) == 1 + 4 + 2  # Lions' heading and cards, Tigers'
