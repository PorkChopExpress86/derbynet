"""The import page's batch import (#49) against the per-row legacy endpoints.

PHP's import pages post one `racer.import`, `award.import` or
`result.import` per row. The batch import runs the same per-row services,
so a file it imports gives the rows those endpoints give, row for row; the
endpoints themselves are checked against PHP by the golden replay and the
shell suites (`import-csv-roster.sh`, `test-import-results.sh`). Unlike
PHP, a file with a failing row imports nothing.
"""

import csv
from pathlib import Path

import pytest
from django.test import Client

from awards.models import Award
from core.models import RaceSettings
from core.tests.factories import EventFactory
from racing.models import HeatLane, RosterEntry, Round
from roster.models import Partition, Racer
from roster.services.racers import insert_new_racer
from transfer.batch_import import Row, import_rows, parse_rows, preview
from ui.tests.roles import PASSWORDS

pytestmark = pytest.mark.django_db

DATA = Path(__file__).resolve().parents[3] / "testing" / "data"


def csv_rows(name: str, encoding: str = "utf-8") -> list[list[str]]:
    text = (DATA / name).read_bytes().decode(encoding)
    return list(csv.reader(text.splitlines()))


def mapped(table, mapping, first_row=2) -> list[Row]:
    """The rows the page posts: each mapped column's text by field."""
    return [
        Row(first_row + i, {f: cells[c] for c, f in enumerate(mapping) if f})
        for i, cells in enumerate(table)
        if any(cell.strip() for cell in cells)
    ]


def coordinator() -> Client:
    client = Client()
    response = client.post(
        "/login/", {"name": "RaceCoordinator", "password": PASSWORDS["RaceCoordinator"]}
    )
    assert response.status_code == 302
    return client


def one_by_one(action: str, rows: list[Row]) -> None:
    """Each row to the legacy endpoint, as PHP's page sends it."""
    client = coordinator()
    for row in rows:
        reply = client.post("/action.php", {"action": action, **row.fields})
        assert reply.status_code == 200
        assert b'"success"' in reply.content or b"success" in reply.content, reply


def racer_rows(event):
    return sorted(
        Racer.objects.filter(event=event).values_list(
            "first_name",
            "last_name",
            "car_number",
            "car_name",
            "note",
            "exclude",
            "partition__name",
            "subgroup__name",
            "group__name",
        )
    )


def active(event):
    from core.models import SiteState

    SiteState.objects.update_or_create(pk=1, defaults={"active_event": event})


# Each sample roster with the encoding it is written in, as the encoding
# select of the import page picks it.
ROSTER_FILES = [
    ("sample-names.csv", "mac_roman"),
    ("sample-names-Mac.csv", "mac_roman"),
    ("sample-names-Windows.csv", "cp1252"),
    ("sample-names-MS-DOS.csv", "cp437"),
    ("sample-names-ascii.csv", "ascii"),
    ("sample-names-firstlast.csv", "utf-8"),
    ("sample-names-last-comma-first.csv", "utf-8"),
    ("sample-names-plurals.csv", "utf-8"),
]
# The fields the import page fills in from these headers.
ROSTER_HEADERS = {
    "First Name": "firstname",
    "Last Name": "lastname",
    "Name": "first-last",
    "Den": "partition",
    "Car Number": "carnumber",
}


def roster_file(name, encoding) -> list[Row]:
    table = csv_rows(name, encoding)
    return mapped(table[1:], [ROSTER_HEADERS[h] for h in table[0]])


@pytest.mark.parametrize(("name", "encoding"), ROSTER_FILES)
def test_a_roster_file_imports_the_racers_racer_import_makes(name, encoding):
    rows = roster_file(name, encoding)
    assert len(rows) > 10

    legacy = EventFactory()
    active(legacy)
    one_by_one("racer.import", rows)
    expected = racer_rows(legacy)

    event = EventFactory()
    outcome = import_rows(event, "roster", rows)

    assert outcome.ok and outcome.imported == len(rows)
    assert racer_rows(event) == expected
    assert len(expected) == len(rows)


def test_the_encoding_variants_read_the_same_racers():
    """The Mac, Windows and MS-DOS files hold the same roster in three
    encodings; each read in its own gives the same names."""
    names = {
        tuple(
            (r.fields["firstname"], r.fields["lastname"], r.fields["partition"])
            for r in roster_file(name, encoding)
        )
        for name, encoding in ROSTER_FILES[:4]
    }
    assert len(names) == 1
    assert ("Antoine", "Akiyama", "Bears and Frères") in next(iter(names))


def test_a_roster_with_one_bad_row_imports_nothing():
    event = EventFactory()
    rows = [
        Row(2, {"firstname": "Ann", "lastname": "Able", "carnumber": "101"}),
        Row(3, {"firstname": "Bob", "lastname": "Baker", "carnumber": "x12"}),
        Row(4, {"firstname": "Cy", "lastname": "Cole", "carnumber": "103"}),
        Row(5, {"firstname": "", "lastname": "Dunn", "carnumber": "104"}),
    ]

    outcome = import_rows(event, "roster", rows)

    assert not outcome.ok
    assert [(e.number, e.message) for e in outcome.errors] == [
        (3, "Car number x12 is not a whole number."),
        (5, "Missing required field firstname."),
    ]
    assert outcome.imported == 0
    assert not Racer.objects.filter(event=event).exists()
    assert not Partition.objects.filter(event=event).exists()


def test_a_partition_named_in_another_case_joins_the_existing_one():
    """D34: `LIONS` beside an existing `Lions` is no new partition."""
    event = EventFactory()
    insert_new_racer(event, first_name="A", last_name="B", partition_name="Lions")
    rows = [Row(2, {"firstname": "Cy", "lastname": "Cole", "partition": "LIONS"})]

    found = preview(event, "roster", rows)
    assert found.new_partitions == []
    assert found.existing_partitions == ["Lions"]

    assert import_rows(event, "roster", rows).ok
    racer = Racer.objects.get(event=event, first_name="Cy")
    assert racer.partition.name == "Lions"
    assert Partition.objects.filter(event=event).count() == 1


def test_the_preview_counts_new_partitions_duplicates_and_missing_fields():
    event = EventFactory()
    insert_new_racer(
        event, first_name="A", last_name="B", partition_name="Lions", car_number=101
    )
    rows = [
        Row(
            2,
            {
                "firstname": "Cy",
                "lastname": "C",
                "partition": "Bears",
                "carnumber": "101",
            },
        ),
        Row(
            3,
            {
                "firstname": "Di",
                "lastname": "D",
                "partition": "bears",
                "carnumber": "202",
            },
        ),
        Row(
            4, {"firstname": "Ed", "lastname": "", "partition": "", "carnumber": "202"}
        ),
    ]

    found = preview(event, "roster", rows)

    assert found.rows == 3
    assert found.new_partitions == ["Bears", "Default"]
    assert found.duplicate_cars == {"101": [2], "202": [3, 4]}
    assert [(e.number, e.message) for e in found.missing] == [
        (4, "Missing required field lastname.")
    ]
    assert Racer.objects.filter(event=event).count() == 1


def test_a_roster_import_saves_the_labels_with_the_racers():
    event = EventFactory()
    rows = [Row(2, {"firstname": "Cy", "lastname": "Cole", "partition": "Lions"})]

    import_rows(
        event, "roster", rows, {"supergroup-label": "Club", "partition-label": "Team"}
    )

    settings = RaceSettings.objects.get(event=event)
    assert (settings.supergroup_label, settings.partition_label) == ("Club", "Team")


def test_a_failed_roster_import_keeps_the_labels():
    event = EventFactory()
    rows = [Row(2, {"firstname": "Cy", "lastname": "Cole", "carnumber": "x"})]

    import_rows(event, "roster", rows, {"partition-label": "Team"})

    assert RaceSettings.objects.get(event=event).partition_label == "Den"


def award_rows(event):
    return sorted(
        Award.objects.filter(event=event).values_list(
            "name", "award_type__name", "group__name", "subgroup__name", "sort"
        ),
        key=str,
    )


def award_file() -> list[Row]:
    return mapped(
        csv_rows("sample-awards.csv")[1:], ["awardname", "awardtype", "classname"]
    )


def test_the_award_file_fails_on_the_row_award_import_refuses():
    """`sample-awards.csv` names "Bears and Frèr", which no group of
    `sample-names.csv` is. PHP's page imports the other ten awards and
    marks that row FAILED; the batch imports none and names the row."""
    event = EventFactory()
    assert import_rows(event, "roster", roster_file(*ROSTER_FILES[0])).ok

    outcome = import_rows(event, "awards", award_file())

    assert [(e.number, e.message) for e in outcome.errors] == [
        (12, "Unrecognized class")
    ]
    assert not Award.objects.filter(event=event).exists()


def test_the_award_file_imports_the_awards_award_import_makes():
    rows = [r for r in award_file() if r.number != 12]
    legacy = EventFactory()
    active(legacy)
    one_by_one("racer.import", roster_file(*ROSTER_FILES[0]))
    one_by_one("award.import", rows)
    event = EventFactory()
    assert import_rows(event, "roster", roster_file(*ROSTER_FILES[0])).ok

    outcome = import_rows(event, "awards", rows)

    assert outcome.ok, outcome.errors
    assert award_rows(event) == award_rows(legacy)
    assert len(award_rows(event)) == len(rows) == 10


def test_an_unknown_award_type_imports_no_award():
    event = EventFactory()
    rows = [
        Row(2, {"awardname": "Best", "awardtype": "Design Trophy"}),
        Row(3, {"awardname": "Worst", "awardtype": "Nonsense"}),
    ]

    outcome = import_rows(event, "awards", rows)

    assert [e.number for e in outcome.errors] == [3]
    assert outcome.errors[0].message.startswith("Unrecognized award type.")
    assert not Award.objects.filter(event=event).exists()


RESULT_HEADERS = {
    "Class": "class",
    "Round": "round",
    "Heat": "heat",
    "Lane": "lane",
    "FirstName": "firstname",
    "LastName": "lastname",
    "CarNumber": "carnumber",
    "FinishTime": "finishtime",
    "FinishPlace": "finishplace",
    "Completed": "completed",
}


def chart_rows(event):
    return sorted(
        HeatLane.objects.filter(event=event).values_list(
            "round__group__name",
            "round__number",
            "heat",
            "lane",
            "racer__first_name",
            "racer__last_name",
            "racer__car_number",
            "finish_time",
            "finish_place",
            "completed",
        )
    )


def test_the_results_file_imports_the_rows_result_import_makes():
    table = csv_rows("derbynet-results-sample.csv")
    rows = mapped(table[1:], [RESULT_HEADERS[h] for h in table[0]])
    legacy = EventFactory()
    active(legacy)
    one_by_one("result.import", rows)

    event = EventFactory()
    outcome = import_rows(event, "results", rows)

    assert outcome.ok, outcome.errors
    assert chart_rows(event) == chart_rows(legacy)
    assert len(chart_rows(event)) == len(rows)
    assert Round.objects.filter(event=event).count() > 1
    assert RosterEntry.objects.filter(event=event).exists()


def test_a_bad_result_line_imports_no_result():
    event = EventFactory()
    rows = [
        Row(
            2,
            {
                "class": "Tiger",
                "round": "1",
                "heat": "1",
                "lane": "1",
                "firstname": "A",
                "lastname": "B",
                "carnumber": "101",
            },
        ),
        Row(
            3,
            {
                "class": "Tiger",
                "round": "one",
                "heat": "1",
                "lane": "2",
                "firstname": "C",
                "lastname": "D",
                "carnumber": "102",
            },
        ),
    ]

    outcome = import_rows(event, "results", rows)

    assert [(e.number, e.message) for e in outcome.errors] == [
        (3, "round is not a number.")
    ]
    assert not HeatLane.objects.filter(event=event).exists()
    assert not Racer.objects.filter(event=event).exists()


def test_the_posted_rows_keep_only_the_kinds_fields_and_skip_blank_rows():
    rows = parse_rows(
        "roster",
        [
            {"row": 2, "fields": {"firstname": "A", "lastname": "B", "classid": "9"}},
            {"row": 3, "fields": {"firstname": " ", "lastname": ""}},
        ],
    )
    assert rows == [Row(2, {"firstname": "A", "lastname": "B"})]


def test_a_row_the_database_refuses_imports_nothing():
    """Postgres refuses a name past its column; SQLite stores it."""
    from django.db import connection

    if connection.vendor != "postgresql":
        pytest.skip("SQLite keeps text past a column's width")
    event = EventFactory()
    rows = [
        Row(2, {"firstname": "Ann", "lastname": "Able"}),
        Row(3, {"firstname": "Bob", "lastname": "B" * 300}),
    ]

    outcome = import_rows(event, "roster", rows)

    assert [e.number for e in outcome.errors] == [3]
    assert not Racer.objects.filter(event=event).exists()
