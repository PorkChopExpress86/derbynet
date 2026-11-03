"""The back button that PHP's `make_banner()` draws (`templates/banner.inc`)."""

import re
from pathlib import Path

from django.urls import reverse

from ui.responses import BANNER_PAGES

BANNER_INC = Path(__file__).resolve().parents[3] / "templates" / "banner.inc"


def test_banner_pages_are_make_banners_list():
    """`make_banner()` draws a back button only for the pages of its
    `in_array` list. A page ported with a back button follows the same list
    through `banner_back()`, so a page left out of it can never be shown."""
    source = BANNER_INC.read_text()
    listed = re.search(r"in_array\(\$back_button,\s*\[([^\]]*)\]", source)
    assert listed, "make_banner() no longer whitelists its back pages"
    php_pages = set(re.findall(r"'([^']+)'", listed.group(1)))
    assert set(BANNER_PAGES) == php_pages
    for name in BANNER_PAGES.values():
        reverse(name)
