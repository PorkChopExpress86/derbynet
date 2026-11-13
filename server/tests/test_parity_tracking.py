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
    label, create = writes(gh)
    assert label[:4] == ["gh", "label", "create", "parity-gate"]
    assert create[:3] == ["gh", "issue", "create"]
    assert create[create.index("--label") + 1] == "parity-gate"
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


def test_a_gate_job_that_wrote_nothing_is_a_failing_entry(tmp_path):
    # The postgres gate job died before `--failing`: the upload found no
    # file, so the track job's path names nothing.
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, NOV_05))
    legs = pt.read_legs(
        [
            f"sqlite={saved(tmp_path, 'sqlite', NOV_05)}",
            f"postgres={tmp_path / 'failing-postgres.json'}",
        ]
    )

    did = pt.track(gh, legs, "https://example/run/7", "a" * 40, tmp_path)

    assert did == "updated"
    comment = (tmp_path / "parity-tracking-comment.md").read_text()
    assert "- Now fails: `(the leg did not report)` (postgres)" in comment
    body = (tmp_path / "parity-tracking-body.md").read_text()
    assert "| `(the leg did not report)` | passes | did not report |" in body


def test_the_issue_is_found_by_its_label_not_by_a_search(tmp_path):
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, NOV_05))

    did = pt.track(
        gh, legs(tmp_path, NOV_05), "https://example/run/8", "b" * 40, tmp_path
    )

    assert did == "unchanged"
    [listing] = [call for call in gh.calls if call[1:3] == ["issue", "list"]]
    assert "--search" not in listing
    assert listing[listing.index("--label") + 1] == "parity-gate"


# The jobs of nightly run 43254181268 (2026-11-12), as `gh run view --json
# jobs` gives them, cut to the fields read: both Django legs failed after
# uploading their steps, so both gates passed and the issue said no step
# failed (#206 retro).
GOLDEN = "Replay the golden fixtures against Django"
NOV_12_JOBS = {
    "jobs": [
        {
            "name": f"leg (django, {database})",
            "conclusion": "failure",
            "status": "completed",
            "steps": [
                {"name": "Run the suite, step by step", "conclusion": "success"},
                {"name": GOLDEN, "conclusion": "failure"},
            ],
        }
        for database in ("postgres", "sqlite")
    ]
    + [
        {"name": name, "conclusion": "success", "status": "completed", "steps": []}
        for name in (
            "leg (php)",
            "gate (django, sqlite)",
            "gate (django, postgres)",
            "track the failing steps",
        )
    ]
}
GREEN = {"short": [], "steps": []}


def test_a_leg_job_that_failed_after_the_gate_passed_changes_the_list(tmp_path):
    gh = pt.Gh(dry_run=True, issue_body=issue_body(tmp_path, GREEN))
    jobs = saved(tmp_path, "jobs", NOV_12_JOBS)

    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")
    did = pt.track(gh, one, "https://example/run/9", "c" * 40, tmp_path)

    assert jobs and did == "updated"
    label = f"(the leg's job failed: {GOLDEN})"
    assert pt.failing_list(one) == {label: ["postgres", "sqlite"]}
    body = (tmp_path / "parity-tracking-body.md").read_text()
    assert (
        f"| `{label}` | job failed | job failed | no WAITING entry |"
        " leg (django, sqlite); leg (django, postgres) |"
    ) in body


def test_a_second_failed_step_behind_the_first_changes_the_list(tmp_path):
    """Both steps after the upload run unless the run is cancelled: the
    derby-timer race starting to fail while the replay still fails is news."""
    saved(tmp_path, "jobs", NOV_12_JOBS)
    night_1 = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")
    gh = pt.Gh(dry_run=True, issue_body=pt.render(night_1, "https://x/1", "a" * 40))
    race = "Race a round with derby-timer.jar against Django"
    sqlite = json.loads(json.dumps(NOV_12_JOBS))
    sqlite["jobs"][1]["steps"].append({"name": race, "conclusion": "failure"})
    saved(tmp_path, "jobs", sqlite)

    night_2 = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    assert pt.track(gh, night_2, "https://x/2", "b" * 40, tmp_path) == "updated"
    comment = (tmp_path / "parity-tracking-comment.md").read_text()
    assert f"- Now fails: `(the leg's job failed: {race})` (sqlite)" in comment


def test_a_failed_php_leg_counts_on_every_database(tmp_path):
    php = {"name": "leg (php)", "conclusion": "cancelled", "status": "completed"}
    others = [
        {**job, "conclusion": "success", "steps": []}
        for job in NOV_12_JOBS["jobs"]
        if job["name"] != "leg (php)"
    ]
    saved(tmp_path, "jobs", {"jobs": [php, *others]})

    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    label = "(the leg's job failed: cancelled)"
    assert pt.failing_list(one) == {label: ["postgres", "sqlite"]}
    assert one[0].job == ((label, "leg (php)"),)


def test_a_failed_gate_job_is_left_to_the_gate_file(tmp_path):
    gate = {"name": "gate (django, sqlite)", "conclusion": "failure", "steps": []}
    legs_green = [
        {"name": name, "conclusion": "success", "steps": []}
        for name in ("leg (php)", "leg (django, sqlite)", "leg (django, postgres)")
    ]
    saved(tmp_path, "jobs", {"jobs": [gate, *legs_green]})

    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    assert pt.failing_list(one) == {}


def test_a_jobs_file_it_cannot_read_is_a_failing_entry(tmp_path):
    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    assert pt.failing_list(one) == {
        "(the leg jobs could not be read)": ["postgres", "sqlite"]
    }


def test_main_reads_the_jobs_file(tmp_path, capsys):
    saved(tmp_path, "jobs", NOV_12_JOBS)
    body = tmp_path / "issue.md"
    body.write_text(issue_body(tmp_path, GREEN))

    pt.main(
        [
            "--dry-run",
            "--issue-body",
            str(body),
            "--workdir",
            str(tmp_path),
            "--jobs",
            str(tmp_path / "jobs.json"),
            f"sqlite={saved(tmp_path, 'sqlite', GREEN)}",
            f"postgres={saved(tmp_path, 'postgres', GREEN)}",
        ]
    )

    out = capsys.readouterr().out
    assert f'"(the leg\'s job failed: {GOLDEN})": ["postgres", "sqlite"]' in out
    assert "tracking issue updated" in out


def test_a_jobs_file_with_no_jobs_is_unread(tmp_path):
    """An empty list must not clear #225's job entries in silence."""
    saved(tmp_path, "jobs", {"jobs": []})

    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    assert pt.failing_list(one) == {
        "(the leg jobs could not be read)": ["postgres", "sqlite"]
    }


def test_a_renamed_leg_job_is_unread_on_its_database(tmp_path):
    renamed = json.loads(json.dumps(NOV_12_JOBS))
    renamed["jobs"][1]["name"] = "leg (django-sqlite)"
    saved(tmp_path, "jobs", renamed)

    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    assert pt.failing_list(one) == {
        f"(the leg's job failed: {GOLDEN})": ["postgres"],
        "(the leg jobs could not be read)": ["sqlite"],
    }
    assert (
        "(the leg jobs could not be read)",
        "jobs.json has no leg (django, sqlite)",
    ) in one[0].job + one[1].job


def test_a_jobs_file_with_no_php_leg_is_unread_everywhere(tmp_path):
    no_php = {"jobs": [j for j in NOV_12_JOBS["jobs"] if j["name"] != "leg (php)"]}
    saved(tmp_path, "jobs", no_php)

    one = pt.read_jobs(legs(tmp_path, GREEN), tmp_path / "jobs.json")

    assert pt.failing_list(one)["(the leg jobs could not be read)"] == [
        "postgres",
        "sqlite",
    ]
