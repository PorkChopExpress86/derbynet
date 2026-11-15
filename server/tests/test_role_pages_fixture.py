"""The page lists of `testing/test-permissions.sh` agree with the page gates
(#319 retro).

`testing/fixtures/role-pages.json` lists the pages each role opens on PHP,
and `role-pages-django.json` moves those Django decided otherwise into the
other list. The shell suite holds a running Django server to that overlay,
and only CI runs it: #319 gave `/timer/fake/` `TIMER_MESSAGE` (D96), left
`fake-timer.php` among RaceCrew's allowed pages, and every CI job that runs
the suite failed on it after the push.

These tests read each old page's gate from the source: the registry entry
its redirect lands on (`ui.redirects.legacy_pages()`), or `OTHER_PAGES` for
the pages Django answers itself. With the seeded roles' bits
(`accounts.defaults.DEFAULT_ROLES`) they fail on each page the overlay puts
in the wrong list, before a push. They check the gate, not the replies,
which need the shell suite's server. `OTHER_PAGES` holds its gates by
hand: its test ties each row to the view that answers the page, not to the
view's check, so a change of that check still reaches only the shell suite.
"""

import json
from collections import Counter
from pathlib import Path

import pytest
from django.urls import resolve

from accounts.defaults import DEFAULT_ROLES
from accounts.permissions import Access, Perm, Requirement
from ui import redirects
from ui.navigation import BY_KEY

FIXTURES = Path(__file__).resolve().parents[2] / "testing" / "fixtures"
PHP_FIXTURE = FIXTURES / "role-pages.json"
DJANGO_FIXTURE = FIXTURES / "role-pages-django.json"
PHP = json.loads(PHP_FIXTURE.read_text())
DJANGO = json.loads(DJANGO_FIXTURE.read_text())

# The fixtures' role names to the seeded roles' names. The shell suite logs
# in as each (`user_login_coordinator`, `user_login_crew`) or logs out.
ROLES = {"anonymous": "", "RaceCrew": "RaceCrew", "RaceCoordinator": "RaceCoordinator"}
SEEDED = {name: (password, bits) for name, password, bits, _ in DEFAULT_ROLES}
KINDS = ("allowed", "denied")

# The old pages Django answers itself, outside the redirect table: the view that
# answers each, and its gate.
OTHER_PAGES: dict[str, tuple[str, Requirement]] = {
    # `legacy_api/views.py`: export_json() and preferences() check these.
    "export-json.php": ("legacy_api.views.export_json", Perm.VIEW_RACE_RESULTS),
    "preferences.php": ("legacy_api.views.preferences", Perm.SET_UP),
    # `ui/timer/views.py`: `_hosting`.
    "timer.php": ("ui.timer.views.browser_timer", Perm.TIMER_MESSAGE),
    # `ui/kiosk_pages/views.py`: the kiosk pages have no gate.
    "kiosk.php": ("ui.kiosk_pages.views.kiosk", Access.ANONYMOUS),
    "fullscreen.php": ("ui.kiosk_pages.views.fullscreen", Access.ANONYMOUS),
    # `redirects.QUERY_PAGES`: with no `repo`, a redirect to the racer photos.
    "photo-thumbs.php": (
        "ui.photos.views.legacy_page",
        BY_KEY["racer-photos"].permission,
    ),
}


def _path(page: str) -> str:
    """`kiosk.php?page=...` is answered by `kiosk.php`."""
    return page.split("?", 1)[0]


def _gate(page: str) -> Requirement:
    built = redirects.legacy_pages()
    if (path := _path(page)) in built:
        return BY_KEY[built[path]].permission
    return OTHER_PAGES[path][1]


def _opens(role: str, requirement: Requirement) -> bool:
    """As `accounts.permissions.allows()` decides for a session logged in as
    the seeded `role` once an event exists, without reading the database."""
    password, bits = SEEDED[ROLES[role]]
    match requirement:
        case Access.ANONYMOUS:
            return True
        case Access.LOGGED_IN:
            return password != ""
        case Perm():
            return bits & requirement != 0


def _other(kind: str) -> str:
    return "denied" if kind == "allowed" else "allowed"


def _overlay(role: str, kind: str) -> list[str]:
    """The pages of that list on Django, as `role_pages` in the shell suite
    computes them."""
    moved = DJANGO.get(role, {})
    kept = [p for p in PHP[role][kind] if p not in moved.get(_other(kind), {})]
    return kept + list(moved.get(kind, {}))


def _fixture_pages() -> set[str]:
    return {p for lists in PHP.values() for kind in KINDS for p in lists[kind]}


def test_the_fixtures_list_the_seeded_roles():
    assert sorted(PHP) == sorted(ROLES)
    assert set(DJANGO) <= set(PHP)
    assert all(set(lists) == set(KINDS) for lists in PHP.values())
    assert all(set(moved) <= set(KINDS) for moved in DJANGO.values())


def test_no_role_lists_a_page_twice():
    twice = [
        (role, page)
        for role, lists in PHP.items()
        for page, count in Counter(lists["allowed"] + lists["denied"]).items()
        if count > 1
    ]
    assert twice == []


def test_each_moved_page_is_in_the_other_list_of_the_php_fixture():
    stray = [
        (role, kind, page)
        for role, moved in DJANGO.items()
        for kind, pages in moved.items()
        for page in pages
        if page not in PHP[role][_other(kind)]
    ]
    assert stray == []


@pytest.mark.parametrize("role", sorted(ROLES))
def test_the_django_lists_hold_each_page_of_the_php_lists(role):
    """As the shell suite's count of pages checked against the fixture's."""
    django = _overlay(role, "allowed") + _overlay(role, "denied")
    assert sorted(django) == sorted(PHP[role]["allowed"] + PHP[role]["denied"])


def test_every_page_of_the_fixture_has_a_gate():
    built = redirects.legacy_pages()
    assert sorted(set(OTHER_PAGES) & set(built)) == [], "in the redirect table already"
    paths = {_path(p) for p in _fixture_pages()}
    assert sorted(paths - set(built) - set(OTHER_PAGES)) == []
    assert sorted(set(OTHER_PAGES) - paths) == []


def test_a_login_is_a_role_with_a_password():
    """No page takes `Access.LOGGED_IN` yet; a page that does is denied to
    the anonymous role and opened by the others, as `allows()` decides."""
    assert {role: _opens(role, Access.LOGGED_IN) for role in ROLES} == {
        "anonymous": False,
        "RaceCrew": True,
        "RaceCoordinator": True,
    }


@pytest.mark.parametrize("page", sorted(OTHER_PAGES))
def test_each_other_page_names_the_view_that_answers_it(page):
    func = resolve(f"/{page}").func
    assert f"{func.__module__}.{func.__qualname__}" == OTHER_PAGES[page][0]


@pytest.mark.parametrize("role", sorted(ROLES))
def test_the_django_lists_match_each_page_s_gate(role):
    wrong = [
        f"{page} is in {kind}, and its gate {_gate(page)!r}"
        f" {'lets' if opens else 'keeps'} {role} {'in' if opens else 'out'}"
        for kind in KINDS
        for page in _overlay(role, kind)
        if (opens := _opens(role, _gate(page))) != (kind == "allowed")
    ]
    assert wrong == [], (
        f"move each page in {DJANGO_FIXTURE.name}, under {role}, with the"
        " quirk id or owner decision that changed its gate (testing/README.md)"
    )
