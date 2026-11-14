"""The page a registry entry serves until its ticket lands: `placeholder` in
ui/views.py and ui/templates/ui/placeholder.html. Moved from test_home.py so
plan/bin/affected-tests maps the template to a test named after it (#312
retro)."""

import dataclasses

import pytest

from ui import navigation
from ui.views import placeholder

pytestmark = pytest.mark.django_db


def test_the_page_is_a_placeholder_until_its_ticket_lands(login, rf):
    """#48 built the last placeholder pages, so the placeholder renders for a
    made-up entry."""
    request = rf.get("/x/")
    request.session = login("RaceCoordinator").session
    request.nav_entry = dataclasses.replace(
        navigation.entry("about"), key="x", view="", php="x.php", ticket=99
    )
    page = placeholder(request).content.decode()

    assert 'data-placeholder="x"' in page
    assert "Ticket #99 builds this page" in page
    assert "x.php" in page
