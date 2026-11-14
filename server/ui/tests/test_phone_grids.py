"""A grid whose columns change at a breakpoint names its phone columns too.

With no `grid-cols-*` below the breakpoint, the grid's column on a phone is
an implicit `auto` track, as wide as its widest item's unbroken text: a
`truncate` name or a long word widens the page past a phone's screen.
`grid-cols-1` (`minmax(0, 1fr)`) keeps the column at the grid's width.

#58's phone check found the check-in list wider than the phone from a long
racer name (review finding H3), fixed with `grid-cols-1`. The awards
editor's list had the same grid, and a long award name drew it wider than
the phone too (`test_a_long_award_name_keeps_the_phone_list_one_screen_wide`,
#58 retro). The browser tests' `no_sideways_scroll` catches it only when a
test's data holds such a name; this reads every template instead.
"""

import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2]
CLASS = re.compile(r'\bclass="([^"]*)"')
BREAKPOINT_COLUMNS = re.compile(r"^(?:sm|md|lg|xl|2xl):grid-cols-")


def phone_grid_gaps(text: str) -> list[str]:
    """Each class list that is a grid on a phone, changes its columns at a
    breakpoint, and names no columns of its own."""
    out = []
    for match in CLASS.finditer(text):
        tokens = match.group(1).split()
        if (
            "grid" in tokens
            and any(BREAKPOINT_COLUMNS.match(t) for t in tokens)
            and not any(t.startswith("grid-cols-") for t in tokens)
        ):
            out.append(match.group(1))
    return out


def test_each_breakpoint_grid_names_its_phone_columns():
    found = []
    for path in sorted(SERVER.glob("*/templates/**/*.html")):
        for classes in phone_grid_gaps(path.read_text()):
            found.append(f"{path.relative_to(SERVER)}: {classes}")
    assert found == [], "add grid-cols-1 to each:\n" + "\n".join(found)


def test_the_check_reads_each_form():
    assert phone_grid_gaps('<ul class="grid gap-2 lg:grid-cols-2">') == [
        "grid gap-2 lg:grid-cols-2"
    ]
    assert phone_grid_gaps('<div class="grid gap-4 lg:grid-cols-[3fr_2fr]">')
    assert phone_grid_gaps('<ul class="grid grid-cols-1 gap-2 lg:grid-cols-2">') == []
    # A grid only from the breakpoint up is a block on a phone.
    assert phone_grid_gaps('<div class="lg:grid lg:grid-cols-2">') == []
    assert phone_grid_gaps('<div class="flex flex-col sm:grid sm:grid-cols-2">') == []
    # A grid that keeps its columns at every size, such as an icon's box.
    assert phone_grid_gaps('<button class="grid size-11 place-items-center">') == []
