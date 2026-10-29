from datetime import UTC, datetime
from pathlib import Path

import pytest
from django.apps import apps

from core import legacy_keys
from core.legacy_keys import (
    EVENT_FOLDER,
    REGISTRY,
    Balloting,
    Bool,
    CurrentRound,
    Dropped,
    Float,
    FullFieldCalc,
    Int,
    LastHeat,
    LegacyValueError,
    Mapped,
    Prefix,
    Ref,
    ReplayQueue,
    StagedHeat,
    Str,
    TimerState,
    UnixTime,
    decode,
    encode,
    entry_for,
)
from core.services import new_event
from racing.models import TimerStatus

# Frozen output of the grep command in #12, run against website/ before the
# cutover deletes it, plus the one key that PHP names through a constant.
KEYS_FILE = Path(__file__).parent / "data" / "legacy_raceinfo_keys.txt"
LEGACY_KEYS = [
    line for line in KEYS_FILE.read_text().splitlines() if not line.startswith("#")
]

T1 = datetime(2026, 10, 24, 15, 30, 5, tzinfo=UTC)
T2 = datetime(2026, 10, 24, 15, 31, tzinfo=UTC)


def test_the_frozen_key_list_has_the_89_grep_literals_and_one_constant():
    assert len(LEGACY_KEYS) == len(set(LEGACY_KEYS)) == 90


def test_every_legacy_key_has_a_target_or_a_dropped_reason():
    for key in LEGACY_KEYS:
        entry = REGISTRY[key]
        if isinstance(entry, Dropped):
            assert entry.reason, key
        elif isinstance(entry, Prefix):
            assert entry.members, key
        else:
            assert isinstance(entry, Mapped), key


def test_no_registry_entry_names_a_key_outside_the_file():
    assert set(REGISTRY) <= set(LEGACY_KEYS)


def _targets():
    for key, entry in REGISTRY.items():
        if isinstance(entry, Prefix):
            yield from ((pattern, member) for pattern, member in entry.members.items())
        elif isinstance(entry, Mapped):
            yield key, entry


@pytest.mark.parametrize(("key", "entry"), list(_targets()))
def test_every_target_names_real_model_fields(key, entry):
    model = apps.get_model(entry.model)
    for field in entry.fields:
        model._meta.get_field(field)


@pytest.mark.parametrize(
    ("key", "field"), [("tt-lane3", "test_results"), ("tt-place12", "test_results")]
)
def test_timer_test_lane_keys_resolve_through_the_tt_prefix(key, field):
    assert entry_for(key).fields == (field,)


def test_unknown_and_dropped_keys_do_not_decode():
    with pytest.raises(KeyError):
        decode("no-such-key", "1")
    with pytest.raises(KeyError):
        decode("token-salt", "abc")


@pytest.mark.django_db
def test_defaults_match_the_rows_new_event_creates():
    event = new_event("Derby")
    for key, entry in _targets():
        if entry.default is EVENT_FOLDER or not entry.fields:
            continue
        row = apps.get_model(entry.model).objects.get(event=event)
        if len(entry.fields) == 1:
            field = row._meta.get_field(entry.fields[0])
            actual = getattr(row, field.attname)
        else:
            actual = {
                name: getattr(row, row._meta.get_field(name).attname)
                for name in entry.fields
            }
        if key.startswith("tt-lane") or key.startswith("tt-place"):
            assert row.test_results == {}
            continue
        assert actual == entry.default, key


ROUND_TRIPS = [
    (Int(), 0),
    (Int(), 2500),
    (Int(), -1),
    (Int(), None),
    (Float(), 3.125),
    (Float(), 4.0),
    (Float(), None),
    (Str(), ""),
    (Str(), "%5.3f"),
    (Str(), "a+b#c"),
    (Bool(), True),
    (Bool(), False),
    (Ref("roster.Group"), 12),
    (Ref("roster.Group"), None),
    (Ref("roster.Group", none="-1"), None),
    (UnixTime(), T1),
    (UnixTime(), None),
    (UnixTime(none="0"), None),
    (
        TimerState(),
        {
            "state": 3,
            "last_contact": T1,
            "assume_connected_for_test": False,
            "malfunction_expires_at": T2,
            "malfunction_requires_reset": False,
            "malfunction_message": "Lane 2 sensor + jammed",
        },
    ),
    (
        TimerState(),
        {
            "state": 2,
            "last_contact": None,
            "assume_connected_for_test": True,
            "malfunction_expires_at": None,
            "malfunction_requires_reset": False,
            "malfunction_message": "",
        },
    ),
    (
        TimerState(),
        {
            "state": 5,
            "last_contact": T1,
            "assume_connected_for_test": False,
            "malfunction_expires_at": None,
            "malfunction_requires_reset": True,
            "malfunction_message": "Reset the timer",
        },
    ),
    (LastHeat(), None),
    (LastHeat(), {"round_id": 4, "heat": 2, "results": None}),
    (LastHeat(), {"round_id": 4, "heat": 2, "results": [3.125, None, 2.5]}),
    (StagedHeat(), {"staged_round": None, "staged_heat": None}),
    (StagedHeat(), {"staged_round": 7, "staged_heat": 3}),
    (StagedHeat(), {"staged_round": None, "staged_heat": 2}),
    (CurrentRound(), {"current_round": None, "timer_test": False}),
    (CurrentRound(), {"current_round": None, "timer_test": True}),
    (CurrentRound(), {"current_round": 9, "timer_test": False}),
    (FullFieldCalc(), {"full_field_rule": "standard", "full_field_group": None}),
    (FullFieldCalc(), {"full_field_rule": "none", "full_field_group": None}),
    (FullFieldCalc(), {"full_field_rule": "group", "full_field_group": 5}),
    (Balloting(), True),
    (Balloting(), False),
    (ReplayQueue(), []),
    (ReplayQueue(), ["START car", "REPLAY 4 2 50"]),
]


@pytest.mark.parametrize(("codec", "value"), ROUND_TRIPS)
def test_decode_of_encode_returns_the_value(codec, value):
    assert codec.decode(codec.encode(value)) == value


def test_every_codec_class_has_a_round_trip_case():
    used = {type(codec) for codec, _ in ROUND_TRIPS}
    defined = {
        cls
        for cls in vars(legacy_keys).values()
        if isinstance(cls, type)
        and issubclass(cls, legacy_keys.Codec)
        and cls not in (legacy_keys.Codec, legacy_keys.Packed)
    }
    assert defined == used


@pytest.mark.parametrize("raw", ["", "0"])
def test_bool_reads_empty_and_zero_as_false_like_php(raw):
    assert Bool().decode(raw) is False


@pytest.mark.parametrize("raw", ["1", "true", "false", "0.0", " "])
def test_bool_reads_other_text_as_true_like_php(raw):
    assert Bool().decode(raw) is True


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("timer_state", "3+1793028605+1793028660+Lane 2 jammed"),
        ("timer_state", "2+-1++"),
        ("timer_state", "5+1793028605+0+bad"),
        ("last_heat", "4#2"),
        ("last_heat", "4#2#3.125##2.5"),
        ("timer_current", "7#3"),
        ("RoundID", "-100"),
        ("RoundID", "9"),
        ("ClassID", "-1"),
        ("full-field-calc", "-1"),
        ("full-field-calc", "5"),
        ("balloting", "open"),
        ("replay_queue", "START car\nREPLAY 4 2 50\n"),
        ("remote-start", "0"),
        ("tt-lane2", "3.125"),
        ("tt-place2", "1"),
    ],
)
def test_canonical_legacy_text_survives_decode_and_encode(key, raw):
    assert encode(key, decode(key, raw)) == raw


def test_timer_state_decodes_the_packed_fields():
    assert decode("timer_state", "5+1793028605++no heartbeat") == {
        "state": 5,
        "last_contact": datetime.fromtimestamp(1793028605, tz=UTC),
        "assume_connected_for_test": False,
        "malfunction_expires_at": None,
        "malfunction_requires_reset": False,
        "malfunction_message": "no heartbeat",
    }


def test_timer_state_pads_short_legacy_values_as_php_does():
    assert decode("timer_state", "2") == {
        "state": 2,
        "last_contact": None,
        "assume_connected_for_test": False,
        "malfunction_expires_at": None,
        "malfunction_requires_reset": False,
        "malfunction_message": "",
    }


def test_timer_state_reads_expiration_0_as_a_malfunction_that_waits_for_reset():
    decoded = decode("timer_state", "5+0+0+bad")

    assert decoded["malfunction_requires_reset"] is True
    assert decoded["malfunction_expires_at"] is None


@pytest.mark.django_db
def test_a_sticky_malfunction_saves_on_the_timer_status():
    status = TimerStatus.for_event(new_event("Derby"))
    for name, value in decode("timer_state", "5+0+0+bad").items():
        setattr(status, name, value)
    status.save()

    status.refresh_from_db()
    assert status.malfunction_requires_reset is True
    assert status.malfunction_expires_at is None


@pytest.mark.parametrize("raw", ["-1", "-1793028605"])
def test_unix_time_refuses_a_negative_time(raw):
    with pytest.raises(ValueError):
        UnixTime().decode(raw)


@pytest.mark.parametrize(
    ("key", "raw"),
    [
        ("lane_count", "3.0"),
        ("lane_count", "abc"),
        ("tt-lane2", "abc"),
        ("racing_scene", "abc"),
        ("reschedule_underway", "-1"),
        ("reschedule_underway", "99999999999999999999"),
        ("timer_state", "abc+0++"),
        ("timer_current", "7"),
        ("last_heat", "4#x"),
    ],
)
def test_unreadable_legacy_text_raises_legacy_value_error_naming_the_key(key, raw):
    with pytest.raises(LegacyValueError) as raised:
        decode(key, raw)

    assert (raised.value.key, raised.value.raw) == (key, raw)
