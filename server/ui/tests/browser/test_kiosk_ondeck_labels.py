"""The on-deck chart's heat labels and round dividers show whole (#273,
#283).

Each row's label ("Heat 12", or "Lions, Round 1, Heat 12" under the master
schedule) wraps in its column at every size, where #273's master held it
on one line and cut it with an ellipsis. The tests below hold the labels
on one line again (`AS_MASTER`) to show which they cut. PHP's ondeck.css
sets no white-space rule on `th` (ondeck.css:49-51), and its label wraps
at 390x844 and 1920x1080 alike (#273's PR quotes the php-scratch run). The
label's column width stays as it was, so the lanes keep their width.
Wrapping at every size, not only on a phone, is the lead's ruling under
D10/ADR-10 on #273.

From 641 px the label's font is capped by the window's width (2.85vw to
1023 px, 2.5vw from 1024 px), so in a tall window it no longer outgrows
its column and breaks "Heat" inside the word; a round's divider wraps
between words instead of being cut. Neither breaks a word that fits its
box on one line; a word wider than its box breaks inside (`overflow-wrap:
anywhere`). D10/ADR-10, #283.

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

# Each `th` the selector names (by default each row label, every `th` but
# the round divider): its text, its lines, and `problems`, what keeps it
# from showing whole: text wider than its box (#273's scrollWidth >
# clientWidth), a line past the th's content box's sides, past its top or
# foot where the th clips (overflow not visible), or a word broken across
# lines although it fits the content box on one line (#283). A word's
# width is the sum of its pieces' rects, the same whether it is broken or
# not; `checked` counts the words that fit, so a label with none never
# passes the word check by default, and `wide` lists the others. A
# line's rect is its glyphs' height, which on this machine's Noto Sans
# stands outside a label that sets its row's height. `rowFont` is the
# font the th would have with no rule of its own, its row's.
LABEL_THS = ".k-deck tbody th:not(.k-divider)"
MEASURE = """(sel) => {
  const ths = [...document.querySelectorAll(sel)];
  const range = document.createRange();
  return ths.map(th => {
    const r = th.getBoundingClientRect(), s = getComputedStyle(th);
    const left = r.left + parseFloat(s.paddingLeft);
    const right = r.right - parseFloat(s.paddingRight);
    range.selectNodeContents(th);
    const rects = [...range.getClientRects()];
    const problems = [];
    const text = th.firstChild;
    let at = 0, checked = 0;
    const wide = [];
    for (const word of text.data.split(' ')) {
      range.setStart(text, at);
      range.setEnd(text, at + word.length);
      at += word.length + 1;
      const pieces = [...range.getClientRects()];
      const width = pieces.reduce((sum, x) => sum + x.width, 0);
      if (!word) continue;
      if (width > right - left - 0.5) {
        wide.push(word);
        continue;
      }
      checked++;
      const tops = new Set(pieces.map(x => Math.round(x.top))).size;
      if (tops > 1)
        problems.push(`"${word}" (${width} px in ${right - left}) on ${tops} lines`);
    }
    range.selectNodeContents(th);
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
            rowFont: parseFloat(getComputedStyle(th.parentElement).fontSize),
            width: r.width, pad: parseFloat(s.paddingLeft) + parseFloat(s.paddingRight),
            checked, wide, problems};
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


def problems(sel: str = LABEL_THS) -> str:
    named = "l => l.problems.map(p => `${l.text}: ${p}`)"
    return f"({MEASURE})({sel!r}).flatMap({named})"


def measure(sel: str = LABEL_THS) -> str:
    return f"({MEASURE})({sel!r})"


WINDOWS = {
    "phone": ({"width": 390, "height": 844}, {"width": 640, "height": 844}),
    "desktop": (
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
        {"width": 1024, "height": 768},
        {"width": 641, "height": 844},
        # Tall windows, where the label's 2.2vh font outgrew its 9vw column
        # and broke "Hea" / "t" before #283's cap.
        {"width": 641, "height": 1024},
        {"width": 800, "height": 1280},
        {"width": 1024, "height": 1366},
        {"width": 1080, "height": 1920},
    ),
}
# The label font's cap, a share of the window's width, by the window's
# width (kiosk.css, D10/ADR-10, #283); a phone's label font is 3.6vw.
CAP = ((640, 0.036), (1023, 0.0285), (10**6, 0.025))
# The windows where the cap is smaller than the row's 2.2vh font, so it
# sets the label's font; in every other window the label keeps its row's
# font.
CAPPED = {"641x844", "641x1024", "800x1280", "1024x1366", "1080x1920"}


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
    return whole(page)


def whole(page, sel: str = LABEL_THS) -> list[dict]:
    """Wait for each `th` the selector names to show whole, with no word
    that fits its box broken; fail with what does not. Each has at least
    one word that fits, so the word check is never vacuous."""
    try:
        page.wait_for_function(f"{problems(sel)}.length === 0", timeout=5000)
    except Error:
        pass
    # One read: the wait above has either seen every th whole or given up,
    # and this read names what is not.
    assert page.evaluate(problems(sel)) == []
    # One read: the wait above has seen the ths whole.
    measured = page.evaluate(measure(sel))
    assert measured, sel
    for th in measured:
        assert th["checked"] >= 1, th
    return measured


def capped_font(labels: list[dict], size: str, width: int) -> None:
    """Each label's font is its row's font or the cap, whichever is
    smaller, and the cap is the smaller exactly in the `CAPPED` windows."""
    cap = next(share for widest, share in CAP if width <= widest) * width
    for label in labels:
        if width <= 640:
            assert label["fontSize"] == pytest.approx(cap, abs=0.05), (size, label)
            continue
        assert (cap < label["rowFont"]) == (size in CAPPED), (size, cap, label)
        assert label["fontSize"] == pytest.approx(
            min(cap, label["rowFont"]), abs=0.05
        ), (size, label)


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
    """ "Heat 1" to "Heat 100" each show whole, with each word fitting its
    box on one line and none broken across lines, at 390 and 640 px wide
    on the phone, and at
    TV, laptop, projector, 641 px and the four tall windows on the
    desktop (`WINDOWS`). Held on one line as #273's master drew them,
    "Heat 100" is cut on the phone and "Heat 1" at 641 px. On a phone the
    label's column stays 15vw and its font 3.6vw (kiosk.css phone block).
    From 641 px the font is its row's or the cap, 2.85vw to 1023 px and
    2.5vw from 1024 px, whichever is smaller, and the cap is the smaller
    in the `CAPPED` windows only (`capped_font`). A label's side padding
    is 0.5vw from 641 to 1023 px, and 1vw on the phone and from 1024 px. A
    label whose words each fit its column takes at most two lines ("Heat"
    over "100"), counted by the tops of its text's line boxes, and at
    least one label is held so (`bounded`); a word wider than its column
    breaks inside, so its label may take more. At
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
        for label in labels:
            assert label["wide"] == [], (size, label)
        bounded(labels, rows)
        capped_font(labels, size, width)
        pad = 0.01 if 641 <= width <= 1023 else 0.02
        for label in labels:
            assert label["pad"] == pytest.approx(pad * width, abs=0.05), (size, label)
        if page.size == "phone":
            assert rows["Heat 100"]["nowrapCut"], rows["Heat 100"]
            for label in labels:
                assert label["width"] == pytest.approx(0.15 * width, abs=0.5), label
        elif width == 641:
            assert rows["Heat 1"]["nowrapCut"], (size, rows["Heat 1"])
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
    1, Heat 14", and shows whole at every window size, with no word that
    fits its box broken across lines. Each word but the unbreakable class
    name fits its box on one line; that name is wider than its column at
    every size, and breaks inside. Its font follows the cap as a plain
    heat's does (`capped_font`). Held on one line, each of the
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
        measured = whole_labels(page, texts)
        for th in measured:
            wide = [f"{UNBREAKABLE},"] if th["text"].startswith(UNBREAKABLE) else []
            assert th["wide"] == wide, (size, th)
        width = page.evaluate("innerWidth")
        capped_font(measured, size, width)
        master, label = (0.10, 0.24) if page.size == "phone" else (0.06, 0.14)
        widths = column_widths(page, master, label)
        # One read: AS_MASTER toggles the labels' nowrap and reads back in
        # one task, so no retrying wait can hold its comparison;
        # whole_labels has already waited for the chart.
        rows = page.evaluate(AS_MASTER)

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


# A class name of one word wider than a phone's divider, which only
# overflow-wrap breaks (asserted where it is used).
UNBREAKABLE_ROUND = UNBREAKABLE * 2
DIVIDERS = ".k-deck th.k-divider"
# Each divider's text, its lines by their tops, its content box's width,
# and the width of its text held on one line (`white-space: nowrap`, set
# and removed in one task, so nothing redraws in between).
DIVIDER_LINES = f"""() => [...document.querySelectorAll({DIVIDERS!r})].map(th => {{
  const s = getComputedStyle(th), range = document.createRange();
  const box = th.getBoundingClientRect().width
    - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
  range.selectNodeContents(th);
  const lines = new Set([...range.getClientRects()].map(x => Math.round(x.top))).size;
  th.style.whiteSpace = 'nowrap';
  const oneLine = range.getBoundingClientRect().width;
  th.style.whiteSpace = '';
  return {{text: th.textContent, lines, box, oneLine}};
}})"""


def test_the_round_divider_shows_whole(page, event):
    """A round's divider, outside the master schedule, shows whole at every
    window size, with no word that fits its box broken across lines: it
    wraps between words, where #273 held it on one line and cut it with an
    ellipsis (D10/ADR-10, #283). A divider whose text fits its box on one
    line keeps one line, and one that does not takes more. At 390x844 the
    long class's divider does not fit on one line, so the wrap is tested
    on every font, and each word of it and of "Lions, Round 1" fits its
    box at every size; the
    unbreakable class's name is the one word wider than its box there, and
    shows whole, so it breaks inside."""
    rounds = race_setup(event, groups=(("Lions", 4), ("Tigers", 4), ("Bears", 4)))
    for r in rounds.values():
        scheduled(r, race=False)
    Group.objects.filter(event=event, name="Tigers").update(name=LONGER)
    Group.objects.filter(event=event, name="Bears").update(name=UNBREAKABLE_ROUND)
    texts = ["Lions, Round 1", f"{LONGER}, Round 1", f"{UNBREAKABLE_ROUND}, Round 1"]

    for size in windows(page):
        open_kiosk(page, event, "kiosks/ondeck.kiosk")
        expect(page.locator(DIVIDERS)).to_have_text(texts)
        measured = {d["text"]: d for d in whole(page, DIVIDERS)}
        assert measured[texts[0]]["wide"] == [], measured
        assert measured[texts[1]]["wide"] == [], measured
        # One read: DIVIDER_LINES toggles nowrap and reads back in one
        # task; whole() has already waited for the dividers.
        dividers = {d["text"]: d for d in page.evaluate(DIVIDER_LINES)}
        for d in dividers.values():
            if d["oneLine"] <= d["box"]:
                assert d["lines"] == 1, (size, d)
            else:
                assert d["lines"] >= 2, (size, d)
        if size == "390x844":
            assert dividers[texts[1]]["oneLine"] > dividers[texts[1]]["box"], dividers
            assert measured[texts[2]]["wide"] == [f"{UNBREAKABLE_ROUND},"], measured
        shoot(page, f"kiosk-ondeck-dividers-{size}")
