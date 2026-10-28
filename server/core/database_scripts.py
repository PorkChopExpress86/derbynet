"""The named scripts of `database.execute` (#63).

PHP runs `website/sql/<dbtype>/<script>.inc` by name. Here the names the shell
suites and the setup page use map to services, and any other name is not a
script. Nothing here runs SQL a caller sends.
"""

from dataclasses import dataclass, field

from django.core.management import call_command
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor

from core.models import Event
from core.services import reset_event
from kiosks.models import SignalMessage


@dataclass
class ScriptResult:
    # `sqlnoscript` or `sqlfailure`, or None on success.
    failure: tuple[str, str] | None = None
    # PHP's `executed`: one entry per statement, `{"sql", "result"}`.
    executed: list[dict] = field(default_factory=list)


def _schema(event: Event) -> list[dict]:
    deleted = reset_event(event)
    return [{"sql": "Empty the active event's tables", "result": deleted}]


def _update_schema(event: Event) -> list[dict]:
    """Apply pending migrations. With none pending it does nothing."""
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if not plan:
        return []
    call_command("migrate", interactive=False, verbosity=0)
    return [{"sql": f"Migrate {m.app_label}.{m.name}", "result": 0} for m, _ in plan]


def _drop_message_queue_table(event: Event) -> list[dict]:
    """The queue is a table of the event's rows, so dropping it empties them."""
    deleted = SignalMessage.objects.filter(event=event).delete()[0]
    return [{"sql": "Empty the message queue", "result": deleted}]


SCRIPTS = {
    "schema": _schema,
    "update-schema": _update_schema,
    "drop-message-queue-table": _drop_message_queue_table,
}


def run(event: Event, script: str) -> ScriptResult:
    runner = SCRIPTS.get(script)
    if runner is None:
        return ScriptResult(("sqlnoscript", f"SQL script {script} not found."))
    try:
        return ScriptResult(executed=runner(event))
    except DatabaseError as error:
        return ScriptResult(("sqlfailure", str(error)))
