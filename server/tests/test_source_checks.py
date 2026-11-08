"""A test module that reads the source is in `tools/source-checks.sh`, which
the pre-push hook runs (#238 retro).

#238's slideshow test took `settings` after `page`.
`ui/tests/test_browser_fixture_order.py` reads only the source and fails that
in a third of a second, but source-checks did not list it, so the push went
through and only the full suite found it; ten more such guards were missing
too. A module that imports `ast` or walks a tree with `rglob` is how a guard
is usually written, so each one is listed in source-checks or in `EXEMPT`
with the reason it is not. A guard that reads its files another way, such as
`test_settings_bounds.py`, is added to source-checks by hand.
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]
SCRIPT = SERVER / "tools" / "source-checks.sh"
SIGN = re.compile(r"^import ast\b|\.rglob\(", re.M)
LISTED = re.compile(r"^\s+([\w/]+\.py)(?:::\w+)?\s*\\$", re.M)

# Each module that carries the sign but needs a database (its tests, or the
# module-wide `django_db` mark, set one up) or walks a folder a test wrote.
EXEMPT = {
    "legacy_api/tests/test_junk_parameters.py": "django_db; fuzzes handler constants",
    "legacy_api/tests/test_read_only_event.py": "django_db; walks an event's folder",
    "legacy_api/tests/test_snapshot.py": "django_db; walks the event folders",
    "media/tests/test_photo_races.py": "django_db; walks the photo folders",
    "media/tests/test_photo_routes.py": "django_db; walks the photo folders",
    "racing/engine/tests/test_speed_awards_parity.py": "a golden parity replay",
    "transfer/tests/test_crash_safety.py": "django_db; walks the data folder",
    "transfer/tests/test_fixtures.py": "django_db; walks the data folder",
    "transfer/tests/test_import_rules.py": "django_db; walks the data folder",
    "ui/tests/test_every_view.py": "django_db; reads view constants to fuzz",
    # Its test_every_fetched_url_is_measured reads only templates, but the
    # module's django_db mark sets up a database: 3.4 s alone, against 0.3 s
    # for a module with no mark (#238 retro). The same goes for the next two.
    "ui/tests/test_query_growth.py": "django_db module",
    "ui/tests/test_timer_pages.py": "django_db module",
    "ui/tests/test_undo_links.py": "django_db module",
}


def listed() -> set[str]:
    return set(LISTED.findall(SCRIPT.read_text()))


def test_the_scan_reads_the_script_s_list():
    assert "tests/test_php_claims.py" in listed()
    assert "ui/tests/test_htmx.py" in listed(), "a node id names its module"


def test_every_listed_test_exists():
    assert sorted(p for p in listed() if not (SERVER / p).is_file()) == []


def test_every_module_that_reads_the_source_is_run_or_exempt():
    signed = {
        str(path.relative_to(SERVER))
        for path in SERVER.rglob("test_*.py")
        if not {".venv", "node_modules", "browser"}
        & set(path.relative_to(SERVER).parts)
        and SIGN.search(path.read_text())
    }
    missing = signed - listed() - set(EXEMPT)
    assert sorted(missing) == [], (
        f"add each to tools/source-checks.sh, or to EXEMPT with the reason: "
        f"{sorted(missing)}"
    )
    assert sorted(set(EXEMPT) - signed) == [], "an exemption with no sign left"
    assert sorted(set(EXEMPT) & listed()) == [], "listed and exempt"


def test_the_sign_finds_a_guard():
    assert SIGN.search('"""A guard."""\n\nimport ast\n')
    assert SIGN.search("SOURCES = sorted(UI.rglob('*.py'))\n")
    assert not SIGN.search("import asterisk\nfiles = folder.glob('*')\n")
