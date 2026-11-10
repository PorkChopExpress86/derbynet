"""The scale-MPH kiosk's car between drives (#252).

PHP's `animate_car()` (`website/kiosks/scale-MPH.kiosk:11-18`) drives the
car from `left: 45vw` to `5vw`, then to `100vw`, where jQuery leaves it
during the 10 s `setTimeout` before the next drive, which sets it back to
`45vw` as it starts. So between drives the car is off the screen.

The tests pause Playwright's clock before the page loads, so the 10 s wait
(a `setTimeout`) passes only when a test runs the clock. The drive itself
is a Web Animation on the document timeline, which the clock does not
control, so it plays in real time (2.8 s) and the test waits on its
`finish` event, not on a sleep."""

from datetime import datetime

from playwright.sync_api import expect

from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_pages import day, open_kiosk  # noqa: F401

PAUSED_AT = datetime(2026, 11, 9, 12, 0, 0)
WAIT_MS = 10_000  # kiosk-simple.js drive(): the setTimeout after a drive
SLACK_MS = 1000

# Records the clock's time at each drive's start (each `animate()` on the
# car) and at each drive's `finish` event. The page sets `onfinish` after
# this listener is added, and both run in the one dispatch of that event,
# so once `finishes` grows the page's own handler has run too: had it run
# later, its timer would start after the clock runs below, and the next
# drive would come too late for the test.
COUNT_DRIVES = """(() => {
  window.drives = [];
  window.finishes = [];
  const animate = Element.prototype.animate;
  Element.prototype.animate = function (...args) {
    const a = animate.apply(this, args);
    if (this.matches('[data-car]')) {
      window.drives.push(Date.now());
      a.addEventListener('finish', () => window.finishes.push(Date.now()));
    }
    return a;
  };
})()"""

# The car's box lies outside the window, or it is hidden.
OFF_SCREEN = """() => {
  const car = document.querySelector('[data-car]');
  const r = car.getBoundingClientRect();
  return getComputedStyle(car).visibility === 'hidden'
    || r.left >= innerWidth || r.right <= 0
    || r.top >= innerHeight || r.bottom <= 0;
}"""


def open_paused(page, event):
    page.clock.install(time=PAUSED_AT)
    page.clock.pause_at(PAUSED_AT)
    page.add_init_script(COUNT_DRIVES)
    open_kiosk(page, event, "kiosks/scale-MPH.kiosk")
    page.wait_for_function("window.drives.length === 1")


def test_the_car_waits_off_screen_between_drives(page, event, day):  # noqa: F811
    """Once a drive ends the car stays off the screen for the whole wait,
    and the next drive starts `WAIT_MS` after the last one ended, within
    `SLACK_MS`, from `45vw`, as PHP's does.

    The clock runs to `WAIT_MS - SLACK_MS` after the drive's end and the
    car must still be off the screen with no new drive started; it then
    runs `2 * SLACK_MS` more, to `WAIT_MS + SLACK_MS`, and the next drive
    must have started in that window. A timer late or early by less than
    `SLACK_MS` passes (the should-pass mutations, 850 ms either way)."""
    open_paused(page, event)
    page.wait_for_function("window.finishes.length === 1")

    page.wait_for_function(OFF_SCREEN, timeout=5000)
    shoot(page, "kiosk-scale-MPH-between-drives")
    page.clock.run_for(WAIT_MS - SLACK_MS)
    # One read each: the counter records any drive the clock started, and
    # with none started nothing else moves the car.
    assert page.evaluate("window.drives.length") == 1
    assert page.evaluate(OFF_SCREEN)

    page.clock.run_for(2 * SLACK_MS)
    # The timer fires inside run_for(), so the drive has started already.
    page.wait_for_function("window.drives.length === 2", timeout=5000)
    # One read: the times were recorded when each event happened.
    finished, started = page.evaluate("[window.finishes[0], window.drives[1]]")
    assert WAIT_MS - SLACK_MS <= started - finished <= WAIT_MS + SLACK_MS

    page.evaluate(
        "() => { const [a] = document.querySelector('[data-car]').getAnimations()"
        ".filter(a => a.playState === 'running'); a.pause(); a.currentTime = 0; }"
    )
    width = page.viewport_size["width"]
    expect(page.locator("[data-car]")).to_have_css("left", f"{0.45 * width:g}px")
