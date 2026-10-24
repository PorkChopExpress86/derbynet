import re
from pathlib import Path

import pytest

from accounts.permissions import Access, Perm
from legacy_api.registry import (
    REGISTRY,
    Endpoint,
    Format,
    Registry,
    format_of,
    needs_event,
)

DATA = Path(__file__).parent / "data"
HANDLERS = Path(__file__).resolve().parent.parent / "handlers"


def php_endpoints():
    lines = (DATA / "php_endpoints.txt").read_text().splitlines()
    return {tuple(line.split(" ")) for line in lines}


def test_the_frozen_list_holds_every_php_handler():
    endpoints = php_endpoints()

    assert len(endpoints) == 108
    assert sum(kind == "action" for kind, _ in endpoints) == 83
    assert ("query", "standings") in endpoints


def test_every_registered_endpoint_has_a_php_name():
    registered = {(e.kind, e.name) for e in REGISTRY}

    assert registered <= php_endpoints()


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
