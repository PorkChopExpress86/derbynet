"""The on-deck chart's heat labels show whole (#273).

Each row's label ("Heat 12", or "Lions, Round 1, Heat 12" under the master
schedule) wraps in its column at every size, where master held it on one
line and cut it with an ellipsis. The tests below hold the labels on one
line again (`AS_MASTER`) to show which they cut. PHP's ondeck.css sets no
white-space rule on `th` (ondeck.css:49-51), and its label wraps at
390x844 and 1920x1080 alike (#273's PR quotes the php-scratch run). The
label's column width and font stay as they were, so the lanes keep their
width. Wrapping at every size, not only on a phone, is the lead's ruling
under D10/ADR-10 on #273.

`tools/kiosk_screens.py` reports a label cut with an ellipsis, as it
reports any text cut with one (#256)."""

import pytest
from playwright.sync_api import Error, expect

from core.models import RaceSettings
from racing.models import TimerStatus
from roster.models import Group
from ui.tests.browser.conftest import shoot
from ui.tests.browser.test_kiosk_pages import open_kiosk
from ui.tests.race import race_setup, scheduled

# Three-digit heats: a group of HEATS racers on LANES lanes, one run,
# races HEATS heats (asserted where it is used).
HEATS, LANES = 100, 4
# A class name of one word, which only overflow-wrap breaks (mutation m01
# in #273's PR).
UNBREAKABLE = "Supercalifragilisticexpialidocious"
# A class name whose label grows its row at 1920x1080 (asserted where it
# is used).
LONGER = "White's Wolves of the Arrow of Light Den Seven"

# Each row label (`th` but the round divider): its text, its lines, and
# `problems`, what keeps it from showing whole: text wider than its box
# (the ticket's scrollWidth > clientWidth), a line past the th's content
# box's sides, or past its top or foot where the th clips (overflow not
# visible). A line's rect is its glyphs' height, which on this machine's
# Noto Sans stands outside a label that sets its row's height.
MEASURE = """() => {
  const ths = [...document.querySelectorAll('.k-deck tbody th:not(.k-divider)')];
  const range = document.createRange();
  return ths.map(th => {
    const r = th.getBoundingClientRect(), s = getComputedStyle(th);
    const left = r.left + parseFloat(s.paddingLeft);
    const right = r.right - parseFloat(s.paddingRight);
    range.selectNodeContents(th);
    const rects = [...range.getClientRects()];
    const problems = [];
    if (th.scrollWidth > th.clientWidth)
      problems.push(`${th.scrollWidth} px wide in ${th.clientWidth}`);
    for (const x of rects) {
      if (x.left < left - 0.5 || x.right > right + 0.5)
        problems.push(`a line [${x.left}, ${x.right}] past [${left}, ${right}]`);
      const clips = s.overflow !== 'visible';
      if (clips && (x.top < r.top - 0.5 || x.bottom > r.bottom + 0.5))
        problems.push(`clipped: [${x.top}, ${x.bottom}] in [${r.top}, ${r.bottom}]`);
    }
    const lines = new Set(rects.map(x => Math.round(x.top))).size;
    return {text: th.textContent, lines, fontSize: parseFloat(s.fontSize),
            width: r.width, pad: parseFloat(s.paddingLeft) + parseFloat(s.paddingRight),
            problems};
  });
}"""
# Each heat row's height and lane widths as drawn, and again with its
# labels held on one line as master drew them (`white-space: nowrap`), in
# one task, so nothing redraws in between; the inline style is removed
# before it returns. Held on one line, `cut` is a label wider than its box
# (master's ellipsis), and `fits` that each label's text fits its content
# box, inside the padding: a label that runs into its padding is not cut
# on one line but wraps ("Heat 100" at 1280x800 on CI's fonts, #273's PR).
# `wordsFit` is that each word of each label fits that box on its own: a
# word that does not breaks inside, so the label may take more lines.
AS_MASTER = """() => {
  const rows = [...document.querySelectorAll('.k-deck tbody tr')]
    .filter(tr => !tr.querySelector('.k-divider'));
  const read = () => rows.map(tr => ({
    height: tr.getBoundingClientRect().height,
    lanes: [...tr.querySelectorAll('td')].map(td => td.getBoundingClientRect().width),
    cut: [...tr.querySelectorAll('th')].some(th => th.scrollWidth > th.clientWidth),
    fits: [...tr.querySelectorAll('th')].every(th => {
      const s = getComputedStyle(th), range = document.createRange();
      range.selectNodeContents(th);
      const pad = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight);
      const box = th.getBoundingClientRect().width - pad;
      return range.getBoundingClientRect().width <= box;
    }),
    wordsFit: [...tr.querySelectorAll('th')].every(th => {
      const s = getComputedStyle(th), text = th.firstChild;
      const range = document.createRange();
      const pad = parseFloat(s.paddingLeft) + parseFloat(s.paddingRight);
      const box = th.getBoundingClientRect().width - pad;
      let at = 0;
      return text.data.split(' ').every(word => {
        range.setStart(text, at);
        range.setEnd(text, at + word.length);
        at += word.length + 1;
        return range.getBoundingClientRect().width <= box;
      });
    })}));
  const wrapped = read();
  const ths = rows.flatMap(tr => [...tr.querySelectorAll('th')]);
  ths.forEach(th => { th.style.whiteSpace = 'nowrap'; });
  const nowrap = read();
  ths.forEach(th => { th.style.whiteSpace = ''; });
  return rows.map((tr, i) => ({
    text: tr.querySelector('th:last-of-type').textContent,
    wrapped: wrapped[i], nowrap: nowrap[i], nowrapCut: nowrap[i].cut,
    fits: nowrap[i].fits, wordsFit: nowrap[i].wordsFit}));
}"""
PROBLEMS = f"({MEASURE})().flatMap(l => l.problems.map(p => `${{l.text}}: ${{p}}`))"

WINDOWS = {
    "phone": ({"width": 390, "height": 844}, {"width": 640, "height": 844}),
    "desktop": (
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
        {"width": 1024, "height": 768},
        {"width": 641, "height": 844},
    ),
}


def connected(event):
    """A connected timer, so no "Check timer" overlay covers the chart in
    a screenshot."""
    TimerStatus.objects.filter(event=event).update(
        state=TimerStatus.State.STAGING, assume_connected_for_test=True
    )


def whole_labels(page, texts: list[str]) -> list[dict]:
    """Wait for the chart to show `texts` as its row labels, then for each
    to show whole; fail with what does not."""
    labels = page.locator(".k-deck tbody th:not(.k-divider):last-of-type")
    expect(labels).to_have_text(texts)
    try:
        page.wait_for_function(f"{PROBLEMS}.length === 0", timeout=5000)
    except Error:
        pass
    # One read: the wait above has either seen every label whole or given
    # up, and this read names what is not.
    assert page.evaluate(PROBLEMS) == []
    return page.evaluate(MEASURE)


def windows(page):
    for window in WINDOWS[page.size]:
        page.set_viewport_size(window)
        yield f"{window['width']}x{window['height']}"


def bounded(labels: list[dict], rows: dict) -> None:
    """Each label whose words each fit its column takes at most two lines,
    and at least one label is so, so the bound never goes vacuous."""
    held = [label for label in labels if rows[label["text"]]["wordsFit"]]
    assert held, labels
    for label in held:
        assert label["lines"] <= 2, label


def test_each_heat_label_shows_whole(page, event):
    """ "Heat 1" to "Heat 100" each show whole, at 390 and 640 px wide on
    the phone, and at TV, laptop, projector and 641 px on the desktop.
    Held on one line as master drew them, "Heat 100" is cut on the phone
    and "Heat 1" at 641 px. On a phone the label's column stays 15vw and
    its font 3.6vw (kiosk.css phone block). A label's side padding is
    0.5vw at 641 px, and 1vw on the phone and from 1024 px. On a phone and
    at 641 px a label whose words each fit its column takes at most two
    lines ("Heat" over "100"), counted by the tops of its text's line
    boxes, and at least one label is held so (`bounded`); a word wider
    than its column breaks inside, so its label may take more. At
    1920x1080 and 1280x800 "Heat 1" fits its column on one line, and each
    label that fits keeps one line, and its row the height it had with the
    label held on one line. A label that fits only by running into its
    padding may wrap: on CI's fonts "Heat 100" took two lines at 1280x800
    (#273's PR)."""
    rounds = race_setup(event, groups=(("Lions", HEATS),), lanes=LANES)
    scheduled(rounds["Lions"])
    connected(event)
    texts = [f"Heat {n}" for n in range(1, HEATS + 1)]

    for size in windows(page):
        open_kiosk(page, event, "kiosks/ondeck.kiosk")
        labels = whole_labels(page, texts)
        # One read: AS_MASTER toggles the labels' nowrap and reads back in
        # one task, so no retrying wait can hold its comparison;
        # whole_labels has already waited for the chart.
        rows = {row["text"]: row for row in page.evaluate(AS_MASTER)}

        width = page.evaluate("innerWidth")
        if page.size == "phone":
            assert rows["Heat 100"]["nowrapCut"], rows["Heat 100"]
            bounded(labels, rows)
            for label in labels:
                assert label["pad"] == pytest.approx(0.02 * width, abs=0.05), label
                assert label["width"] == pytest.approx(0.15 * width, abs=0.5), label
                assert label["fontSize"] == pytest.approx(0.036 * width, abs=0.05)
        elif width == 641:
            assert rows["Heat 1"]["nowrapCut"], rows["Heat 1"]
            bounded(labels, rows)
            for label in labels:
                assert label["pad"] == pytest.approx(0.01 * width, abs=0.05), label
        else:
            for label in labels:
                assert label["pad"] == pytest.approx(0.02 * width, abs=0.05), label
        if width >= 1280:
            assert rows["Heat 1"]["fits"], (size, rows["Heat 1"])
            lines = {label["text"]: label["lines"] for label in labels}
            for text, row in rows.items():
                if not row["fits"]:
                    continue
                assert lines[text] == 1, (size, text, row)
                assert row["wrapped"]["height"] == pytest.approx(
                    row["nowrap"]["height"], abs=0.5
                ), (size, row)
        shoot(page, f"kiosk-ondeck-labels-{size}")


COLUMNS = """() => [...document.querySelectorAll('.k-deck colgroup col')]
  .map(c => c.getBoundingClientRect().width)"""


def column_widths(page, master: float, label: float) -> list[float]:
    """Wait up to 5 s for the master heat's and the label's columns to be
    `master` and `label` of the window's width, then read them once for
    the asserts to name."""
    try:
        page.wait_for_function(
            f"""() => {{ const w = ({COLUMNS})();
              return Math.abs(w[0] - {master} * innerWidth) <= 0.5
                && Math.abs(w[1] - {label} * innerWidth) <= 0.5; }}""",
            timeout=5000,
        )
    except Error:
        pass
    # One read: the wait above has either seen both widths or given up.
    return page.evaluate(COLUMNS)


def test_each_master_schedule_label_shows_whole(page, event):
    """Under the master schedule each label names its round, "Lions, Round
    1, Heat 14", and shows whole at every window size; the unbreakable
    class name's breaks inside its column. Held on one line, each of the
    22 is cut at every size. The label's column stays 14vw and the master
    heat's 6vw (24vw and 10vw on a phone), so the lanes keep their width,
    the same as with the labels held on one line. At 1920x1080 the rows of
    the two long class names grow past the height they have with the
    label on one line. The classes are renamed after their racers are
    made, so the racers keep their short names, such as "Bears1 Racer"."""
    rounds = race_setup(
        event, groups=(("Lions", 14), ("Bears", 4), ("Tigers", 4)), lanes=LANES
    )
    RaceSettings.objects.filter(event=event).update(use_master_sched=True)
    for r in rounds.values():
        scheduled(r, race=False)
    Group.objects.filter(event=event, name="Bears").update(name=UNBREAKABLE)
    Group.objects.filter(event=event, name="Tigers").update(name=LONGER)
    connected(event)

    for size in windows(page):
        open_kiosk(page, event, "kiosks/ondeck.kiosk")
        expect(page.locator(".k-deck col.k-master")).to_have_count(1)
        labels = page.locator(".k-deck tbody th:not(.k-divider):last-of-type")
        expect(labels).to_have_count(22)
        texts = labels.all_text_contents()
        assert "Lions, Round 1, Heat 14" in texts, texts
        assert f"{UNBREAKABLE}, Round 1, Heat 4" in texts, texts
        assert f"{LONGER}, Round 1, Heat 4" in texts, texts
        expect(
            page.locator(".k-deck td .k-racer", has_text="Bears1 Racer").first
        ).to_have_text("Bears1 Racer")
        whole_labels(page, texts)
        master, label = (0.10, 0.24) if page.size == "phone" else (0.06, 0.14)
        widths = column_widths(page, master, label)
        # One read: AS_MASTER toggles the labels' nowrap and reads back in
        # one task, so no retrying wait can hold its comparison;
        # whole_labels has already waited for the chart.
        rows = page.evaluate(AS_MASTER)

        width = page.evaluate("innerWidth")
        assert widths[0] == pytest.approx(master * width, abs=0.5), (size, widths)
        assert widths[1] == pytest.approx(label * width, abs=0.5), (size, widths)
        assert all(row["nowrapCut"] for row in rows), (size, rows)
        for row in rows:
            assert row["wrapped"]["lanes"] == pytest.approx(
                row["nowrap"]["lanes"], abs=0.5
            ), (size, row)
        if size == "1920x1080":
            grown = [r for r in rows if r["text"].startswith((UNBREAKABLE, LONGER))]
            assert len(grown) == 8, rows
            for row in grown:
                assert row["wrapped"]["height"] > row["nowrap"]["height"] + 1, row
        shoot(page, f"kiosk-ondeck-master-labels-{size}")


# The lines of the long class's divider, by their tops.
DIVIDER_LINES = f"""() => {{
  const th = [...document.querySelectorAll('.k-deck th.k-divider')]
    .find(t => t.textContent.startsWith({LONGER!r}));
  const range = document.createRange();
  range.selectNodeContents(th);
  return new Set([...range.getClientRects()].map(x => Math.round(x.top))).size;
}}"""


def divider_lines(page) -> int:
    """Wait up to 5 s for the divider to take one line, then read its
    lines once for the assert to name."""
    try:
        page.wait_for_function(f"({DIVIDER_LINES})() === 1", timeout=5000)
    except Error:
        pass
    # One read: the wait above has either seen one line or given up.
    return page.evaluate(DIVIDER_LINES)


def test_the_round_divider_stays_on_one_line(page, event):
    """A round's divider, outside the master schedule, stays on one line
    at every window size, cut with an ellipsis past its box, as before
    #273: the divider is no heat label (the lead's ruling under D10/ADR-10,
    #273)."""
    rounds = race_setup(event, groups=(("Lions", 4), ("Tigers", 4)))
    for r in rounds.values():
        scheduled(r, race=False)
    Group.objects.filter(event=event, name="Tigers").update(name=LONGER)

    for size in windows(page):
        open_kiosk(page, event, "kiosks/ondeck.kiosk")
        divider = page.locator(".k-deck th.k-divider", has_text=LONGER)
        expect(divider).to_have_text(f"{LONGER}, Round 1")
        expect(divider).to_have_css("white-space", "nowrap")
        expect(divider).to_have_css("text-overflow", "ellipsis")
        expect(divider).to_have_css("overflow", "hidden")
        assert divider_lines(page) == 1, size
