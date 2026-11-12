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
        "390x844",
        '"Lions, Round 1, Heat 14"',
        "15vw",
        "2 lines",
        '"Heat 100"',
        "58.3 px",
    ]
    body = tmp_path / "body.md"
    body.write_text("<details>\n" + "\n".join(rows) + "\n</details>\n")
    assert dc.main(["--check", str(body), str(path)]) == 0

    body.write_text(
        "58.3 px outside the block\n<details>\n"
        + "\n".join(r for r in rows if r != "58.3 px")
        + "\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1
    assert capsys.readouterr().out.strip() == (
        f"{path}:12: test_labels: 58.3 px is in no <details> block"
    )
