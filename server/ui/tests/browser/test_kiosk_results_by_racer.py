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

from ui.tests.browser.conftest import assert_accessible, shoot
from ui.tests.browser.test_kiosk_pages import day, open_kiosk  # noqa: F401
from ui.tests.race import run_heat

PAUSED_AT = datetime(2026, 11, 9, 12, 0, 0)
WCAG_AA = 4.5

# The text of the results that sits on no opaque background of its own, so
# on the kiosk's gradient (the round headings, "No heats yet"), whose
# contrast is below WCAG AA (4.5:1) against either end of the gradient
# (`--k-bg`, `--k-bg-2`). axe leaves this text "incomplete" with the
# message key `bgGradient`, and so passes it however dark it is
# (test_axe_passes_dark_text_on_the_gradient_and_waits_on_the_clock).
# `checked` counts the elements read, so a selector that finds none fails.
LOW_ON_GRADIENT = """(minimum) => {
  const parseRgb = (text) => {
    const match = /^rgba?\\(([^)]*)\\)$/.exec(text.trim());
    if (!match) return null;
    const parts = match[1].split(/[\\s,\\/]+/).filter(Boolean).map(Number);
    return {red: parts[0], green: parts[1], blue: parts[2],
            alpha: parts.length > 3 ? parts[3] : 1};
  };
  const parseHex = (text) => {
    const digits = text.trim().replace('#', '');
    const channel = (at) => parseInt(digits.slice(at, at + 2), 16);
    return {red: channel(0), green: channel(2), blue: channel(4), alpha: 1};
  };
  const luminance = (color) => {
    const linear = (value) => {
      value /= 255;
      return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * linear(color.red) + 0.7152 * linear(color.green)
      + 0.0722 * linear(color.blue);
  };
  const contrast = (one, other) => {
    const lighter = Math.max(luminance(one), luminance(other));
    const darker = Math.min(luminance(one), luminance(other));
    return (lighter + 0.05) / (darker + 0.05);
  };
  const onGradient = (element) => {
    for (let at = element; at !== document.body; at = at.parentElement) {
      const background = parseRgb(getComputedStyle(at).backgroundColor);
      if (background === null || background.alpha > 0) return false;
    }
    return true;
  };
  const root = getComputedStyle(document.documentElement);
  const end = (name) => parseHex(root.getPropertyValue(name));
  const ends = [end('--k-bg'), end('--k-bg-2')];
  const low = [];
  let checked = 0;
  for (const element of document.querySelectorAll('[data-results] *')) {
    const isText = (node) => node.nodeType === 3 && node.textContent.trim();
    if (![...element.childNodes].some(isText) || !onGradient(element)) continue;
    checked += 1;
    const color = getComputedStyle(element).color;
    const ink = parseRgb(color);
    const text = element.textContent.trim().slice(0, 40);
    if (ink === null || ink.alpha < 1) {
      low.push({text, color, unparsed: true});
      continue;
    }
    for (const ground of ends) {
      const ratio = contrast(ink, ground);
      if (ratio < minimum) {
        const background = `rgb(${ground.red}, ${ground.green}, ${ground.blue})`;
        low.push({text, color, background, ratio: Math.round(ratio * 100) / 100});
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


def assert_readable_on_gradient(page):
    """Wait for no text on the gradient below WCAG AA; on a timeout, fail
    with what is low."""
    try:
        page.wait_for_function(
            f"m => ({LOW_ON_GRADIENT})(m).low.length === 0", arg=WCAG_AA, timeout=3000
        )
    except Error:
        pass
    # One read: the wait above has either seen it pass or given up, and this
    # read names what is low.
    found = page.evaluate(LOW_ON_GRADIENT, WCAG_AA)
    on_gradient = page.locator("[data-round] > h2, [data-no-heats]")
    assert found["checked"] == on_gradient.count() == 3, found
    assert found["low"] == [], found["low"]


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_racer_headers_and_heat_times_read_on_their_cards(page, event, day, scheme):  # noqa: F811
    """Each racer header (car and name), heat time, heat label, round
    heading, "Now racing" pill and "No heats yet" reads at 4.5:1 or more
    against what it sits on, at both sizes and in both color schemes:
    app.css's cards follow the device setting (D20), the kiosk's background
    does not. The light shots, taken at the top of the list, are the page's
    evidence (D75).

    axe (`assert_accessible`) judges the cards and the pill, once the
    clock resumes; `LOW_ON_GRADIENT` judges the text on the kiosk's
    gradient, which axe passes however dark it is. The next test shows
    both."""
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
    assert_readable_on_gradient(page)
    if scheme == "light":
        expect(page.locator("[data-rounds]")).to_have_css("transform", "none")
        shoot(page, "kiosk-results-by-racer")

    page.clock.resume()
    assert_accessible(page)


# axe started with no await, so a test can wait on it with a bound.
START_AXE = """tags => {
  window.axeResult = null;
  axe.run(document, {runOnly: {type: 'tag', values: tags}})
    .then((result) => { window.axeResult = result; });
}"""


def test_axe_passes_dark_text_on_the_gradient_and_waits_on_the_clock(page, event, day):  # noqa: F811
    """Why the test above keeps its own check for the text on the gradient,
    and resumes the clock before axe. With "No heats yet" in app.css's
    light-theme muted ink, #4b5563 (2.36:1 on `--k-bg`), axe finds no
    contrast violation, where `LOW_ON_GRADIENT` reports it. axe waits on
    page timers between rules, so on the paused clock it has not finished
    after 2 s; it finishes once the clock resumes."""
    from ui.tests.browser.conftest import AXE, AXE_TAGS

    open_paused(page, event)
    page.add_style_tag(content=".k-results [data-no-heats] { color: #4b5563; }")
    expect(page.locator("[data-no-heats]")).to_have_css("color", "rgb(75, 85, 99)")
    # One read: the paused clock holds the page still.
    found = page.evaluate(LOW_ON_GRADIENT, WCAG_AA)
    assert [item["text"] for item in found["low"]] == ["No heats yet."]

    page.add_script_tag(path=str(AXE))
    page.evaluate(START_AXE, AXE_TAGS)
    page.wait_for_timeout(2000)
    # One read: the point is that axe has not finished on the paused clock.
    assert page.evaluate("window.axeResult === null")
    page.clock.resume()
    page.wait_for_function("window.axeResult !== null", timeout=10_000)
    result = page.evaluate("window.axeResult")
    flagged = [
        node["html"]
        for rule in result["violations"]
        if rule["id"] == "color-contrast"
        for node in rule["nodes"]
    ]
    assert flagged == []


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
    2 s (results-by-racer-scrolling.js:71), a look the rebuild changes (D10),
    so in turn every heading and card is wholly on screen. The clock runs
    400 ms (10 px) at a time, through the overflow and 2 s more."""
    open_paused(page, event)
    expect(page.locator("[data-round]")).to_have_count(2)
    # One read: the clock is paused, so the list stands still.
    overflow = page.evaluate(
        "(() => { const r = document.querySelector('[data-results]');"
        " return r.querySelector('[data-rounds]').scrollHeight - r.clientHeight; })()"
    )
    if page.size == "phone":
        assert overflow > 0, "the rounds fit the phone; the test checks no glide"
    # One read: the clock is paused, so nothing changes the page's size.
    assert page.evaluate(
        "document.documentElement.scrollHeight <= innerHeight"
        " && getComputedStyle(document.body).overflowY === 'hidden'"
    )
    found = page.evaluate(SEEN)
    for _ in range(max(overflow, 0) // 10 + 5):
        if set(found["seen"]) == set(found["all"]):
            break
        page.clock.run_for(400)
        # One read per step: the clock is paused between steps.
        found = page.evaluate(SEEN)
    assert sorted(found["seen"]) == sorted(found["all"])
    assert len(found["all"]) == 1 + 4 + 2  # Lions' heading and cards, Tigers'
