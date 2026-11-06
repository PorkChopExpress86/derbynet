"""Every write service refuses a read-only event (#212; #9, Past events).

The refusal is in `lock_race_state`, which every writer takes (#20), and in
`require_writable`. The check is a lint over the service modules: a public
function that writes through the ORM, itself or through a function of its
module, must take `lock_race_state` (without `writable=False`) or call
`require_writable`, itself or through a function of its module. A function
whose first parameter is the `RaceState` its caller locked (`race`,
`state`) runs under that lock. `UNGUARDED` lists the rest, with the reason
each may write a read-only event; a new write service is in neither and
fails here. `legacy_api/tests/test_read_only_event.py` sends every legacy
action to a read-only event.
"""

import ast
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
ORM_WRITE = re.compile(
    r"\.(save|create|update|delete|bulk_create|bulk_update|get_or_create"
    r"|update_or_create)\("
)
# Service modules outside a `services` module or package.
SERVICE_FILES = (
    "awards/editing.py",
    "awards/balloting.py",
    "awards/presentation.py",
    "core/events.py",
    "core/race_settings.py",
    "core/settings_form.py",
    "core/settings_page.py",
    "core/setup_event.py",
    "media/photos.py",
    "media/videos.py",
    "kiosks/messages.py",
    "transfer/backups.py",
    "transfer/batch_import.py",
    "transfer/export.py",
)
FIELD_CLIENT = "what a timer, kiosk, replay station or voter writes to stay connected"
UNDER_CALLERS_LOCK = "a step its callers run under their own lock_race_state"
UNGUARDED = {
    "accounts/services.py:change_password": "roles are site-wide (ADR-07)",
    "awards/balloting.py:voter": "a voter's visit; vote.cast refuses",
    "awards/services.py:reconcile_partition_lists": UNDER_CALLERS_LOCK,
    "core/services.py:new_event": "makes an event: the lifecycle itself",
    "core/events.py:create_event": "makes an event: the lifecycle itself",
    "core/events.py:make_active": "the lifecycle itself, under its own locks",
    "core/events.py:switch_to": "the lifecycle itself, under its own locks",
    "core/events.py:archive": "the lifecycle itself, under its own locks",
    "core/events.py:reopen": "the lifecycle itself, under its own locks",
    "kiosks/messages.py:send": "WebRTC signalling for the replay camera",
    "kiosks/messages.py:retrieve": FIELD_CLIENT,
    "kiosks/services.py:kiosk_setting": FIELD_CLIENT,
    "kiosks/services.py:all_kiosks": FIELD_CLIENT,
    "kiosks/services.py:dashboard": FIELD_CLIENT,
    "media/photos.py:find_or_make": "a thumbnail of a stored photo",
    "media/photos.py:racer_photo": "a thumbnail of a stored photo",
    "media/photos.py:autocrop": UNDER_CALLERS_LOCK,
    "racing/services/audit.py:write": UNDER_CALLERS_LOCK,
    "racing/services/finals.py:place_allowed_racers": UNDER_CALLERS_LOCK,
    "racing/services/heats.py:release_emptied_heats_left_behind": UNDER_CALLERS_LOCK,
    "racing/services/lanes.py:close_lanes": UNDER_CALLERS_LOCK,
    "racing/services/master.py:release_passed_emptied_heats": UNDER_CALLERS_LOCK,
    "racing/services/playlist.py:apply_due_scene": "a kiosk poll shows a scene"
    " that fell due before the switch",
    "racing/services/polls.py:poll": FIELD_CLIENT,
    "racing/services/replay.py:send": UNDER_CALLERS_LOCK,
    "racing/services/replay.py:poll": FIELD_CLIENT,
    "racing/services/rounds.py:record_cut": UNDER_CALLERS_LOCK,
    "racing/services/timer.py:check_contact": FIELD_CLIENT,
    "racing/services/timer.py:handle_message": "the timer's connection;"
    " FINISHED refuses its results",
    "racing/services/timer.py:decode_flags": FIELD_CLIENT,
    "racing/services/timer.py:take_remote_start": FIELD_CLIENT,
    "racing/services/timer.py:append_log": FIELD_CLIENT,
    "racing/services/workbook_files.py:xlsx": "saves a workbook file, not a row",
    "roster/services/groups.py:record_partition_structure": UNDER_CALLERS_LOCK,
    "roster/services/weigh_in.py:record_locked": UNDER_CALLERS_LOCK,
    "transfer/export.py:export_event": "reads; writes only the zip",
}


def service_modules() -> list[Path]:
    found = {*SERVER.glob("*/services.py"), *SERVER.glob("*/services/*.py")}
    found |= {SERVER / name for name in SERVICE_FILES}
    return sorted(p for p in found if p.name != "__init__.py")


def _locks(node: ast.AST) -> bool:
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        name = ast.unparse(call.func)
        if name.endswith("require_writable"):
            return True
        if name.endswith("lock_race_state") and not any(
            k.arg == "writable" for k in call.keywords
        ):
            return True
    return False


def _writes(node: ast.AST) -> bool:
    return bool(ORM_WRITE.search(ast.unparse(node)))


def unguarded(source: str) -> list[str]:
    """The public functions of `source` that write and never refuse."""
    tree = ast.parse(source)
    functions = {f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)}

    def callees(f):
        return {
            ast.unparse(c.func)
            for c in ast.walk(f)
            if isinstance(c, ast.Call) and ast.unparse(c.func) in functions
        }

    def reaches(name, test, seen=frozenset()):
        f = functions[name]
        return test(f) or any(
            reaches(c, test, seen | {name}) for c in callees(f) if c not in seen
        )

    found = []
    for name, f in functions.items():
        if name.startswith("_"):
            continue
        params = f.args.args
        if params and (
            params[0].arg in ("race", "state")
            or "RaceState" in ast.unparse(params[0].annotation or ast.Constant(""))
        ):
            continue
        if reaches(name, _writes) and not reaches(name, _locks):
            found.append(name)
    return found


def test_every_write_service_refuses_a_read_only_event():
    found = {
        f"{path.relative_to(SERVER)}:{name}"
        for path in service_modules()
        for name in unguarded(path.read_text(encoding="utf-8"))
    }

    assert found - set(UNGUARDED) == set(), (
        "Take lock_race_state(event_id) first in each of these, so a read-only"
        " event refuses it, or add it to UNGUARDED with the reason it may write"
        " a past event."
    )
    assert set(UNGUARDED) - found == set(), "UNGUARDED names a function it need not"


def test_the_check_flags_a_write_without_the_lock():
    assert unguarded(
        "def rename(group, name):\n    group.name = name\n    group.save()\n"
    ) == ["rename"]


def test_the_check_passes_a_write_under_the_lock_or_a_helper_that_takes_it():
    assert (
        unguarded(
            "def rename(group, name):\n"
            "    with transaction.atomic():\n"
            "        lock_race_state(group.event_id)\n"
            "        group.save()\n"
            "def _locked(event):\n"
            "    lock_race_state(event.pk)\n"
            "def other(event):\n"
            "    _locked(event)\n"
            "    Group.objects.create(event=event)\n"
        )
        == []
    )


def test_the_check_flags_a_lock_that_does_not_refuse():
    assert unguarded(
        "def rename(group, name):\n"
        "    lock_race_state(group.event_id, writable=False)\n"
        "    group.save()\n"
    ) == ["rename"]
