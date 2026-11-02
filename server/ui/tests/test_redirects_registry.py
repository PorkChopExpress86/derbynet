"""M7: the 301 table comes from the registry."""

from ui import navigation, redirects


def test_every_built_registry_page_redirects_from_its_php_page():
    built = {
        e.php: e.key
        for e in navigation.ENTRIES
        if e.view and e.php and "?" not in e.php
    }

    assert redirects.legacy_pages() == {**built, **redirects.EXTRA_PAGES}


def test_a_placeholder_does_not_take_its_php_url():
    assert "setup.php" not in redirects.legacy_pages()
    assert "coordinator.php" in redirects.legacy_pages()


def test_a_php_page_with_a_query_is_not_a_path():
    assert all("?" not in php for php in redirects.legacy_pages())
