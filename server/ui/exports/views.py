"""The export page (#49): `export.php`, with the files built on the server
by `racing.services.workbook_files` from `export_all()`, the workbook
`export-json.php` serves (#36). Each file downloads as an attachment, so a
phone saves it. VIEW_RACE_RESULTS, as in PHP.
"""

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.urls import get_script_prefix
from django.utils import timezone
from django.views.decorators.http import require_GET

from racing.services import workbook_files
from racing.services.workbook import export_all
from ui.navigation import requires_page
from ui.responses import active_event

guard = requires_page("export")

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _stamp() -> str:
    return timezone.localdate().isoformat()


def _download(body: bytes, content_type: str, filename: str) -> HttpResponse:
    response = HttpResponse(body, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store"
    return response


@require_GET
def page(request):
    workbook = export_all(active_event(request))
    names = workbook_files.sheet_names(workbook)
    return render(
        request,
        "ui/exports/page.html",
        {
            "sheets": list(enumerate(names)),
            "json_url": get_script_prefix() + "export-json.php",
        },
    )


@require_GET
@guard
def everything(request, kind: str):
    workbook = export_all(active_event(request))
    if kind == "xlsx":
        body, content_type = workbook_files.xlsx(workbook), XLSX
    elif kind == "ods":
        body, content_type = workbook_files.ods(workbook), workbook_files.ODS_MIMETYPE
    else:
        raise Http404("No such file type.")
    return _download(body, content_type, f"derbynet-{_stamp()}.{kind}")


@require_GET
@guard
def sheet(request, index: int):
    workbook = export_all(active_event(request))
    if index >= len(workbook):
        raise Http404("No such sheet.")
    name = workbook_files.sheet_names(workbook)[index]
    safe = "".join(c if c.isalnum() or c in " -_" else "-" for c in name)
    return _download(
        workbook_files.csv_sheet(workbook[index][1]),
        "text/csv; charset=utf-8",
        f"derbynet-{safe}-{_stamp()}.csv",
    )
