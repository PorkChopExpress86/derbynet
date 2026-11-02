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
