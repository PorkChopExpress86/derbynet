"""Each library a ported PHP page loads is ported too, or replaced on record
(#52 retro).

`fullscreen.php` loads `js/screenfull.min.js`; #52's port left it out until
review found it (`website/js/` had it all along). This reads the
`<script src="js/...">` tags of every PHP page the port has built, and
fails a third-party library with no entry in `LIBRARIES`: the static file
the port loads in its place, or why it needs none.
"""

import re
from pathlib import Path

from kiosks.pages import PAGES as KIOSK_PAGES
from ui.navigation import ENTRIES

SERVER = Path(__file__).resolve().parents[2]
WEBSITE = SERVER.parent / "website"
STATIC = SERVER / "ui" / "static"
TEMPLATES = SERVER / "ui" / "templates"

# The files of `website/js/` that DerbyNet did not write. DerbyNet's own
# scripts are rebuilt with each page.
THIRD_PARTY = {
    "adapter.js", "dropzone.min.js", "image-picker.min.js", "jquery-ui.min.js",
    "jquery.Jcrop.min.js", "jquery.js", "jquery.ui.touch-punch.min.js",
    "qrcode.min.js", "screenfull.min.js", "xlsx.full.min.js", "zxing.js",
}  # fmt: skip

# A path under ui/static/ that a template loads, or why the port needs none.
REPLACED = "replaced: "
LIBRARIES = {
    "jquery.js": REPLACED + "htmx, Alpine.js and plain scripts (ADR-9); the"
    " browser timer keeps a legacy copy under ui/static/timer/ (#50).",
    "jquery-ui.min.js": REPLACED + "htmx and Alpine.js (ADR-9); SortableJS"
    " sorts lists.",
    "jquery.ui.touch-punch.min.js": REPLACED + "SortableJS handles touch, and"
    " every drag has a tap alternative (ADR-9).",
    "dropzone.min.js": REPLACED + "a file input on the photo and check-in"
    " pages (#42, #47).",
    "jquery.Jcrop.min.js": "ui/vendor/cropper-1.6.2.min.js",
    "qrcode.min.js": "ui/vendor/qrcode-generator-1.4.4.js",
    "xlsx.full.min.js": "ui/vendor/xlsx-0.13.5.full.min.js",
    "screenfull.min.js": "ui/kiosk/screenfull.min.js",
}

SCRIPT = re.compile(r"""<script\b[^>]*\bsrc=["']js/([^"'?]+)""")
COMMENT = re.compile(r"<!--.*?-->", re.S)


def built_pages() -> list[str]:
    """The PHP files under website/ whose pages the port serves."""
    pages = {e.php.split("?")[0] for e in ENTRIES if e.php and e.view}
    pages |= {"kiosk.php", "fullscreen.php", *KIOSK_PAGES}
    return sorted(pages)


def scripts(source: str) -> set[str]:
    return set(SCRIPT.findall(COMMENT.sub("", source)))


def unported(pages: dict[str, str], libraries: dict[str, str]) -> list[str]:
    return sorted(
        f"{page}: js/{lib}"
        for page, source in pages.items()
        for lib in scripts(source) & THIRD_PARTY
        if lib not in libraries
    )


def test_every_built_page_has_its_php_file():
    assert [p for p in built_pages() if not (WEBSITE / p).is_file()] == []


def test_every_library_of_a_built_page_is_ported_or_replaced():
    pages = {p: (WEBSITE / p).read_text(errors="replace") for p in built_pages()}
    assert unported(pages, LIBRARIES) == []


def test_every_ported_library_is_served_and_loaded():
    loaded = "\n".join(p.read_text() for p in TEMPLATES.rglob("*.html"))
    for lib, port in LIBRARIES.items():
        assert lib in THIRD_PARTY, lib
        if port.startswith(REPLACED):
            continue
        assert (STATIC / port).is_file(), port
        assert f"'{port}'" in loaded or f'"{port}"' in loaded, port


def test_a_library_left_out_is_found():
    fullscreen = (WEBSITE / "fullscreen.php").read_text()
    without = {k: v for k, v in LIBRARIES.items() if k != "screenfull.min.js"}
    assert unported({"fullscreen.php": fullscreen}, without) == [
        "fullscreen.php: js/screenfull.min.js"
    ]
    assert scripts('<!-- <script src="js/qrcode.min.js"></script> -->') == set()
