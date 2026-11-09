"""The settings page's ICE servers field in a browser (#241), at 390x844 and
1280x800: two lines save and reach the remote camera's `RTCPeerConnection`,
a turn: line is refused with the field's error, clearing the field leaves
the camera no ICE server, and the field takes no URL Chromium's own parsing
throws on. The Replay section's screenshot, for D75."""

from playwright.sync_api import expect

from core.models import RaceSettings
from racing.services import replay
from ui.tests.browser.conftest import assert_accessible, htmx_post, shoot

DEFAULT = "stun:stun.l.google.com:19302"


def open_replay_section(page):
    page.goto("/settings/")
    section = page.locator('details[data-section="replay"]')
    if section.get_attribute("open") is None:
        section.locator("summary").click()
    expect(section).to_have_attribute("open", "")
    return section


def ice_field(page):
    return page.get_by_label("STUN servers for the remote camera")


def type_and_leave(page, text):
    """Type into the text area and leave it, which fires its change."""
    with htmx_post(page, "/settings/save/"):
        ice_field(page).fill(text)
        ice_field(page).blur()


def camera_ice_servers(page) -> list:
    """The `iceServers` the remote camera's own `RTCPeerConnection` takes
    from the camera page's settings."""
    page.goto("/replay/camera/")
    page.wait_for_function("typeof camera_rtc_configuration === 'function'")
    return page.evaluate(
        "() => new RTCPeerConnection(camera_rtc_configuration())"
        ".getConfiguration().iceServers"
    )


def test_two_lines_save_and_reach_the_camera(login, event):
    page = login("RaceCoordinator")
    section = open_replay_section(page)
    expect(ice_field(page)).to_have_value(DEFAULT)

    type_and_leave(page, "stun:a.example:3478\nstuns:b.example")

    expect(page.locator("#field-replay-ice-servers [data-saved]")).to_be_visible()
    expect(ice_field(page)).to_have_value("stun:a.example:3478\nstuns:b.example")
    row = RaceSettings.for_event(event)
    assert row.replay_ice_servers == "stun:a.example:3478 stuns:b.example"
    section.scroll_into_view_if_needed()
    assert shoot(page, "settings-replay-ice-servers", full_page=False).exists()
    assert_accessible(page)

    servers = camera_ice_servers(page)
    assert [s["urls"] for s in servers] == [["stun:a.example:3478", "stuns:b.example"]]


def test_a_turn_line_is_refused_with_the_fields_error(login, event):
    page = login("RaceCoordinator")
    open_replay_section(page)

    type_and_leave(page, "stun:a.example\nturn:b.example")

    error = page.locator("#field-replay-ice-servers [role=alert]")
    expect(error).to_contain_text("username and password")
    expect(page.locator("#field-replay-ice-servers [data-saved]")).to_have_count(0)
    # The refused text stays, to be corrected.
    expect(ice_field(page)).to_have_value("stun:a.example\nturn:b.example")
    assert RaceSettings.for_event(event).replay_ice_servers == DEFAULT

    type_and_leave(page, "stun:a.example")
    expect(page.locator("#field-replay-ice-servers [data-saved]")).to_be_visible()
    expect(error).to_have_count(0)


def test_clearing_the_field_leaves_the_camera_no_ice_server(login, event):
    page = login("RaceCoordinator")
    open_replay_section(page)

    type_and_leave(page, "")

    expect(page.locator("#field-replay-ice-servers [data-saved]")).to_be_visible()
    assert RaceSettings.for_event(event).replay_ice_servers == ""
    assert camera_ice_servers(page) == []


# URLs that Chromium's RTCPeerConnection and the field agree on: True for
# taken, False for refused (Chromium throws).
URLS = (
    ("stun:a.example", True),
    ("stuns:a.example", True),
    ("STUN:a.example", True),
    ("Stuns:a.example", True),
    ("sTuN:a.example:3478", True),
    ("stun:a.example:65535", True),
    ("stun:[::1]:3478", True),
    ("stun:[fe80::1%25eth0]:3478", True),
    ("stun:[fe80::1%eth0]", True),
    ("stun:1.2.3.4", True),
    ("stun:a_b.example", True),
    ("turn:a.example", False),
    ("TURN:a.example", False),
    ("turns:a.example", False),
    ("stun:", False),
    ("stun::3478", False),
    ("stun:a.example:", False),
    ("stun:a.example:abc", False),
    ("stun:a.example:0", False),
    ("stun:a.example:99999", False),
    ("stun:[::1]:65536", False),
    ("stun:a.example?transport=udp", False),
    ("stun:a.example:3478/x", False),
    ("stun://a.example", False),
    ("http://a.example", False),
    ("a.example", False),
    # Review round 1's host characters (#241).
    *((f"stun:a{c}b", False) for c in '"<>\\^`{|}'),
)

# URLs Chromium takes and the field refuses: refusing more is safe.
OVER_REFUSED = (
    "stun:a.example:000001",
    "stun:[::1]x",
    "stun:[gg::1]",
    "stun:ünï.example",
)

# `stun:a<c>b` for every printable ASCII character but the space.
SWEEP = tuple(chr(code) for code in range(33, 127))

CHROMIUM_TAKES = """urls => urls.map(u => {
  try { new RTCPeerConnection({iceServers: [{urls: [u]}]}); return true; }
  catch (e) { return false; }
})"""


def _taken(url: str) -> bool:
    try:
        replay.ice_servers_text(url)
    except replay.IceServersRefused:
        return False
    return True


def test_the_field_takes_what_chromium_takes(login, event):
    """The field takes no URL that makes Chromium's `RTCPeerConnection`
    throw, over every printable ASCII character in a host and the listed
    URLs, and takes each listed URL Chromium takes but the over-refused
    ones. A `turn:` URL throws for want of a credential, which
    `ice_servers()` does not send. The field takes every host character
    Chromium takes but the comma, which separates URLs."""
    page = login("RaceCoordinator")
    page.goto("/settings/")

    listed = [url for url, _ in URLS]
    chromium = page.evaluate(CHROMIUM_TAKES, listed)
    assert dict(zip(listed, chromium, strict=True)) == dict(URLS)
    assert {url: _taken(url) for url in listed} == dict(URLS)

    assert page.evaluate(CHROMIUM_TAKES, list(OVER_REFUSED)) == [True] * len(
        OVER_REFUSED
    )
    assert [_taken(url) for url in OVER_REFUSED] == [False] * len(OVER_REFUSED)

    swept = page.evaluate(CHROMIUM_TAKES, [f"stun:a{c}b" for c in SWEEP])
    chromium_chars = {c for c, ok in zip(SWEEP, swept, strict=True) if ok}
    field_chars = {c for c in SWEEP if _taken(f"stun:a{c}b")}
    assert field_chars <= chromium_chars, sorted(field_chars - chromium_chars)
    assert chromium_chars - field_chars == {","}


def test_a_stored_value_shows_one_url_per_line(login, event):
    """A value stored under the legacy key with spaces and commas between
    its URLs shows one per line."""
    RaceSettings.objects.filter(event=event).update(
        replay_ice_servers="stun:a.example, stun:b.example stun:c.example"
    )
    page = login("RaceCoordinator")
    open_replay_section(page)
    expect(ice_field(page)).to_have_value(
        "stun:a.example\nstun:b.example\nstun:c.example"
    )
