"""Every row of the On Deck chart, and every heat of Results by Racer, lines
up, at 390x844 and 1280x800 (#240). This replaces
`testing/puppeteer/ondeck-columns-test.js`, which drives PHP's pages with
jQuery, and so is skipped against Django in
`testing/exercise-one-database.sh`.

The PHP test counts the cells of every `tr` on ondeck.php and
racer-results.php, a colspan as that many columns, and a rowspan carried into
the rows below it, and expects every row to have the first row's count.
`COLUMN_COUNTS` counts as it does. On Deck (`kiosk-ondeck.js`) is a table:
a divider row per round, whose one `th` spans the lanes and the label, and a
row per heat with its label `th` and a `td` per lane, byes filled in; under
the master schedule a heat row has a second `th`, its master heat number,
and no dividers. So the count is also checked against the lanes, and rows
must exist, so an empty chart can't pass.

Results by Racer is rebuilt as racer cards (#49) and draws no table, so its
check is the PHP test's intent instead: at least one card, each with at least
one heat, and every heat item made of the same fields, compared by their
tags, `data-` attribute names and classes, not by their text.
"""

import pytest
from playwright.sync_api import expect

from core.models import RaceSettings
from racing.services import heats
from ui.tests.race import race_setup, run_heat, scheduled

LANES = 4

# The cells of each `tr`, with colspan, and a rowspan's extra rows carried
# into the rows below it, as get_column_counts in
# testing/puppeteer/ondeck-columns-test.js counts them.
COLUMN_COUNTS = """() => {
  const results = [];
  let carry = 0;
  for (const tr of document.querySelectorAll('tr')) {
    let count = carry;
    if (carry > 0) carry -= 1;
    for (const cell of tr.querySelectorAll('td,th')) {
      const rowspan = cell.getAttribute('rowspan');
      const colspan = cell.getAttribute('colspan');
      if (rowspan) carry = Number(rowspan) - 1;
      count += colspan ? Number(colspan) : 1;
    }
    results.push(count);
  }
  return results;
}"""

# The fields of each heat item of each racer card: per item, its children's
# tag, `data-` attribute names and classes.
HEAT_FIELDS = """() => [...document.querySelectorAll('[data-racer]')].map(card =>
  [...card.querySelectorAll('[data-result]')].map(item =>
    [...item.children].map(child => [
      child.tagName,
      child.getAttributeNames().filter(n => n.startsWith('data-')).sort().join(' '),
      child.className,
    ].join('|')).join(',')))"""


def raced(event, master=False):
    """Two groups of 3 on 4 lanes, so every heat has a bye, both scheduled,
    and the first two heats raced."""
    rounds = race_setup(event, groups=(("Lions", 3), ("Tigers", 3)), lanes=LANES)
    if master:
        RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    for r in rounds.values():
        scheduled(r, race=False)
    for heat in (1, 2):
        if master:
            heats.select_heat(event, heat="next-up", now_racing=True)
        else:
            heats.select_heat(
                event, round_id=rounds["Lions"].pk, heat=heat, now_racing=True
            )
        run_heat(event)
    return rounds


@pytest.mark.parametrize("master", [False, True], ids=["by-round", "master"])
def test_every_on_deck_row_has_a_cell_per_lane_and_its_labels(login, event, master):
    raced(event, master)
    page = login("anonymous")
    page.goto("/ondeck/")
    rows = page.locator("[data-chart] tr[data-heat]")
    # 3 racers on 4 lanes: 4 heats a round, 8 in all.
    expect(rows).to_have_count(8)
    expect(page.locator("[data-chart] .k-divider")).to_have_count(0 if master else 2)
    # 12 cars in 16 lane cells: a bye in every heat.
    expect(page.locator("[data-chart] tr[data-heat] .k-bye")).to_have_count(8)
    expect(rows.filter(has=page.locator(".k-bye"))).to_have_count(8)

    # one read: the chart is drawn (the counts above), and is redrawn only
    # when the poll's answer changes, which nothing here does.
    counts = page.evaluate(COLUMN_COUNTS)

    labels = 2 if master else 1
    assert counts == [LANES + labels] * (8 if master else 10), counts


def test_every_heat_of_every_racer_card_shows_the_same_fields(login, event):
    raced(event)
    page = login("anonymous")
    page.goto("/results/")
    expect(page.locator("[data-racer]")).to_have_count(6)
    expect(page.locator("table")).to_have_count(0)

    # one read: the cards are drawn (the count above), and nothing races here.
    cards = page.evaluate(HEAT_FIELDS)

    assert all(cards), cards
    fields = {item for card in cards for item in card}
    assert len(fields) == 1, fields
