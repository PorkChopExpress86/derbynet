"""The Playwright harness for every M4 page (#39, #40).

Each test that takes `page` runs twice, on a phone (390x844, touch) and on a
desktop (1280x800), against pytest-django's live server, which serves the
static files. Run them, after installing Chromium once:

    uv run playwright install chromium
    uv run pytest -m browser ui

Every page fails its test on a page error, a console error, or a failed
request for a static file, unless the test expects that error with
`page_errors.allow(pattern)`. `shoot(page, name)` saves a screenshot under
`DERBYNET_SCREENSHOT_DIR` (default `server/var/screenshots`), which CI
uploads, and `assert_accessible(page)` runs axe-core and fails on a serious or
critical violation. `no_sideways_scroll(page)` fails a page wider than its
viewport; each `shoot()` runs it, and so does the end of every test (#44
retro: a phone page scrolled 407 px sideways, seen only in a screenshot).

After a control posts, wrap the click in `with htmx_post(page, path):` before
reading the database: it waits for the POST's reply and htmx's swap. When the
test body returns, the harness stops every tab's timers, closes the other
tabs, and waits for every request still in flight before any fixture tears
down (#45 and #53 retros), so a late reply or a late poll never meets a
flushed database or `settings` already put back. Close a second tab with
`close_tab(page, tab)`: a tab closed with a request out fails the test.
`wait_for_load_state("networkidle")` waits for a page load only, and returns
at once after an htmx request. A swap that leaves an `hx-get` or `hx-post`
control unbound once its task ends fails the test (#46 retro).
"""

import os
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright", reason="install the browser dependency group")

from importlib import import_module  # noqa: E402

from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402
from django.db import connection  # noqa: E402

from accounts.models import Role  # noqa: E402
from awards.models import AwardType  # noqa: E402
from core.tests.factories import EventFactory  # noqa: E402
from ui.tests.roles import PASSWORDS  # noqa: E402

SIZES = {
    "phone": {
        "viewport": {"width": 390, "height": 844},
        "has_touch": True,
        "is_mobile": True,
        "device_scale_factor": 2,
    },
    "desktop": {"viewport": {"width": 1280, "height": 800}},
}
# A swap must bind its content's hx-* attributes before the browser can run
# anything else, or a tap on a control just shown sends nothing (#45's award
# sheet in CI, #46 retro). htmx binds at settle, so the base template settles
# at once; this fails any test whose swap leaves a control unbound after the
# task that swapped it, whatever the timing of the test's own steps.
UNBOUND_AFTER_SWAP = """document.addEventListener('htmx:afterSwap', (event) => {
  const root = event.detail.target;
  setTimeout(() => {
    if (!root.isConnected) return;
    const unbound = [root, ...root.querySelectorAll('[hx-get], [hx-post]')].filter(
      (e) => e.matches('[hx-get], [hx-post]') && e.isConnected
        && !(e['htmx-internal-data'] || {}).initHash);
    if (unbound.length) {
      const asked = event.detail.requestConfig || {};
      const tag = unbound[0].outerHTML.slice(0, 120);
      console.error(`htmx left unbound after ${asked.verb} ${asked.path}: ${tag}`);
    }
  }, 0);
});"""
AXE = Path(__file__).parent / "vendor" / "axe-4.10.3.min.js"
AXE_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
SCREENSHOTS = Path(
    os.environ.get("DERBYNET_SCREENSHOT_DIR")
    or settings.BASE_DIR / "var" / "screenshots"
)


def pytest_collection_modifyitems(items):
    here = Path(__file__).parent
    for item in items:
        if here in Path(item.fspath).parents:
            item.add_marker(pytest.mark.browser)


# A host name that reaches the live server over plain HTTP. `localhost` is a
# secure context, so a page there may use the camera; check-in's photo sheet
# must also work where the browser refuses it, as on a venue's LAN (#42).
PLAIN_HTTP_HOST = "derbynet.test"


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Chromium resolves `PLAIN_HTTP_HOST` to the live server, and offers
    two fake cameras that need no permission prompt, so the replay setup can
    choose between them (#53)."""
    return {
        **browser_type_launch_args,
        "args": [
            *browser_type_launch_args.get("args", []),
            f"--host-resolver-rules=MAP {PLAIN_HTTP_HOST} 127.0.0.1",
            "--use-fake-device-for-media-stream=device-count=2",
            "--use-fake-ui-for-media-stream",
        ],
    }


@pytest.fixture(scope="session", autouse=True)
def _async_unsafe():
    """Playwright's sync API runs an event loop in the test thread, and Django
    refuses ORM calls from a thread with a running loop. Session scope sets it
    before pytest-django opens the database, and only once a browser test
    runs, so a default run keeps the check."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


def _css_is_fresh(tmp_path_factory, worker_id) -> bool:
    """`css_build.check()`, run once per pytest run. Tailwind CLIs started at
    once by several xdist workers can fail to load their native binding, so
    the first worker runs it under a file lock in the directory every worker
    shares, and the others read its answer."""
    from ui import css_build

    if worker_id == "master":
        return css_build.check()
    import fcntl

    shared = tmp_path_factory.getbasetemp().parent
    answer = shared / "css-fresh.txt"
    with (shared / "css-fresh.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not answer.exists():
            answer.write_text(str(css_build.check()))
        return answer.read_text() == "True"


@pytest.fixture(scope="session", autouse=True)
def _fresh_css(tmp_path_factory, worker_id):
    """A page styled by a stale `app.css` lacks the classes a template just
    gained, so its screenshots and layout checks mislead. Where the Tailwind
    CLI is installed, refuse to run on a stale build; the `ui` CI job checks
    the same before it runs these tests."""
    from ui import css_build

    if css_build.binary_path().exists() and not _css_is_fresh(
        tmp_path_factory, worker_id
    ):
        pytest.fail(css_build.OUT_OF_DATE.format(css_build.OUTPUT), pytrace=False)


@pytest.fixture(autouse=True)
def _database(transactional_db, live_server):
    """The live server reads what the test writes, so the test runs outside a
    transaction. The flush after each test removes the roles the migration
    seeded, so seed them again, with the migration's own function."""
    if not Role.objects.exists():
        import_module("accounts.migrations.0002_seed_default_roles").seed(apps, None)
    if not AwardType.objects.exists():
        seed = import_module("awards.migrations.0003_seed_award_types")
        seed.seed_award_types(apps, SimpleNamespace(connection=connection))


@pytest.fixture
def event(_database):
    return EventFactory()


@pytest.fixture(params=list(SIZES))
def size(request) -> str:
    return request.param


class PageErrors:
    """What went wrong while a page loaded or ran.

    htmx logs an aborted request (an `hx-sync` replace, or a poll cut short)
    as two console errors, `htmx:afterRequest` then `htmx:sendAbort`. An abort
    is no failure, so that pair is dropped here and counted in `aborts`; the
    browser's `net::ERR_ABORTED` for the request still needs the test's
    `allow()`. A lone `htmx:afterRequest` is a failed request and stays."""

    def __init__(self):
        self.errors: list[str] = []
        self.allowed: list[re.Pattern] = []
        self.aborts = 0
        self._after_request = False

    def allow(self, pattern: str):
        self.allowed.append(re.compile(pattern))

    def add(self, message: str):
        if not any(p.search(message) for p in self.allowed):
            self.errors.append(message)

    def console_error(self, text: str):
        after_request, self._after_request = self._after_request, False
        if text == "htmx:sendAbort":
            self.aborts += 1
            return
        if after_request:
            self.add("console: htmx:afterRequest")
        if text == "htmx:afterRequest":
            self._after_request = True
        else:
            self.add(f"console: {text}")

    def final(self) -> list[str]:
        if self._after_request:
            self._after_request = False
            self.add("console: htmx:afterRequest")
        return self.errors


@pytest.fixture
def page_errors() -> PageErrors:
    return PageErrors()


class InFlight:
    """The requests a browser context has sent and not yet finished.

    A navigation ends the old document's htmx and fetch requests and its
    image, script and stylesheet loads, but Playwright then reports neither
    `requestfinished` nor `requestfailed` for them (seen on a reload right
    after a click, #188, for a thumbnail left loading by a link, #47, and
    for the timer's scripts when the Web Serial guard leaves `/timer.php`,
    #50). So when a frame
    navigates, those requests leave the set: their replies can no longer
    reach the page. The new document sends none before it commits."""

    TIMEOUT_MS = 10_000
    ORPHANED = ("xhr", "fetch", "image", "script", "stylesheet")

    def __init__(self, context):
        self.requests = set()
        # Requests a tab still had out when it closed, as "METHOD URL".
        self.closed_with = []
        context.on("request", lambda r: self.requests.add(r))
        context.on("requestfinished", lambda r: self.requests.discard(r))
        context.on("requestfailed", lambda r: self.requests.discard(r))
        context.on("page", self.watch)

    def watch(self, page):
        page.on("framenavigated", lambda frame: self.navigated(frame))
        page.on("close", lambda closed: self.page_closed(closed))

    def of(self, tab) -> set:
        """The requests in flight that `tab` sent."""
        return {r for r in self.requests if _sent_by(r) == tab}

    def page_closed(self, tab):
        """Playwright reports no end for the requests of a tab closed with
        them out (the camera tab's POST, PR #205's ui (postgres)), so they
        leave the set and the harness names them: close a tab with
        `close_tab()`, which waits for them first."""
        left = self.of(tab)
        self.requests -= left
        self.closed_with += sorted(f"{r.method} {r.url}" for r in left)

    def navigated(self, frame):
        self.requests -= {
            r
            for r in self.requests
            if r.frame == frame and r.resource_type in self.ORPHANED
        }

    def drain(self, page) -> list[str]:
        """Wait until nothing is in flight; return what still is after the
        timeout. Playwright delivers the events only inside its own calls,
        hence the short waits."""
        waited = 0
        while self.requests and waited < self.TIMEOUT_MS and not page.is_closed():
            page.wait_for_timeout(50)
            waited += 50
        return sorted(f"{r.method} {r.url}" for r in self.requests)


def _sent_by(request):
    try:
        return request.frame.page
    except Exception:  # noqa: BLE001 - a service worker's request has no frame
        return None


# Clears every timeout and interval the frame has set. Timer ids count up
# from 1, or from 1e12 under Playwright's clock (`page.clock.install()`), so
# the loop runs over the first and the last SPAN ids: from 0 it spun the
# renderer for hours on test_toasts.py's fake clock (#53 retro).
STOP_TIMERS = """() => {
  const SPAN = 100000;
  const last = setTimeout(() => {});
  const stop = (id) => { clearTimeout(id); clearInterval(id); };
  for (let id = 0; id <= Math.min(last, SPAN); id++) stop(id);
  for (let id = Math.max(SPAN + 1, last - SPAN); id <= last; id++) stop(id);
}"""


def stop_timers(tab):
    """Stop every poll in every frame of `tab`, so no request starts after
    the harness drained them."""
    for frame in tab.frames:
        try:
            frame.evaluate(STOP_TIMERS)
        except Exception:  # noqa: BLE001, S110 - a detached frame has no timers
            pass


def close_tab(page, tab):
    """Close a second tab of `page`'s context, such as the camera or a
    kiosk, once its polls have stopped and its requests are answered. A tab
    closed with a request out fails the test (`InFlight.page_closed`)."""
    stop_timers(tab)
    waited = 0
    while waited < InFlight.TIMEOUT_MS and page.in_flight.of(tab):
        tab.wait_for_timeout(50)
        waited += 50
    tab.close()


def _settle(page) -> list[str]:
    """Stop every tab's polls, close the other tabs, and drain what is out.
    A page that polls more often than the drain's 50 ms check, such as the
    replay page's 250 ms `replay.message`, could otherwise start a request
    after the drain that met the database flush (a Postgres deadlock, #53).
    Returns the requests still out after the timeout."""
    if page.is_closed():
        return []
    for tab in page.context.pages:
        if tab != page:
            close_tab(page, tab)
    stop_timers(page)
    return page.in_flight.drain(page)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    """After the test body and before any fixture's teardown, stop every
    tab's polls and wait for its requests in flight. A test that ends on a
    click, or a poll that happens to be out, would otherwise get its reply
    after `settings` turned the test endpoints off (a 404) or the database
    was flushed."""
    page = item.funcargs.get("page")
    in_flight = getattr(page, "in_flight", None)
    try:
        result = yield
    except BaseException:
        if in_flight is not None:
            try:
                _settle(page)
            except Exception:  # noqa: BLE001, S110 - the test's own error wins
                pass
        raise
    if in_flight is None:
        return result
    stuck = _settle(page)
    if in_flight.closed_with:
        raise AssertionError(
            f"a tab closed with requests out: {in_flight.closed_with}; "
            "close it with close_tab(page, tab)"
        )
    if stuck:
        raise AssertionError(f"still in flight after 10 s: {stuck}")
    return result


@pytest.fixture
def page(browser, live_server, size, page_errors):
    context = browser.new_context(**SIZES[size], base_url=live_server.url)
    context.add_init_script(UNBOUND_AFTER_SWAP)
    in_flight = InFlight(context)
    page = context.new_page()
    page.size = size
    page.in_flight = in_flight
    page.on("pageerror", lambda error: page_errors.add(f"pageerror: {error}"))
    page.on(
        "console",
        lambda msg: msg.type == "error" and page_errors.console_error(msg.text),
    )
    page.on(
        "requestfailed",
        lambda request: page_errors.add(
            f"request failed: {request.url} {request.failure}"
        ),
    )

    def static_status(response):
        if "/static/" in response.url and response.status >= 400:
            page_errors.add(f"static {response.status}: {response.url}")

    page.on("response", static_status)
    # PHP's pages used alert() and confirm(). No new page may.
    page.on("dialog", lambda dialog: page_errors.add(f"dialog: {dialog.message}"))
    yield page
    try:
        if page.url.startswith("http"):
            no_sideways_scroll(page)
    finally:
        context.close()
    assert page_errors.final() == []


def log_in(page, role: str):
    """Log in through the login page, as a race crew would: tap the role,
    type the password, press Enter."""
    page.goto("/login/?all")
    page.locator(f'button[data-role="{role}"]').click()
    password = page.get_by_label("Password")
    password.fill(PASSWORDS[role])
    password.press("Enter")
    page.wait_for_url(re.compile(r"/$"))


@pytest.fixture
def login(page, event):
    """`login(role)`: the page, logged in as that default role, or left a
    visitor for "anonymous"."""

    def as_role(role: str):
        if role != "anonymous":
            log_in(page, role)
        return page

    return as_role


@contextmanager
def htmx_post(page, path: str):
    """Wait for the change the block makes: a POST to a URL holding `path`
    answers OK, and htmx has swapped and settled what it sent back."""
    with page.expect_response(
        lambda r: r.request.method == "POST" and path in r.url
    ) as reply:
        yield
    assert reply.value.ok, reply.value.status
    htmx_settled(page)


def htmx_settled(page):
    """Wait until htmx has no request in flight and has settled every swap.
    The page settles at once (`defaultSettleDelay` 0), so a swapped-in
    control is bound as soon as it shows, and every swap is checked for that
    (UNBOUND_AFTER_SWAP); wait for this before reading what a request
    changed."""
    page.wait_for_function(
        "!document.querySelector('.htmx-request, .htmx-swapping, .htmx-settling')"
    )


def shoot(page, name: str, full_page: bool = True) -> Path:
    """`full_page=False` for an open sheet, which is fixed to the screen."""
    no_sideways_scroll(page)
    path = SCREENSHOTS / f"{name}-{page.size}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=path, full_page=full_page)
    return path


def no_sideways_scroll(page):
    width = page.evaluate("document.documentElement.scrollWidth")
    viewport = page.viewport_size["width"]
    assert width <= viewport, f"{page.url} is {width} px wide in {viewport} px"


def axe_violations(page) -> list[dict]:
    page.add_script_tag(path=str(AXE))
    result = page.evaluate(
        "tags => axe.run(document, {runOnly: {type: 'tag', values: tags}})", AXE_TAGS
    )
    return [v for v in result["violations"] if v["impact"] in ("serious", "critical")]


def assert_accessible(page):
    violations = axe_violations(page)
    assert violations == [], [
        (v["id"], v["help"], [n["target"] for n in v["nodes"]]) for v in violations
    ]
