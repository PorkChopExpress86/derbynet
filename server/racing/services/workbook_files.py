"""The export files of #49: `export_all()`'s workbook as `.xlsx`, `.ods`, or
one sheet as `.csv`, built on the server.

PHP's `export.php` builds the files in the browser with SheetJS 0.13.5,
which throws on a sheet name holding `\\ / ? * [ ]`, past 31 characters, or
repeating an earlier sheet's (run under node on `website/js/xlsx.full.min.js`
for #49). `export_all()` replaces `/` and shortens a long standings title
(`website/inc/export-all.inc:26` to `:30`), but its loop meant to clean the
rest edits a copy (`foreach ($workbook as $w)`, `:49` to `:54`), so a group
name holding another of those characters, or two titles alike, failed
PHP's export. Here each such character, and `:`, which Excel and openpyxl
refuse, becomes `-`, a name is cut to 31
characters, and a repeated name takes a number (C51, #49).
PHP also offers `.xls`; `.xlsx` opens wherever `.xls` did, so the port drops
it, as #49 plans (Risks and notes).

A cell keeps the type the workbook gives it: a number stays a number, text
stays text (so a car name "111" is text, as `export-json.php` sends it in
the golden capture `test_export_files` reads), and
a missing value is an empty cell.
"""

import csv
import io
import re
import zipfile
from xml.sax.saxutils import escape, quoteattr

from openpyxl import Workbook

SHEET_NAME_WIDTH = 31
_UNSAFE = re.compile(r"[:\\/?*\[\]]")


def sheet_names(workbook: list) -> list[str]:
    """Each sheet's name as the files write it, in order."""
    names: list[str] = []
    for title, _ in workbook:
        base = _UNSAFE.sub("-", str(title))[:SHEET_NAME_WIDTH] or "Sheet"
        name, n = base, 1
        while name.casefold() in (taken.casefold() for taken in names):
            n += 1
            suffix = f" ({n})"
            name = base[: SHEET_NAME_WIDTH - len(suffix)] + suffix
        names.append(name)
    return names


def _cell(value):
    if value is None or isinstance(value, bool | int | float | str):
        return value
    return str(value)


def xlsx(workbook: list) -> bytes:
    book = Workbook(write_only=True)
    for name, (_, rows) in zip(sheet_names(workbook), workbook, strict=True):
        sheet = book.create_sheet(title=name)
        for row in rows:
            sheet.append([_cell(v) for v in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


_ODS_NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0"'
    ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
)
ODS_MIMETYPE = "application/vnd.oasis.opendocument.spreadsheet"
_MANIFEST = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:'
    'manifest:1.0" manifest:version="1.2">'
    f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{ODS_MIMETYPE}"'
    ' manifest:version="1.2"/>'
    '<manifest:file-entry manifest:full-path="content.xml"'
    ' manifest:media-type="text/xml"/>'
    "</manifest:manifest>"
)


def _ods_cell(value) -> str:
    value = _cell(value)
    if value is None:
        return "<table:table-cell/>"
    if isinstance(value, bool):
        value = int(value)
    if isinstance(value, int | float):
        return (
            f'<table:table-cell office:value-type="float" office:value="{value!r}">'
            f"<text:p>{value!r}</text:p></table:table-cell>"
        )
    lines = "".join(f"<text:p>{escape(line)}</text:p>" for line in value.split("\n"))
    return f'<table:table-cell office:value-type="string">{lines}</table:table-cell>'


def ods(workbook: list) -> bytes:
    """An OpenDocument spreadsheet, written by hand: the mimetype, the
    manifest, and one `content.xml` table per sheet."""
    tables = []
    for name, (_, rows) in zip(sheet_names(workbook), workbook, strict=True):
        body = "".join(
            "<table:table-row>"
            + "".join(_ods_cell(v) for v in row)
            + "</table:table-row>"
            for row in rows
        )
        tables.append(f"<table:table table:name={quoteattr(name)}>{body}</table:table>")
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<office:document-content {_ODS_NS} office:version="1.2">'
        "<office:body><office:spreadsheet>"
        + "".join(tables)
        + "</office:spreadsheet></office:body></office:document-content>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        # The mimetype comes first, stored, so a reader can sniff it.
        archive.writestr(
            zipfile.ZipInfo("mimetype"), ODS_MIMETYPE, compress_type=zipfile.ZIP_STORED
        )
        archive.writestr(
            "META-INF/manifest.xml", _MANIFEST, compress_type=zipfile.ZIP_DEFLATED
        )
        archive.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
    return out.getvalue()


def csv_sheet(rows: list) -> bytes:
    """One sheet as CSV, UTF-8 with a byte order mark so a spreadsheet
    reads the names right, as SheetJS writes it."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    for row in rows:
        writer.writerow(["" if v is None else _cell(v) for v in row])
    return ("﻿" + out.getvalue()).encode()
