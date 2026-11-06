import pytest
from django.test import Client

from ui.tests.sessions import no_session_writes

pytestmark = pytest.mark.django_db


def test_a_new_install_goes_to_set_up(db):
    """index.php sends a database with no schema to setup.php. Django's first
    run is the one with no event."""
    response = Client().get("/")

    assert response.status_code == 302
    assert response["Location"] == "/setup/"


def test_a_visitor_on_a_new_install_follows_home_to_set_up(db):
    """H1: no login page on the way, as PHP sent everyone to setup.php."""
    response = Client().get("/index.php", follow=True)

    assert [url for url, _ in response.redirect_chain] == ["/", "/setup/"]
    assert response.status_code == 200
    assert "data-setup" in response.content.decode()


def test_set_up_opens_on_a_new_install(db):
    """Every session holds SET_UP until the first event exists."""
    assert Client().get("/setup/").status_code == 200


def test_a_role_with_set_up_gets_two_columns(login):
    page = login("RaceCoordinator").get("/").content.decode()

    assert 'data-home-columns="2"' in page
    assert "lg:grid-cols-2" in page


def test_a_role_without_set_up_gets_one_column(login):
    page = login("RaceCrew").get("/").content.decode()

    assert 'data-home-columns="1"' in page


def test_a_visitor_is_offered_a_login(login):
    page = login("anonymous").get("/").content.decode()

    assert 'href="/login/"' in page
    assert 'action="/logout/"' not in page


def test_a_role_logs_out_with_a_post_from_the_app_bar(login):
    client = login("RaceCrew")
    page = client.get("/").content.decode()

    assert 'action="/logout/"' in page
    assert 'data-role-chip><span class="sr-only">Logged in as </span>RaceCrew' in page
    response = client.post("/logout/")
    assert response["Location"] == "/login/"
    assert "role" not in client.session


def test_the_vote_entry_shows_to_a_visitor(client, event):
    """`index.php:137` links Vote! for VIEW_RACE_RESULTS, which the
    anonymous role has (#54)."""
    page = client.get("/").content.decode()

    assert 'href="/vote/"' in page


def test_the_drawer_shows_the_help_link_and_the_version(login):
    page = login("RaceCrew").get("/").content.decode()

    assert 'href="/quickref.pdf"' in page
    assert "data-version>DerbyNet " in page


@pytest.mark.parametrize("role", ["anonymous", "RaceCrew", "RaceCoordinator"])
@pytest.mark.parametrize("path", ["/", "/login/", "/checkin/", "/standings/"])
def test_page_views_never_write_the_session(login, role, path):
    client = login(role)

    with no_session_writes():
        response = client.get(path)

    assert response.status_code in (200, 302)
    assert "sessionid" not in response.cookies


def test_the_helper_catches_a_write(login):
    client = login("RaceCrew")

    with no_session_writes(), pytest.raises(AssertionError, match="wrote the session"):
        client.post("/logout/")


def test_the_page_is_a_placeholder_until_its_ticket_lands(login, rf):
    """#48 built the last placeholder pages, so the placeholder renders for a
    made-up entry."""
    import dataclasses

    from ui import navigation
    from ui.views import placeholder

    request = rf.get("/x/")
    request.session = login("RaceCoordinator").session
    request.nav_entry = dataclasses.replace(
        navigation.entry("about"), key="x", view="", php="x.php", ticket=99
    )
    page = placeholder(request).content.decode()

    assert 'data-placeholder="x"' in page
    assert "Ticket #99 builds this page" in page
    assert "x.php" in page


def test_the_active_page_is_marked_in_the_menus(login):
    page = login("RaceCoordinator").get("/standings/").content.decode()

    assert page.count('href="/standings/" aria-current="page"') >= 2
