"""The parity gate's tracking issue (`tools/parity_tracking.py`, #218), in dry
runs against two saved gate summaries: the 2026-11-05 nightly's failing steps
and the 2026-11-06 nightly's, as `parity_gate.py --failing` writes them."""

import json

from tools import parity_tracking as pt

NOV_05 = {
    "short": [],
    "steps": [
        {
            "label": "test-basic-racing.sh",
            "differences": 16,
            "waiting": "#49 (history.php)",
            "problems": ["16 differences from PHP"],
        },
        {
            "label": "test-new-rounds.sh",
            "differences": 21,
            "waiting": None,
            "problems": ["21 differences from PHP"],
        },
        {
            "label": "test-scenes.sh",
            "differences": 10,
            "waiting": None,
            "problems": ["10 differences from PHP"],
        },
    ],
}
NOV_06 = {
    "short": [],
    "steps": [
        *NOV_05["steps"],
        {
            "label": "test-permissions.sh",
            "differences": 9,
            "waiting": None,
            "problems": ["9 differences from PHP"],
        },
    ],
}


def saved(tmp_path, name: str, summary: dict) -> str:
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(summary))
    return str(path)


def legs(tmp_path, summary: dict) -> list[pt.Leg]:
    return pt.read_legs(
        [
            f"sqlite={saved(tmp_path, 'sqlite', summary)}",
            f"postgres={saved(tmp_path, 'postgres', summary)}",
        ]
    )


def issue_body(tmp_path, summary: dict) -> str:
    return pt.render(legs(tmp_path, summary), "https://example/run/1", "a" * 40)


def writes(gh: pt.Gh) -> list[list[str]]:
    return [call for call in gh.calls if call[2] in ("create", "edit", "comment")]


def test_it_opens_the_issue_when_there_is_none(tmp_path):
    gh = pt.Gh(dry_run=True, issue_body=None)

    did = pt.track(
        gh, legs(tmp_path, NOV_05), "https://example/run/1", "a" * 40, tmp_path
    )

    assert did == "opened"
    [create] = writes(gh)
    assert create[:3] == ["gh", "issue", "create"]
    body = (tmp_path / "parity-tracking-body.md").read_text()
    assert (
        "| `test-scenes.sh` | 10 differences | 10 differences | no WAITING entry |"
        in body
    )
    assert (
        "| `test-basic-racing.sh` | 16 differences | 16 differences"
        " | #49 (history.php) |" in body
    )


def test_it_updates_the_issue_when_the_failing_list_changes(tmp_path):
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, NOV_05))

    did = pt.track(
        gh, legs(tmp_path, NOV_06), "https://example/run/2", "b" * 40, tmp_path
    )

    assert did == "updated"
    assert [call[:3] for call in writes(gh)] == [
        ["gh", "issue", "edit"],
        ["gh", "issue", "comment"],
    ]
    comment = (tmp_path / "parity-tracking-comment.md").read_text()
    assert "- Now fails: `test-permissions.sh` (postgres, sqlite)" in comment
    assert "test-permissions.sh" in (tmp_path / "parity-tracking-body.md").read_text()


def test_it_names_a_step_that_stopped_failing(tmp_path):
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, NOV_06))

    pt.track(gh, legs(tmp_path, NOV_05), "https://example/run/3", "c" * 40, tmp_path)

    comment = (tmp_path / "parity-tracking-comment.md").read_text()
    assert "- No longer fails: `test-permissions.sh`" in comment


def test_it_leaves_the_issue_alone_when_the_list_is_the_same(tmp_path):
    # Tonight's counts differ, but the same steps fail on the same databases.
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, NOV_05))
    recounted = json.loads(json.dumps(NOV_05))
    recounted["steps"][0]["differences"] = 49

    did = pt.track(
        gh, legs(tmp_path, recounted), "https://example/run/4", "d" * 40, tmp_path
    )

    assert did == "unchanged"
    assert writes(gh) == []


def test_a_step_failing_on_one_more_database_changes_the_list(tmp_path):
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, NOV_05))
    one = pt.read_legs(
        [
            f"sqlite={saved(tmp_path, 'one-sqlite', NOV_06)}",
            f"postgres={saved(tmp_path, 'one-postgres', NOV_05)}",
        ]
    )

    assert pt.track(gh, one, "https://example/run/5", "e" * 40, tmp_path) == "updated"
    comment = (tmp_path / "parity-tracking-comment.md").read_text()
    assert "- Now fails: `test-permissions.sh` (sqlite)" in comment


def test_a_leg_cut_short_is_a_failing_entry(tmp_path):
    short = {"short": ["/tmp/leg has no complete file"], "steps": []}

    listed = pt.failing_list(legs(tmp_path, short))

    assert listed == {"(the leg was cut short)": ["postgres", "sqlite"]}


def test_every_gh_call_names_the_fork(tmp_path):
    for body, summary in ((None, NOV_05), (issue_body(tmp_path, NOV_05), NOV_06)):
        gh = pt.Gh(dry_run=True, issue_body=body)
        pt.track(
            gh, legs(tmp_path, summary), "https://example/run/6", "f" * 40, tmp_path
        )

        assert gh.calls
        for call in gh.calls:
            assert call[0] == "gh"
            at = call.index("--repo")
            assert call[at + 1] == "PorkChopExpress86/derbynet"
            assert "jeffpiazza" not in " ".join(call)


def test_the_gate_writes_the_file_the_tracker_reads(tmp_path):
    from tools.parity_gate import StepResult, Waiting, failing

    waiting = StepResult(
        "test-each-role.sh", None, None, problems=["fails"], waiting=Waiting("#41")
    )
    passing = StepResult("ez_setup", None, None)

    written = failing([waiting, passing], [])

    path = saved(tmp_path, "gate", written)
    [leg] = pt.read_legs([f"sqlite={path}"])
    assert leg.steps == [
        {
            "label": "test-each-role.sh",
            "differences": 0,
            "waiting": "#41",
            "problems": ["fails"],
        }
    ]
