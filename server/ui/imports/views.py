"""The import flow (#49): one page for a roster, awards or results file,
replacing `import-roster.php`, `import-awards.php` and `import-results.php`.
SET_UP, as in PHP.

The browser reads the file with SheetJS, loaded only on this page, with
PHP's encoding select, worksheet select and header row switch, and maps each
column to a field. It posts the mapped rows as JSON. Preview reads them and
writes nothing; Import hands them to `transfer.batch_import.import_rows`,
which imports the whole file in one transaction or nothing, and the reply
lists each row with its error.
"""

import json

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_POST

from core.models import RaceSettings
from roster.services.groups import labels
from transfer import batch_import
from ui.navigation import requires_page
from ui.responses import active_event, service_error

KINDS = ("roster", "awards", "results")
PREVIEW_ROWS = 10


def field_labels(kind: str, settings: RaceSettings) -> list[tuple[str, str]]:
    """Each field of `kind` with the label PHP's page gives it."""
    names = labels(settings)
    return {
        "roster": [
            ("lastname", "Last Name"),
            ("firstname", "First Name"),
            ("first-last", "First & Last Name"),
            ("partition", names.partition),
            ("carnumber", "Car Number"),
            ("carname", "Car Name"),
            ("note_from", "From"),
            ("exclude", "Ineligible for Award?"),
        ],
        "awards": [
            ("awardname", "Award Name"),
            ("awardtype", "Award Type"),
            ("classname", names.group),
            ("subgroup", names.subgroup),
            ("carnumber", "Winning Car Number"),
        ],
        "results": [
            ("class", "Class"),
            ("round", "Round"),
            ("heat", "Heat"),
            ("lane", "Lane"),
            ("firstname", "FirstName"),
            ("lastname", "LastName"),
            ("carnumber", "CarNumber"),
            ("carname", "CarName"),
            ("finishtime", "FinishTime"),
            ("finishplace", "FinishPlace"),
            ("scale-mph", "Scale MPH"),
            ("completed", "Completed"),
        ],
    }[kind]


# Header names that pick a field, besides the field's key and label, matched
# with case, spaces and punctuation ignored.
ALIASES = {
    "roster": {
        "first": "firstname",
        "last": "lastname",
        "name": "first-last",
        "firstlast": "first-last",
        "firstandlastname": "first-last",
        "den": "partition",
        "class": "partition",
        "group": "partition",
        "car": "carnumber",
        "carno": "carnumber",
        "carnum": "carnumber",
        "from": "note_from",
        "note": "note_from",
        "ineligible": "exclude",
    },
    "awards": {
        "name": "awardname",
        "award": "awardname",
        "type": "awardtype",
        "class": "classname",
        "den": "classname",
        "group": "classname",
        "rank": "subgroup",
        "car": "carnumber",
        "winner": "carnumber",
    },
    "results": {},
}


def _page(request, kind: str):
    event = active_event(request)
    settings = RaceSettings.objects.get(event=event)
    fields = field_labels(kind, settings)
    return render(
        request,
        "ui/imports/page.html",
        {
            "kind": kind,
            "kinds": [
                ("roster", "Roster", "import-roster"),
                ("awards", "Awards", "import-awards"),
                ("results", "Results", "import-results"),
            ],
            "config": {
                "kind": kind,
                "fields": fields,
                "aliases": ALIASES[kind],
                "required": batch_import.REQUIRED[kind],
                # Results come from a DerbyNet export, in its fixed columns.
                "fixed": [f for f, _ in fields] if kind == "results" else [],
                # A results text file keeps its cells as written.
                "raw": kind == "results",
                "previewRows": PREVIEW_ROWS,
            },
            "labels": batch_import.current_labels(event),
        },
    )


@require_GET
def roster_page(request):
    return _page(request, "roster")


@require_GET
def awards_page(request):
    return _page(request, "awards")


@require_GET
def results_page(request):
    return _page(request, "results")


def _rows(request, kind: str):
    try:
        data = json.loads(request.POST.get("rows", ""))
    except ValueError:
        raise batch_import.BadRequest("The rows could not be read.") from None
    return batch_import.parse_rows(kind, data)


def _labels(request) -> dict[str, str]:
    return {
        key: request.POST[key]
        for key in batch_import.LABELS
        if isinstance(request.POST.get(key), str)
    }


# Each kind's Preview and Import take the guard of its page.
GUARDS = {kind: requires_page(f"import-{kind}") for kind in KINDS}


@require_POST
def preview(request, kind: str):
    """The summary of the mapped rows. Writes nothing."""
    if kind not in GUARDS:
        raise Http404("No such import.")
    return GUARDS[kind](_preview)(request, kind)


@require_POST
def run(request, kind: str):
    """Import the mapped rows, all or none, and list each row's outcome."""
    if kind not in GUARDS:
        raise Http404("No such import.")
    return GUARDS[kind](_run)(request, kind)


def _preview(request, kind: str):
    try:
        rows = _rows(request, kind)
    except batch_import.BadRequest as failure:
        return service_error(request, str(failure))
    if not rows:
        return service_error(request, "The file has no rows to import.")
    event = active_event(request)
    return render(
        request,
        "ui/imports/preview.html",
        {
            "kind": kind,
            "summary": batch_import.preview(event, kind, rows),
            "labels": batch_import.current_labels(event),
        },
    )


def _run(request, kind: str):
    try:
        rows = _rows(request, kind)
    except batch_import.BadRequest as failure:
        return service_error(request, str(failure))
    if not rows:
        return service_error(request, "The file has no rows to import.")
    event = active_event(request)
    labels_posted = _labels(request) if kind == "roster" else {}
    outcome = batch_import.import_rows(event, kind, rows, labels_posted)
    errors = {e.number: e.message for e in outcome.errors}
    warnings: dict[int, list[str]] = {}
    for w in outcome.warnings:
        warnings.setdefault(w.number, []).append(w.message)
    return render(
        request,
        "ui/imports/outcome.html",
        {
            "kind": kind,
            "outcome": outcome,
            "general": [e.message for e in outcome.errors if e.number == 0],
            "rows": [
                (row.number, errors.get(row.number, ""), warnings.get(row.number, []))
                for row in rows
            ],
        },
    )
