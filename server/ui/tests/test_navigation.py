"""The navigation registry is both the menu and the page guard (#40, C36)."""

import re

import pytest
from django.conf import settings
from django.test import Client
from django.urls import reverse

from accounts.permissions import Access, Perm
from legacy_api.registry import LIKE_PHP, ROUTE_DIVERGENCES, UNDECIDED_DIVERGENCES
from ui import navigation
from ui.navigation import ENTRIES, NavEntry
from ui.tests.roles import ROLE_NAV, ROLES

pytestmark = pytest.mark.django_db

GUARDED = [e for e in ENTRIES if e.permission is not Access.ANONYMOUS]
OPEN = [e for e in ENTRIES if e.permission is Access.ANONYMOUS]


def client_with_bits(bits: int, name: str = "Tester") -> Client:
    client = Client()
    session = client.session
    session.update({"role": name, "permissions": bits})
    session.save()
    client.cookies[settings.SESSION_COOKIE_NAME] = session.session_key
    return client


def url(entry: NavEntry) -> str:
    return reverse(entry.key)


@pytest.mark.parametrize("entry", GUARDED, ids=lambda e: e.key)
def test_a_role_without_the_bit_is_sent_to_log_in(event, entry):
    """Every bit but the page's own still leaves the page shut."""
    client = client_with_bits(-1 & ~int(entry.permission))

    response = client.get(url(entry))

    assert response.status_code == 302
    assert response["Location"] == f"/login/?next={url(entry).replace('/', '%2F')}"


@pytest.mark.parametrize("entry", GUARDED, ids=lambda e: e.key)
def test_an_htmx_request_without_the_bit_gets_403(event, entry):
    client = client_with_bits(-1 & ~int(entry.permission))

    response = client.get(url(entry), headers={"HX-Request": "true"})

    assert response.status_code == 403
    assert "Log in again." in response.content.decode()
    assert "Location" not in response


@pytest.mark.parametrize("entry", GUARDED, ids=lambda e: e.key)
def test_a_role_with_the_bit_opens_the_page(event, entry):
    response = client_with_bits(int(entry.permission)).get(url(entry))

    assert response.status_code == 200


@pytest.mark.parametrize("entry", OPEN, ids=lambda e: e.key)
def test_an_open_page_needs_no_bit(event, entry):
    response = client_with_bits(0).get(url(entry))

    assert response.status_code == 200


@pytest.mark.parametrize("entry", [e for e in ENTRIES if e.menu], ids=lambda e: e.key)
def test_the_menu_shows_a_link_exactly_when_the_page_opens(event, entry):
    """C36: the link gate is the page gate, for a role holding every bit but
    the page's and for one holding only the page's."""
    bits = (
        (0,)
        if entry.permission is Access.ANONYMOUS
        else (
            int(entry.permission),
            -1 & ~int(entry.permission),
        )
    )
    for held in bits:
        client = client_with_bits(held)
        home = client.get("/").content.decode()
        opens = client.get(url(entry)).status_code == 200
        assert (f'href="{url(entry)}"' in home) == opens, (entry.key, held)


def test_keys_and_paths_are_unique():
    assert len({e.key for e in ENTRIES}) == len(ENTRIES)
    assert len({e.path for e in ENTRIES}) == len(ENTRIES)


def test_every_page_has_a_section_a_permission_and_a_ticket():
    for e in ENTRIES:
        assert isinstance(e.section, navigation.Section), e.key
        assert isinstance(e.permission, Perm | Access), e.key
        assert e.ticket >= 40, e.key
        assert all(1 <= rank for rank in e.phone_rank.values()), e.key


# #39's Context lists these pages, from the index and reached from other
# pages. Printables are not ported (D6).
CONTEXT_PAGES = {
    "setup.php", "checkin.php",
    "photo-thumbs.php?repo=head", "photo-thumbs.php?repo=car",
    "coordinator.php", "kiosk-dashboard.php", "judging.php", "slideshow.php",
    "ondeck.php", "racer-results.php", "awards-presentation.php", "standings.php",
    "export.php", "history.php", "about.php", "settings.php", "racing-groups.php",
    "playlist.php", "scenes.php", "import-roster.php", "import-results.php",
    "import-awards.php", "awards-editor.php", "fakeroster.php", "fake-timer.php",
    "timer-test.php",
}  # fmt: skip


def test_every_page_of_the_spec_has_an_entry():
    assert CONTEXT_PAGES <= {e.php for e in ENTRIES}


# The pages built before the #50 retro, whose differences from PHP are in
# code comments and READMEs. A page leaves this set once its PHP page is in
# ROUTE_DIVERGENCES, UNDECIDED_DIVERGENCES or LIKE_PHP; none joins it.
BUILT_BEFORE_THE_CHECK = {
    "index.php", "login.php", "checkin.php", "photo-thumbs.php?repo=head",
    "photo-thumbs.php?repo=car", "coordinator.php", "kiosk-dashboard.php",
    "judging.php", "racer-results.php", "awards-presentation.php",
    "standings.php", "export.php", "history.php", "racing-groups.php",
    "playlist.php", "scenes.php", "import-roster.php", "import-awards.php",
    "import-results.php", "awards-editor.php", "fake-timer.php",
}  # fmt: skip


def unrecorded(entries) -> list[str]:
    recorded = set(ROUTE_DIVERGENCES) | set(UNDECIDED_DIVERGENCES) | LIKE_PHP
    return [
        e.php
        for e in entries
        if e.php and e.view and e.php not in recorded | BUILT_BEFORE_THE_CHECK
    ]


def test_every_built_page_records_how_it_differs_from_php():
    """#49's and #50's reviews each found five differences from the PHP page
    that the page's ticket listed nowhere. Building a page names its
    differences, or says it has none, in legacy_api/registry.py."""
    assert unrecorded(ENTRIES) == []


def test_a_new_page_with_no_record_fails_and_the_old_list_only_shrinks():
    # replay.php was the example until #53 built it; a page with no record
    # stands in.
    replay = navigation.entry("replay")
    built = NavEntry(**{**vars(replay), "php": "unrecorded.php"})
    assert unrecorded([built]) == ["unrecorded.php"]
    by_php = {e.php: e for e in ENTRIES}
    assert all(by_php[php].view for php in BUILT_BEFORE_THE_CHECK)
    recorded = set(ROUTE_DIVERGENCES) | set(UNDECIDED_DIVERGENCES) | LIKE_PHP
    assert BUILT_BEFORE_THE_CHECK & recorded == set()
    assert all(texts for texts in UNDECIDED_DIVERGENCES.values())


# #15's page gates, with the page gate winning where index.php's link differed.
PAGE_GATES = {
    "dashboard": Perm.CONTROL_RACE,
    "kiosk-dashboard": Perm.PRESENT_AWARDS,
    "history": Perm.CHECK_IN_RACERS,
    "standings": Access.ANONYMOUS,
    "slideshow": Access.ANONYMOUS,
    "ondeck": Access.ANONYMOUS,
    "racer-results": Access.ANONYMOUS,
    "timer-test": Perm.CONTROL_RACE,
    "replay": Perm.CONTROL_RACE,
}


@pytest.mark.parametrize("key", PAGE_GATES)
def test_the_permission_is_the_page_gate(key):
    assert navigation.entry(key).permission == PAGE_GATES[key]


def labels(entries):
    return [e.phone_label for e in entries]


@pytest.mark.parametrize("role", ROLES)
def test_the_bottom_nav_is_the_d20_table(login, role):
    response = login(role).get("/")
    nav = navigation.bottom_nav(response.wsgi_request)
    expected = ROLE_NAV[role]["phone"]

    if expected is None:
        assert nav is None
    else:
        assert [*labels(nav), "Log in" if role == "anonymous" else "More"] == expected


def test_a_role_d20_does_not_name_ranks_by_the_star_column(event):
    response = client_with_bits(int(Perm.CHECK_IN_RACERS), "Marshal").get("/")

    nav = navigation.bottom_nav(response.wsgi_request)

    assert labels(nav) == ["Check-In", "Standings", "On Deck"]


def section_links(page: str) -> dict[str, list[str]]:
    found = {}
    for section, body in re.findall(
        r'data-section="(\w+)">(.*?)</section>', page, re.S
    ):
        found[section] = re.findall(r"<span>([^<]+)</span></a>", body)
    return found


@pytest.mark.parametrize("role", ROLES)
def test_the_home_page_shows_the_roles_entries(login, role):
    page = login(role).get("/").content.decode()

    assert section_links(page) == ROLE_NAV[role]["sections"]
