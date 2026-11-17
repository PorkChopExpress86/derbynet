"""`@pytest.mark.sizes(...)` runs a browser test at the sizes it names, and
collection deselects the others before any page opens (#366)."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVER_DIR = Path(__file__).resolve().parents[2]


def case(size, *marks, name="t"):
    """A collected test at `size` (None: one that takes no `size`)."""
    own = [m.mark for m in marks]
    return SimpleNamespace(
        nodeid=f"test_a.py::{name}[{size}]",
        fspath=SERVER_DIR / "test_a.py",
        callspec=SimpleNamespace(params={} if size is None else {"size": size}),
        get_closest_marker=lambda n: next((m for m in own if m.name == n), None),
    )


def split(items):
    from ui.tests.browser.conftest import split_by_size

    return split_by_size(items)


DESKTOP = pytest.mark.sizes("desktop", reason="a laptop")


def test_a_marked_test_keeps_the_sizes_it_names():
    one = [case("phone", DESKTOP, name="one"), case("desktop", DESKTOP, name="one")]
    both = [case("phone", name="both"), case("desktop", name="both")]
    kept, dropped = split([*one, *both])
    assert [c.nodeid for c in kept] == [
        "test_a.py::one[desktop]",
        "test_a.py::both[phone]",
        "test_a.py::both[desktop]",
    ]
    assert [c.nodeid for c in dropped] == ["test_a.py::one[phone]"]
    every = pytest.mark.sizes("phone", "desktop", reason="both")
    assert split([case("phone", every), case("desktop", every)])[1] == []


@pytest.mark.parametrize(
    ("item", "message"),
    [
        (
            case("phone", pytest.mark.sizes(reason="x")),
            r"takes names from .*, not \(\)",
        ),
        (
            case("phone", pytest.mark.sizes("tablet", reason="x")),
            r"t\[phone\]: sizes\(\) takes names from \('phone', 'desktop'\), "
            r"not \('tablet',\)",
        ),
        (
            case("phone", pytest.mark.sizes("phone", "Desktop", reason="x")),
            "takes names from",
        ),
        (case("phone", pytest.mark.sizes("phone")), r"needs a reason="),
        (case("phone", pytest.mark.sizes("phone", reason="")), r"needs a reason="),
        (case(None, DESKTOP), r"t\[None\]: sizes\(\) on a test with no page"),
    ],
    ids=["no size", "unknown", "one unknown", "no reason", "empty reason", "no page"],
)
def test_a_mark_the_harness_cannot_follow_stops_the_collection(item, message):
    with pytest.raises(pytest.UsageError, match=message):
        split([item])


def test_a_test_with_no_callspec_stops_the_collection():
    item = case("phone", DESKTOP)
    del item.callspec
    with pytest.raises(pytest.UsageError, match="on a test with no page"):
        split([item])


def test_the_collection_hook_deselects_the_dropped_cases():
    """The browser conftest's `pytest_collection_modifyitems` reports the
    other size as deselected and takes it out of the run, in place."""
    from ui.tests.browser import conftest

    phone, desktop = case("phone", DESKTOP), case("desktop", DESKTOP)
    for item in (phone, desktop):
        item.fixturenames, item.add_marker = (), lambda mark: None
    reported = []
    config = SimpleNamespace(
        hook=SimpleNamespace(pytest_deselected=lambda items: reported.append(items))
    )
    items = [phone, desktop]
    conftest.pytest_collection_modifyitems(config, items)
    assert items == [desktop]
    assert reported == [[phone]]


def test_a_collection_lists_no_case_at_a_size_the_test_does_not_name():
    """A real collection, in its own process: a test marked on itself and a
    case marked through `pytest.param(..., marks=...)` keep only the sizes
    they name, and `--strict-markers` knows the mark."""
    env = {k: v for k, v in os.environ.items() if k != "PYTEST_ADDOPTS"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-m",
            "browser",
            "-p",
            "no:xdist",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            "-k",
            "test_the_camera_page or test_the_delay_counts_after_the_post_roll"
            " or test_a_cancelled_press_applies_only_the_newest_held_board",
            "ui/tests/browser/test_replay.py",
            "ui/tests/browser/test_kiosks.py",
        ],
        cwd=SERVER_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    ids = [line for line in result.stdout.splitlines() if "::" in line]
    replay = "ui/tests/browser/test_replay.py::"
    press = "ui/tests/browser/test_kiosks.py::" + (
        "test_a_cancelled_press_applies_only_the_newest_held_board"
    )
    assert ids == [
        f"{replay}test_the_camera_page[phone-chromium]",
        f"{replay}test_the_camera_page[desktop-chromium]",
        f"{replay}test_the_delay_counts_after_the_post_roll[desktop-chromium]",
        f"{press}[phone-chromium-moved off]",
        f"{press}[phone-chromium-pointercancel]",
        f"{press}[desktop-chromium-moved off]",
    ], result.stdout
