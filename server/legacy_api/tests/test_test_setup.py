"""`test.setup` (#63, S24, C17): the slide seeding that
`testing/test-slide-next.sh` uses."""

import json
import os
from urllib.parse import urlencode

import pytest
from django.test import override_settings

from accounts.permissions import Perm
from core.slides import slides_dir
from legacy_api.handlers import testing
from legacy_api.registry import REGISTRY
from legacy_api.tests.conftest import log_in, post

pytestmark = pytest.mark.django_db


@pytest.fixture
def coordinator(client, event, test_endpoints, settings, tmp_path):
    settings.DATA_DIR = tmp_path
    log_in(client, Perm.SET_UP)
    return client


def setup(client, **fields):
    return json.loads(
        post(client, urlencode({"action": "test.setup", **fields})).content
    )


def test_the_four_calls_of_the_slide_suite_put_the_slides_on_disk(coordinator, event):
    for fields in (
        {"slide": "slide1.png"},
        {"slide": "slide2.png"},
        {"subdir": "alt", "slide": "alt1.png"},
        {"subdir": "alt", "slide": "alt2.png"},
    ):
        assert setup(coordinator, **fields)["outcome"]["code"] == "success"

    root = slides_dir(event)
    assert sorted(p.name for p in root.iterdir()) == ["alt", "slide1.png", "slide2.png"]
    assert sorted(p.name for p in (root / "alt").iterdir()) == ["alt1.png", "alt2.png"]
    assert (root / "slide1.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_reply_names_the_slides_folder(coordinator, event):
    body = setup(coordinator, subdir="alt", slide="a.png")

    assert body["slides_dir"] == str(slides_dir(event, "alt"))


@pytest.mark.parametrize("subdir", ["a/b", "..", ".", "a b", "", "x\\y", "é"])
def test_a_bad_subdir_is_a_two_argument_failure_and_writes_nothing(
    coordinator, event, subdir
):
    body = setup(coordinator, subdir=subdir, slide="a.png")

    assert body["outcome"] == {
        "summary": "failure",
        "code": "invalid-subdir",
        "description": "Invalid subdir",
    }
    assert not slides_dir(event).exists()


@pytest.mark.parametrize("slide", ["a/b", "..", ".", "../x.png", ""])
def test_a_bad_slide_name_is_a_failure_and_writes_nothing(coordinator, event, slide):
    body = setup(coordinator, slide=slide)

    assert body["outcome"]["code"] == "invalid-slide"
    assert body["outcome"]["description"] == "Invalid slide name"
    assert not any(slides_dir(event).glob("*")) if slides_dir(event).exists() else True


def test_a_post_with_no_slide_is_not_a_known_operation(coordinator):
    body = setup(coordinator, subdir="alt")

    assert body["outcome"] == {
        "summary": "failure",
        "code": "unrecognized-operation",
        "description": "Operation not recognized",
    }


def test_a_slide_replaces_a_link_instead_of_writing_through_it(
    coordinator, event, tmp_path
):
    target = tmp_path / "precious.txt"
    target.write_text("keep")
    folder = slides_dir(event)
    folder.mkdir(parents=True)
    os.symlink(target, folder / "s.png")

    assert setup(coordinator, slide="s.png")["outcome"]["code"] == "success"

    assert target.read_text() == "keep"
    assert not (folder / "s.png").is_symlink()


def test_a_caller_without_set_up_cannot_seed_a_slide(
    client, event, test_endpoints, settings, tmp_path
):
    settings.DATA_DIR = tmp_path
    log_in(client, Perm.CONTROL_RACE)

    body = setup(client, slide="s.png")

    assert body["outcome"]["code"] == "notauthorized"
    assert not slides_dir(event).exists()


def test_the_handler_is_unregistered_when_the_flag_is_off(client, event):
    with override_settings(ENABLE_TEST_ENDPOINTS=False):
        testing.sync()
        log_in(client, Perm.SET_UP)

        body = setup(client, slide="s.png")

    assert REGISTRY.get("action", "test.setup") is None
    assert body["outcome"]["code"] == "unrecognized"
    assert not slides_dir(event).exists()
