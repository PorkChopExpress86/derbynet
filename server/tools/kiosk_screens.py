"""Screenshots and recordings of the 15 kiosk pages (#52), against PHP or
Django, for the PR's evidence (D63).

Each page loads by preview, `kiosk.php?page=kiosks/<name>.kiosk`, which
registers no kiosk on either server, at 1920x1080, 1280x800 and 390x844.
The script fails a page that scrolls or clips text, and with `--video`
records each page at 1920x1080 for `--seconds`. The screenshot check runs
once, 2.5 s after load; with `--video` the recording's tab is checked every
second too, because a page draws some of what it shows later (a heat's
times, an award's reveal), and a problem at any point fails the page. A
check that a reload interrupts is tried again once the page loads, and a
page that never stays loaded is reported unchecked (#239,
`ui/tests/test_kiosk_screens_tool.py`).

For the same fixture database on both servers, pass `--fixture`, which runs
`testing/reset-database.sh`, `testing/import-roster.sh` and
`testing/test-den-changes.sh` against the server first, as `server.yml`
does. Then, for example:

    uv run python tools/kiosk_screens.py http://127.0.0.1:8000 \
        --out var/kiosk-django --fixture --video
    uv run python tools/kiosk_screens.py http://127.0.0.1:8080 \
        --out var/kiosk-php --fixture

Django serves its static files itself only with DERBYNET_DEBUG=1.

The Raspberry Pi recordings are the owner's: run the same command on a
laptop beside the Pi's screen, or record the Pi's own screen while this
script drives the pages with `--headed`.
"""

import argparse
import subprocess
import sys
from pathlib import Path

PAGES = (
    "award-presentations",
    "derbynet",
    "flag",
    "identify",
    "now-racing",
    "now-racing-columnar",
    "now-racing-columnar-reversed",
    "ondeck",
    "please-check-in",
    "qrcode",
    "results-by-racer",
    "scale-MPH",
    "slideshow",
    "standings",
    "welcome",
)
SIZES = {"tv": (1920, 1080), "laptop": (1280, 800), "phone": (390, 844)}
REPO = Path(__file__).resolve().parents[2]
FIXTURE = ("reset-database.sh", "import-roster.sh", "test-den-changes.sh")
PARAMETERS = {"qrcode": '{"title":"Scan for results","content":"https://example.org"}'}

# Elements whose text is cut off: wider or taller content than their box,
# where the box does not scroll on purpose.
CLIPPED = """() => [...document.querySelectorAll('body *')].filter(el => {
  if (!el.offsetParent || !el.childElementCount && !el.textContent.trim()) return false;
  const style = getComputedStyle(el);
  if (style.textOverflow === 'ellipsis') return false;
  const hides = /hidden|clip/.test(style.overflowX + style.overflowY);
  return hides && el.childElementCount === 0
    && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
}).map(el => el.outerHTML.slice(0, 80))"""
SCROLLS = """() => {
  const root = document.documentElement;
  return root.scrollWidth > innerWidth || root.scrollHeight > innerHeight;
}"""


def url(base: str, page: str) -> str:
    from urllib.parse import urlencode

    query = {"page": f"kiosks/{page}.kiosk"}
    if page in PARAMETERS:
        query["parameters"] = PARAMETERS[page]
    return f"{base.rstrip('/')}/kiosk.php?{urlencode(query)}"


# How many times a check that a reload interrupts is tried.
TRIES = 3


def check(tab) -> list[str]:
    """Scrollbars and up to 3 clipped elements on `tab` now. A reload or
    navigation that destroys the page's script context in the middle of the
    check (`kiosk-now-racing.js` reloads every 5 s with no lane count) is
    waited out and the check tried again, up to `TRIES` times; a page that
    never stays loaded is reported unchecked, never passed."""
    from playwright.sync_api import Error

    for _ in range(TRIES):
        try:
            found = ["scrollbars"] if tab.evaluate(SCROLLS) else []
            return found + [f"clipped {el}" for el in tab.evaluate(CLIPPED)[:3]]
        except Error as error:
            if "context was destroyed" not in str(error):
                raise
            tab.wait_for_load_state()
    return [f"not checked, the page reloaded through {TRIES} tries"]


def watch(tab, page_name: str, seconds: float) -> list[str]:
    """Waits `seconds` on the recording's tab, checking it every second,
    and returns each distinct problem seen, with the second it first
    showed."""
    problems, seen = [], set()
    for second in range(int(seconds) + 1):
        wait = min(1.0, seconds - second)
        if wait > 0:
            tab.wait_for_timeout(wait * 1000)
        for problem in check(tab):
            if problem not in seen:
                seen.add(problem)
                at = min(second + 1, seconds)
                problems.append(f"{page_name} in the recording at {at:g} s: {problem}")
    return problems


def fixture(base: str) -> None:
    for script in FIXTURE:
        subprocess.run(["bash", f"testing/{script}", base], cwd=REPO, check=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("base", help="the server, such as http://127.0.0.1:8000")
    parser.add_argument("--out", type=Path, default=Path("var/kiosk-screens"))
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--video", action="store_true")
    parser.add_argument("--seconds", type=float, default=12)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--page", action="append", choices=PAGES)
    args = parser.parse_args(argv)

    from playwright.sync_api import sync_playwright

    if args.fixture:
        fixture(args.base)
    args.out.mkdir(parents=True, exist_ok=True)
    problems = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        for page_name in args.page or PAGES:
            for size, (width, height) in SIZES.items():
                context = browser.new_context(
                    viewport={"width": width, "height": height}
                )
                tab = context.new_page()
                tab.goto(url(args.base, page_name))
                tab.wait_for_timeout(2500)
                tab.screenshot(path=args.out / f"{page_name}-{size}.png")
                problems += [f"{page_name} at {size}: {p}" for p in check(tab)]
                context.close()
            if args.video:
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    record_video_dir=args.out / "video",
                    record_video_size={"width": 1920, "height": 1080},
                )
                tab = context.new_page()
                tab.goto(url(args.base, page_name))
                problems += watch(tab, page_name, args.seconds)
                video = tab.video
                context.close()
                video.save_as(args.out / f"{page_name}-1920x1080.webm")
                video.delete()
        browser.close()
    for problem in problems:
        print(problem, file=sys.stderr)
    print(f"wrote {args.out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
