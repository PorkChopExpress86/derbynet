"""The export page's files (#49) against PHP's `export_all()` workbook.

Every `export-json.php` body the golden capture recorded from PHP is
written as `.xlsx`, `.ods` and `.csv` by `racing.services.workbook_files`
and read back: the same sheets, in order, with the same cell values. The
download views write `export_all()` of the event, which
`racing/tests/test_workbook_parity.py` and the golden replay hold to PHP.
"""

import csv
import io
import json
import zipfile
from xml.etree import ElementTree

import pytest
from openpyxl import load_workbook

from racing.engine.tests.golden import read_transcript
from racing.engine.tests.standings_fixtures import EXPORT, STANDINGS_DIR
from racing.services import workbook_files
from racing.services.workbook import export_all
from ui.tests.race import standings_day


def php_workbooks():
    for path in sorted(STANDINGS_DIR.glob("*.http")):
        for name, exchange in read_transcript(path).items():
            if exchange["request"] == EXPORT and exchange["body"].startswith(b"["):
                yield pytest.param(
                    json.loads(exchange["body"]), id=f"{path.stem}-{name}"
                )


PHP_WORKBOOKS = list(php_workbooks())


def trimmed(rows):
    """Rows with their trailing empty cells left off, as a sheet holds them."""
    out = []
    for row in rows:
        row = list(row)
        while row and row[-1] in (None, ""):
            row.pop()
        out.append(row)
    while out and not out[-1]:
        out.pop()
    return out


def from_xlsx(body: bytes):
    book = load_workbook(io.BytesIO(body), read_only=True)
    return [
        (sheet.title, trimmed(sheet.iter_rows(values_only=True)))
        for sheet in book.worksheets
    ]


NS = {
    "table": "urn:oasis:names:tc:opendocument:xmlns:table:1.0",
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
}


def from_ods(body: bytes):
    archive = zipfile.ZipFile(io.BytesIO(body))
    assert archive.namelist()[0] == "mimetype"
    assert archive.read("mimetype") == workbook_files.ODS_MIMETYPE.encode()
    root = ElementTree.fromstring(archive.read("content.xml"))
    office = "{{{}}}".format(NS["office"])
    sheets = []
    for table in root.iter("{{{}}}table".format(NS["table"])):
        rows = []
        for row in table.findall("table:table-row", NS):
            cells = []
            for cell in row.findall("table:table-cell", NS):
                kind = cell.get(office + "value-type")
                if kind == "float":
                    value = float(cell.get(office + "value"))
                    cells.append(int(value) if value.is_integer() else value)
                elif kind == "string":
                    cells.append(
                        "\n".join(p.text or "" for p in cell.findall("text:p", NS))
                    )
                else:
                    cells.append(None)
            rows.append(cells)
        sheets.append((table.get("{{{}}}name".format(NS["table"])), trimmed(rows)))
    return sheets


def expected(workbook):
    names = workbook_files.sheet_names(workbook)
    return [
        (name, trimmed([[_number(v) for v in row] for row in rows]))
        for name, (_, rows) in zip(names, workbook, strict=True)
    ]


def _number(value):
    """A spreadsheet keeps one number type, and reads an empty text cell
    back as an empty cell."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if value == "":
        return None
    return value


def loose(sheets):
    """As `_number` reads each cell."""
    return [
        (name, [[_number(v) for v in row] for row in rows]) for name, rows in sheets
    ]


def test_there_are_php_exports_to_compare():
    assert len(PHP_WORKBOOKS) >= 8


@pytest.mark.parametrize("workbook", PHP_WORKBOOKS)
def test_the_xlsx_holds_phps_sheets_and_cells(workbook):
    found = from_xlsx(workbook_files.xlsx(workbook))

    assert loose(found) == expected(workbook)
    assert [name for name, _ in found] == [title for title, _ in workbook]


@pytest.mark.parametrize("workbook", PHP_WORKBOOKS)
def test_the_ods_holds_phps_sheets_and_cells(workbook):
    assert loose(from_ods(workbook_files.ods(workbook))) == expected(workbook)


@pytest.mark.parametrize("workbook", PHP_WORKBOOKS)
def test_each_csv_holds_its_sheet(workbook):
    for _, rows in workbook:
        body = workbook_files.csv_sheet(rows)
        assert body.startswith("﻿".encode())
        read = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        assert trimmed(read) == trimmed(
            [["" if v is None else str(v) for v in row] for row in rows]
        )


def test_sheet_names_are_made_safe_and_unique():
    names = workbook_files.sheet_names(
        [["Standings A:B", []], ["Standings A?B", []], ["x" * 40, []]]
    )
    assert names == ["Standings A-B", "Standings A-B (2)", "x" * 31]


# ----- the download views --------------------------------------------------------


@pytest.fixture
def day(event):
    return standings_day(event)


@pytest.mark.django_db
def test_everything_downloads_as_an_xlsx_of_the_events_workbook(login, event, day):
    response = login("RaceCoordinator").get("/export/everything.xlsx")

    assert response.status_code == 200
    assert response["Content-Disposition"].startswith('attachment; filename="derbynet-')
    assert response["Content-Disposition"].endswith('.xlsx"')
    assert loose(from_xlsx(response.content)) == expected(export_all(event))


@pytest.mark.django_db
def test_everything_downloads_as_an_ods(login, event, day):
    response = login("RaceCoordinator").get("/export/everything.ods")

    assert response["Content-Type"] == workbook_files.ODS_MIMETYPE
    assert loose(from_ods(response.content)) == expected(export_all(event))


@pytest.mark.django_db
def test_each_sheet_downloads_as_csv(login, event, day):
    client = login("RaceCoordinator")
    workbook = export_all(event)
    page = client.get("/export/").content.decode()
    for index, (_, rows) in enumerate(workbook):
        assert f'href="/export/sheet/{index}.csv"' in page
        response = client.get(f"/export/sheet/{index}.csv")
        assert response["Content-Type"] == "text/csv; charset=utf-8"
        assert response.content == workbook_files.csv_sheet(rows)
    assert client.get(f"/export/sheet/{len(workbook)}.csv").status_code == 404
    assert 'href="/export-json.php"' in page


@pytest.mark.django_db
def test_the_export_needs_view_race_results(login, event):
    """The default visitor role holds VIEW_RACE_RESULTS, as in PHP; the
    Photo role does not."""
    assert login("Photo").get("/export/").status_code == 302
    assert login("Photo").get("/export/everything.xlsx").status_code == 302
    assert login("anonymous").get("/export/").status_code == 200
    assert login("anonymous").get("/export.php").status_code == 301
