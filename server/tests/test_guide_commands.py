"""Every command a guide in `docs/` tells the user to run has a row in
`server/docs/guide-commands.tsv`: a stage of `server/docker/ci/guide-commands.sh`
that runs it as written, a live workflow step that holds it, or the reason no
check can run it (#414). A command with no row fails, and so does a row
whose command left the guide; the error names the guide, the line and the
command.

#58, #59 and #61 each shipped a guide command that failed as written, and each
time a reviewer found it (AGENT-BRIEF.md's Evidence rules).
"""

import itertools
import os
import re
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.workflow_steps import WORKFLOWS, steps
from tools import guide_commands as gc
from ui.docs.build import fenced_blocks

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "server"
SCRIPT = SERVER / "docker" / "ci" / "guide-commands.sh"
DOCS = ROOT / "docs"

ARM = re.compile(r"^        ([a-z][a-z-]*)\) (stage_\w+) ;;$", re.M)
CALL = re.compile(
    r"^(?:\S+=\S+ +)*server/docker/ci/guide-commands\.sh((?: [a-z][a-z-]*)+) *$"
)
STAGES_HEADER = re.compile(r"^# Stages:\n((?:#[^\n]*\n)*?)#\n", re.M)
STAGE_NAME = re.compile(r"^#   ([a-z][a-z-]*)(?: |$)", re.M)


def fence(language: str, *lines: str, mark: str = "```") -> str:
    return "\n".join([f"{mark}{language}", *lines, mark, ""])


def found(text: str, guide: str = "g.md") -> list[tuple[int, str]]:
    return [(c.line, c.text) for c in gc.commands(guide, text)]


# The real guides against the real table.


def test_every_guide_command_has_a_row_and_every_row_a_command():
    entries = gc.parse_registry(gc.REGISTRY.read_text())
    assert gc.problems(gc.guide_commands(), entries) == []


def test_a_changed_guide_command_fails_by_guide_line_and_command():
    """The acceptance run: change `docker compose up -d` in install-docker.md,
    not the table."""
    entries = gc.parse_registry(gc.REGISTRY.read_text())
    guide = (DOCS / "install-docker.md").read_text()
    old = "\ndocker compose up -d\n"
    changed = guide.replace(old, "\ndocker compose up -d --build\n", 1)
    line = guide[: guide.index(old)].count("\n") + 2
    commands = [
        c for c in gc.guide_commands() if c.guide != "install-docker.md"
    ] + gc.commands("install-docker.md", changed)
    errors = gc.problems(commands, entries)
    assert len(errors) == 2, errors
    assert errors[0].startswith(
        f"docs/install-docker.md:{line}: `docker compose up -d --build` "
        "(under 'Start DerbyNet') has no entry in server/docs/guide-commands.tsv"
    )
    assert (
        "the entry for `docker compose up -d` under 'Start DerbyNet' in "
        "docs/install-docker.md" in errors[1]
    )


def shell_commands(folder: Path = DOCS) -> list[tuple[str, int, str]]:
    """(guide, line, command) of each command in the guides' blocks of a
    language bash reads."""
    return [
        (path.name, line, command)
        for path in sorted(folder.glob("*.md"))
        for language, first, code in fenced_blocks(path.read_text())
        if language in gc.LANGUAGES - {"powershell", "pwsh"}
        for line, command in gc.split(language, code, first)
    ]


def test_every_command_of_a_shell_block_parses_as_whole_commands():
    """Bash is the oracle for where a command ends: each command the guides
    yield is accepted by `bash -n`, none is a fragment of a construct."""
    commands = shell_commands()
    assert commands
    assert [c for c in commands if not gc.complete(c[2])] == []


@pytest.mark.parametrize("language", ["powershell", "pwsh"])
def test_the_bash_check_leaves_out_a_powershell_block(tmp_path, language):
    (tmp_path / "g.md").write_text(fence(language, "if ($x) {") + fence("sh", "ls"))
    assert shell_commands(tmp_path) == [("g.md", 5, "ls")]


def test_a_function_over_several_lines_is_one_command():
    """The CHDK zoom block of the photo stand guide that D139 deleted."""
    guide = "# Point it at the server\n\n" + fence(
        "sh",
        "unset prepare_camera_before_shot\n"
        'prepare_camera_before_shot() {\n  chdkptp -c -e"rec" -e"luar set_zoom(40)"\n}',
    )
    texts = [c.text for c in gc.commands("g.md", guide)]
    assert texts[0] == "unset prepare_camera_before_shot"
    assert (
        'prepare_camera_before_shot() {\n  chdkptp -c -e"rec" -e"luar set_zoom(40)"\n}'
        in texts
    )
    assert "}" not in texts


# The fences.


SHELLS = ["sh", "bash", "shell", "console", "powershell", "pwsh"]


def test_the_languages_are_the_four_the_ticket_names_and_powershell():
    assert gc.LANGUAGES == frozenset(SHELLS)


@pytest.mark.parametrize("language", SHELLS)
def test_each_shell_language_yields_its_commands(language):
    prompt = gc.PROMPT if language == "console" else ""
    assert found(fence(language, f"{prompt}ls", f"{prompt}pwd")) == [
        (2, "ls"),
        (3, "pwd"),
    ]


@pytest.mark.parametrize("language", ["", "text", "json", "nginx", "yaml", "python"])
def test_other_blocks_yield_none(language):
    assert found(fence(language, "ls")) == []


def test_a_tilde_fence_and_the_attribute_and_case_forms_are_read():
    assert found(fence("sh", "ls", mark="~~~")) == [(2, "ls")]
    assert found("```{.bash}\nls\n```\n") == [(2, "ls")]
    assert found("```SH\nls\n```\n") == [(2, "ls")]
    assert found("```{#only-an-id}\nls\n```\n") == []
    assert found("```{.json .bash}\nls\n```\n") == []


def test_the_blocks_are_the_ones_the_docs_build_takes():
    text = "intro\n\n```sh\nls\n```\n\n    ```sh\n    not a fence\n    ```\n"
    assert [(lang, first) for lang, first, _ in fenced_blocks(text)] == [("sh", 4)]
    assert found(text) == [(4, "ls")]


# What a line is.


def test_blank_lines_and_comments_are_no_commands():
    code = "# a comment\n\nls\n   \n  # another\npwd"
    assert found(fence("sh", *code.split("\n"))) == [(4, "ls"), (7, "pwd")]


def test_a_trailing_backslash_joins_the_next_line():
    text = fence("sh", "docker run -d \\", "  --name x \\", "  image", "ls")
    assert found(text) == [(2, "docker run -d --name x image"), (5, "ls")]


def test_a_console_block_has_commands_after_its_prompt_and_output_between():
    text = fence("console", "$ ls -l", "total 0", "# not a command", "$ pwd", "/home")
    assert found(text) == [(2, "ls -l"), (5, "pwd")]


@pytest.mark.parametrize("language", ["sh", "powershell"])
def test_an_indented_line_and_a_dollar_prompt_in_a_non_console_block_stay_whole(
    language,
):
    assert found(fence(language, "  $ ls")) == [(2, "$ ls")] or language == "sh"


@pytest.mark.parametrize("language", ["powershell", "pwsh"])
def test_a_powershell_line_is_not_parsed_as_bash(language):
    text = fence(language, 'docker run -v "${PWD}:/backup:ro" x', "if ($x) {")
    assert [t for _, t in found(text)] == [
        'docker run -v "${PWD}:/backup:ro" x',
        "if ($x) {",
    ]


# A construct that spans lines is a command of its own, found by asking bash.

CONSTRUCTS = {
    "plain": (["ls -l"], 1),
    "function": (["f() {", "  ls", "  pwd", "}"], 1),
    "heredoc": (["cat > .env <<ENV", "A=1", "B=2", "ENV"], 1),
    "heredoc-quoted": (["cat <<'EOF'", "$HOME", "EOF"], 1),
    "if": (["if true; then", "  ls", "else", "  pwd", "fi"], 1),
    "loop": (["for x in a b; do", "  echo $x", "done"], 1),
    "quote": (["echo 'one", "two'"], 1),
    "group": (["{", "  ls", "}"], 1),
    "substitution": (["echo $(", "  id -u", ")"], 1),
    "pair": (["ls", "pwd"], 2),
}


@pytest.mark.parametrize(
    ("first", "second"), list(itertools.product(CONSTRUCTS, repeat=2))
)
def test_every_pair_of_constructs_splits_where_bash_says(first, second):
    """A sweep of the shapes through bash's own parser: each construct is
    the number of commands it holds, in either order, with a plain command
    between."""
    lines_a, count_a = CONSTRUCTS[first]
    lines_b, count_b = CONSTRUCTS[second]
    code = [*lines_a, "echo between", *lines_b]
    commands = found(fence("sh", *code))
    assert len(commands) == count_a + 1 + count_b
    assert all(gc.complete(text) for _, text in commands)
    assert "\n".join(text for _, text in commands).count("echo between") == 1


def test_a_construct_no_line_closes_raises_naming_its_line():
    with pytest.raises(ValueError) as error:
        gc.commands("g.md", "# T\n\n" + fence("sh", "ls", "f() {", "  ls"))
    assert str(error.value).startswith("docs/g.md: line 5: `f() {` opens a construct")


def test_a_heredoc_no_line_ends_raises():
    with pytest.raises(ValueError, match="line 2: `cat <<E`"):
        gc.commands("g.md", fence("sh", "cat <<E", "body"))


def test_a_prompt_ends_a_console_construct_that_a_shell_block_would_go_on_with():
    """The next prompt starts a new command, so a quote it closes does not
    belong to the one before it."""
    lines = ["$ echo 'a", "$ b'"]
    with pytest.raises(ValueError, match="line 2"):
        gc.commands("g.md", fence("console", *lines))
    assert found(fence("sh", "echo 'a", "$ b'")) == [(2, "echo 'a\n$ b'")]
    assert found(fence("console", "$ echo 'a", "b'")) == [(2, "echo 'a\nb'")]


def test_a_backslash_ends_the_last_line_of_a_block_as_it_is():
    assert gc.split("sh", "ls \\", 1) == [(1, "ls \\")]


def test_a_console_construct_stops_at_the_next_prompt():
    with pytest.raises(ValueError, match="line 2"):
        gc.commands("g.md", fence("console", "$ echo 'a", "$ ls"))


# The headings and the turn.


def test_a_command_is_under_the_nearest_heading_outside_the_blocks():
    text = textwrap.dedent(
        """\
        # Title

        ## First

        ```sh
        # not a heading
        ls
        ```

        ### Second ###

        ```sh
        ls
        ```
        """
    )
    assert [(c.heading, c.text) for c in gc.commands("g.md", text)] == [
        ("First", "ls"),
        ("Second", "ls"),
    ]


def test_a_comment_in_an_earlier_block_is_no_heading():
    text = (
        "## First\n\n"
        + fence("sh", "# not a heading", "ls")
        + "\n"
        + fence("sh", "pwd")
    )
    assert [(c.heading, c.text) for c in gc.commands("g.md", text)] == [
        ("First", "ls"),
        ("First", "pwd"),
    ]


@pytest.mark.parametrize("newline", ["\r\n", "\r"])
def test_a_guide_with_carriage_returns_keeps_its_headings_and_lines(newline):
    """The docs build reads a CR or a CRLF as a newline, and so do the
    headings, so each command keeps its heading and its line."""
    text = ("## A\n\n" + fence("sh", "ls") + "\n## B\n\n" + fence("sh", "pwd")).replace(
        "\n", newline
    )
    assert [(c.line, c.heading, c.text) for c in gc.commands("g.md", text)] == [
        (4, "A", "ls"),
        (10, "B", "pwd"),
    ]


def test_a_command_before_every_heading_has_none():
    assert [c.heading for c in gc.commands("g.md", fence("sh", "ls"))] == [""]


def test_the_turn_counts_the_same_text_under_the_same_heading():
    text = (
        "## A\n\n" + fence("sh", "ls", "pwd", "ls") + "\n## B\n\n" + fence("sh", "ls")
    )
    assert [(c.heading, c.text, c.n) for c in gc.commands("g.md", text)] == [
        ("A", "ls", 1),
        ("A", "pwd", 1),
        ("A", "ls", 2),
        ("B", "ls", 1),
    ]


def test_an_edit_above_a_command_moves_its_line_and_not_its_key():
    before = "## A\n\n" + fence("sh", "ls")
    after = "intro\n\nmore\n\n" + before
    (one,), (two,) = gc.commands("g.md", before), gc.commands("g.md", after)
    assert one.line != two.line
    assert one.key == two.key


# The table.

ROW = "g.md\tA\t1\tls\tstage name"


def test_the_table_reads_its_rows_and_skips_comments_and_blank_lines():
    (entry,) = gc.parse_registry(f"# c\n\n{ROW}\n")
    assert (entry.line, entry.guide, entry.heading, entry.text, entry.n) == (
        3,
        "g.md",
        "A",
        "ls",
        1,
    )
    assert (entry.kind, entry.detail) == ("stage", "name")


@pytest.mark.parametrize("kind", gc.KINDS)
def test_each_kind_is_read(kind):
    (entry,) = gc.parse_registry(f"g.md\tA\t1\tls\t{kind} some words")
    assert (entry.kind, entry.detail) == (kind, "some words")


@pytest.mark.parametrize(
    "row",
    [
        "g.md\tA\t1\tls",
        "g.md\tA\t1\tls\tstage x\textra",
        "g.md\tA\t0\tls\tstage x",
        "g.md\tA\tone\tls\tstage x",
        "g.md\tA\t1\tls\tmaybe x",
        "g.md\tA\t1\tls\tstage",
        "g.md\tA\t1\tls\treason ",
    ],
)
def test_a_malformed_row_is_refused_by_its_line(row):
    with pytest.raises(ValueError, match=r"guide-commands\.tsv:2"):
        gc.parse_registry(f"# c\n{row}")


def test_the_same_command_twice_is_refused_by_both_lines():
    with pytest.raises(ValueError, match=r"tsv:3: the same command as line 1"):
        gc.parse_registry(f"{ROW}\n# c\n{ROW}")


def test_a_second_turn_is_a_row_of_its_own():
    assert len(gc.parse_registry(f"{ROW}\ng.md\tA\t2\tls\treason x")) == 2


@pytest.mark.parametrize(
    "text", ["ls", "a\nb", "a\\nb", "back\\slash", "\\\\n", "tab-free \\ end\\"]
)
def test_a_cell_round_trips(text):
    assert gc.decode(gc.encode(text)) == text
    assert "\n" not in gc.encode(text)


def test_a_command_with_a_newline_is_found_by_its_encoded_cell():
    text = "## A\n\n" + fence("sh", "f() {", "  ls", "}")
    (command,) = gc.commands("g.md", text)
    (entry,) = gc.parse_registry(f"g.md\tA\t1\t{gc.encode(command.text)}\treason x")
    assert entry.key == command.key


def test_problems_names_a_command_with_no_row_and_a_row_with_no_command():
    commands = gc.commands("g.md", "## A\n\n" + fence("sh", "ls", "pwd"))
    entries = gc.parse_registry(
        "g.md\tA\t1\tls\treason x\ng.md\tA\t1\twhoami\treason y"
    )
    assert gc.problems(commands, entries) == [
        "docs/g.md:5: `pwd` (under 'A') has no entry in "
        "server/docs/guide-commands.tsv: "
        "add `stage <name>` for the stage of server/docker/ci/guide-commands.sh that "
        "runs it, `workflow <file>` for a workflow step that holds it, or "
        "`reason <why no check can run it>`",
        "server/docs/guide-commands.tsv:2: the entry for `whoami` under 'A' in "
        "docs/g.md (occurrence 1) matches no command of the guide: the command "
        "changed or went, so change or remove the entry",
    ]


def test_problems_names_the_turn_of_a_repeated_command():
    commands = gc.commands("g.md", "## A\n\n" + fence("sh", "ls", "ls"))
    entries = gc.parse_registry("g.md\tA\t1\tls\treason x")
    (error,) = gc.problems(commands, entries)
    assert error.startswith("docs/g.md:5: `ls` (under 'A', occurrence 2)")


def test_the_same_text_under_another_heading_is_another_command():
    commands = gc.commands(
        "g.md", "## A\n\n" + fence("sh", "ls") + "\n## B\n\n" + fence("sh", "ls")
    )
    entries = gc.parse_registry("g.md\tA\t1\tls\treason x")
    (error,) = gc.problems(commands, entries)
    assert "(under 'B')" in error


def test_a_stage_gets_its_commands_in_guide_order_and_no_others():
    commands = gc.commands(
        "g.md", "## A\n\n" + fence("sh", "one", "two", "three", "four")
    )
    entries = gc.parse_registry(
        "\n".join(
            [
                "g.md\tA\t1\tfour\tstage s",
                "g.md\tA\t1\tone\tstage s",
                "g.md\tA\t1\ttwo\tstage t",
                "g.md\tA\t1\tthree\treason s",
            ]
        )
    )
    assert [c.text for c in gc.stage_commands(commands, entries, "s")] == [
        "one",
        "four",
    ]
    assert gc.stage_commands(commands, entries, "u") == []


def run_tool(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "tools.guide_commands", *args],
        cwd=SERVER,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_tool_prints_a_stage_as_nul_separated_where_and_command():
    done = run_tool("stage", "time-zone")
    assert done.returncode == 0, done.stderr
    records = done.stdout.split("\0")
    assert records[-1] == ""
    pairs = list(zip(records[0:-1:2], records[1:-1:2], strict=True))
    assert [text.split()[:2] for _, text in pairs] == [
        ["docker", "rm"],
        ["docker", "run"],
    ]
    assert all(re.fullmatch(r"install-docker\.md:\d+", where) for where, _ in pairs)


def test_the_tool_refuses_a_stage_with_no_commands():
    done = run_tool("stage", "no-such-stage")
    assert (done.returncode, done.stdout) == (1, "")
    assert "no command is registered to stage no-such-stage" in done.stderr


def test_the_tool_with_no_argument_is_the_check_and_passes_today():
    done = run_tool()
    assert (done.returncode, done.stdout.strip()) == (0, "")


@pytest.mark.parametrize(
    "args", [("frobnicate",), ("frobnicate", "x"), ("stage",), ("stage", "a", "b")]
)
def test_the_tool_refuses_another_argument(args):
    assert run_tool(*args).returncode == 2


def stand_in(tmp_path, guide: str, rows: str):
    (tmp_path / "g.md").write_text(guide)
    (tmp_path / "t.tsv").write_text(rows)
    return tmp_path / "t.tsv", tmp_path


def test_the_check_exits_1_and_prints_each_problem_when_a_command_has_no_row(
    tmp_path, capsys
):
    registry, folder = stand_in(tmp_path, "## A\n\n" + fence("sh", "ls"), "")
    assert gc.main([], registry, folder) == 1
    assert capsys.readouterr().out.startswith("docs/g.md:4: `ls` (under 'A')")


def test_the_check_exits_0_and_prints_nothing_when_every_command_has_a_row(
    tmp_path, capsys
):
    registry, folder = stand_in(
        tmp_path, "## A\n\n" + fence("sh", "ls"), "g.md\tA\t1\tls\treason x"
    )
    assert gc.main([], registry, folder) == 0
    assert capsys.readouterr().out.strip() == ""


def test_the_check_names_a_malformed_row_and_an_unclosed_construct(tmp_path, capsys):
    registry, folder = stand_in(tmp_path, fence("sh", "ls"), "g.md\tA\tls")
    assert gc.main([], registry, folder) == 1
    assert "guide-commands.tsv:1" in capsys.readouterr().err
    registry, folder = stand_in(tmp_path, fence("sh", "f() {"), "")
    assert gc.main([], registry, folder) == 1
    assert "docs/g.md: line 2: `f() {`" in capsys.readouterr().err


# What runs the stages.


def arms() -> dict[str, str]:
    return dict(ARM.findall(SCRIPT.read_text()))


def function_body(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{\n(.*?)^\}}$", SCRIPT.read_text(), re.M | re.S)
    assert match, f"guide-commands.sh has no function {name}"
    return match.group(1)


def registered_stages() -> set[str]:
    entries = gc.parse_registry(gc.REGISTRY.read_text())
    return {e.detail for e in entries if e.kind == "stage"}


def stages_called(workflow: str) -> set[str]:
    """The stages a live step of the workflow's text names on a
    guide-commands.sh line."""
    return {
        name
        for step in steps(workflow)
        if step.runs
        for line in step.script.splitlines()
        if (match := CALL.match(line.strip()))
        for name in match.group(1).split()
    }


def run_by_workflows() -> set[str]:
    names: set[str] = set()
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        names |= stages_called(path.read_text())
    return names


def test_a_stage_name_in_the_table_is_an_arm_of_the_script():
    assert sorted(registered_stages() - set(arms())) == []


def test_an_arm_of_the_script_has_commands_in_the_table():
    assert sorted(set(arms()) - registered_stages()) == []


def test_each_arm_calls_its_function_and_the_function_runs_its_own_stage():
    for name, function in arms().items():
        assert function == f"stage_{name.replace('-', '_')}"
        body = function_body(function)
        assert re.search(rf"\b(run_guide|write_env) {re.escape(name)}\b", body), (
            f"{function} never runs the commands of stage {name}"
        )


def test_the_header_names_each_stage():
    match = STAGES_HEADER.search(SCRIPT.read_text())
    assert match, (
        "guide-commands.sh's header has no `# Stages:` list ending in a bare `#`"
    )
    assert sorted(STAGE_NAME.findall(match.group(1))) == sorted(arms())


def test_a_live_workflow_step_runs_each_stage():
    """A `# run:` commented out, or a step with `if: false`, runs nothing."""
    assert sorted(set(arms()) - run_by_workflows()) == []


def test_a_workflow_runs_no_stage_the_script_lacks():
    assert sorted(run_by_workflows() - set(arms())) == []


def test_a_live_step_holds_each_command_the_table_gives_to_a_workflow():
    entries = [
        e for e in gc.parse_registry(gc.REGISTRY.read_text()) if e.kind == "workflow"
    ]
    assert entries, "no row names a workflow"
    for entry in entries:
        path = WORKFLOWS / entry.detail
        assert path.is_file(), f"line {entry.line}: no workflow {entry.detail}"
        lines = {
            line.strip()
            for step in steps(path.read_text())
            if step.runs
            for line in step.script.splitlines()
        }
        assert entry.text in lines, (
            f"guide-commands.tsv:{entry.line}: no live step of {entry.detail} "
            f"runs `{entry.text}` as one of its lines"
        )


def test_the_workflow_step_finder_reads_a_call_and_skips_a_dead_one():
    workflow = textwrap.dedent(
        """\
        jobs:
          guide:
            steps:
              - run: server/docker/ci/guide-commands.sh compose-env compose-start
              - run: |
                  # server/docker/ci/guide-commands.sh time-zone
                  IMAGE=x server/docker/ci/guide-commands.sh postgres
              - if: false
                run: server/docker/ci/guide-commands.sh proxies
        """
    )
    assert stages_called(workflow) == {"compose-env", "compose-start", "postgres"}


def test_each_stage_a_reason_names_is_an_arm_of_the_script():
    names = {
        name
        for e in gc.parse_registry(gc.REGISTRY.read_text())
        if e.kind == "reason"
        for name in re.findall(r"\bstage ([a-z][a-z-]*)", e.detail)
    }
    assert names
    assert sorted(names - set(arms())) == []


# The only checks.sh left for a reason to name: D139 deleted the Pi images' one.
CHECKS_SH = "server/docker/ci/checks.sh"
CHECKS_SH_CHECK = re.compile(r"(?:server/docker/ci/)?checks\.sh ([a-z][a-z-]*)")


def checks_missing(registry: str, root: Path = ROOT) -> list[str]:
    """Each `checks.sh <check>` a reason of `registry` names that
    server/docker/ci/checks.sh has no arm for."""
    script = (root / CHECKS_SH).read_text()
    return [
        f"{CHECKS_SH} has no check {check}"
        for e in gc.parse_registry(registry)
        if e.kind == "reason"
        for check in CHECKS_SH_CHECK.findall(e.detail)
        if not re.search(rf"^ +{re.escape(check)}\) check_\w+ ;;$", script, re.M)
    ]


def test_each_checks_sh_check_a_reason_names_is_a_check_of_that_script():
    assert checks_missing(gc.REGISTRY.read_text()) == []


@pytest.mark.parametrize(
    "named, missing",
    [
        ("`server/docker/ci/checks.sh upload`", []),
        ("`checks.sh upload`", []),
        ("`checks.sh uploads`", ["server/docker/ci/checks.sh has no check uploads"]),
    ],
)
def test_a_reason_s_checks_sh_check_is_looked_up_in_the_script(named, missing):
    """No reason of the registry names a checks.sh check since D139, so
    these entries hold the lookup into the real server/docker/ci/checks.sh,
    whose `upload) check_upload ;;` arm the first two find."""
    registry = f"g.md\tStart\t1\tls\treason in its place image.yml runs {named}\n"
    assert checks_missing(registry) == missing


def test_the_script_starts_the_registry_the_workflows_pin():
    pinned = re.compile(r"registry:3@sha256:[0-9a-f]{64}")
    script = set(pinned.findall(SCRIPT.read_text()))
    assert len(script) == 1
    assert set(pinned.findall((WORKFLOWS / "image.yml").read_text())) == script


# The guard and the cleanup, against a stand-in `docker` that logs each call
# and lists what the test gives it.  DOCKER_HOST names no daemon, so a call
# that slipped past the stand-in reaches nothing.

STUB_DOCKER = r"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB_LOG"
case "$1" in
    info) exit "${STUB_INFO:-0}" ;;
    ps) [[ " $* " != *" --format "* ]] || printf '%s\n' ${STUB_CONTAINERS:-} ;;
    volume) [[ "$2 $*" != "ls "*"--format"* ]] || printf '%s\n' ${STUB_VOLUMES:-} ;;
    network) [[ "$2 $*" != "ls "*"--format"* ]] || printf '%s\n' ${STUB_NETWORKS:-} ;;
    image) [[ " ${STUB_IMAGES:-} " == *" $3 "* ]]; exit ;;
    logs) echo 'listening on' ;;
esac
exit 0
"""
STUB_UV = r"""#!/bin/bash
printf 'uv %s\n' "$*" >> "$STUB_LOG"
[ -z "${STUB_UV_FAILS:-}" ] || exit 1
exec sleep 30
"""
GUARD_CALLS = {"info", "ps", "volume ls", "network ls", "image inspect"}
GHCR_LATEST = "ghcr.io/porkchopexpress86/derbynet_server:latest"


def stub_env(tmp_path, **listed: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, text in (("docker", STUB_DOCKER), ("uv", STUB_UV)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "tmp").mkdir(exist_ok=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("GUIDE_", "STUB_", "DOCKER_"))
    }
    env |= {
        "PATH": f"{bin_dir}:{env['PATH']}",
        "DOCKER_HOST": f"unix://{tmp_path}/no-daemon.sock",
        "TMPDIR": str(tmp_path / "tmp"),
        "STUB_LOG": str(tmp_path / "docker.log"),
        **{f"STUB_{key.upper()}": value for key, value in listed.items()},
    }
    return env


def calls(tmp_path) -> list[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def run_script(env, *stages: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *stages],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize(
    ("kind", "name"),
    [
        ("containers", "derbynet"),
        ("containers", "derbynet-timer-timer-1"),
        ("containers", "guide-registry"),
        ("volumes", "derbynet_data"),
        ("networks", "traefik"),
        ("networks", "guide-pi"),
        ("images", GHCR_LATEST),
    ],
)
def test_the_guard_refuses_a_daemon_holding_a_name_the_guides_use(tmp_path, kind, name):
    """And changes nothing on it: the cleanup removes only once the guard
    has passed."""
    done = run_script(stub_env(tmp_path, **{kind: name}), "plain-http")
    assert done.returncode == 2, done.stderr
    assert f"this Docker daemon already holds:\n{name}\n" in done.stderr
    made = [
        c
        for c in calls(tmp_path)
        if " ".join(c.split()[:2]) not in GUARD_CALLS
        and c.split()[0] not in GUARD_CALLS
    ]
    assert made == []


def test_the_guard_refuses_no_daemon(tmp_path):
    done = run_script(stub_env(tmp_path, info="1"), "plain-http")
    assert done.returncode == 2
    assert "no Docker daemon answers" in done.stderr
    assert calls(tmp_path) == ["info"]


def test_names_that_only_contain_a_guide_name_pass_and_the_cleanup_runs(
    tmp_path, monkeypatch
):
    """A failing stage after the guard passed still removes what the guides'
    commands name, and the script's own folder: a GUIDE_WORK of the test
    run's own environment does not reach it."""
    monkeypatch.setenv("GUIDE_WORK", str(tmp_path / "kept"))
    env = stub_env(
        tmp_path,
        containers="my-derbynet",
        networks="bridge mytraefik traefik-proxy",
        images="ghcr.io/porkchopexpress86/derbynet_server:v0.1.0-rc3",
    )
    done = run_script(env, "restore")
    assert done.returncode == 1, done.stderr
    assert "already holds" not in done.stderr
    logged = calls(tmp_path)
    assert "ps -aq --filter name=^derbynet" in logged
    assert "network rm traefik" in logged
    assert "ps -aq --filter name=^guide-" in logged
    assert f"rmi {GHCR_LATEST}" in logged
    assert list((tmp_path / "tmp").iterdir()) == []
    assert not (tmp_path / "kept").exists()


def test_a_stop_signal_mid_stage_exits_143_after_the_cleanup(tmp_path):
    """A SIGTERM to the run's process group, as a Ctrl-C or a cancelled job
    sends, while a stage waits on a command."""
    process = subprocess.Popen(
        ["bash", str(SCRIPT), "plain-http"],
        env=stub_env(tmp_path),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not any(c.startswith("uv run") for c in calls(tmp_path)):
            assert process.poll() is None, process.stderr.read()
            assert time.monotonic() < deadline, calls(tmp_path)
            time.sleep(0.1)
        # Our own child leads its own group, so the signal reaches it alone.
        assert os.getpgid(process.pid) == process.pid != os.getpgid(0)
        os.killpg(process.pid, signal.SIGTERM)
        assert process.wait(timeout=30) == 143
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    logged = calls(tmp_path)
    after = logged[[c.startswith("uv run") for c in logged].index(True) :]
    assert "ps -aq --filter name=^derbynet" in after
    assert f"rmi {GHCR_LATEST}" in " ".join(after)


def test_a_failing_dev_stage_asks_no_docker(tmp_path):
    """The dev stages run on a machine with no Docker: the guard and the
    failure's diagnostics leave it alone."""
    done = run_script(stub_env(tmp_path, uv_fails="1"), "dev-setup")
    assert done.returncode == 1
    assert "FAILED: no commands for stage dev-setup" in done.stderr
    assert [c for c in calls(tmp_path) if not c.startswith("uv ")] == []
