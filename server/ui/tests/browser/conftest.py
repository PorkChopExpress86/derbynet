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
critical violation.
"""

import os
import re
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="install the browser dependency group")

from importlib import import_module  # noqa: E402

from django.apps import apps  # noqa: E402
from django.conf import settings  # noqa: E402

from accounts.models import Role  # noqa: E402
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


@pytest.fixture(scope="session", autouse=True)
def _async_unsafe():
    """Playwright's sync API runs an event loop in the test thread, and Django
    refuses ORM calls from a thread with a running loop. Session scope sets it
    before pytest-django opens the database, and only once a browser test
    runs, so a default run keeps the check."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
        yield


@pytest.fixture(scope="session", autouse=True)
def _fresh_css():
    """A page styled by a stale `app.css` lacks the classes a template just
    gained, so its screenshots and layout checks mislead. Where the Tailwind
    CLI is installed, refuse to run on a stale build; the `ui` CI job checks
    the same before it runs these tests."""
    from ui import css_build

    if css_build.binary_path().exists() and not css_build.check():
        pytest.fail(css_build.OUT_OF_DATE.format(css_build.OUTPUT), pytrace=False)


@pytest.fixture(autouse=True)
def _database(transactional_db, live_server):
    """The live server reads what the test writes, so the test runs outside a
    transaction. The flush after each test removes the roles the migration
    seeded, so seed them again, with the migration's own function."""
    if not Role.objects.exists():
        import_module("accounts.migrations.0002_seed_default_roles").seed(apps, None)


@pytest.fixture
def event(_database):
    return EventFactory()


@pytest.fixture(params=list(SIZES))
def size(request) -> str:
    return request.param


class PageErrors:
    """What went wrong while a page loaded or ran."""

    def __init__(self):
        self.errors: list[str] = []
        self.allowed: list[re.Pattern] = []

    def allow(self, pattern: str):
        self.allowed.append(re.compile(pattern))

    def add(self, message: str):
        if not any(p.search(message) for p in self.allowed):
            self.errors.append(message)


@pytest.fixture
def page_errors() -> PageErrors:
    return PageErrors()


@pytest.fixture
def page(browser, live_server, size, page_errors):
    context = browser.new_context(**SIZES[size], base_url=live_server.url)
    page = context.new_page()
    page.size = size
    page.on("pageerror", lambda error: page_errors.add(f"pageerror: {error}"))
    page.on(
        "console",
        lambda msg: msg.type == "error" and page_errors.add(f"console: {msg.text}"),
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
    context.close()
    assert page_errors.errors == []


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


def shoot(page, name: str) -> Path:
    path = SCREENSHOTS / f"{name}-{page.size}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=path, full_page=True)
    return path


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
