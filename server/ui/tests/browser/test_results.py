"""Results by racer, standings, the Retrospective, the import flow and the
export page in a browser (#49), at 390x844 and 1280x800, with the
screenshots the PR attaches."""

import csv
import re
import uuid
from pathlib import Path

import pytest
from openpyxl import load_workbook
from playwright.sync_api import expect

from core.models import RaceSettings, RaceState
from racing.models import HeatLane, Round
from racing.services.standings import standings_snapshot
from racing.services.workbook import export_all
from roster.models import Partition, Racer
from roster.services.racers import insert_new_racer
from transfer.batch_import import Row, import_rows
from ui.tests.browser.conftest import assert_accessible, htmx_settled, shoot
from ui.tests.browser.test_playlist import Timer
from ui.tests.race import car_times, race_setup, run_heat, scheduled, standings_day

DATA = Path(__file__).resolve().parents[4] / "testing" / "data"


def phone_only(page):
    if page.size != "phone":
        pytest.skip("this acceptance check is at 390x844")


# ----- results by racer --------------------------------------------------------


def test_results_by_racer_updates_within_2_s_of_a_finished_heat(
    login, event, playwright, live_server
):
    rounds = race_setup(event, groups=(("Wolves", 4), ("Bears", 4)))
    scheduled(rounds["Wolves"])
    page = login("anonymous")
    page.goto("/results/")
    first = page.locator("[data-round] [data-result]").first
    expect(first.locator("[data-shown]")).to_have_text("--")
    shoot(page, "results-by-racer-before")
    timer = Timer(playwright, live_server)
    times = dict(car_times(event))
    try:
        timer.message("STARTED")
        timer.message("FINISHED", **times)
    finally:
        timer.close()

    # The poll runs every second; 2 s covers a poll and its reply.
    lane = (
        HeatLane.objects.filter(event=event, round=rounds["Wolves"], heat=1)
        .order_by("lane")
        .first()
    )
    shown = page.locator(f'[data-result="{lane.pk}"] [data-shown]')
    expect(shown).to_have_text(f"{lane.finish_time:5.3f}", timeout=2000)
    assert_accessible(page)
    shoot(page, "results-by-racer")


def test_results_show_places_photos_and_the_racerid_filter(login, event):
    rounds = race_setup(event, groups=(("Wolves", 4),))
    scheduled(rounds["Wolves"])
    run_heat(event)
    RaceSettings.objects.filter(event=event).update(use_points=True)
    racer = Racer.objects.filter(event=event).order_by("car_number").first()
    page = login("anonymous")

    page.goto("/results/")
    expect(page.locator("[data-shown]").filter(has_text="1st")).to_have_count(1)
    page.locator(f'[data-racer="{racer.pk}"] a').click()

    expect(page).to_have_url(re.compile(rf"/results/\?racerid={racer.pk}$"))
    expect(page.locator("[data-racer]")).to_have_count(1)
    expect(page.locator("[data-car]")).to_have_text(f"{racer.car_number:03d}")
    page.get_by_role("link", name="View all racers").click()
    expect(page.locator("[data-racer]")).to_have_count(4)


# ----- standings ---------------------------------------------------------------


def card(page, car: int):
    return page.locator("#standings-list [data-racer]").filter(
        has=page.locator(f'[data-car]:text-is("{car:03d}")')
    )


def places(page) -> list[str]:
    return [
        p.strip()
        for p in page.locator(
            "#standings-list [data-racer] [data-place]"
        ).all_inner_texts()
    ]


def test_d33_d35_the_pack_standings_mark_racers_not_eligible(login, event):
    day = standings_day(event)
    page = login("anonymous")
    page.goto("/standings/")
    expect(page.locator("#standings-view")).to_have_value("st-ff")

    # D33: one racer of each award setting, marked, with no place.
    for car in (102, 202):
        expect(card(page, car).locator("[data-why]")).to_contain_text("Not eligible")
        expect(card(page, car).locator("[data-place]")).to_contain_text("No place")
    # D35: Parents are outside the pack's list, the others numbered 1 to 6.
    for car in (301, 302, 303, 304):
        expect(card(page, car).locator("[data-why]")).to_contain_text(
            "partition is not in the award's list"
        )
    numbered = [p for p in places(page) if p.isdigit()]
    assert numbered == ["1", "2", "3", "4", "5", "6"]
    snapshot = standings_snapshot(event)
    php_places = [
        str(row.outcome["st-ff"])
        for row in snapshot.standings.rows
        if "st-ff" in row.outcome
    ]
    assert numbered == php_places
    assert_accessible(page)
    shoot(page, "standings-pack")

    # The Parents' own standings place them.
    parents = day["rounds"]["Parents"].pk
    page.locator("#standings-view").select_option(f"st-r{parents}")
    htmx_settled(page)
    expect(page).to_have_url(re.compile(rf"/standings/\?view=st-r{parents}$"))
    expect(page.locator("[data-title]")).to_have_text("Parents")
    assert places(page) == ["1", "2", "3", "4"]
    expect(page.locator("#standings-list [data-not-eligible]")).to_have_count(0)


def test_d37_a_closed_lanes_times_show_dropped(login, event):
    day = standings_day(event, close_lane_3=True)
    lions = day["rounds"]["Lions"].pk
    page = login("anonymous")

    page.goto(f"/standings/?view=st-r{lions}")

    dropped = page.locator("#standings-list [data-dropped]")
    lane_3 = HeatLane.objects.filter(round_id=lions, lane=3).exclude(finish_time=None)
    expect(dropped).to_have_count(lane_3.count())
    assert lane_3.count() > 0
    for title in dropped.evaluate_all("els => els.map(e => e.title)"):
        assert re.fullmatch(r"Heat \d+, lane 3, dropped: the lane was closed", title)
    expect(dropped.first).to_have_css("text-decoration-line", "line-through")
    snapshot = standings_snapshot(event)
    key = f"st-r{lions}"
    assert [p for p in places(page) if p.isdigit()] == [
        str(r.outcome[key]) for r in snapshot.standings.rows if key in r.outcome
    ]
    shoot(page, "standings-closed-lane")


# ----- the Retrospective -------------------------------------------------------


def test_the_retrospective_shows_lane_bias_and_a_debug_view(login, event):
    rounds = race_setup(event, groups=(("Wolves", 8),))
    scheduled(rounds["Wolves"])
    from ui.tests.race import race_round

    race_round(event)
    page = login("RaceCoordinator")

    page.goto("/history/")
    expect(page.locator("[data-bias-summary]")).to_be_visible()
    page.get_by_text("Details").click()
    expect(page.locator("[data-lane]")).to_have_count(4)
    expect(page.locator("[data-f]")).to_contain_text("F statistic is")
    assert_accessible(page)
    shoot(page, "history")

    page.get_by_role("link", name="Debug view").click()
    expect(page).to_have_url(re.compile(r"/history/\?debug$"))
    expect(page.locator("[data-request]").first).to_contain_text('"action"')
    shoot(page, "history-debug")


# ----- export ------------------------------------------------------------------


def test_everything_downloads_as_xlsx_and_each_sheet_as_csv(login, event, tmp_path):
    standings_day(event)
    page = login("RaceCoordinator")
    page.goto("/export/")
    assert_accessible(page)
    shoot(page, "export")

    with page.expect_download() as download:
        page.get_by_role("link", name="Everything as .xlsx").click()
    path = tmp_path / download.value.suggested_filename
    download.value.save_as(path)
    assert path.suffix == ".xlsx"
    book = load_workbook(path, read_only=True)
    workbook = export_all(event)
    assert book.sheetnames == [title for title, _ in workbook]

    with page.expect_download() as download:
        page.locator("[data-download=csv]").filter(has_text="Roster").click()
    path = tmp_path / download.value.suggested_filename
    download.value.save_as(path)
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == workbook[0][1][0]
    assert len(rows) == len(workbook[0][1])

    with page.expect_download() as download:
        page.get_by_role("link", name="Everything as .ods").click()
    assert download.value.suggested_filename.endswith(".ods")


# ----- import ------------------------------------------------------------------


def racer_rows(event):
    return sorted(
        Racer.objects.filter(event=event).values_list(
            "first_name", "last_name", "car_number", "partition__name"
        )
    )


def choose_file(page, path: Path):
    page.locator("[data-file]").set_input_files(str(path))
    expect(page.locator("[data-step=map]")).to_be_visible()


def import_now(page):
    page.get_by_role("button", name="Import").click()
    htmx_settled(page)


def expected_roster(name: str, encoding: str):
    """The racers the batch import makes from the file read in Python, as
    `transfer/tests/test_batch_import.py` holds them to `racer.import`."""
    from core.tests.factories import EventFactory

    text = (DATA / name).read_bytes().decode(encoding)
    table = list(csv.reader(text.splitlines()))
    fields = {
        "First Name": "firstname",
        "Last Name": "lastname",
        "Den": "partition",
        "Car Number": "carnumber",
    }
    mapping = [fields[h] for h in table[0]]
    rows = [
        Row(i + 2, dict(zip(mapping, cells, strict=True)))
        for i, cells in enumerate(table[1:])
        if cells
    ]
    other = EventFactory()
    assert import_rows(other, "roster", rows, token=uuid.uuid4().hex).ok
    return racer_rows(other)


@pytest.mark.parametrize(
    ("name", "codepage", "encoding"),
    [
        ("sample-names-MS-DOS.csv", "437", "cp437"),
        ("sample-names-Mac.csv", "10000", "mac_roman"),
        ("sample-names-Windows.csv", "1252", "cp1252"),
    ],
)
def test_a_roster_file_imports_in_its_encoding(login, event, name, codepage, encoding):
    page = login("RaceCoordinator")
    phone_only(page)
    page.goto("/import/roster/")
    choose_file(page, DATA / name)
    page.locator("[data-encoding]").select_option(codepage)

    expect(page.locator("[data-preview-table]")).to_contain_text("Bears and Frères")
    import_now(page)

    expect(page.locator("[data-imported]")).to_contain_text("Imported 82 racers")
    assert racer_rows(event) == expected_roster(name, encoding)


def test_the_xlsx_roster_imports_from_the_chosen_sheet(login, event):
    page = login("RaceCoordinator")
    page.goto("/import/roster/")
    assert_accessible(page)
    shoot(page, "import-1-file")

    choose_file(page, DATA / "sample-names.xlsx")
    sheet = page.locator("[data-worksheet]")
    expect(sheet.locator("option")).to_have_count(5)
    # Each sheet holds the roster as one encoding misread it; the first
    # reads right.
    sheet.select_option(label="DOS PC-8")
    expect(page.locator("[data-preview-table]")).to_contain_text("Bears and FrÅres")
    sheet.select_option(label="Western Mac Roman")
    expect(page.locator("[data-preview-table]")).to_contain_text("Bears and Frères")
    # Each column's field comes from its header.
    for column, field in enumerate(["firstname", "lastname", "partition", "carnumber"]):
        expect(page.locator(f'[data-column="{column}"]')).to_have_value(field)
    shoot(page, "import-2-map")

    page.get_by_role("button", name="Preview").click()
    htmx_settled(page)
    expect(page.locator("[data-rows]")).to_have_text("82 racers to import.")
    expect(page.locator("[data-new-partitions]")).to_contain_text("5 new dens")
    page.locator('[data-label="partition-label"]').fill("Patrol")
    shoot(page, "import-3-preview")

    import_now(page)
    expect(page.locator("[data-imported]")).to_contain_text("Imported 82 racers")
    shoot(page, "import-4-done")
    assert racer_rows(event) == expected_roster("sample-names-Mac.csv", "mac_roman")
    assert RaceSettings.objects.get(event=event).partition_label == "Patrol"


def test_the_header_switch_and_the_combined_name_field(login, event, tmp_path):
    phone_only(login("RaceCoordinator"))
    path = tmp_path / "names.csv"
    path.write_text("Ann Able,Lions,101\nBob Baker,Tigers,102\n")
    page = login("RaceCoordinator")
    page.goto("/import/roster/")
    choose_file(page, path)
    page.locator("[data-header]").click()
    expect(page.locator("[data-header]")).to_have_attribute("aria-checked", "false")
    page.locator('[data-column="0"]').select_option("first-last")
    page.locator('[data-column="1"]').select_option("partition")
    page.locator('[data-column="2"]').select_option("carnumber")
    expect(page.locator("[data-unmet]")).to_be_hidden()

    import_now(page)

    expect(page.locator("[data-imported]")).to_contain_text("Imported 2 racers")
    assert racer_rows(event) == [
        ("Ann", "Able", 101, "Lions"),
        ("Bob", "Baker", 102, "Tigers"),
    ]


def test_a_roster_with_one_bad_row_imports_nothing(login, event, tmp_path):
    path = tmp_path / "roster.csv"
    path.write_text(
        "First Name,Last Name,Den,Car Number\n"
        "Ann,Able,Lions,101\nBob,Baker,Lions,1o2\nCy,Cole,Lions,103\n"
    )
    page = login("RaceCoordinator")
    page.goto("/import/roster/")
    choose_file(page, path)

    import_now(page)

    expect(page.locator("[data-failed]")).to_contain_text("Nothing was imported")
    bad = page.locator('[data-row-outcomes] [data-row="3"]')
    expect(bad).to_contain_text("Car number 1o2 is not a whole number.")
    expect(page.locator("[data-row-outcomes] [data-error]")).to_have_count(1)
    assert not Racer.objects.filter(event=event).exists()
    shoot(page, "import-error")


def test_d34_a_case_variant_joins_the_existing_partition(login, event, tmp_path):
    phone_only(login("RaceCoordinator"))
    insert_new_racer(event, first_name="Old", last_name="Timer", partition_name="Lions")
    path = tmp_path / "roster.csv"
    path.write_text("First Name,Last Name,Den\nCy,Cole,LIONS\n")
    page = login("RaceCoordinator")
    page.goto("/import/roster/")
    choose_file(page, path)

    page.get_by_role("button", name="Preview").click()
    htmx_settled(page)
    expect(page.locator("[data-new-partitions]")).to_have_text("No new den.")
    import_now(page)

    expect(page.locator("[data-imported]")).to_be_visible()
    assert Racer.objects.get(event=event, first_name="Cy").partition.name == "Lions"
    assert Partition.objects.filter(event=event).count() == 1


def test_awards_and_results_import(login, event):
    from awards.models import Award

    insert_new_racer(
        event, first_name="A", last_name="B", partition_name="White's Wolves"
    )
    insert_new_racer(
        event, first_name="C", last_name="D", partition_name="Lions & Tigers"
    )
    page = login("RaceCoordinator")
    page.goto("/import/awards/")
    choose_file(page, DATA / "sample-awards.csv")
    for column, field in enumerate(["awardname", "awardtype", "classname"]):
        expect(page.locator(f'[data-column="{column}"]')).to_have_value(field)
    shoot(page, "import-awards")
    import_now(page)
    # The file names "Bears and Frèr", which no group is (row 12).
    expect(page.locator('[data-row-outcomes] [data-row="12"]')).to_contain_text(
        "Unrecognized class"
    )
    assert not Award.objects.filter(event=event).exists()

    page.goto("/import/results/")
    choose_file(page, DATA / "derbynet-results-sample.csv")
    expect(page.locator('[data-column="7"]')).to_have_value("finishtime")
    expect(page.locator('[data-column="9"]')).to_have_value("completed")
    expect(page.locator("[data-preview-table]")).to_contain_text("2017-03-04 15:25:07")
    shoot(page, "import-results")
    import_now(page)

    expect(page.locator("[data-imported]")).to_contain_text("Imported 200 results")
    assert HeatLane.objects.filter(event=event).count() == 200
    assert Round.objects.filter(event=event).exists()
    assert RaceState.objects.get(event=event)


def test_a_double_tap_on_import_imports_once(login, event, tmp_path, page_errors):
    # The third tap is refused with a 422 toast, which the browser logs.
    page_errors.allow(r"status of 422|/import/roster/run/")
    path = tmp_path / "roster.csv"
    path.write_text("First Name,Last Name\nAnn,Able\nBob,Baker\n")
    page = login("RaceCoordinator")
    phone_only(page)
    page.goto("/import/roster/")
    choose_file(page, path)

    page.get_by_role("button", name="Import").dblclick()
    htmx_settled(page)
    expect(page.locator("[data-imported]")).to_contain_text("Imported 2 racers")
    page.get_by_role("button", name="Import").click()
    htmx_settled(page)

    expect(page.locator("#toasts")).to_contain_text("imported already")
    assert Racer.objects.filter(event=event).count() == 2
