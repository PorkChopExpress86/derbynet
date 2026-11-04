"""The import flow's views (#49): the page per kind, Preview and Import."""

import json
import re

import pytest

from awards.models import Award
from core.models import RaceSettings
from roster.models import Racer
from roster.services.racers import insert_new_racer

pytestmark = pytest.mark.django_db


def post(client, kind, step, rows, **extra):
    return client.post(
        f"/import/{kind}/{step}/",
        {"rows": json.dumps(rows), **extra},
        HTTP_HX_REQUEST="true",
    )


ROWS = [
    {
        "row": 2,
        "fields": {"firstname": "Ann", "lastname": "Able", "partition": "Lions"},
    },
    {
        "row": 3,
        "fields": {"firstname": "Bob", "lastname": "Baker", "partition": "LIONS"},
    },
]


@pytest.mark.parametrize("kind", ["roster", "awards", "results"])
def test_each_kind_has_a_page_that_loads_sheetjs(login, event, kind):
    html = login("RaceCoordinator").get(f"/import/{kind}/").content.decode()

    assert "ui/vendor/xlsx-0.13.5.full.min.js" in html
    assert f'data-import="{kind}"' in html


def test_only_the_import_page_loads_sheetjs(login, event):
    html = login("RaceCoordinator").get("/standings/").content.decode()
    assert "xlsx" not in html


def test_the_preview_writes_nothing(login, event):
    reply = post(login("RaceCoordinator"), "roster", "preview", ROWS)

    assert reply.status_code == 200
    html = reply.content.decode()
    assert "2 racers to import." in html
    assert "1 new den: Lions." in html
    assert 'name="partition-label"' in html
    assert not Racer.objects.filter(event=event).exists()


def test_import_saves_every_row_and_the_labels(login, event):
    reply = post(
        login("RaceCoordinator"),
        "roster",
        "run",
        ROWS,
        **{"supergroup-label": "Club", "partition-label": "Team"},
    )

    assert 'data-imported="2"' in reply.content.decode()
    assert Racer.objects.filter(event=event, partition__name="Lions").count() == 2
    settings = RaceSettings.objects.get(event=event)
    assert (settings.supergroup_label, settings.partition_label) == ("Club", "Team")


def test_a_bad_row_imports_nothing_and_is_named(login, event):
    rows = [
        *ROWS,
        {"row": 4, "fields": {"firstname": "Cy", "lastname": "C", "carnumber": "1x"}},
    ]

    html = post(login("RaceCoordinator"), "roster", "run", rows).content.decode()

    assert 'data-failed="1"' in html
    assert re.search(r'data-row="4" data-error>\s*<span class="font-mono">Row 4', html)
    assert "Car number 1x is not a whole number." in html
    assert not Racer.objects.filter(event=event).exists()


def test_awards_import_through_the_page(login, event):
    rows = [{"row": 2, "fields": {"awardname": "Best", "awardtype": "Design Trophy"}}]

    html = post(login("RaceCoordinator"), "awards", "run", rows).content.decode()

    assert 'data-imported="1"' in html
    assert Award.objects.filter(event=event, name="Best").exists()


@pytest.mark.parametrize("body", ["", "{", '{"rows": 1}', "[1]", '[{"row": "2"}]'])
def test_rows_the_page_never_sends_are_refused(login, event, body):
    reply = login("RaceCoordinator").post(
        "/import/roster/run/", {"rows": body}, HTTP_HX_REQUEST="true"
    )
    assert reply.status_code == 422


def test_no_rows_is_refused(login, event):
    reply = post(login("RaceCoordinator"), "roster", "run", [{"row": 2, "fields": {}}])
    assert reply.status_code == 422


def test_import_needs_set_up(login, event):
    reply = post(login("RaceCrew"), "roster", "run", ROWS)
    assert reply.status_code == 403
    assert not Racer.objects.filter(event=event).exists()
    assert login("RaceCrew").get("/import/roster/").status_code == 302


def test_an_unknown_kind_is_404(login, event):
    assert post(login("RaceCoordinator"), "people", "run", ROWS).status_code == 404


def test_d34_a_case_variant_previews_no_new_partition(login, event):
    insert_new_racer(event, first_name="A", last_name="B", partition_name="Lions")

    html = post(
        login("RaceCoordinator"), "roster", "preview", ROWS[1:]
    ).content.decode()

    assert "No new den." in html
    assert "Already set up: Lions." in html


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("/import-roster.php", "/import/roster/"),
        ("/import-awards.php", "/import/awards/"),
        ("/import-results.php", "/import/results/"),
        ("/history.php", "/history/"),
        ("/standings.php", "/standings/"),
    ],
)
def test_the_old_urls_redirect(client, event, old, new):
    response = client.get(old)
    assert response.status_code == 301
    assert response["Location"] == new


def test_print_php_has_no_replacement(client, event):
    assert client.get("/print.php").status_code == 404
