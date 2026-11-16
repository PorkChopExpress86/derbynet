"""tools/docstring_claims.py lists each number and quoted label in a test
file's prose, and `--check` refuses a PR body whose `<details>` blocks
leave one out (#283 retro)."""

import textwrap

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


# `pause_clock_at` as #348's second head (6d6ac602) had it, in part.
PAUSE_CLOCK_AT = '''\
def pause_clock_at(page, at):
    """Pause the clock. A timer a loaded page queued between the install
    and the pause fired once in the pause's jump (the #359 review's
    probe)."""
    page.clock.pause_at(at)


def stop(page):
    """It probes the page, and `probe` in backticks names code: no cite."""
    # Probes run here, as a verb.
    page.clock.pause_at(0)
'''


def test_a_cited_probe_is_a_claim(tmp_path, capsys):
    """#348's docstring cited the review's probe, which no test held, and
    cutting it cost a third head and its load runs (#348 retro)."""
    path = tmp_path / "helpers.py"
    path.write_text(PAUSE_CLOCK_AT)
    found = [(c.scope, c.text, c.line) for c, _ in dc.claims(path)]
    assert found == [("pause_clock_at", "probe", 4)]
    body = tmp_path / "body.md"
    body.write_text("<details>\n| `pause_clock_at` | 1 | x |\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert (
        "pause_clock_at: probe is in no <details> block line" in capsys.readouterr().out
    )
    body.write_text(
        "<details>\n| `pause_clock_at` | probe | test_a_loaded_page_s_timer |\n"
        "</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 0
