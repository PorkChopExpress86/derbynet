"""Every write service refuses a read-only event before it writes (#212;
D14, D81).

The refusal is in `lock_race_state`, which every writer takes (#20), and in
`require_writable`. The check is a lint over the service modules: in each
public function, every write, a database row (`ORM_WRITE`) or a file
(`FILE_WRITE`), itself or through a function of its module, must come after
a refusal on every path to it: a refusal earlier in its block or an
enclosing one, in every branch of an `if` before it, or in a `with` block
before it (`_Module`). A function whose first parameter is the `RaceState`
its caller locked (`race`, `state`) runs under that lock. `UNGUARDED` lists
the rest, with the reason each may write a read-only event; a new write
service is in neither and fails here. `legacy_api/tests/test_read_only_event.py`
sends every legacy action to a read-only event.
"""

import ast
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
ORM_WRITE = re.compile(
    r"\.(save|create|update|delete|bulk_create|bulk_update|get_or_create"
    r"|update_or_create)\("
)
# A call that writes a file, or removes or renames one.
FILE_WRITE = re.compile(
    r"\b(write_bytes|write_text|store_upload|_write_atomically|unlink|rmtree"
    r"|utime|copyfile|copytree|shutil\.copy|shutil\.move|os\.remove"
    r"|os\.rename|os\.replace)\("
    r"|\bopen\([^)]*[\"'][wax]b?\+?[\"']"
)
# Service modules outside a `services` module or package.
SERVICE_FILES = (
    "awards/editing.py",
    "awards/balloting.py",
    "awards/presentation.py",
    "core/database_scripts.py",
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
    "awards/balloting.py:ballot": "a voter's visit adds its row; vote.cast refuses",
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
    "media/photos.py:open_render": "a thumbnail of a stored photo",
    "media/photos.py:open_racer_photo": "a thumbnail of a stored photo",
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
    "media/photos.py:store_upload": UNDER_CALLERS_LOCK,
    "media/photos.py:assign_photo": "renders the photo's crop before the"
    " racer's row, which _assign_path writes under the refusing lock",
    "roster/services/racers.py:delete_racer": "the action log records the"
    " attempt after a refusal too, as the dispatcher logs each action",
    "transfer/backups.py:take_snapshot": UNDER_CALLERS_LOCK,
}


def service_modules() -> list[Path]:
    found = {*SERVER.glob("*/services.py"), *SERVER.glob("*/services/*.py")}
    found |= {SERVER / name for name in SERVICE_FILES}
    return sorted(p for p in found if p.name != "__init__.py")


def _refusal_call(call: ast.Call) -> bool:
    name = ast.unparse(call.func)
    if name.endswith("require_writable"):
        return True
    return name.endswith("lock_race_state") and not any(
        k.arg == "writable" for k in call.keywords
    )


def _calls(node: ast.AST) -> list[ast.Call]:
    return [c for c in ast.walk(node) if isinstance(c, ast.Call)]


def _header(statement: ast.stmt) -> list[ast.AST]:
    """What a statement runs itself, without the bodies it holds."""
    match statement:
        case ast.If() | ast.While():
            return [statement.test]
        case ast.For() | ast.AsyncFor():
            return [statement.iter]
        case ast.With() | ast.AsyncWith():
            return [item.context_expr for item in statement.items]
        case ast.Match():
            return [statement.subject]
        case ast.Try() | ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            return []
    return [statement]


class _Module:
    """The functions of one module, with what each writes and refuses.

    A statement writes when it calls an ORM write (`ORM_WRITE`), writes a
    file (`FILE_WRITE`), or calls a function of the module that writes. It
    refuses when it calls `lock_race_state` without `writable=False`,
    `require_writable`, or a function of the module that refuses before it
    writes. A refusal guards what comes after it in its block and in the
    blocks inside; after an `if`, only when every branch refused. A `with`
    block's refusal guards what follows it; a loop's or a `try`'s does not.
    """

    def __init__(self, source: str):
        tree = ast.parse(source)
        self.functions = {
            f.name: f for f in tree.body if isinstance(f, ast.FunctionDef)
        }
        self._writes: dict[str, bool] = {}
        self._refuses: dict[str, bool] = {}

    def writes(self, name: str) -> bool:
        if name not in self._writes:
            self._writes[name] = False  # A cycle adds nothing.
            self._writes[name] = self._node_writes(self.functions[name])
        return self._writes[name]

    def refuses(self, name: str) -> bool:
        """Whether every path through the function refuses before it writes."""
        if name not in self._refuses:
            self._refuses[name] = False
            unguarded, guarded = self._scan(self.functions[name].body, False)
            self._refuses[name] = guarded and not unguarded
        return self._refuses[name]

    def _node_writes(self, node: ast.AST) -> bool:
        text = ast.unparse(node)
        return bool(ORM_WRITE.search(text) or FILE_WRITE.search(text)) or any(
            ast.unparse(c.func) in self.functions and self.writes(ast.unparse(c.func))
            for c in _calls(node)
        )

    def _node_refuses(self, node: ast.AST) -> bool:
        return any(
            _refusal_call(c)
            or (
                ast.unparse(c.func) in self.functions
                and self.refuses(ast.unparse(c.func))
            )
            for c in _calls(node)
        )

    def _scan(self, body: list[ast.stmt], guarded: bool) -> tuple[bool, bool]:
        """(whether a write in `body` runs unguarded, guarded at its end)."""
        bad = False
        for statement in body:
            for part in _header(statement):
                if self._node_refuses(part):
                    guarded = True
                elif not guarded and self._node_writes(part):
                    bad = True
            match statement:
                case ast.If():
                    b1, g1 = self._scan(statement.body, guarded)
                    b2, g2 = self._scan(statement.orelse, guarded)
                    bad |= b1 or b2
                    guarded = g1 and g2
                case ast.With() | ast.AsyncWith():
                    b, guarded = self._scan(statement.body, guarded)
                    bad |= b
                case ast.For() | ast.AsyncFor() | ast.While():
                    b, _ = self._scan(statement.body, guarded)
                    b2, _ = self._scan(statement.orelse, guarded)
                    bad |= b or b2
                case ast.Try():
                    b, g = self._scan(statement.body, guarded)
                    bad |= b
                    for handler in statement.handlers:
                        bad |= self._scan(handler.body, guarded)[0]
                    bad |= self._scan(statement.orelse, g)[0]
                    b, g2 = self._scan(statement.finalbody, guarded)
                    bad |= b
                    guarded = guarded or g2
                case ast.Match():
                    ends = []
                    for case in statement.cases:
                        b, g = self._scan(case.body, guarded)
                        bad |= b
                        ends.append(g)
                    guarded = guarded or (bool(ends) and all(ends))
                # A nested function runs when it is called, and the call's
                # own statement counts its writes.
        return bad, guarded

    def unguarded(self) -> list[str]:
        found = []
        for name, f in self.functions.items():
            if name.startswith("_"):
                continue
            params = f.args.args
            if params and (
                params[0].arg in ("race", "state")
                or "RaceState" in ast.unparse(params[0].annotation or ast.Constant(""))
            ):
                continue
            if self._scan(f.body, False)[0]:
                found.append(name)
        return found


def unguarded(source: str) -> list[str]:
    """The public functions of `source` with a write no refusal comes before."""
    return _Module(source).unguarded()


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


def test_the_check_flags_a_lock_in_one_branch_only():
    assert unguarded(
        "def rename(group, name, fast):\n"
        "    if fast:\n"
        "        lock_race_state(group.event_id)\n"
        "    group.save()\n"
    ) == ["rename"]


def test_the_check_flags_a_write_before_the_lock():
    assert unguarded(
        "def rename(group, name):\n"
        "    group.save()\n"
        "    with transaction.atomic():\n"
        "        lock_race_state(group.event_id)\n"
    ) == ["rename"]


def test_the_check_flags_a_file_write():
    assert unguarded(
        "def keep(event, folder, data):\n    (folder / 'x.jpg').write_bytes(data)\n"
    ) == ["keep"]
    assert unguarded(
        "def keep(event, folder, upload):\n"
        "    name = store_upload(folder, 'x.jpg', upload)\n"
        "    with transaction.atomic():\n"
        "        lock_race_state(event.pk)\n"
    ) == ["keep"]


def test_the_check_reads_database_scripts():
    assert SERVER / "core/database_scripts.py" in service_modules()
