"""The kiosk page registry (#52): the 15 built-in pages, keyed by the legacy
page string, such as `kiosks/now-racing.kiosk`.

`KioskPoller` reloads a kiosk whose page string differs from the one it
loaded, so the keys stay the legacy strings exactly. Django loads no custom
page from any folder (D10, ADR-10): a page string with no entry, every
`local/kiosks/` page included, shows `kiosks/identify.kiosk`.

`Kiosk.page` and `SceneKiosk.page` hold the key, `params` the text after
`#`, and `Kiosk.reload_requested` PHP's leading `!`. `from_legacy()` splits
a legacy string into those three, `legacy_string()` packs them again for a
snapshot, and `setting()` is the one serializer of the legacy
`{page, name, parameters, reload}` shape `poll.kiosk` answers.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

IDENTIFY = "kiosks/identify.kiosk"
NO_PARAMS = "{}"


@dataclass(frozen=True)
class KioskPage:
    # The legacy page string, `kiosks/<name>.kiosk`.
    key: str
    # The template under `ui/templates/`.
    template: str
    # The parameters the page reads, from `kiosks.parameters`; empty for none.
    parameters: tuple[str, ...] = ()
    # Shown to anyone, with no login. Every kiosk page is: a kiosk has no
    # login, and standings are public (D5, S29 kept on purpose in #15).
    public: bool = True
    # A page of the racing scene, which shows the leaders of the round that
    # ended while the playlist waits for the operator (D12).
    racing: bool = False

    @property
    def name(self) -> str:
        """`now-racing` for `kiosks/now-racing.kiosk`, the `brief` of
        `kiosk-pages`."""
        return self.key.removeprefix("kiosks/").removesuffix(".kiosk")


# The pages the seeded Racing scene shows, and the other now-racing layouts.
RACING = {
    "now-racing",
    "now-racing-columnar",
    "now-racing-columnar-reversed",
    "ondeck",
    "results-by-racer",
}


def _page(name: str, *parameters: str) -> KioskPage:
    return KioskPage(
        f"kiosks/{name}.kiosk",
        f"kiosk/pages/{name}.html",
        tuple(parameters),
        racing=name in RACING,
    )


# Sorted as PHP's `sort()` sorts `all_kiosk_pages()`.
PAGES: dict[str, KioskPage] = {
    p.key: p
    for p in (
        _page("award-presentations", "confetti"),
        _page("derbynet"),
        _page("flag"),
        _page("identify"),
        _page("now-racing"),
        _page("now-racing-columnar"),
        _page("now-racing-columnar-reversed"),
        _page("ondeck"),
        _page("please-check-in", "classids"),
        _page("qrcode", "title", "content"),
        _page("results-by-racer"),
        _page("scale-MPH"),
        _page("slideshow", "title", "subdir", "classids"),
        _page("standings", "key"),
        _page("welcome"),
    )
}


def page_for(key: str) -> KioskPage:
    """The registry entry for `key`, or the identify page for a key with
    none."""
    return PAGES.get(key) or PAGES[IDENTIFY]


def is_built_in(key: str) -> bool:
    return key in PAGES


@dataclass(frozen=True)
class Legacy:
    page: str
    params: str
    reload: bool
    # The page string the registry has no entry for, mapped to identify.
    unknown: str = ""


def from_legacy(raw: str) -> Legacy:
    """Split a legacy `Kiosks.page` or `SceneKiosk.page` string, `!` +
    page + `#` + parameters, into the key, the parameters text and the
    reload flag. PHP never parses the parameters, so their text is kept
    (`testing/test-scenes.sh` stores `{confetti:false}`). A page with no
    registry entry becomes the identify page with no parameters, and a
    warning names it (D10)."""
    text = raw or ""
    reload = text.startswith("!")
    page, _, params = text.removeprefix("!").partition("#")
    if page in PAGES:
        return Legacy(page, params, reload)
    logger.warning("Kiosk page %r is not a built-in page; showing identify.", page)
    return Legacy(IDENTIFY, "", reload, unknown=page)


def legacy_string(page: str, params: str, reload: bool = False) -> str:
    """The legacy string for a page, its parameters and its reload flag."""
    return ("!" if reload else "") + page + (f"#{params}" if params else "")


def setting(page: str, params: str, *, name: str | None, reload: bool) -> dict:
    """The legacy `kiosk-setting` of `poll.kiosk`. `parameters` is a JSON
    string the client parses, `"{}"` for none. A kiosk with no row yet, or
    no page, has no `name` key, as `kiosk_page()` returns none."""
    if name is None or not page:
        return {"page": page or IDENTIFY, "parameters": NO_PARAMS, "reload": False}
    return {
        "page": page,
        "name": name,
        "reload": reload,
        "parameters": params or NO_PARAMS,
    }
