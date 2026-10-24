"""Registry of every legacy `RaceInfo` key name.

The PHP server keeps settings and live state as text in `RaceInfo(itemkey,
itemvalue)`. The Django models type them instead. This registry maps each
legacy key to the model fields that replace it, with a codec between the
legacy text and the typed value, so the importer, preferences files, and the
`settings.*` endpoints keep the legacy vocabulary.

`decode(key, raw)` turns legacy text into a typed value and `encode(key,
value)` turns it back. A `Ref` value is an id, which the importer maps
through `transfer.LegacyId`. Times become aware UTC datetimes.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


class Codec:
    def decode(self, raw: str) -> Any:
        raise NotImplementedError

    def encode(self, value: Any) -> str:
        raise NotImplementedError


def _number(value):
    """Format a number as PHP writes it: `3`, not `3.0`."""
    return str(int(value)) if float(value).is_integer() else repr(float(value))


class Int(Codec):
    def decode(self, raw):
        return None if raw == "" else int(raw)

    def encode(self, value):
        return "" if value is None else str(int(value))


class Float(Codec):
    def decode(self, raw):
        return None if raw == "" else float(raw)

    def encode(self, value):
        return "" if value is None else _number(value)


class Str(Codec):
    def decode(self, raw):
        return raw

    def encode(self, value):
        return value


class Bool(Codec):
    """PHP truthiness: `""` and `"0"` are false, and any other text is true."""

    def decode(self, raw):
        return raw not in ("", "0")

    def encode(self, value):
        return "1" if value else "0"


@dataclass(frozen=True)
class Ref(Codec):
    """An id of `model`. Zero, a negative id, or `""` means none."""

    model: str
    none: str = ""

    def decode(self, raw):
        if raw == "" or int(raw) <= 0:
            return None
        return int(raw)

    def encode(self, value):
        return self.none if value is None else str(value)


def _from_unix(seconds):
    return datetime.fromtimestamp(int(seconds), tz=UTC)


def _to_unix(moment):
    return str(int(moment.timestamp()))


@dataclass(frozen=True)
class UnixTime(Codec):
    """Unix seconds as an aware datetime. `""` and `"0"` mean none."""

    none: str = ""

    def decode(self, raw):
        return None if raw in ("", "0") else _from_unix(raw)

    def encode(self, value):
        return self.none if value is None else _to_unix(value)


class Packed(Codec):
    """A legacy value that packs several fields into one string."""


class TimerState(Packed):
    """`state+last_contact+malfunction_expiration+message` (`timer-state.inc`).

    A negative `last_contact` marks a test that skips the contact timeout. An
    empty expiration means no malfunction.
    """

    def decode(self, raw):
        parts = raw.split("+", 3) if raw else ["1"]
        state, last_contact, expiration, message = (
            parts + ["0", "", ""][len(parts) - 1 :]
        )
        last_contact = int(last_contact or 0)
        return {
            "state": int(state),
            "last_contact": _from_unix(last_contact) if last_contact > 0 else None,
            "assume_connected_for_test": last_contact < 0,
            "malfunction_expires_at": None
            if expiration == ""
            else _from_unix(expiration),
            "malfunction_message": message,
        }

    def encode(self, value):
        if value["assume_connected_for_test"]:
            last_contact = "-1"
        elif value["last_contact"] is None:
            last_contact = "0"
        else:
            last_contact = _to_unix(value["last_contact"])
        expires = value["malfunction_expires_at"]
        return "+".join(
            [
                str(value["state"]),
                last_contact,
                "" if expires is None else _to_unix(expires),
                value["malfunction_message"],
            ]
        )


class LastHeat(Packed):
    """`roundid#heat`, or `roundid#heat#lane1#lane2...` after a rerun (`last-heat.inc`).

    `results` is None when the heat can be rerun, and the list of captured lane
    results when it can be reinstated. An empty lane is None.
    """

    def decode(self, raw):
        parts = raw.split("#")
        if len(parts) < 2:
            return None
        results = [None if r == "" else float(r) for r in parts[2:]]
        return {
            "round_id": int(parts[0]),
            "heat": int(parts[1]),
            "results": results if len(parts) > 2 else None,
        }

    def encode(self, value):
        if value is None:
            return ""
        parts = [str(value["round_id"]), str(value["heat"])]
        for result in value["results"] or []:
            parts.append("" if result is None else _number(result))
        return "#".join(parts)


class StagedHeat(Packed):
    """`roundid#heat` while a heat is staged on the timer, else `""`."""

    def decode(self, raw):
        if raw == "":
            return {"staged_round": None, "staged_heat": None}
        round_id, heat = raw.split("#")
        return {"staged_round": int(round_id), "staged_heat": int(heat)}

    def encode(self, value):
        if value["staged_round"] is None:
            return ""
        return f"{value['staged_round']}#{value['staged_heat']}"


# Legacy RoundID while the timer is being tested (TIMER_TEST_ROUNDID).
TIMER_TEST_ROUND_ID = -100


class CurrentRound(Packed):
    """`RoundID`: a round id, `-1` for none, or `-100` while testing the timer."""

    def decode(self, raw):
        round_id = int(raw) if raw else -1
        return {
            "current_round": round_id if round_id > 0 else None,
            "timer_test": round_id == TIMER_TEST_ROUND_ID,
        }

    def encode(self, value):
        if value["timer_test"]:
            return str(TIMER_TEST_ROUND_ID)
        return "-1" if value["current_round"] is None else str(value["current_round"])


class FullFieldCalc(Packed):
    """`full-field-calc`: `0` standard, `-1` none, or an aggregate group's id."""

    def decode(self, raw):
        group_id = int(raw) if raw else 0
        if group_id > 0:
            return {"full_field_rule": "group", "full_field_group": group_id}
        rule = "standard" if group_id == 0 else "none"
        return {"full_field_rule": rule, "full_field_group": None}

    def encode(self, value):
        return {"standard": "0", "none": "-1", "group": str(value["full_field_group"])}[
            value["full_field_rule"]
        ]


class Balloting(Packed):
    """`open` or `closed`."""

    def decode(self, raw):
        return raw == "open"

    def encode(self, value):
        return "open" if value else "closed"


class ReplayQueue(Packed):
    """Commands, each ending in a newline (`replay.inc`)."""

    def decode(self, raw):
        return [line for line in raw.split("\n") if line]

    def encode(self, value):
        return "".join(f"{command}\n" for command in value)


# The setting defaults to a folder of the event's own directory.
EVENT_FOLDER = object()


@dataclass(frozen=True)
class Mapped:
    """A key stored in `fields` of `model`. A packed key fills several fields."""

    model: str
    fields: tuple[str, ...]
    codec: Codec
    default: Any


@dataclass(frozen=True)
class Dropped:
    reason: str


@dataclass(frozen=True)
class Prefix:
    """Keys written as a prefix plus a suffix, such as `tt-lane3`.

    Each member pattern names the full key, with the lane number in group 1.
    """

    members: dict[str, Mapped]


SETTINGS = "core.RaceSettings"
STATE = "core.RaceState"
TIMER = "racing.TimerStatus"
REPLAY = "kiosks.ReplayStatus"


def _setting(field, codec, default):
    return Mapped(SETTINGS, (field,), codec, default)


def _state(field, codec, default):
    return Mapped(STATE, (field,), codec, default)


def _timer(field, codec, default):
    return Mapped(TIMER, (field,), codec, default)


def _replay(field, codec, default):
    return Mapped(REPLAY, (field,), codec, default)


REGISTRY: dict[str, Mapped | Dropped | Prefix] = {
    # Lanes, scheduling, and scoring.
    "lane_count": _setting("lane_count", Int(), 0),
    "unused-lane-mask": _setting("unused_lane_mask", Int(), 0),
    "reverse-lanes": _setting("reverse_lanes", Bool(), False),
    "use-master-sched": _setting("use_master_sched", Bool(), False),
    "use-points": _setting("use_points", Bool(), False),
    "scoring": _setting("scoring", Int(), 0),
    "max-runs-per-car": _setting("max_runs_per_car", Int(), 0),
    "rotation-schedule": _setting("rotation_schedule", Bool(), False),
    "track-length": _setting("track_length", Int(), 40),
    "finish-formatting": _setting("finish_formatting", Str(), "%5.3f"),
    "update-period": _setting("update_period_ms", Int(), 2500),
    "warn-no-timer": _setting("warn_no_timer", Bool(), True),
    "timer-send-logs": _setting("timer_send_logs", Bool(), False),
    # Groups and awards.
    "group-formation-rule": _setting("group_formation_rule", Str(), "by-partition"),
    "use-subgroups": _setting("use_subgroups", Bool(), False),
    "supergroup-label": _setting("supergroup_label", Str(), "Pack"),
    "partition-label": _setting("partition_label", Str(), "Den"),
    "full-field-calc": Mapped(
        SETTINGS,
        ("full_field_rule", "full_field_group"),
        FullFieldCalc(),
        {"full_field_rule": "standard", "full_field_group": None},
    ),
    "n-pack-trophies": _setting("n_pack_trophies", Int(), 3),
    "n-den-trophies": _setting("n_den_trophies", Int(), 3),
    "n-rank-trophies": _setting("n_rank_trophies", Int(), 0),
    "one-trophy-per": _setting("one_trophy_per", Bool(), False),
    "use-xbs": _setting("use_xbs", Bool(), False),
    "xbs-award": _setting("xbs_award", Str(), "Exclusively By Scout"),
    "car-numbering": _setting("car_numbering", Str(), "100+101"),
    "name-style": _setting("name_style", Int(), 0),
    "ballot_password": _setting("ballot_password", Str(), ""),
    # Display.
    "photos-on-now-racing": _setting("photos_on_now_racing", Str(), "head"),
    "show-cars-on-deck": _setting("show_cars_on_deck", Bool(), True),
    "show-racer-photos-rr": _setting("show_racer_photos_rr", Bool(), True),
    "show-car-photos-rr": _setting("show_car_photos_rr", Bool(), True),
    "now-racing-linger-ms": _setting("now_racing_linger_ms", Int(), 10000),
    "lane-colors": _setting("lane_colors", Str(), ""),
    "images-dir": _setting("images_dir", Str(), "Generic"),
    "racing_scene": _setting("racing_scene", Ref("kiosks.Scene"), None),
    "upload-videos": _setting("upload_videos", Bool(), True),
    # Replay.
    "replay-skipback": _setting("replay_skipback_ms", Int(), 4000),
    "replay-num-showings": _setting("replay_num_showings", Int(), 2),
    "replay-rate": _setting("replay_rate", Int(), 50),
    # Folders.
    "photo-directory": _setting("photo_directory", Str(), EVENT_FOLDER),
    "car-photo-directory": _setting("car_photo_directory", Str(), EVENT_FOLDER),
    "video-directory": _setting("video_directory", Str(), EVENT_FOLDER),
    "logs-directory": _setting("logs_directory", Str(), EVENT_FOLDER),
    # Websocket relay.
    "_websocket_url": _setting("websocket_url", Str(), ""),
    "_ws_trigger_port": _setting("ws_trigger_port", Str(), ""),
    # Current heat.
    "ClassID": _state("current_group", Ref("roster.Group", none="-1"), None),
    "RoundID": Mapped(
        STATE,
        ("current_round", "timer_test"),
        CurrentRound(),
        {"current_round": None, "timer_test": False},
    ),
    "Heat": _state("current_heat", Int(), None),
    "NowRacingState": _state("racing", Bool(), False),
    "last_heat": _state("last_heat", LastHeat(), None),
    "refused-results": _state("refused_results", Int(), 0),
    "reschedule_underway": _state("reschedule_started_at", UnixTime(), None),
    "fake-racers": _state("fake_racers", Bool(), False),
    "balloting": _state("balloting_open", Balloting(), False),
    "current_scene": _state("current_scene", Ref("kiosks.Scene"), None),
    "playlist_scene": _state("playlist_scene", Ref("kiosks.Scene"), None),
    "playlist_scene_at": _state("playlist_scene_at", UnixTime(), None),
    "current_award_key": _state("current_award_key", Str(), ""),
    "reveal_current_award": _state("reveal_current_award", Bool(), False),
    # Timer.
    "timer_state": Mapped(
        TIMER,
        (
            "state",
            "last_contact",
            "assume_connected_for_test",
            "malfunction_expires_at",
            "malfunction_message",
        ),
        TimerState(),
        {
            "state": 1,
            "last_contact": None,
            "assume_connected_for_test": False,
            "malfunction_expires_at": None,
            "malfunction_message": "",
        },
    ),
    "timer_current": Mapped(
        TIMER,
        ("staged_round", "staged_heat"),
        StagedHeat(),
        {"staged_round": None, "staged_heat": None},
    ),
    "timer-agent": _timer("agent", Str(), ""),
    "timer-interface": _timer("interface", Str(), ""),
    "timer-type": _timer("timer_type", Str(), ""),
    "timer-human": _timer("human", Str(), ""),
    "timer-ident": _timer("ident", Str(), ""),
    "timer-options": _timer("options", Str(), ""),
    "timer-vid": _timer("vid", Str(), ""),
    "timer-pid": _timer("pid", Str(), ""),
    "reported_lane_count": _timer("reported_lane_count", Int(), None),
    "timer-log": _timer("log_path", Str(), ""),
    "remote-start": _timer("remote_start_seen_at", UnixTime(none="0"), None),
    "remote-start-go": _timer("remote_start_requested", Bool(), False),
    # Timer test.
    "tt-roundid": _timer("test_saved_round", Ref("racing.Round", none="-1"), None),
    "tt-heat": _timer("test_saved_heat", Int(), None),
    "tt-mask": _timer("test_lane_mask", Int(), 0),
    "tt-results-heat": _timer("test_results_heat", Int(), None),
    "tt-": Prefix(
        {
            r"tt-lane(\d+)": _timer("test_results", Float(), None),
            r"tt-place(\d+)": _timer("test_results", Int(), None),
        }
    ),
    # Replay.
    "replay_state": _replay("state", Int(), 1),
    "replay_reported": _replay("reported", Str(), ""),
    "replay_last_contact": _replay("last_contact", UnixTime(none="0"), None),
    "replay_trouble": _replay("trouble", Str(), ""),
    "replay_queue": Mapped("kiosks.ReplayCommand", (), ReplayQueue(), []),
    # Dropped.
    "current-scene": Dropped(
        "Nothing writes it. Readers get current_scene, which scenes.inc writes."
    ),
    "drop-slowest": Dropped("The importer converts it to scoring=1."),
    "show-racer-photos": Dropped("The importer converts it to photos-on-now-racing."),
    "schema": Dropped("Only the importer reads it, to pick the legacy schema."),
    "token-salt": Dropped("Django signing replaces it (#15)."),
    "kiosk-page": Dropped("An obsolete global kiosk default."),
    "timer-humant": Dropped("A typo for timer-human, read only by about.php."),
}


def entry_for(key):
    """Return the `Mapped` or `Dropped` entry for a legacy key, or raise KeyError."""
    entry = REGISTRY.get(key)
    if isinstance(entry, Mapped | Dropped):
        return entry
    for prefix in REGISTRY.values():
        if isinstance(prefix, Prefix):
            for pattern, member in prefix.members.items():
                if re.fullmatch(pattern, key):
                    return member
    raise KeyError(key)


def _codec(key):
    entry = entry_for(key)
    if isinstance(entry, Dropped):
        raise KeyError(f"{key} is dropped: {entry.reason}")
    return entry.codec


def decode(key, raw):
    return _codec(key).decode(raw)


def encode(key, value):
    return _codec(key).encode(value)
