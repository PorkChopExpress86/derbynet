"""Rules every page's htmx follows, so a control's request does what its own
markup says (#42 retro)."""

import html
import json
import re
from pathlib import Path

UI = Path(__file__).resolve().parent.parent


def test_inheritance_is_off(client, db):
    """#42 lost a toast and wiped a sheet to attributes a control inherited
    from its polled list and its form. With inheritance off, each element that
    makes a request names its own target, swap, sync and include."""
    page = client.get("/login/").content.decode()
    meta = re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", page)
    assert json.loads(html.unescape(meta.group(1)))["disableInheritance"] is True


def test_a_swap_settles_at_once(client, db):
    """htmx binds the hx-* attributes of swapped-in content when the swap
    settles, and Alpine shows that content before then. With htmx's default
    20 ms settle, more under load, a control used in between sent nothing:
    #45's award sheet select in CI, and every browser test that taps after a
    swap when the settle is slowed to 1.5 s (#46 retro: 61 of 248 failed).
    A `settle:` modifier on an element brings the gap back."""
    page = client.get("/login/").content.decode()
    meta = re.search(r"<meta name=\"htmx-config\" content='([^']+)'>", page)
    assert json.loads(html.unescape(meta.group(1)))["defaultSettleDelay"] == 0
    settles = [
        p.name
        for p in (UI / "templates").rglob("*.html")
        if re.search(r"hx-swap=\"[^\"]*\bsettle:", p.read_text())
    ]
    assert settles == []


def test_no_reply_replaces_an_element_a_trigger_listens_to():
    """`hx-trigger="... from:#x"` listens on the element #x was when htmx
    processed the trigger. A reply that swaps #x itself (out of band, or as an
    outerHTML target) leaves the trigger deaf, as #42's car-number hint was.
    Swap the element's contents, or its siblings, instead."""
    templates = list((UI / "templates").rglob("*.html"))
    code = [p for p in UI.rglob("*.py") if "tests" not in p.parts]
    listened = {
        name for p in templates for name in re.findall(r"from:#([\w-]+)", p.read_text())
    }
    assert listened, "no from: trigger found; is the pattern stale?"
    swapped = []
    for path in templates + code:
        text = path.read_text()
        for name in listened:
            for tag in re.findall(rf"<[^>]*\bid=\"{name}\"[^>]*>", text):
                if "hx-swap-oob" in tag:
                    swapped.append((path.name, tag))
            if re.search(rf"hx-target=\"#{name}\"[^>]*hx-swap=\"outerHTML", text):
                swapped.append((path.name, f"outerHTML of #{name}"))
    assert swapped == []


def _polled_regions(templates) -> set[str]:
    regions = set()
    for path in templates:
        for tag in re.findall(
            r"<\w[^<>]*\bhx-trigger=\"[^\"]*\bevery\b[^<>]*>", path.read_text()
        ):
            found = re.search(r"\bid=\"([\w-]+)\"", tag)
            if found:
                regions.add(found.group(1))
    return regions


def test_no_control_queues_behind_a_poll():
    """A control that syncs on a polled region with `queue` waits behind the
    poll; when the poll's reply replaces the control, htmx sends nothing for
    the detached element and the tap is lost (#46's kiosk board). Such a
    control syncs with `replace`, as #41's `#live` does. Only a request that
    loads the region itself, such as a filter form, may queue on it."""
    templates = list((UI / "templates").rglob("*.html"))
    regions = _polled_regions(templates)
    assert {"kiosk-board", "checkin-list"} <= regions, "is the poll pattern stale?"
    queued = []
    for path in templates:
        for tag in re.findall(
            r"<\w[^<>]*\bhx-sync=\"#[\w-]+:queue[^<>]*>", path.read_text()
        ):
            region = re.search(r"hx-sync=\"#([\w-]+):", tag).group(1)
            if region in regions and f'hx-target="#{region}"' not in tag:
                queued.append((path.name, tag))
    assert queued == []
