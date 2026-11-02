"""Every page in the navigation registry opens cleanly for every default role
at both sizes (#40). It replaces testing/puppeteer/all-pages-test.js.

A later M4 ticket adds its page to the registry, and this test covers it with
no change here.
"""

import pytest
from django.urls import reverse

from ui.navigation import ENTRIES
from ui.tests.roles import ROLES


@pytest.mark.parametrize("role", ROLES)
def test_every_registry_page_opens_without_errors(login, role):
    page = login(role)

    for entry in ENTRIES:
        response = page.goto(reverse(entry.key))
        assert response is not None and response.ok, (entry.key, response.status)
        page.wait_for_load_state("networkidle")
