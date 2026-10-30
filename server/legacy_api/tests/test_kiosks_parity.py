"""The kiosk endpoints against the PHP captures (#34): `capture/scenarios/
10-kiosks.sh` replayed step by step, as `test_race_control_parity` runs its
scenarios. The exchanges `deviations.json` lists (C18, C20) must match their
expected Django output instead, and must differ from PHP's (D25).

`WRITE_DEVIATIONS=1 uv run pytest legacy_api/tests/test_kiosks_parity.py`
rewrites the #34 kiosk entries and their expected transcripts.
"""

import os

import pytest

from legacy_api.tests.test_race_control_parity import (
    Scenario,
    _dump,
    assert_listed,
    listed_claim,
    write_deviations,
)

pytestmark = pytest.mark.django_db

ADDRESSES = (
    "192.168.1.10 192.168.1.11 192.168.1.12 192.168.1.13 192.168.1.14 192.168.1.15"
    " 192.168.1.16 192.168.1.17 192.168.1.18 192.168.1.19 10.0.0.1 10.0.0.2"
    " 10.0.0.254 172.16.5.5 fe80::1 localhost 00:1A:2B:3C:4D:5E AA:BB:CC:DD:EE:FF"
    " kiosk-with-a-name 127.0.0.1"
).split()
PAGES = (
    "award-presentations derbynet flag identify now-racing-columnar-reversed"
    " now-racing-columnar now-racing ondeck please-check-in qrcode"
    " results-by-racer scale-MPH slideshow standings welcome"
).split()

POLL_KIOSK = "http/kiosks/poll-kiosk.http"
KIOSK_PAGES = "http/kiosks/pages.http"


def run_kiosks(client) -> Scenario:
    s = Scenario(client)
    s.reset()
    s.post_ok("action=settings.write&unused-lane-mask=0&n-lanes=4")

    s.scenario(POLL_KIOSK)
    for address in ADDRESSES:
        s.capture(f"first-poll-from-{address}")
    s.capture("poll-without-an-address")
    s.capture("poll-kiosk-all-after-twenty-addresses")

    s.scenario(KIOSK_PAGES)
    for page in PAGES:
        s.capture(f"assign-{page}")
        s.capture(f"poll-{page}")
    for label in (
        "assign-with-parameters",
        "poll-with-parameters",
        "assign-name",
        "poll-named",
        "reload",
        "poll-after-reload",
        "poll-kiosk-all",
    ):
        s.capture(label)
    return s


def test_the_kiosk_scenario_matches_php(client, event):
    s = run_kiosks(client)
    _dump(s)
    assert s.mismatches() == []


SCENARIOS = {"kiosks": run_kiosks}
# Every kiosk.assign reply carries the kiosk list, as poll.kiosk.all does.
_LISTS = [
    f"{n:03d} assign-{page}" for n, page in zip(range(1, 31, 2), PAGES, strict=True)
] + ["031 assign-with-parameters", "033 assign-name", "037 poll-kiosk-all"]
# quirk: (reason, scenario, {fixture: [exchange names]}, expected file name)
DEVIATIONS = {
    "C18": (
        "action.kiosk.reload.inc never calls json_success(), so PHP's reply"
        " stays in-progress; Django answers success (C18).",
        "kiosks",
        {KIOSK_PAGES: ["035 reload"]},
        "reload.http",
    ),
    "C20": (
        "all_kiosks() reads $row['params'], a key that does not exist, so PHP"
        " lists every kiosk's parameters as null; Django lists each kiosk's"
        ' own, "{}" when it has none (C20).',
        "kiosks",
        {
            POLL_KIOSK: ["022 poll-kiosk-all-after-twenty-addresses"],
            KIOSK_PAGES: _LISTS,
        },
        None,
    ),
}


def test_every_listed_deviation_is_a_reply_the_scenario_makes():
    assert_listed(DEVIATIONS, SCENARIOS)


DEVIATION_CLAIM = listed_claim(DEVIATIONS)


@pytest.mark.skipif(
    not os.environ.get("WRITE_DEVIATIONS"), reason="writes the #34 kiosk entries"
)
def test_write_kiosk_deviations(event):
    write_deviations(DEVIATIONS, SCENARIOS)
