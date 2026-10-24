"""Helpers for the importer tests: legacy fixtures, the command, and dumps."""

import io
import json
import shutil
import sqlite3
from pathlib import Path

from django.core.management import call_command

from core.models import event_scoped_models
from core.services import event_dir
from transfer.models import LegacyId

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
SNAPSHOT = FIXTURES / "v10.snapshot"


def legacy_folder(tmp_path, name, *, sql=(), folder=None, photos=True):
    """A legacy event folder holding fixture `name` as `derbynet.sqlite3`.

    `sql` statements run on the copy first. With `photos`, the folder also
    holds the v10 fixture's `racers/` and `cars/` folders, as an event folder
    does. Returns the database's path.
    """
    root = tmp_path / (folder or name)
    root.mkdir(parents=True)
    database = root / "derbynet.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript((FIXTURES / f"{name}.sql").read_text())
    for statement in sql:
        connection.execute(statement)
    connection.commit()
    connection.close()
    if photos:
        shutil.copytree(FIXTURES / "v10-photos", root, dirs_exist_ok=True)
    return database


def source_rows(database, sql):
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql)]
    finally:
        connection.close()


def run_import(source, *args, report_dir):
    """Run `import_derbynet`, returning its JSON report and its stdout."""
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    report = Path(report_dir) / "report.json"
    out = io.StringIO()
    call_command(
        "import_derbynet",
        str(source),
        *args,
        "--report",
        str(report),
        "--force-report",
        stdout=out,
    )
    return json.loads(report.read_text()), out.getvalue()


def legacy_ids(event):
    """`{(model label, new id): legacy id}` for the event's imported rows."""
    return {
        (row.model, row.new_id): row.legacy_id
        for row in LegacyId.objects.filter(event=event)
    }


def legacy_dump(event, *, omit=()):
    """Every event-scoped row of `event`, with ids replaced by legacy ids.

    Two imports of the same rows give equal dumps, whatever ids they got.
    `omit` names fields to leave out, as `"<model label>.<field>"`. Paths
    into the event's folder name it `<event>`.
    """
    mapping = legacy_ids(event)
    folder = str(event_dir(event.slug))
    dump = {}
    for model in event_scoped_models():
        model_label = model._meta.label_lower
        if model is LegacyId:
            continue
        rows = []
        for obj in model.objects.filter(event=event):
            row = {"legacy_id": mapping.get((model_label, obj.pk))}
            for field in model._meta.concrete_fields:
                if (
                    field.name in ("id", "event")
                    or f"{model_label}.{field.name}" in omit
                ):
                    continue
                value = getattr(obj, field.attname)
                if field.is_relation and value is not None:
                    target = field.related_model._meta.label_lower
                    value = (target, mapping.get((target, value), f"site:{value}"))
                elif field.name == "last_heat" and value is not None:
                    # A JSON reference: {"round_id", "heat", "results"}.
                    value = {
                        **value,
                        "round_id": mapping[("racing.round", value["round_id"])],
                    }
                elif isinstance(value, str) and value.startswith(folder):
                    value = "<event>" + value[len(folder) :]
                row[field.name] = value
            rows.append(row)
        dump[model_label] = sorted(rows, key=repr)
    return dump
