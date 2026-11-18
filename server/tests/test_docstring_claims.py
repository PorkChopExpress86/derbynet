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
    inside a fence leaves it open, as in CommonMark. The inner mark comes
    twice, so a fence it closed opens again and the block still ends."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        f"<details>\n{fence}\n{inner}\n- `test_waits`: 3 s\n{inner}\n{fence}\n"
        "</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1


def test_a_fence_that_quotes_a_details_tag_opens_no_block(tmp_path):
    """retro-320's body quoted `<details>` in a diff fence ahead of its real
    blocks, so the text between read as a block."""
    path = tmp_path / "test_sample.py"
    path.write_text('def test_waits():\n    """A 3 s bound."""\n')
    body = tmp_path / "body.md"
    body.write_text(
        "```diff\n- a claim on a <details> line\n```\n"
        "- `test_waits`: 3 s\n<details>\n| `test_lanes` | 2 |\n</details>\n"
    )
    assert dc.main(["--check", str(body), str(path)]) == 1


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
