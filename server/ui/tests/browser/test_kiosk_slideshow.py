"""The slideshow kiosk shows one slide at a time (#253).

PHP cuts from one slide to the next: `refresh_page()` removes `div.current`
and shows `div.next` (slideshow.js:51-52), which slideshow.css:56-59 keeps
hidden until then. Django crossfades for 1.2 s instead (kiosk.css
`.k-slide` and `.k-slide.k-leaving`, D10, ADR-10): the new slide fades in
while the old one fades out, and the old one is removed when its fade ends.
After that only the new slide shows, as in PHP.

The slides have different shapes, so a slide left uncovered would show past
the edges of the one on top: the title slide, 2000x520, fills the width;
the narrow slide, 300x900 and drawn here, leaves room on both sides
(`NARROW`); then two racers with portrait headshots, whose captions would
print over each other.

The page's own 10 s timer is not waited for: `kioskNudge()` runs the same
`poll()` that timer runs (kiosk.js `run()`), as
`test_the_slideshow_asks_slide_next_at_the_nudge` does, so the test does not
wait 10 s per slide (the ticket's Risks)."""

import shutil

import pytest
from PIL import Image
from playwright.sync_api import expect

from core.models import RaceSettings
from roster.models import Racer
from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_pages import (  # noqa: F401
    HEADSHOTS,
    day,
    nudge,
    open_kiosk,
)

# Each fade is 1200 ms (kiosk.css `.k-slide`, `.k-slide.k-leaving`). A wait
# for part of a fade starts before the nudge's reply, so it covers that
# reply and up to 1200 ms of fade: 5000 ms leaves the reply 3800 ms.
FADE_MS = 5000
# The old slide is removed at its fade's `animationend`. Once no animation
# runs, SLACK_MS is how late that removal may still come; the should-pass
# mutation removes it 850 ms late (SLACK_MS - 150).
SLACK_MS = 1000

SLIDES = "[data-slides] > .k-slide"

MARK_OLD = """() => {
  const slides = document.querySelectorAll('[data-slides] > .k-slide');
  if (slides.length !== 1) throw new Error(slides.length + ' slides at the change');
  slides[0].dataset.old = '1';
}"""

# The old slide, still in the page, part way through its fade out, and no
# animation running inside a slide.
OLD_FADING = """() => {
  const old = document.querySelector('[data-old]');
  if (!old) return false;
  const inside = document.getAnimations().filter(a => a.playState === 'running' &&
    !a.effect.target.matches('[data-slides] > .k-slide') &&
    a.effect.target.closest('[data-slides]'));
  if (inside.length) {
    throw new Error('animated inside a slide: ' + inside[0].effect.target.outerHTML);
  }
  const opacity = parseFloat(getComputedStyle(old).opacity);
  return opacity > 0.05 && opacity < 0.95;
}"""

# The old slide is gone and one slide is left, and shows.
ONE_SLIDE = """() => {
  const slides = document.querySelectorAll('[data-slides] > .k-slide');
  return !document.querySelector('[data-old]') && slides.length === 1 &&
    slides[0].checkVisibility({opacityProperty: true, visibilityProperty: true});
}"""

# The loaded photo leaves at least 100 px of the slides' box beside it.
NARROW = """() => {
  const img = document.querySelector('[data-slides] > .k-slide img');
  const box = document.querySelector('[data-slides]').getBoundingClientRect();
  return img.complete && img.naturalWidth > 0 &&
    img.getBoundingClientRect().width < box.width - 100;
}"""

# The new slide part way through its fade in.
NEW_FADING_IN = """() => {
  const slides = document.querySelectorAll('[data-slides] > .k-slide:not([data-old])');
  if (slides.length !== 1) return false;
  const opacity = parseFloat(getComputedStyle(slides[0]).opacity);
  return opacity > 0.2 && opacity < 0.8;
}"""

# The slide marked "2" is leaving, and its opacity has started to fall.
SECOND_LEAVING = """() => {
  const old = document.querySelector('[data-old="2"]');
  return !!old && old.classList.contains('k-leaving') &&
    parseFloat(getComputedStyle(old).opacity) < 0.95;
}"""

# A pause takes effect at the next frame (`ready`), so each hold awaits it
# before it reads the opacity it holds.
# Hold the slide fading in at its opacity, mark it "2", and record its
# opacity as the class that starts its fade out is added.
HOLD_SECOND = """async () => {
  const slide = document.querySelector('[data-slides] > .k-slide:not([data-old])');
  const animations = slide.getAnimations();
  animations.forEach(a => a.pause());
  await Promise.all(animations.map(a => a.ready));
  slide.dataset.old = '2';
  new MutationObserver((records, observer) => {
    if (!slide.classList.contains('k-leaving')) return;
    window.leftAt = parseFloat(getComputedStyle(slide).opacity);
    observer.disconnect();
  }).observe(slide, {attributes: true, attributeFilter: ['class']});
  return parseFloat(getComputedStyle(slide).opacity);
}"""

SECOND_OPACITY = """() =>
  parseFloat(getComputedStyle(document.querySelector('[data-old="2"]')).opacity)"""

# Once slide "2" has faded to 60% of the opacity it left at, far enough
# that a fade started again from there would show a step, hold it there.
# It rejects after FADE_MS, so the evaluate is bounded.
PAUSE_SECOND = """([reached, ms]) => new Promise((resolve, reject) => {
  const slide = document.querySelector('[data-old="2"]');
  const deadline = performance.now() + ms;
  const check = async () => {
    if (performance.now() > deadline) return reject(new Error('no fade in ' + ms));
    if (parseFloat(getComputedStyle(slide).opacity) >= reached * 0.6) {
      return requestAnimationFrame(check);
    }
    const animations = slide.getAnimations();
    animations.forEach(a => a.pause());
    await Promise.all(animations.map(a => a.ready));
    resolve(parseFloat(getComputedStyle(slide).opacity));
  };
  check();
})"""

PLAY_SECOND = """() =>
  document.querySelector('[data-old="2"]').getAnimations().forEach(a => a.play())"""

SETTLED = "document.getAnimations().every(a => a.playState !== 'running')"


@pytest.fixture
def slideshow(settings, tmp_path, page, event, day):  # noqa: F811
    """The slideshow kiosk at rest on its title slide, with the narrow slide
    and two racers' headshots to come."""
    settings.DATA_DIR = tmp_path / "data"
    settings.CONFIG_DIR = tmp_path / "config"
    slides = tmp_path / "config" / "slides"
    slides.mkdir(parents=True)
    Image.new("RGB", (300, 900), (200, 40, 40)).save(slides / "narrow.png")
    (tmp_path / "racers").mkdir()
    shutil.copy(HEADSHOTS / "Cub-0033.jpg", tmp_path / "racers")
    shutil.copy(HEADSHOTS / "head-A.jpg", tmp_path / "racers")
    RaceSettings.objects.filter(event=event).update(
        photo_directory=str(tmp_path / "racers")
    )
    Racer.objects.filter(event=event, first_name="Lions1").update(
        headshot="Cub-0033.jpg"
    )
    Racer.objects.filter(event=event, first_name="Lions2").update(headshot="head-A.jpg")
    open_kiosk(page, event, "kiosks/slideshow.kiosk")
    expect(page.locator(SLIDES).locator("img")).to_have_attribute(
        "src", "/slide.php/title"
    )
    page.wait_for_function(SETTLED, timeout=FADE_MS)
    return page.locator(SLIDES)


def change_slide(page):
    """Nudge to the next slide, see the old one fade out, then see it gone."""
    page.evaluate(MARK_OLD)
    nudge(page, "query=slide.next")
    page.wait_for_function(OLD_FADING, timeout=FADE_MS)
    page.wait_for_function(SETTLED, timeout=FADE_MS)
    page.wait_for_function(ONE_SLIDE, timeout=SLACK_MS)


def test_the_slideshow_crossfades_to_one_slide(page, slideshow):
    change_slide(page)
    expect(slideshow.locator("img")).to_have_attribute("src", "slide.php/narrow.png")
    page.wait_for_function(NARROW, timeout=FADE_MS)
    shoot(page, "kiosk-slideshow-narrow")

    change_slide(page)
    expect(slideshow.locator(".k-caption")).to_have_text("101: Lions1 Racer")

    change_slide(page)
    expect(slideshow.locator(".k-caption")).to_have_text("102: Lions2 Racer")
    shoot(page, "kiosk-slideshow-racer")


def test_a_slide_that_leaves_while_fading_in_fades_out_too(page, slideshow):
    """Changes that come while the last one is still fading, such as a nudge
    just after the 10 s timer. The slide that was fading in fades out from
    the opacity it had reached, with no jump to full, and is removed; a
    third change leaves a slide already fading out on its course; and the
    last slide is left. A slide removed as soon as it starts to leave fails
    `SECOND_LEAVING`.

    The test pauses a slide's animation to hold it at one opacity across a
    nudge's reply, and plays it again after."""
    page.evaluate(MARK_OLD)
    nudge(page, "query=slide.next")
    page.wait_for_function(NEW_FADING_IN, timeout=FADE_MS)
    reached = page.evaluate(HOLD_SECOND)
    nudge(page, "query=slide.next")
    page.wait_for_function(SECOND_LEAVING, timeout=FADE_MS)
    # one read: the opacity the observer saw as the class was added.
    assert page.evaluate("window.leftAt") == pytest.approx(reached, abs=0.02)

    held = page.evaluate(PAUSE_SECOND, [reached, FADE_MS])
    nudge(page, "query=slide.next")
    expect(slideshow.locator(".k-caption")).to_have_count(2)
    # one read: the slide's animation is paused, so its opacity changes
    # only if the third change touched it.
    assert page.evaluate(SECOND_OPACITY) == pytest.approx(held, abs=0.005)
    page.evaluate(PLAY_SECOND)

    page.wait_for_function(SETTLED, timeout=FADE_MS)
    page.wait_for_function(ONE_SLIDE, timeout=SLACK_MS)
    expect(slideshow.locator(".k-caption")).to_have_text("102: Lions2 Racer")
