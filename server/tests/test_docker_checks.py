"""Every check `server/docker/ci/checks.sh` defines runs in
`.github/workflows/image.yml`, and its header names it.

#58's first review round added the checks stop, reload, forwarded-proto,
engine, postgres-password, bad-env, upgrade and backup. They ran only locally
until 7391a51a, since the workflow's checks job named each check by hand and
listed none of them (#58 retro).
The checks job runs `checks.sh` with no arguments, which runs its default
list, and names each check left out of that list in a step of its own. Only a
step that runs counts: a `# run:` commented out, or a step with `if: false`,
gave 4 passed in #58's retro review (tests/workflow_steps.py).
"""

import os
import re
import subprocess
from pathlib import Path

from tests.workflow_steps import steps

ROOT = Path(__file__).resolve().parents[2]
CHECKS = ROOT / "server" / "docker" / "ci" / "checks.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"

ARM = re.compile(r"^ +([a-z][a-z-]*)\) (check_\w+) ;;$", re.M)
FUNCTION = re.compile(r"^(check_\w+)\(\) \{", re.M)
DEFAULT = re.compile(r"^\[ \$# -gt 0 \] \|\| set -- ((?:[^\n]*\\\n)*[^\n]*)$", re.M)
# The header's list under `# Checks:`: a name, then what it checks, or the
# name alone on its line when it is too long for the column.
HEADER = re.compile(r"^# Checks:\n((?:#[^\n]*\n)*?)#\n", re.M)
HEADER_NAME = re.compile(r"^#   ([a-z][a-z-]*)(?: |$)", re.M)
# A script line that runs checks.sh, after any VAR=value; a `#` line is no call.
CALL = re.compile(r"^(?:\S+=\S+ +)*server/docker/ci/checks\.sh((?: [a-z][a-z-]*)*) *$")


def arms(text: str) -> dict[str, str]:
    return dict(ARM.findall(text))


def default_list(text: str) -> list[str]:
    match = DEFAULT.search(text)
    assert match, "checks.sh sets no default list with `[ $# -gt 0 ] || set -- ...`"
    return match.group(1).replace("\\\n", " ").split()


def header_names(text: str) -> list[str]:
    match = HEADER.search(text)
    assert match, "checks.sh's header has no `# Checks:` list ending in a bare `#`"
    return HEADER_NAME.findall(match.group(1))


def run_by(workflow: str, default: list[str]) -> set[str]:
    """The checks the workflow's live steps run: a bare `checks.sh` runs the
    default list."""
    calls = [
        match.group(1).split()
        for step in steps(workflow)
        if step.runs
        for line in step.script.splitlines()
        if (match := CALL.match(line.strip()))
    ]
    assert calls, "image.yml runs no server/docker/ci/checks.sh"
    return {name for call in calls for name in (call or default)}


def test_each_check_function_has_its_name():
    text = CHECKS.read_text()
    named = arms(text)
    assert sorted(named.values()) == sorted(FUNCTION.findall(text))
    assert {f"check_{n.replace('-', '_')}" for n in named} == set(named.values())


def test_the_header_names_each_check():
    text = CHECKS.read_text()
    assert sorted(header_names(text)) == sorted(arms(text))


def test_image_yml_runs_each_check():
    text = CHECKS.read_text()
    default = default_list(text)
    assert set(default) <= set(arms(text)), "the default list names no such check"
    ran = run_by(WORKFLOW.read_text(), default)
    unknown = ran - set(arms(text))
    assert not unknown, f"image.yml runs no such check: {unknown}"
    missing = sorted(set(arms(text)) - ran)
    assert missing == [], (
        f"image.yml never runs {missing}: add each to checks.sh's default list, "
        "or give it a step of its own in image.yml's checks job"
    )


def test_a_check_left_out_of_the_workflow_fails():
    """The guard against a workflow that drops a check: the stand-in names
    every check but backup."""
    text = CHECKS.read_text()
    default = default_list(text)
    stand_in = workflow(
        [
            f"server/docker/ci/checks.sh {name}"
            for name in arms(text)
            if name not in default
        ]
    )
    assert set(arms(text)) - run_by(stand_in, default) == set()
    stand_in = stand_in.replace("checks.sh backup", "checks.sh other")
    assert set(arms(text)) - run_by(stand_in, default) == {"backup"}


def workflow(calls: list[str], extra: str = "") -> str:
    """A stand-in checks job: a bare checks.sh, then a step per call."""
    out = "jobs:\n  checks:\n    steps:\n      - name: default\n"
    out += "        run: server/docker/ci/checks.sh\n"
    for n, call in enumerate(calls):
        out += f"      - name: step {n}\n{extra}        run: {call}\n"
    return out


def test_a_commented_out_step_runs_nothing():
    """#58's retro review commented out both backup steps as `# run:` and
    this file still passed."""
    text = CHECKS.read_text()
    default = default_list(text)
    stand_in = workflow(["server/docker/ci/checks.sh backup"])
    assert "backup" in run_by(stand_in, default)
    commented = stand_in.replace(
        "        run: server/docker/ci/checks.sh backup",
        "        # run: server/docker/ci/checks.sh backup",
    )
    assert "backup" not in run_by(commented, default)
    in_a_block = stand_in.replace(
        "        run: server/docker/ci/checks.sh backup",
        "        run: |\n          # server/docker/ci/checks.sh backup\n          true",
    )
    assert "backup" not in run_by(in_a_block, default)


def test_a_step_that_never_runs_runs_nothing():
    text = CHECKS.read_text()
    default = default_list(text)
    for never in ("false", "${{ false }}", "'${{ false }}'"):
        stand_in = workflow(
            ["server/docker/ci/checks.sh backup"], f"        if: {never}\n"
        )
        assert "backup" not in run_by(stand_in, default), never
    live = workflow(["server/docker/ci/checks.sh backup"], "        if: always()\n")
    assert "backup" in run_by(live, default)
    with_env = workflow(
        ["server/docker/ci/checks.sh backup"],
        "        env:\n          PROFILE: postgres\n",
    )
    assert "backup" in run_by(with_env, default)


# ----- checks.sh timer's file lists (#312) -----------------------------------


def shell_function(name: str) -> str:
    match = re.search(rf"^{name}\(\) \{{.*?^\}}$", CHECKS.read_text(), re.M | re.S)
    assert match, f"checks.sh defines no {name}()"
    return match.group()


def run_function(name: str, tmp_path: Path, text: str) -> list[str]:
    """`name <file>` under checks.sh's own shell options, on a file holding
    `text`."""
    path = tmp_path / "input"
    path.write_text(text)
    done = subprocess.run(
        [
            "bash",
            "-c",
            f'set -e -E -u -o pipefail\n{shell_function(name)}\n{name} "$1"',
            "bash",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.split()


def test_page_files_takes_any_attribute_order_and_quote_style(tmp_path):
    page = (
        "<link href='css/a.css' type=\"text/css\" rel='stylesheet'/>\n"
        '<link rel="icon" href="img/icon.png"/>\n'
        '<LINK rel=stylesheet href="css/b.css">\n'
        "<script src='js/one.js' type=\"text/javascript\"></script>\n"
        '<script type="text/javascript" src="js/two.js"></script>\n'
        '<img id="x"\n     src="img/three.png"\n     onclick="go()"/>\n'
    )
    assert sorted(run_function("page_files", tmp_path, page)) == [
        "css/a.css",
        "css/b.css",
        "img/three.png",
        "js/one.js",
        "js/two.js",
    ]


def test_page_files_reads_the_timer_page(tmp_path):
    """The committed template, where the gear icon's <img> spans lines."""
    from ui import timer_build

    found = run_function("page_files", tmp_path, timer_build.TEMPLATE.read_text())
    assert "img/gear_icon.png" in found
    assert {"js/timer/main.js", "js/timer/profiles.js", "css/timer.css"} <= set(found)


def test_script_files_takes_either_quote_escaped_or_not(tmp_path):
    script = (
        "g = new Worker('js/timer/w.js');\n"
        '$("<img src=\\"img/c.png\\"/>");\n'
        "$(\"<img src='img/d.png'/>\");\n"
        "x.prop('src', 'img/status/' + png + '.png');\n"
        "var y = 'css/not-loaded.css';\n"
    )
    assert run_function("script_files", tmp_path, script) == [
        "js/timer/w.js",
        "img/c.png",
        "img/d.png",
    ]


def test_script_files_with_no_match_prints_nothing(tmp_path):
    assert run_function("script_files", tmp_path, "var x = 1;\n") == []


# ----- an exec'd manage.py runs as derbynet (#308 retro) --------------------
#
# `docker exec` and `docker compose exec` skip the entrypoint, so they run as
# root unless told otherwise.  #308's first round wrote
# `docker compose exec server python manage.py ...` in install-docker.md and a
# compose.yaml comment.  Review found both: run that way, an import leaves the
# event's photo folder root's (#327's commit message).  Since D103 (#345)
# manage.py in the image refuses root instead, so such a command fails with a
# message; checks.sh's root check runs it on purpose, each line marked with
# ROOT_ON_PURPOSE.

# Where a command a reader or a script runs is written.  Python tests hold
# stand-ins, this file's own among them.
EXEC_SUFFIXES = (".md", ".sh", ".yml", ".yaml", ".html", ".txt", ".py")
# `docker exec`, `docker compose exec`, a `compose` shell function, or a
# compose command held in "$@" or "${compose[@]}", with any options before
# `exec` (`--profile postgres`, `-f FILE`), then manage.py later in the same
# command: before a pipe, an `&&`, a closing backtick or the end of the line,
# once backslash continuations are joined.
EXEC = re.compile(
    r"""(?:\bdocker|\bcompose|"\$@"|"\$\{\w+\[@\]\}")"""
    r"(?:[ \t]+(?:compose|-\S+(?:[ \t]+(?!exec\b|-)\S+)?))*"
    r"[ \t]+exec[ \t]+((?:(?!&&)[^|`\n])*?)\bmanage\.py\b"
)
# exec options that take a value, for docker exec and docker compose exec.
EXEC_VALUED = {
    "-e",
    "--env",
    "--env-file",
    "-w",
    "--workdir",
    "--index",
    "--detach-keys",
}


def exec_user(between: str) -> str | None:
    """The user the exec options before the container or service name give."""
    tokens = between.split()
    user = None
    i = 0
    while i < len(tokens) and tokens[i].startswith("-"):
        token = tokens[i]
        if token in ("-u", "--user"):
            user = tokens[i + 1] if i + 1 < len(tokens) else None
            i += 2
        elif token.startswith("--user="):
            user = token.removeprefix("--user=")
            i += 1
        elif token in EXEC_VALUED:
            i += 2
        else:
            i += 1
    return user


def exec_commands(text: str) -> list[tuple[int, str | None]]:
    """(line, user) for each exec'd manage.py in `text`."""
    joined = text.replace("\\\n", "  ")  # the same length, so offsets hold
    return [
        (text.count("\n", 0, match.start()) + 1, exec_user(match.group(1)))
        for match in EXEC.finditer(joined)
    ]


# The mark of an exec'd manage.py that runs as root on purpose, which only
# checks.sh's check_root may carry.
ROOT_ON_PURPOSE = "# root on purpose (D103)"
CHECKS_NAME = "server/docker/ci/checks.sh"


def root_execs(name: str, text: str) -> tuple[list[int], list[int]]:
    """The lines of `text`'s exec'd manage.py commands that run as another
    user than derbynet, and those of them marked ROOT_ON_PURPOSE inside
    checks.sh's check_root, which pass."""
    span = range(0)
    if name == CHECKS_NAME:
        match = re.search(r"^check_root\(\) \{.*?^\}$", text, re.M | re.S)
        if match:
            first = text.count("\n", 0, match.start()) + 1
            span = range(first, first + match.group().count("\n") + 1)
    lines = text.splitlines()
    root, on_purpose = [], []
    for line, user in exec_commands(text):
        if user == "derbynet":
            continue
        # The command's last physical line, which holds a trailing comment.
        end = line - 1
        while lines[end].endswith("\\"):
            end += 1
        if line in span and lines[end].endswith(ROOT_ON_PURPOSE):
            on_purpose.append(line)
        else:
            root.append(line)
    return root, on_purpose


def exec_files() -> list[str]:
    names = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")
    return [
        name
        for name in names
        if name
        and not name.startswith("website/")
        and name.endswith(EXEC_SUFFIXES)
        and not (name.endswith(".py") and "/tests/" in name)
        and (ROOT / name).is_file()
    ]


def test_every_execd_manage_py_runs_as_derbynet():
    found: dict[str, int] = {}
    root = []
    on_purpose = []
    for name in exec_files():
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        found[name] = len(exec_commands(text))
        flagged, marked = root_execs(name, text)
        root += [f"{name}:{line}" for line in flagged]
        on_purpose += [f"{name}:{line}" for line in marked]
    # The scan still reaches a guide and a script that hold such commands.
    for name in ("docs/database.md", CHECKS_NAME):
        assert found.get(name), f"no exec'd manage.py found in {name}"
    # check_root's refused check and shell, and the build's collectstatic.
    assert len(on_purpose) == 3, on_purpose
    assert root == [], (
        "pass `-u derbynet` before the container or service name: an exec runs "
        "as root, which manage.py in the image refuses (D103)\n" + "\n".join(root)
    )


def test_root_on_purpose_passes_only_in_check_root():
    """The mark passes a root exec inside check_root alone: the same line
    without it, or marked in another check or another file, still fails."""
    text = CHECKS.read_text()
    root, on_purpose = root_execs(CHECKS_NAME, text)
    assert root == [] and len(on_purpose) == 3
    marked = next(
        line
        for line in text.splitlines()
        if "exec -T server python manage.py check" in line
    )
    assert marked.endswith(ROOT_ON_PURPOSE)
    unmarked = text.replace(marked, marked.removesuffix(ROOT_ON_PURPOSE))
    assert len(root_execs(CHECKS_NAME, unmarked)[0]) == 1
    elsewhere = text.replace(
        "check_timeout() {\n", f"check_timeout() {{\n{marked}\n", 1
    )
    assert len(root_execs(CHECKS_NAME, elsewhere)[0]) == 1
    assert len(root_execs("docs/database.md", text)[0]) == 3
    # A command continued over lines carries the mark on its last line.
    continued = (
        '    out="$("${compose[@]}" exec -T server \\\n'
        '        python manage.py check 2>&1)" || status=$?  ' + ROOT_ON_PURPOSE
    )
    inside = text.replace(marked, continued)
    root, on_purpose = root_execs(CHECKS_NAME, inside)
    assert root == [] and len(on_purpose) == 3
    unmarked = text.replace(marked, continued.removesuffix(ROOT_ON_PURPOSE))
    assert len(root_execs(CHECKS_NAME, unmarked)[0]) == 1


def test_an_exec_without_derbynet_is_found():
    text = (
        "docker compose exec server python manage.py import_derbynet x\n"
        "docker exec -it derbynet python manage.py shell\n"
        "docker exec -u root derbynet python manage.py shell\n"
        "docker compose exec server -u derbynet python manage.py shell\n"
        '"${compose[@]}" exec -T -e A=b server \\\n'
        "    python manage.py migrate\n"
        "docker compose exec server sh -c 'cd /opt; python manage.py check'\n"
        "docker compose --profile postgres exec server python manage.py migrate\n"
        "docker compose -p x -f compose.yaml exec -T server python manage.py check\n"
        "compose exec -T server python manage.py check\n"
    )
    assert exec_commands(text) == [
        (1, None),
        (2, None),
        (3, "root"),
        (4, None),
        (5, None),
        (7, None),
        (8, None),
        (9, None),
        (10, None),
    ]


def test_an_exec_as_derbynet_and_prose_pass():
    text = (
        "docker compose exec -u derbynet server python manage.py import_derbynet x\n"
        "docker exec --user=derbynet derbynet python manage.py shell\n"
        '"$@" exec -T -u derbynet server python manage.py shell -c x\n'
        '"${compose[@]}" exec -T -u derbynet -e A=b server \\\n'
        "    python manage.py migrate\n"
        "A `docker compose exec`'d `manage.py` reaches the server's database.\n"
        "an exec'd manage.py's database: postgres\n"
        "docker compose exec server ls | grep manage.py\n"
        "docker compose exec server true && python manage.py check\n"
        "docker compose --profile postgres exec -u derbynet server python manage.py x\n"
        "docker exec --user derbynet derbynet python manage.py shell\n"
        "compose exec -T -u derbynet server python manage.py check\n"
    )
    assert exec_commands(text) == [
        (1, "derbynet"),
        (2, "derbynet"),
        (3, "derbynet"),
        (4, "derbynet"),
        (10, "derbynet"),
        (11, "derbynet"),
        (12, "derbynet"),
    ]


# ----- checks.sh uid-exec's .env lines from the guide (D106) ------------------


def guide_env_lines(tmp_path: Path, text: str) -> str:
    path = tmp_path / "guide.md"
    path.write_text(text)
    return subprocess.run(
        [
            "bash",
            "-c",
            f"set -e -E -u -o pipefail\n{shell_function('guide_env_lines')}\n"
            'guide_env_lines "$1"',
            "bash",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_guide_env_lines_run_write_the_host_users_ids(tmp_path):
    """The guide's Compose step, run as uid-exec runs it, writes the ids `id`
    prints into .env, the first address `hostname -I` prints (D116), and the
    time zone."""
    lines = guide_env_lines(tmp_path, (ROOT / "docs" / "install-docker.md").read_text())
    assert lines == (
        'echo "DERBYNET_UID=$(id -u)" >> .env\necho "DERBYNET_GID=$(id -g)" >> .env\n'
        "echo \"DERBYNET_ADDRESS=$(hostname -I | cut -d' ' -f1)\" >> .env\n"
        'echo "TZ=America/Chicago" >> .env\n'
    )
    work = tmp_path / "work"
    work.mkdir()
    subprocess.run(["bash", "-euo", "pipefail", "-c", lines], cwd=work, check=True)
    address = subprocess.run(
        ["hostname", "-I"], capture_output=True, text=True, check=True
    ).stdout.split()
    # A host with no network address prints none, and cut gives an empty value.
    address = address[0] if address else ""
    assert (work / ".env").read_text() == (
        f"DERBYNET_UID={os.getuid()}\nDERBYNET_GID={os.getgid()}\n"
        f"DERBYNET_ADDRESS={address}\nTZ=America/Chicago\n"
    )


def test_guide_env_lines_skip_other_blocks(tmp_path):
    """A `docker run` naming DERBYNET_UID, another line into .env, and a
    PowerShell block are not the step."""
    text = (
        "# T\n\n```sh\ndocker run -e DERBYNET_UID=$(id -u) image\n```\n\n"
        "```sh\necho DERBYNET_IMAGE=x >> .env\n```\n\n"
        "```powershell\necho DERBYNET_UID=1 >> .env\n```\n"
    )
    assert guide_env_lines(tmp_path, text) == ""
    step = "```sh\necho DERBYNET_UID=1 >> .env\n```\n"
    assert guide_env_lines(tmp_path, text + step) == "echo DERBYNET_UID=1 >> .env\n"
    # A block in another language after the step closes no second copy.
    after = "\n```powershell\nx\n```\n"
    assert guide_env_lines(tmp_path, text + step + after) == (
        "echo DERBYNET_UID=1 >> .env\n"
    )


# A command in checks.sh that waits on a server or the network carries its
# own bound: curl its `-m`, and `openssl s_client`, a Compose `run` and a
# `docker run` of the image's own server a `timeout` before them.  #351's
# check that Caddy stops at a malformed DERBYNET_ADDRESS ran `compose run`
# unbounded; a mutant that left Caddy running held the check for about 5 h
# 45 min, until f01f8fb3 put `timeout 60` before it (#351 retro).
CURL = re.compile(r"(?<![\w/.-])curl\s")
CURL_BOUND = re.compile(r"\s(?:-m\s*\d|--max-time[=\s]\d)")
# Compose's run, through the `compose` array or `docker compose ...`; and a
# `docker run` that is neither detached nor given another entrypoint, which
# runs the image's server in the foreground.
COMPOSE_RUN = re.compile(r'(?:"\$\{compose\[@\]\}"|docker compose\b[^|;&]*?)\s+run\s')
DOCKER_RUN = re.compile(r"(?<![\w-])docker run\s")
TIMED = re.compile(r"\btimeout\s+\d+\s")


def logical_lines(text: str) -> list[tuple[int, str]]:
    """Each command line with its `\\` continuations joined, by its first
    line's number; comment lines are left out."""
    found: list[tuple[int, str]] = []
    joined, first = "", 0
    for number, line in enumerate(text.splitlines(), 1):
        if not joined:
            if line.lstrip().startswith("#"):
                continue
            first = number
        joined += line.removesuffix("\\") + " "
        if not line.endswith("\\"):
            found.append((first, joined))
            joined = ""
    return found


def unbounded(text: str) -> list[str]:
    """`line: what` for each command that waits with no bound of its own."""
    found = []
    for number, line in logical_lines(text):
        # A trailing comment is prose.
        line = re.sub(r"(?:^|\s)#\s.*$", "", line)
        for match in CURL.finditer(line):
            # An echo's words name curl; `$(curl ...)` inside it runs it.
            if re.search(r"\becho\b", line[: match.start()].rsplit("$(", 1)[-1]):
                continue
            # A pipe or `;` ends curl's own arguments.
            args = re.split(r"[|;)]", line[match.end() :], maxsplit=1)[0]
            if not CURL_BOUND.search(" " + args):
                found.append(f"{number}: curl with no -m")
        for match in re.finditer(r"\bs_client\b", line):
            if not TIMED.search(line[: match.start()]):
                found.append(f"{number}: openssl s_client with no timeout")
        for match in COMPOSE_RUN.finditer(line):
            if not TIMED.search(line[: match.start()]):
                found.append(f"{number}: compose run with no timeout")
        for match in DOCKER_RUN.finditer(line):
            args = line[match.end() :]
            if (
                re.match(r"(?:\S+\s+)*?(?:-d|--detach)\s", args)
                or "--entrypoint" in args
            ):
                continue
            if not TIMED.search(line[: match.start()]):
                found.append(f"{number}: docker run of the server with no timeout")
    return found


def test_every_wait_in_checks_sh_is_bounded():
    assert unbounded(CHECKS.read_text()) == [], (
        "give curl `-m 120`, and `timeout 60` before openssl s_client, a Compose "
        "run or a foreground docker run of the image: an unbounded one held "
        "#351's caddy check for about 5 h 45 min"
    )


def test_an_unbounded_wait_is_found():
    """The caddy check's line before f01f8fb3, and the shapes beside it."""
    text = (
        '    out="$(DERBYNET_ADDRESS=\'192.168.1.20;x\' "${compose[@]}" run --rm '
        '--no-deps -T caddy 2>&1)" || status=$?\n'
        '    code="$(curl -s -o /dev/null -w \'%{http_code}\' "$url/")"\n'
        '    docker exec "$name" curl -fsS http://127.0.0.1/ | grep -q x\n'
        '    grep -q success <<<"$(curl -s -b "$jar" \\\n'
        '        -F action=photo.upload "$1/action.php")"\n'
        "    openssl s_client -connect 127.0.0.1:443 </dev/null | openssl x509\n"
        '    out="$(docker run --rm -e A=b "$IMAGE" 2>&1)"\n'
        "    docker compose -p x run --rm server true\n"
        "    curl -s url | timeout 5 cat -m 3\n"
        '    echo "racers $(curl -s "$1/action.php" | jq .)"\n'
        "    # A comment that ends in a backslash continues nothing: \\\n"
        "    curl -s url\n"
    )
    assert unbounded(text) == [
        "1: compose run with no timeout",
        "2: curl with no -m",
        "3: curl with no -m",
        "4: curl with no -m",
        "6: openssl s_client with no timeout",
        "7: docker run of the server with no timeout",
        "8: compose run with no timeout",
        "9: curl with no -m",
        "10: curl with no -m",
        "12: curl with no -m",
    ]


def test_a_bounded_wait_passes():
    text = (
        '    out="$(DERBYNET_ADDRESS=\'x\' timeout 60 "${compose[@]}" '
        'run --rm caddy)"\n'
        "    curl -fsSk -m 30 -o a https://x/ \\\n"
        "        || fail no\n"
        "    curl -s --max-time 30 url | jq .\n"
        "    timeout 30 openssl s_client -connect a:443 </dev/null\n"
        '    docker run -d --name "$name" "$IMAGE" >/dev/null\n'
        '    docker run --rm --entrypoint ls "$IMAGE" -A /\n'
        '    out="$(timeout 120 docker run --name x "$IMAGE" 2>&1)"\n'
        "    # curl -s http://x/ in a comment\n"
        '    "${compose[@]}" exec -T server curl -s -m 5 http://127.0.0.1/\n'
        '    echo "curl --cacert root.crt https://$https/: $code"\n'
        "    origin_post() { # origin_post <url> [curl options]: the code\n"
    )
    assert unbounded(text) == []


# The image tag in a guide's `DERBYNET_IMAGE=` example.
EXAMPLE_IMAGE = re.compile(r"DERBYNET_IMAGE=ghcr\.io/\S+?:(v[\w.-]+)")


def example_images() -> dict[str, list[str]]:
    guides = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    return {
        str(guide.relative_to(ROOT)): EXAMPLE_IMAGE.findall(guide.read_text())
        for guide in guides
    }


def test_no_guide_example_names_the_image_from_before_caddy():
    """checks.sh holds v0.1.0-rc2 as the last image before D107, which
    serves HTTPS itself and does not fit compose.yaml's Caddy. README.md and
    docs/install-docker.md still named it as the image to put in `.env` when
    rc3 shipped for the owner's clean-machine test (#369 retro)."""
    old = re.search(
        r'^OLD_TLS_IMAGE="\$\{OLD_TLS_IMAGE:-\S+?:(v[\w.-]+)@', CHECKS.read_text(), re.M
    )
    assert old, "checks.sh names no OLD_TLS_IMAGE tag"
    found = example_images()
    assert found["README.md"] and found["docs/install-docker.md"], found
    stale = {guide: tags for guide, tags in found.items() if old.group(1) in tags}
    assert stale == {}, (
        f"name a test release newer than {old.group(1)}, which predates the "
        "Caddy compose.yaml (D107)"
    )
