"""tools/docstring_claims.py lists each number and quoted label in a test
file's prose, and `--check` refuses a PR body whose `<details>` blocks
leave one out (#283 retro)."""

import os
import subprocess
import textwrap

import pytest

from tools import docstring_claims as dc

SAMPLE = textwrap.dedent(
    '''\
    """The chart's labels show whole at 390x844 (#273, D10/ADR-10).

    PHP's ondeck.css:49-51 sets no rule; "Lions, Round
    1, Heat 14" wraps."""

    # A column of 15vw, `HEATS` heats (asserted where used).
    WIDTH = 0.15


    def test_labels(page):
        """Each label takes at most two lines, "Heat 100" among them, in
        58.3 px."""
        # One read: the wait above has seen it.
        assert page.lines("Heat 100") <= 2
        assert page.width == 58.3
    '''
)


def test_it_lists_each_claim_with_the_code_lines_of_its_literal(tmp_path):
    path = tmp_path / "test_sample.py"
    path.write_text(SAMPLE)
    found = {(c.scope, c.text): (c.line, hits) for c, hits in dc.claims(path)}
    assert found == {
        ("module", "390x844"): (1, []),
        ("module", '"Lions, Round\n1, Heat 14"'.replace("\n", " ")): (3, []),
        ("module", "15vw"): (6, [7]),
        ("test_labels", "two lines"): (11, []),
        ("test_labels", '"Heat 100"'): (11, [14]),
        ("test_labels", "58.3 px"): (12, [15]),
    }


def test_check_refuses_a_body_that_leaves_a_claim_out(tmp_path, capsys):
    path = tmp_path / "test_sample.py"
    path.write_text(SAMPLE)
    rows = [
        "| test_sample.py:1 `module` | 390x844 |",
        '| test_sample.py:3 `module` | "Lions, Round 1, Heat 14" |',
        "| test_sample.py:6 `module` | 15vw |",
        "| test_sample.py:11 `test_labels` | 2 lines |",
        '| test_sample.py:11 `test_labels` | "Heat 100" |',
        "| test_sample.py:12 `test_labels` | 58.3 px |",
    ]
    body = tmp_path / "body.md"
    body.write_text("<details>\n" + "\n".join(rows) + "\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 0

    body.write_text(
        "58.3 px outside the block\n<details>\n"
        + "\n".join(r for r in rows if "58.3 px" not in r)
        + "\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert capsys.readouterr().out.strip() == (
        f"{path}:12: test_labels: 58.3 px is in no <details> block line that"
        " names test_labels (by its function name, not a nickname)"
    )


def test_a_claim_counts_only_on_a_line_that_names_its_test(tmp_path, capsys):
    """#324's body listed "2" for no test, and `--check` passed
    `test_lane_two_off_leaves_it_out_of_heat_ready`'s 2 because a 2 stood
    elsewhere in a block; a 12 or another test's row counts no more (#324
    retro). A module claim counts on any line of a block."""
    path = tmp_path / "test_sample.py"
    path.write_text(
        '"""1920x1080 here."""\n\n\ndef test_lanes():\n    """2 lanes."""\n\n\n'
        'def test_heats():\n    """2 heats."""\n'
    )
    body = tmp_path / "body.md"
    body.write_text(
        "<details>\n| `test_lanes` | 12 lanes |\n| `test_heats` | 2 heats |\n"
        "| test_sample.py `module` | 1920x1080 |\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert capsys.readouterr().out.strip() == (
        f"{path}:5: test_lanes: 2 is in no <details> block line that names test_lanes"
        " (by its function name, not a nickname)"
    )
    body.write_text(
        "<details>\n| `test_lanes` | 2 lanes |\n| `test_heats` | 2 heats |\n"
        "| `CONSTANT` comment | 1920x1080 |\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 0
    body.write_text(body.read_text().replace("1920x1080", "1920x108"))
    assert dc.main(["--check", str(body), str(path)]) == 1


def test_a_same_row_lists_for_the_test_above_it(tmp_path):
    """#330's table wrote `same` for a test's second row, as a reader
    would."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_lanes():\n    """2 lanes in 58.3 px."""\n')
    body = tmp_path / "body.md"
    body.write_text("<details>\n| `test_lanes` | 2 |\n| same | 58.3 px |\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 0
    body.write_text(
        "<details>\n| `test_lanes` | 2 |\n</details>\n<details>\n58.3 px\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1


def test_a_row_naming_another_test_by_nickname_lists_nothing(tmp_path):
    """Only a `same` row or a wrapped line takes the scope above it: a row
    that names a test by nickname is #324's failure again (#340 review)."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_lanes():\n    """2 lanes, 9 heats."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        "<details>\n| `test_lanes` | 9 |\n| the heats one | 2, 7 |\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1
    body.write_text(
        "<details>\n- `test_lanes`: 9, and\n  2 in the lane count\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 0


PASTED = """\
<details><summary>one failure from the log</summary>

{fence}
_____ test_waits[single] _____
E   AssertionError: ("ERROR  mod.py: (timed out after 3 s)
{fence}
{after}
</details>
"""


@pytest.mark.parametrize("fence", ["```", "~~~", "````"])
def test_pasted_output_in_a_details_block_lists_no_claim(tmp_path, fence):
    """#367's pasted traceback named the test and held `timed out after
    3 s`, and passed a "3 s" no assert read (#367 retro). A wrapped line
    after the fence takes no scope from inside it."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(PASTED.format(fence=fence, after=""))
    assert dc.main(["--check", str(body), str(path)]) == 1
    body.write_text(PASTED.format(fence=fence, after="  3 s, wrapped"))
    assert dc.main(["--check", str(body), str(path)]) == 1
    body.write_text(PASTED.format(fence=fence, after="- `test_waits`: 3 s"))
    assert dc.main(["--check", str(body), str(path)]) == 0


def test_a_row_before_a_fence_keeps_its_scope_after_it(tmp_path):
    """The fenced lines drop out whole, so a wrapped line after the fence
    lists for the row above it."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        "<details>\n- `test_waits`: the log shows\n```\nwaiting\n```\n"
        "  3 s, the bound\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 0


@pytest.mark.parametrize(
    "fence, inner", [("````", "```"), ("```", "~~~"), ("```", "```python")]
)
def test_a_fence_closes_only_on_its_own_mark(tmp_path, fence, inner):
    """A shorter mark, the other mark, or a mark with an info string
    inside a fence leaves it open. The inner mark comes twice, so a fence
    it closed opens again and the block still ends."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        f"<details>\n{fence}\n{inner}\n- `test_waits`: 3 s\n{inner}\n{fence}\n"
        "</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1


def test_a_fence_that_quotes_a_details_tag_opens_no_block(tmp_path):
    """#340's body quoted `<details>` in a diff fence ahead of its real
    blocks, so the text between read as a block."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        "```diff\n- a claim on a <details> line\n```\n"
        "- `test_waits`: 3 s\n<details>\n| `test_lanes` | 2 |\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1


def test_an_unclosed_fence_is_named_by_its_line(tmp_path, capsys):
    """A fence left open drops the rest of the body, its `</details>`
    too, so the error names the fence rather than the claim it hid
    (#367 retro)."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text("<details>\n```\nfoo\n- `test_waits`: 3 s\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert capsys.readouterr().out == (
        f"{body}:2: this fence never closes at its own indent, so the rest of"
        " the body, every <details> block in it, reads as code\n"
    )


def test_a_line_left_of_its_fence_is_named(tmp_path, capsys):
    """A line left of a list item's fence may end the item, and the fence
    with it, so the error names that line (#402 review)."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        "<details>\n- `test_waits`: the log\n  ```\nlog\n  ```\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert capsys.readouterr().out == (
        f"{body}:4: this line sits left of the fence opened on line 3, so the"
        " tool can't tell where that fence ends: indent it as the fence is, or"
        " close the fence above it\n"
    )


def test_a_fence_mark_off_its_fence_indent_is_named(tmp_path, capsys):
    """Whether CommonMark closes a fence on a mark at another indent than
    its opener's depends on the container, which the tool can't see, so it
    names such a mark (#402 review)."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text("<details>\n```\nlog\n  ```\n- `test_waits`: 3 s\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert capsys.readouterr().out == (
        f"{body}:4: this fence mark sits off the indent of the fence opened on"
        " line 2, so the tool can't tell whether it closes it: put it at that"
        " fence's indent\n"
    )


# Each case's id, its lines, the words of `inner` and `after` that
# markdown-it-py's CommonMark parser fences in those lines alone (the probe
# and its output are in PR #402's body), the words details() keeps, and the
# line its error names, where it stops.
FENCE_SWEEP = [
    ("backtick-0", "```\ninner\n```\nafter", "inner", "after", None),
    ("backtick-0-py", "```py\ninner\n```\nafter", "inner", "after", None),
    ("backtick-0-tick", "```a`b\ninner\n```\nafter", "after", "", 5),
    ("tilde-0", "~~~\ninner\n~~~\nafter", "inner", "after", None),
    ("tilde-0-py", "~~~py\ninner\n~~~\nafter", "inner", "after", None),
    ("tilde-0-tick", "~~~a`b\ninner\n~~~\nafter", "inner", "after", None),
    ("backtick-1", " ```\n inner\n ```\nafter", "inner", "after", None),
    ("backtick-1-py", " ```py\n inner\n ```\nafter", "inner", "after", None),
    ("backtick-1-tick", " ```a`b\n inner\n ```\nafter", "after", "", 6),
    ("tilde-1", " ~~~\n inner\n ~~~\nafter", "inner", "after", None),
    ("tilde-1-py", " ~~~py\n inner\n ~~~\nafter", "inner", "after", None),
    ("tilde-1-tick", " ~~~a`b\n inner\n ~~~\nafter", "inner", "after", None),
    ("backtick-2", "  ```\n  inner\n  ```\nafter", "inner", "after", None),
    ("backtick-2-py", "  ```py\n  inner\n  ```\nafter", "inner", "after", None),
    ("backtick-2-tick", "  ```a`b\n  inner\n  ```\nafter", "after", "", 6),
    ("tilde-2", "  ~~~\n  inner\n  ~~~\nafter", "inner", "after", None),
    ("tilde-2-py", "  ~~~py\n  inner\n  ~~~\nafter", "inner", "after", None),
    ("tilde-2-tick", "  ~~~a`b\n  inner\n  ~~~\nafter", "inner", "after", None),
    ("backtick-3", "   ```\n   inner\n   ```\nafter", "inner", "after", None),
    ("backtick-3-py", "   ```py\n   inner\n   ```\nafter", "inner", "after", None),
    ("backtick-3-tick", "   ```a`b\n   inner\n   ```\nafter", "after", "", 6),
    ("tilde-3", "   ~~~\n   inner\n   ~~~\nafter", "inner", "after", None),
    ("tilde-3-py", "   ~~~py\n   inner\n   ~~~\nafter", "inner", "after", None),
    ("tilde-3-tick", "   ~~~a`b\n   inner\n   ~~~\nafter", "inner", "after", None),
    ("backtick-4", "    ```\n    inner\n    ```\nafter", "", "after", None),
    ("backtick-4-py", "    ```py\n    inner\n    ```\nafter", "", "after", None),
    ("backtick-4-tick", "    ```a`b\n    inner\n    ```\nafter", "", "", 6),
    ("tilde-4", "    ~~~\n    inner\n    ~~~\nafter", "", "after", None),
    ("tilde-4-py", "    ~~~py\n    inner\n    ~~~\nafter", "", "after", None),
    ("tilde-4-tick", "    ~~~a`b\n    inner\n    ~~~\nafter", "", "after", None),
    ("tab-indent", "\t```\n\tinner\n\t```\nafter", "", "after", None),
    ("inline-span", "```code``` is a span\ninner\nafter", "", "after inner", None),
    ("close-shorter", "````\ninner\n```\nafter", "after inner", "", 3),
    ("close-same", "````\ninner\n````\nafter", "inner", "after", None),
    ("close-longer", "````\ninner\n`````\nafter", "inner", "after", None),
    ("close-other-mark", "````\ninner\n~~~~\nafter", "after inner", "", 3),
    ("close-with-info", "````\ninner\n```` x\nafter", "after inner", "", 3),
    ("close-trailing-spaces", "````\ninner\n````  \nafter", "inner", "after", None),
    ("close-trailing-tab", "````\ninner\n````\t\nafter", "inner", "after", None),
    ("close-1", "````\ninner\n ````\nafter", "inner", "", 5),
    ("close-3", "````\ninner\n   ````\nafter", "inner", "", 5),
    ("close-4", "````\ninner\n    ````\nafter", "after inner", "", 5),
    ("unclosed", "```\ninner\nafter", "after inner", "", 3),
    ("list-item-2", "- row\n  ```\n  inner\n  ```\nafter", "inner", "after", None),
    (
        "list-item-4",
        "- row\n    ```\n    inner\n    ```\nafter",
        "inner",
        "after",
        None,
    ),
    (
        "list-item-6",
        "- row\n      ```\n      inner\n      ```\nafter",
        "",
        "after",
        None,
    ),
    (
        "ordered-item-6",
        "1. row\n      ```\n      inner\n      ```\nafter",
        "inner",
        "after",
        None,
    ),
    (
        "fence-on-the-marker-line",
        "- ```\n  inner\n  ```\nafter",
        "inner",
        "after",
        None,
    ),
    ("star-marker-line", "* ```\n  inner\n  ```\nafter", "inner", "after", None),
    ("plus-marker-line", "+ ```\n  inner\n  ```\nafter", "inner", "after", None),
    ("paren-marker-line", "1) ```\n   inner\n   ```\nafter", "inner", "after", None),
    ("blockquote", "> ```\n> inner\n> ```\nafter", "inner", "after", None),
    ("blockquote-ends-it", "> ```\n> inner\n\nafter", "inner", "", 5),
    (
        "fence-on-an-ordered-marker-line",
        "1. ```\n   inner\n   ```\nafter",
        "inner",
        "after",
        None,
    ),
    ("close-tab-indent", "````\ninner\n\t````\nafter", "after inner", "", 5),
    ("open3-close4", "   ```\ninner\n    ```\nafter", "after inner", "", 4),
    ("open2-close5", "  ```\ninner\n     ```\nafter", "after inner", "", 4),
    ("close-quoted", "```\ninner\n> ```\nafter", "after inner", "", 5),
    ("close-on-marker", "```\ninner\n- ```\nafter", "after inner", "", 3),
    ("close-on-ordered", "```\ninner\n1. ```\nafter", "after inner", "", 3),
    ("quoted-open-bare-close", "> ```\n> inner\n```\nafter", "after inner", "", 5),
    ("item4-close7", "- row\n    ```\n    inner\n       ```\nafter", "inner", "", 6),
    ("item4-close1", "- row\n    ```\n    inner\n ```\nafter", "after inner", "", 6),
    ("item2-close0", "- row\n  ```\n  inner\n```\nafter", "after inner", "", 6),
    ("item4-close2", "- row\n    ```\n    inner\n  ```\nafter", "inner", "", 6),
    ("quote-close-deeper", "> ```\n> inner\n>     ```\nafter", "inner", "", 5),
    ("nested-quote-close", "> ```\n> inner\n> > ```\nafter", "inner", "", 5),
    ("item-open-quote-close", "- ```\n  inner\n  > ```\nafter", "inner", "", 5),
    ("item-line-left-of-fence", "- row\n  ```\ninner\n  ```\nafter", "after", "", 5),
    ("quote-blank-line", "> ```\n> inner\n\n> ```\nafter", "inner", "", 5),
    ("quote-bare-mark-line", "> ```\n>\n> inner\n> ```\nafter", "inner", "after", None),
    (
        "item-blank-line",
        "- row\n  ```\n  inner\n\n  ```\nafter",
        "inner",
        "after",
        None,
    ),
]


@pytest.mark.parametrize("eol", ["\n", "\r\n"], ids=["LF", "CRLF"])
@pytest.mark.parametrize(
    "lines, fenced, kept, error",
    [pytest.param(*row[1:], id=row[0]) for row in FENCE_SWEEP],
)
def test_the_fence_rule_drops_what_commonmark_fences(lines, fenced, kept, error, eol):
    """details() keeps no word CommonMark fences in these cases: where it
    can't tell the container it drops more, or stops with an error. Each
    case's outcome is pinned, so a change shows in the table (#402
    review)."""
    body = eol.join(["<details>", "", *lines.split("\n"), "", "</details>", ""])
    try:
        text, line = "\n".join(dc.details(body)), None
    except dc.FenceError as e:
        text, line = "", e.line
    left = {w for w in ("inner", "after") if w in text}
    assert (left, line) == (set(kept.split()), error)
    assert not set(fenced.split()) & left


def test_a_class_claim_counts_on_a_row_naming_its_method(tmp_path):
    """#333's rows name the test method, under its class's claims."""
    path = tmp_path / "test_sample.py"
    path.write_text(
        'class Merge:\n    def test_pr(self):\n        """PR 7."""\n\n\n'
        "def test_other():\n    pass\n"
    )
    body = tmp_path / "body.md"
    body.write_text("<details>\n| `test_pr` | PR 7 |\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 0
    body.write_text("<details>\n| `test_other` | PR 7 |\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 1


# `pause_clock_at` as #348's second head (6d6ac602) had it, in part, and
# the tree's own uses of the word that cite nothing (#370 review).
PAUSE_CLOCK_AT = '''\
def pause_clock_at(page, at):
    """Pause the clock. A timer a loaded page queued between the install
    and the pause fired once in the pause's jump (the #359 review's
    probe)."""
    page.clock.pause_at(at)


def cites(page):
    """As #341's PHP probe showed, and #360's probe, and probe-php.log's
    probe."""


def ticket(page):
    """As #360's probe showed."""


def stop(page):
    """It probes the page, and `#1's probe` in backticks names code."""
    # Probes run here, as a verb.
    os.close(fd)  # which drops the probe's own shared lock
    """The probe of a held lock returns. A probe that kept its shared lock
    would queue. The download hung after a probe that passed, above the
    probe's 50 kB/s floor, as when the probe passed and the stall came
    later."""
'''


def test_a_cited_probe_is_a_claim(tmp_path, capsys):
    """#348's docstring cited the review's probe, which no test held, and
    cutting it cost a third head and its load runs (#348 retro). A probe
    the code names as its own, as conftest.py, test_report_header.py and
    test_ubuntu_mirror.py do, is no cite (#370 review)."""
    path = tmp_path / "helpers.py"
    path.write_text(PAUSE_CLOCK_AT)
    found = [(c.scope, c.text, c.line) for c, _ in dc.claims(path)]
    assert found == [
        ("pause_clock_at", "probe", 4),
        ("cites", "probe", 9),
        ("ticket", "probe", 14),
    ]
    body = tmp_path / "body.md"
    body.write_text(
        "<details>\n| `pause_clock_at` | 1 | x |\n| `cites` | probe | y |\n"
        "| `ticket` | probe | z |\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert (
        "pause_clock_at: probe is in no <details> block line" in capsys.readouterr().out
    )
    body.write_text(
        "<details>\n| `pause_clock_at` | probe | test_a_loaded_page_s_timer |\n"
        "| `cites` | probe | test_cites |\n| `ticket` | probe | test_ticket |\n"
        "</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 0


@pytest.mark.parametrize(
    "prose, cited",
    [
        ("the #359 review's probe", True),
        ("the #342\n    review's probe", True),
        ("#360's probe", True),
        ("the review's PHP probe", True),
        ("probe-php.log's probe", True),
        ("#247 retro: a probe on a paused clock", False),
        ("the probe's own shared lock", False),
        ("(#316 retro). The probe of a held lock", False),
        ("the #359 review probe", False),
        ("#360's probes", False),
    ],
)
def test_a_probe_counts_only_cited_as_a_ticket_s_review_s_or_log_s(
    tmp_path, prose, cited
):
    path = tmp_path / "t.py"
    path.write_text(f'def test_x():\n    """{prose}."""\n')
    found = [c.text for c, _ in dc.claims(path) if c.text == "probe"]
    assert found == ["probe"] * cited


# D123, #374: `--check --base` asks only for the claims of the scopes the
# diff adds or changes, and of the tests that read what it changes.
SCOPED = textwrap.dedent(
    '''\
    """Two lanes."""

    import pytest

    # 4 lanes.
    LANES = 4


    def race(page):
        """Stops 4 lanes."""
        return LANES


    def test_unchanged(page):
        """9 heats."""
        assert page


    def test_changed(page):
        """3 heats."""
        assert page


    def test_calls_helper(page):
        """5 cars."""
        race(page)


    class TestBoard:
        def test_one(self):
            """7 rows."""
    '''
)


def edit(old, new, text=SCOPED):
    assert text.count(old) == 1, old
    return text.replace(old, new)


def test_an_unchanged_test_s_claim_is_not_named_and_a_changed_one_s_is(
    tmp_path, capsys, monkeypatch
):
    """The acceptance's case: `test_unchanged` keeps its 9 heats out of the
    check, `test_changed` names its 3 heats (D123)."""
    path = tmp_path / "test_scoped.py"
    path.write_text(
        edit("    assert page\n\n\ndef test_calls", "    assert 1\n\n\ndef test_calls")
    )
    monkeypatch.setattr(dc, "base_text", lambda f, rev: SCOPED)
    monkeypatch.setattr(dc, "deleted_tests", lambda near, rev: [])
    body = tmp_path / "body.md"
    body.write_text("<details>\n</details>\n")
    assert dc.main(["--check", str(body), "--base", "origin/master", str(path)]) == 1
    out = capsys.readouterr().out
    assert "test_changed: 3 is in no <details> block" in out
    assert "test_unchanged" not in out
    assert out.count("\n") == 1
    # Without --base every claim counts, as before (#374's out of scope).
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert "test_unchanged: 9 is" in capsys.readouterr().out
    body.write_text("<details>\n| test_changed | 3 heats |\n</details>\n")
    assert dc.main(["--check", str(body), "--base", "origin/master", str(path)]) == 0


def counted(head, base=SCOPED):
    return dc.counted(base, head)


def test_a_test_that_reads_a_changed_helper_counts():
    assert counted(edit("return LANES", "return LANES + 1")) == (
        {"race", "test_calls_helper"},
        "",
    )


def test_a_comment_alone_counts_its_scope():
    """AST sees no comment: the scope's text decides (#374)."""
    head = edit(
        "    assert page\n\n\ndef test_changed",
        "    assert page  # 2\n\n\ndef test_changed",
    )
    assert counted(head) == ({"test_unchanged"}, "")


def test_a_class_counts_for_its_method():
    assert counted(edit('"""7 rows."""', '"""8 rows."""')) == ({"TestBoard"}, "")


def test_nothing_changed_counts_nothing():
    assert counted(SCOPED) == (set(), "")


def test_a_module_comment_or_docstring_counts_the_module():
    assert counted(edit("# 4 lanes.", "# 6 lanes.")) == ({"module"}, "")
    assert counted(edit('"""Two lanes."""', '"""Three lanes."""')) == ({"module"}, "")


def test_a_module_statement_counts_the_module_and_its_readers():
    """Any module-level statement, an import too (the lead's choice on
    #374, beyond D123's words); a constant reaches the tests that read it,
    through a helper too, and no helper's claims."""
    assert counted(edit("import pytest\n", "import pytest\nimport time\n")) == (
        {"module"},
        "",
    )
    assert counted(edit("LANES = 4", "LANES = 5")) == (
        {"module", "test_calls_helper"},
        "",
    )


def test_a_new_file_or_one_that_cannot_be_resolved_counts_every_claim(
    tmp_path, capsys, monkeypatch
):
    assert counted(SCOPED, base=None) == (None, "new since the merge base")
    head = edit(
        "    assert page\n\n\ndef test_changed",
        "    global LANES\n\n\ndef test_changed",
    )
    names, why = counted(head)
    assert names is None and "`global`" in why
    path = tmp_path / "test_scoped.py"
    path.write_text(head)
    monkeypatch.setattr(dc, "base_text", lambda f, rev: SCOPED)
    monkeypatch.setattr(dc, "deleted_tests", lambda near, rev: [])
    body = tmp_path / "body.md"
    body.write_text("<details>\n</details>\n")
    assert dc.main(["--check", str(body), "--base", "HEAD", str(path)]) == 1
    out = capsys.readouterr().out
    assert f"{path}: every claim counts (D123): " in out
    assert "test_unchanged: 9 is" in out


def test_base_needs_check(tmp_path, capsys):
    path = tmp_path / "test_scoped.py"
    path.write_text(SCOPED)
    assert dc.main([str(path)]) == 0
    assert "| test_scoped.py:20 `test_changed` | 3 | ? |" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        dc.main(["--base", "HEAD", str(path)])


def test_the_base_is_the_merge_base_s_text(tmp_path, monkeypatch):
    """From the merge base with REV, not REV itself, as affected-tests
    diffs; None for a file new since then."""
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)
    for name, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }.items():
        monkeypatch.setenv(name, value)

    def git(*argv):
        subprocess.run(["git", *argv], cwd=tmp_path, check=True, capture_output=True)

    sub = tmp_path / "tests"
    sub.mkdir()
    path = sub / "test_a.py"
    path.write_text("A = 1\n")
    git("init", "-q", "-b", "master")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "side")
    path.write_text("A = 2\n")
    git("commit", "-q", "-am", "side")
    git("checkout", "-q", "master")
    path.write_text("A = 3\n")
    git("commit", "-q", "-am", "master moves on")
    git("checkout", "-q", "side")
    path.write_text("A = 4\n")
    assert dc.base_text(path, "master") == "A = 1\n"
    assert dc.base_text(sub / "test_new.py", "master") is None


def test_a_change_pytest_applies_to_every_test_counts_every_claim():
    """A changed pytestmark or autouse fixture, or a statement that binds
    nothing, has no reader by name (the lead's choice on #379)."""
    for old, new in (
        ("import pytest\n", "import pytest\npytestmark = pytest.mark.slow\n"),
        ("    return LANES\n", "    return LANES\n\n\nLANES.bit_length()\n"),
    ):
        names, why = counted(edit(old, new))
        assert names is None and why, (old, why)


def test_a_decorated_test_or_a_test_case_that_reads_a_change_counts():
    """D101 holds both in the frame: the #379 review's shapes A and S."""
    body = (
        "\n\n@override_settings(X=1)\ndef test_decorated(page):\n    race(page)\n"
        "\n\nclass Reach(unittest.TestCase):\n"
        "    def test_a(self):\n        race(self)\n"
    )
    base = SCOPED + body
    head = edit("return LANES", "return LANES + 1", base)
    assert counted(head, base) == (
        {"race", "test_calls_helper", "test_decorated", "Reach"},
        "",
    )


# The sentence of #404's first body that review sent back (#65 retro), and
# the lines of its last body that say what the lead does after merge.
ROUND_0_LEAD = (
    "`racing/tests/test_did_not_finish.py::test_places_with_zero_times_are_"
    "recorded_as_php_records_them` asserted the old behaviour. It moved to"
    " `racing/tests/test_all_zero_lanes.py::test_zero_times_with_places_on_the_"
    "used_lanes_stop_racing` with the new expectation, which the lead confirmed"
    " for #65."
)
AFTER_MERGE = (
    "Both Scope lines are drafted for the lead to post on #62 after merge, and"
    " no new docs file is added.\n"
    "- The #20 quirk-table comment, which the lead posts after merge.\n"
    "- The release-note lines on #62, which the lead posts after merge.\n"
)


def test_a_ruling_credited_to_the_lead_is_named(tmp_path, capsys):
    """#404's round-0 sentence is refused by its line; what the lead does
    next, a ruling that names its decision, a quote and pasted output pass."""
    path = tmp_path / "test_x.py"
    path.write_text("def test_x():\n    assert True\n")
    body = tmp_path / "body.md"
    body.write_text(f"Intro.\n{ROUND_0_LEAD}\n{AFTER_MERGE}")
    assert dc.main(["--check", str(body), str(path)]) == 1
    out = capsys.readouterr().out
    assert out.startswith(f"{body}:2: credits a ruling to the lead")
    assert "'It moved to `racing/tests/test_all_zero_lanes.py::" in out
    assert out.count("\n") == 1
    fine = (
        ROUND_0_LEAD.replace("for #65", "for #65 under D42"),
        "#272's docstring said \"the lead's choice B\" until review.",
        "```\nwhich the lead confirmed\n```",
        "Per the `lead` column, it is filled.",
    )
    exits = []
    for text in fine:
        body.write_text(f"{text}\n{AFTER_MERGE}")
        exits.append(dc.main(["--check", str(body), str(path)]))
    assert exits == [0, 0, 0, 0]
    cited = (
        "This is the lead's choice C.",
        "The lead approved this reading.",
        "It stays, per the lead.",
        "Cost (accepted by the lead).",
    )
    assert [dc.lead_cites(text) for text in cited] == [[(1, text)] for text in cited]
    assert len(cited) == 4


# Sentences from merged and open bodies (#65 retro's sweep) that credit the
# lead with no ruling, or cite where it is recorded.
NOT_RULINGS = (
    "CI was green at b46d7af6, as the lead reported.",
    "The lead decides on them, since this PR changes neither list:",
    "Whether testing/ belongs in those paths is the lead's call.",
    "The rest is left to the lead.",
    "This was the lead's call on the third Scope box, and the docstring cites #378.",
    "- a changed module-level statement (the lead's choice on #379).",
    "That is the lead's ruling on #273.",
    "This is the lead's choice C (#346), plus eight verbs C missed.",
    "Per the lead (Q17), it is read as the pages of #41 to #50.",
    "The lead's answer is in ADR-10.",
    "Cite the lead's answer by the decision it rests on (`D10`, `#272`).",
    "A value unconfirmed by the lead.",
)
RULINGS = (
    "Cost (accepted by the lead).",
    "The lead's answer was to fix both rows here.",
    "`conftest.py` is unchanged here, per the lead.",
    "The lead said to keep it, and the lead OK'd the rest.",
    "A third check was dropped at the lead's call.",
    "Left unfixed, as the lead ruled for this PR.",
    "It was settled for #65 by the lead's choice, as the lead confirmed for #65.",
)


def test_a_sentence_that_credits_the_lead_no_ruling_is_not_named():
    """A past ruling is named until the sentence cites a decision, a
    question, an ADR or a ticket; `for #65` names what it was about, not
    where it is recorded. What the lead decides next, an open call, a report
    and a quoted or backticked phrase pass. A citation clears its own
    sentence only."""
    assert [dc.lead_cites(text) for text in NOT_RULINGS] == [[]] * len(NOT_RULINGS)
    assert [bool(dc.lead_cites(text)) for text in RULINGS] == [True] * len(RULINGS)
    assert dc.lead_cites("The lead chose B. Ticket #5 says so.") == [
        (1, "The lead chose B.")
    ]
    assert dc.lead_cites("The lead chose B for #5.") != []
    assert dc.lead_cites("The lead chose B (#5).") == []
    assert dc.lead_cites("The lead's choice on D10.") == []
    assert dc.lead_cites("The lead's choice on Q2.") == []


GONE_BASE = textwrap.dedent(
    """\
    import os


    def helper():
        return os.sep


    class Helper:
        pass


    def test_old():
        assert 1


    def test_moved():
        assert 2


    class TestBoard:
        size = 4

        def setup_method(self):
            pass

        def test_row(self):
            assert 3

        def test_kept(self):
            assert 4


    class TestGone:
        def test_a(self):
            assert 5
    """
)
GONE_HEAD = textwrap.dedent(
    """\
    class TestBoard:
        def test_kept(self):
            assert 4
    """
)


def test_a_test_gone_since_the_merge_base_is_named(tmp_path, capsys, monkeypatch):
    """#404's deleted `test_adjuster.py` test: each test the head no longer
    defines is named until the body names it; a test moved to another file
    given counts as kept, and a class that is gone is named once."""
    path = tmp_path / "test_gone.py"
    path.write_text(GONE_HEAD)
    other = tmp_path / "test_other.py"
    other.write_text("def test_moved():\n    assert 2\n")
    monkeypatch.setattr(
        dc, "base_text", lambda f, rev: GONE_BASE if f == path else None
    )
    monkeypatch.setattr(dc, "deleted_tests", lambda near, rev: [])
    body = tmp_path / "body.md"
    body.write_text("Nothing here.\n")
    argv = ["--check", str(body), "--base", "origin/master", str(path), str(other)]
    assert dc.main(argv) == 1
    out = capsys.readouterr().out
    gone = [
        line.split(": ")[1].split(" is gone")[0]
        for line in out.splitlines()
        if " is gone since the merge base" in line
    ]
    assert gone == ["TestBoard::test_row", "TestGone", "test_old"]
    # Without --base nothing is compared with the merge base.
    assert dc.main(["--check", str(body), str(path), str(other)]) == 0
    capsys.readouterr()
    body.write_text(
        "- test_old: its assert is in test_kept.\n"
        "```\ntest_row\n```\n"
        "TestGone and test_row moved too.\n"
    )
    assert dc.main(argv) == 0
    # A name only in fenced code names nothing.
    body.write_text("- test_old, TestGone.\n```\ntest_row\n```\n")
    assert dc.main(argv) == 1
    assert "TestBoard::test_row is gone" in capsys.readouterr().out


NESTED = textwrap.dedent(
    """\
    import sys

    if sys.platform == "linux":

        def test_here():
            assert 1

    else:

        class TestElse:
            def test_there(self):
                assert 2


    class TestOuter:
        class TestInner:
            def test_deep(self):
                assert 3

            class TestInnermost:
                def test_deeper(self):
                    assert 4

        class Helper:
            def test_not_collected(self):
                assert 5

        def test_outer(self):
            assert 6

        @pytest.mark.parametrize("n", [1, 2, 3])
        def test_cases(self, n):
            assert n
    """
)


def test_the_tests_in_a_nested_class_or_under_an_if_are_listed():
    """A parametrized test is one name, however many cases it has."""
    assert dc.test_names(NESTED) == {
        "test_here",
        "TestElse",
        "TestElse::test_there",
        "TestOuter",
        "TestOuter::TestInner",
        "TestOuter::TestInner::test_deep",
        "TestOuter::TestInner::TestInnermost",
        "TestOuter::TestInner::TestInnermost::test_deeper",
        "TestOuter::test_outer",
        "TestOuter::test_cases",
    }
    # A nested test or class that is gone is named, and a class gone with
    # its methods once; a nested method moved to another class is kept.
    head = "import sys\n\n\nclass TestOuter:\n    class TestInner:\n        pass\n"
    lost = dc.test_names(NESTED) - dc.test_names(head)
    assert "TestOuter::TestInner::TestInnermost::test_deeper" in lost
    assert "test_here" in lost


def test_a_nested_test_gone_since_the_merge_base_is_named_once_per_class(
    tmp_path, capsys, monkeypatch
):
    path = tmp_path / "test_nested.py"
    path.write_text(
        "import sys\n\n\nclass TestOuter:\n    class TestInner:\n        pass\n"
    )
    monkeypatch.setattr(dc, "base_text", lambda f, rev: NESTED)
    monkeypatch.setattr(dc, "deleted_tests", lambda near, rev: [])
    body = tmp_path / "body.md"
    body.write_text("Nothing here.\n")
    argv = ["--check", str(body), "--base", "origin/master", str(path)]
    assert dc.main(argv) == 1
    gone = [
        line.split(": ")[1].split(" is gone")[0]
        for line in capsys.readouterr().out.splitlines()
        if " is gone since the merge base" in line
    ]
    assert gone == [
        "TestElse",
        "TestOuter::TestInner::TestInnermost",
        "TestOuter::TestInner::test_deep",
        "TestOuter::test_cases",
        "TestOuter::test_outer",
        "test_here",
    ]


def repo(tmp_path, monkeypatch):
    """A git repository in tmp_path with no `GIT_*` of the caller's, and a
    function that runs git in it."""
    for name in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(name)
    for name, value in {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }.items():
        monkeypatch.setenv(name, value)

    def git(*argv):
        subprocess.run(["git", *argv], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "master")
    return git


def test_a_deleted_test_file_is_read_from_the_merge_base(tmp_path, capsys, monkeypatch):
    """A deleted or moved test file is named test by test, whether or not it
    is on the command line; a test moved to a file given counts as kept, and
    a path that is nowhere is refused. A deleted file that is no test file is
    left out."""
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "a hook's repository"))
    monkeypatch.setenv("HOME", str(tmp_path / "no home"))
    git = repo(tmp_path, monkeypatch)
    assert os.environ["HOME"] == str(tmp_path / "no home")
    assert "GIT_DIR" not in os.environ
    old = tmp_path / "tests" / "deep" / "test_old.py"
    old.parent.mkdir(parents=True)
    old.write_text(
        "def test_a():\n    assert 1\n\n\ndef test_b():\n    assert 2\n\n\n"
        "class TestC:\n    def test_c(self):\n        assert 3\n"
    )
    (tmp_path / "tests" / "deep" / "helper.py").write_text("def test_h():\n    pass\n")
    lane = tmp_path / "tests" / "deep" / "lane_test.py"
    lane.write_text("def test_z():\n    assert 5\n")
    keep = tmp_path / "tests" / "test_keep.py"
    keep.write_text("def test_k():\n    assert 4\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    author = subprocess.run(
        ["git", "log", "-1", "--format=%an"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert author.stdout.strip() == "t"
    git("checkout", "-q", "-b", "side")
    git("rm", "-q", "-r", "tests/deep")
    new = tmp_path / "tests" / "test_new.py"
    new.write_text("def test_b():\n    assert 2\n")
    git("add", ".")
    git("commit", "-q", "-m", "side")
    assert dc.base_text(old, "master").startswith("def test_a")
    assert dc.deleted_tests(new, "master") == [lane, old]
    body = tmp_path / "body.md"
    body.write_text("Nothing here.\n")
    for files in ([new], [new, old]):
        argv = ["--check", str(body), "--base", "master", *map(str, files)]
        assert dc.main(argv) == 1
        out = capsys.readouterr().out
        assert sorted(
            line.split(" is gone")[0].split(": ")[1]
            for line in out.splitlines()
            if " is gone" in line
        ) == ["TestC", "test_a", "test_z"]
    body.write_text("test_a, test_z and TestC are covered by test_b.\n")
    assert dc.main(["--check", str(body), "--base", "master", str(new)]) == 0
    git("mv", "tests/test_keep.py", "tests/test_kept.py")
    git("commit", "-q", "-m", "a pure rename")
    assert dc.deleted_tests(new, "master") == [lane, old, keep]
    missing = tmp_path / "tests" / "test_never.py"
    for argv in (
        ["--check", str(body), str(old)],
        ["--check", str(body), "--base", "master", str(missing)],
    ):
        with pytest.raises(SystemExit):
            dc.main(argv)
    assert "no such file" in capsys.readouterr().err


def test_check_takes_no_test_file(tmp_path, capsys, monkeypatch):
    """A diff that changes no test file still gets its body checked: #537's
    implementer passed an empty .py for want of a file (#537 retro). With
    `--base`, a test file the diff deletes is read from the directory the
    tool runs in. With no `--check`, a file is still needed."""
    body = tmp_path / "body.md"
    body.write_text("The new rule, which the lead confirmed.\n")
    assert dc.main(["--check", str(body)]) == 1
    assert "credits a ruling to the lead" in capsys.readouterr().out
    body.write_text("Nothing here.\n")
    assert dc.main(["--check", str(body)]) == 0
    git = repo(tmp_path, monkeypatch)
    gone = tmp_path / "tests" / "test_gone.py"
    gone.parent.mkdir()
    gone.write_text("def test_g():\n    assert 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "side")
    git("rm", "-q", "tests/test_gone.py")
    git("commit", "-q", "-m", "side")
    monkeypatch.chdir(tmp_path)
    assert dc.main(["--check", str(body), "--base", "master"]) == 1
    assert "test_g is gone since the merge base" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        dc.main([])
    assert "name a test file, or give --check" in capsys.readouterr().err
