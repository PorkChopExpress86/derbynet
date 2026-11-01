import re
from pathlib import Path

import pytest

from accounts.permissions import Access, Perm
from legacy_api.handlers.testing import MANAGED
from legacy_api.registry import (
    DEFERRED,
    REGISTRY,
    REMOVED,
    ROUTE_DIVERGENCES,
    Deferred,
    Endpoint,
    Format,
    Registry,
    Removed,
    format_of,
    needs_event,
)
from racing.engine.tests.golden import QUIRK_DECISIONS, deviations

DATA = Path(__file__).parent / "data"
DECISIONS = Path(__file__).resolve().parents[3] / "plan" / "owner-decisions.md"
HANDLERS = Path(__file__).resolve().parent.parent / "handlers"
AJAX = Path(__file__).resolve().parents[3] / "website" / "ajax"
AJAX_FILE = re.compile(r"(action|query)\.(.+)\.inc")
COVERAGE_FIX = (
    "Django has no handler and no registry entry for these PHP endpoints:"
    " {missing}. Port each one with @action or @query in"
    " server/legacy_api/handlers/; or, if a backlog ticket ports it later, add a"
    " Deferred(kind, name, ticket, reason) to DEFERRED in"
    " server/legacy_api/registry.py; or, if Django drops it on purpose, add a"
    " Removed(kind, name, reason) to REMOVED there, citing the decision."
)


def php_endpoints():
    """Every PHP handler, frozen in `php_endpoints.txt`, so the list outlives
    #62 deleting `website/`."""
    lines = (DATA / "php_endpoints.txt").read_text().splitlines()
    return {tuple(line.split(" ")) for line in lines}


def test_the_frozen_list_holds_every_php_handler():
    endpoints = php_endpoints()

    assert len(endpoints) == 108
    assert sum(kind == "action" for kind, _ in endpoints) == 83
    assert ("query", "standings") in endpoints


def ajax_endpoints():
    """Every PHP handler, as `website/ajax/<kind>.<name>.inc` names it today."""
    return {
        (found[1], found[2])
        for path in AJAX.iterdir()
        if (found := AJAX_FILE.fullmatch(path.name))
    }


def unaccounted(
    php, registry=REGISTRY, deferred=DEFERRED, removed=REMOVED, managed=MANAGED
):
    """The PHP endpoints Django neither serves, defers to a backlog ticket,
    records as removed, nor manages for the shell suites.

    A test-managed endpoint (`legacy_api.handlers.testing.MANAGED`) counts as
    covered even when it is not registered: Django ports it, and registers it
    only while `DERBYNET_ENABLE_TEST_ENDPOINTS` is on, so a real race answers
    `unrecognized` for `test.setup`. The check reads the port, not this run's
    setting, so it passes with the flag on or off."""
    accounted = {(e.kind, e.name) for e in registry}
    accounted |= {(d.kind, d.name) for d in deferred}
    accounted |= {(r.kind, r.name) for r in removed}
    accounted |= set(managed)
    return sorted(set(php) - accounted)


def test_every_php_handler_is_served_deferred_removed_or_test_managed():
    """The #37 coverage check, over the frozen list of PHP's handlers."""
    missing = unaccounted(php_endpoints())

    if missing:
        names = ", ".join(f"{kind} {name}" for kind, name in missing)
        pytest.fail(COVERAGE_FIX.format(missing=names), pytrace=False)


def test_the_coverage_check_names_an_endpoint_nobody_accounts_for():
    registry = Registry()
    registry.register(Endpoint("query", "poll", print, Access.ANONYMOUS))
    deferred = (Deferred("action", "later", "#1", "Backlog."),)
    removed = (Removed("action", "gone", "D1: dropped."),)
    managed = (("action", "test.setup"),)
    php = {("query", "poll"), ("action", "later"), ("action", "gone")}
    php |= {("action", "test.setup"), ("query", "standings"), ("action", "racer.pass")}

    missing = unaccounted(
        php, registry=registry, deferred=deferred, removed=removed, managed=managed
    )

    assert missing == [("action", "racer.pass"), ("query", "standings")]


def test_the_coverage_failure_says_how_to_fix_it():
    text = COVERAGE_FIX.format(missing="query standings")

    assert "query standings" in text
    assert all(fix in text for fix in ("@query", "DEFERRED", "REMOVED"))


def test_a_name_is_registered_deferred_or_removed_only_once():
    registered = {(e.kind, e.name) for e in REGISTRY} | set(MANAGED)
    deferred = {(d.kind, d.name) for d in DEFERRED}
    removed = [(r.kind, r.name) for r in REMOVED]

    assert registered & deferred == set()
    assert registered & set(removed) == set()
    assert deferred & set(removed) == set()
    assert len(removed) == len(set(removed))
    assert set(removed) <= php_endpoints()


def test_the_frozen_list_is_what_website_ajax_holds():
    """`php_endpoints.txt` keeps the PHP names for when #62 deletes
    `website/`; until then it must agree with the directory."""
    if not AJAX.is_dir():
        pytest.skip("website/ajax is gone (#62); php_endpoints.txt is the record")
    assert ajax_endpoints() == php_endpoints()


# Endpoints Django adds on purpose (D48). PHP has no such name.
DJANGO_ONLY = {
    ("query", "partition.range-list"),
    ("action", "partition.range-add"),
    ("action", "partition.range-remove"),
}


def test_every_registered_endpoint_has_a_php_name():
    registered = {(e.kind, e.name) for e in REGISTRY}

    assert registered - DJANGO_ONLY <= php_endpoints()
    assert DJANGO_ONLY <= registered


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("timer-message", Format.XML),
        ("login", Format.XML),
        ("roles", Format.XML),
        ("snapshot.get", Format.XML),
        ("snapshot.put", Format.XML),
        # PHP tests strpos($name, "snapshot"), not a prefix.
        ("no.snapshot", Format.XML),
        ("role.login", Format.JSON),
        ("timer.test", Format.JSON),
        ("", Format.JSON),
    ],
)
def test_the_format_follows_phps_name_rule(name, fmt):
    assert format_of(name) is fmt


def test_only_nodata_names_run_without_an_event():
    assert not needs_event("setup.nodata")
    assert needs_event("role.login")
    assert needs_event("nodata")


def test_a_name_registers_once_per_kind():
    registry = Registry()
    registry.register(Endpoint("query", "poll", print, Access.ANONYMOUS))
    registry.register(Endpoint("action", "poll", print, Access.ANONYMOUS))

    with pytest.raises(ValueError):
        registry.register(Endpoint("query", "poll", print, Access.ANONYMOUS))


def test_an_xml_endpoint_cannot_answer_the_permission_code():
    with pytest.raises(ValueError):
        Endpoint("action", "snapshot.put", print, Perm.SET_UP, "permission")


def test_only_a_query_declares_get_writes():
    with pytest.raises(ValueError):
        Endpoint("action", "racer.pass", print, Perm.CHECK_IN_RACERS, get_writes=True)


ORM_WRITE = re.compile(r"\.(save|create|update|delete|bulk_\w+|get_or_create)\(")


def test_handlers_never_write_through_the_orm():
    """Handlers call services, so a timer and a phone see the same rules, and
    an id from a request never reaches a write unresolved (#17)."""
    offenders = [
        f"{path.name}:{n}: {line.strip()}"
        for path in sorted(HANDLERS.rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if ORM_WRITE.search(line)
    ]

    assert offenders == []


DIVERGENCE = re.compile(r"(?P<ids>[A-Z]\d+(?:, [A-Z]\d+)*): \S")


def test_every_divergence_names_a_recorded_decision():
    """Each entry reads `<id>: <what>`, and every id is a quirk decision or a
    recorded deviation, so the API docs never cite a row nobody decided."""
    known = set(QUIRK_DECISIONS) | {entry["quirk"] for entry in deviations()}
    known |= set(re.findall(r"^\| (D\d+) \|", DECISIONS.read_text(), re.MULTILINE))
    unknown = []
    listed = [(f"{e.kind} {e.name}", e.divergences) for e in REGISTRY]
    listed += list(ROUTE_DIVERGENCES.items())
    listed += [(f"{r.kind} {r.name}", (r.reason,)) for r in REMOVED]
    for where, divergences in listed:
        for text in divergences:
            found = DIVERGENCE.match(text)
            ids = found["ids"].split(", ") if found else [text]
            unknown += [f"{where}: {id}" for id in ids if id not in known]

    assert unknown == []


def test_every_deferred_endpoint_is_a_php_handler_django_does_not_serve():
    for deferred in DEFERRED:
        assert (deferred.kind, deferred.name) in php_endpoints(), deferred
        assert REGISTRY.get(deferred.kind, deferred.name) is None, deferred
