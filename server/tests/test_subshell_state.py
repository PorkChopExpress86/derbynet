"""No shell function that records state for the script runs in a subshell:
inside `$(...)`, backquotes or a pipeline.

The subshell gets a copy of the script's variables and traps, and its
changes end with it. #59's round-1 `extras/docker/ci/checks.sh` ran
`id="$(compose_up ...)"`, and compose_up's `COMPOSE_DOWN+=(...)` went with
the subshell, so `cleanup` took no Compose project down and a
`t59-timer-compose` container was left (#59 review, retro).

A function records state when, outside a quoted string and outside a
subshell of its own, it appends to a variable it has not declared `local`,
sets a trap, assigns a global that some code, itself included, reads
before setting it, or calls a function that does. A function that prints
its result for `$(...)` takes its state from its caller instead, or the
caller runs it bare and reads the global it sets, as checks.sh's compose_up
now does.

The rule reads the script's text, not a bash parse: a function read across
files is found only through a `source` or `.` line naming a .sh file by its
basename, a call is a name in command position, and a read is a `$NAME`
or `${NAME`, not a bare name inside `$(( ))`.
"""

import re
from pathlib import Path

import pytest

from tests.test_pipefail_pipes import ROOT, logical_lines, shell_files

DEFINITION = re.compile(r"^\s*(?:function\s+)?([A-Za-z_][\w-]*)\s*\(\)\s*(\{.*)?$")
# `NAME=`, `NAME+=`, `NAME[...]=`, at a command's start or after a builtin
# that assigns (`declare -g`, `export`, `readonly`); not `[ "$a" = b ]`.
ASSIGNMENT = re.compile(
    r"(?:^|[;&|({]\s*|\b(?:then|do|else|export|readonly|declare\s+-g\w*)\s+)"
    r"([A-Za-z_]\w*)(?:\[[^]]*\])?\+?="
)
LOCAL = re.compile(r"\b(?:local|declare(?!\s+-\w*g)|typeset)\s+((?:-\w+\s+)*)([^;&|]*)")
TRAP = re.compile(r"(?:^|[;&|({]\s*|\b(?:then|do|else)\s+)trap\s+(?!-p\b)")
# A command word: at a line's start, after `;`, `&&`, `||`, `|`, `(`, `{`, or
# after a keyword that starts a command.
COMMAND = r"(?:^|[;&|({]\s*|\b(?:then|do|else|if|elif|while|until|!)\s+)(?:\w+=\S*\s+)*"
SOURCE = re.compile(r"^\s*(?:source|\.)\s+[^\n#;]*?([\w.-]+\.sh)\b", re.M)


def functions(text: str) -> dict[str, tuple[int, str]]:
    """Each function's first line number and body, read by its closing `}`
    at the definition's indent, or on its one line."""
    lines = text.splitlines()
    found = {}
    for i, line in enumerate(lines):
        m = DEFINITION.match(line)
        if not m:
            continue
        rest = (m.group(2) or "").strip()
        if rest.endswith("}"):
            found[m.group(1)] = (i + 1, rest[1:-1])
            continue
        indent = len(line) - len(line.lstrip())
        body = []
        for later in lines[i + 1 :]:
            if later.strip() == "}" and len(later) - len(later.lstrip()) == indent:
                break
            body.append(later)
        found[m.group(1)] = (i + 1, "\n".join(body))
    return found


def _code(body: str) -> list[str]:
    return [line.strip() for _, line in logical_lines(body)]


def locals_of(body: str) -> set[str]:
    names = set()
    for line in _code(body):
        for m in LOCAL.finditer(line):
            names |= set(re.findall(r"(?:^|\s)([A-Za-z_]\w*)(?==|\s|$)", m.group(2)))
    return names


def records(body: str, read_first: set[str]) -> list[str]:
    """What the body changes in its caller's shell, in order: each append to
    a global (`NAME+=`, `NAME[i]=`), each trap it sets, and each global it
    assigns that some code reads before setting it (`read_first`). A global
    set and then read only inside functions that each set it first, such as
    common.sh's OK, is scratch, and a subshell loses nothing by it."""
    local = locals_of(body)
    out = []
    for line in _code(unquoted(body)):
        line = _drop_substitutions(line)
        for m in ASSIGNMENT.finditer(line):
            name = m.group(1)
            append = line[m.end() - 2] in "+]"
            if name not in local and (append or name in read_first):
                out.append(f"{name}{'+=' if append else '='}")
        if TRAP.search(line):
            out.append("trap")
    return out


def unquoted(text: str) -> str:
    """`text` with each quoted string's characters blanked, newlines kept:
    an awk program's `on=1` or a Python one's `finish_time=None` assigns no
    shell variable. A `$(...)` inside double quotes keeps its own quoting,
    so `"$(curl -d "a=1")"` blanks `a=1`, and a `$NAME` inside double quotes,
    which the shell expands, is kept."""
    out, stack, quote, i = [], [], None, 0
    while i < len(text):
        c = text[i]
        if quote != "'" and c == "\\":
            pair = text[i : i + 2]
            out.append(pair if quote is None else re.sub(r"[^\n]", " ", pair))
            i += 2
            continue
        if quote != "'" and text.startswith("$(", i):
            stack.append(quote)
            quote = None
            out.append("$(")
            i += 2
            continue
        if quote is None and c == ")" and stack:
            quote = stack.pop()
            out.append(c)
        elif quote is None and c in "'\"":
            quote = c
            out.append(c)
        elif c == quote:
            quote = None
            out.append(c)
        elif quote == '"' and (m := READ.match(text, i)):
            out.append(m.group())  # expanded inside double quotes: a read
            i = m.end()
            continue
        else:
            out.append(c if quote is None or c == "\n" else " ")
        i += 1
    return "".join(out)


READ = re.compile(r"\$\{?([A-Za-z_]\w*)")
EMPTY = {"", '""', "''", "()"}


def readers(chunk: str) -> set[str]:
    """The names a chunk of code reads before it assigns them; an empty
    initialisation (`NAME=""`, `NAME=()`) counts as no assignment."""
    assigned, read = set(), set()
    for line in _code(unquoted(chunk)):
        for command in re.split(r"[;&|]+", _drop_substitutions(line)):
            read |= {n for n in READ.findall(command) if n not in assigned}
            for m in ASSIGNMENT.finditer(command.strip()):
                if re.match(r"\S*", command.strip()[m.end() :]).group() not in EMPTY:
                    assigned.add(m.group(1))
    return read


def top_level(text: str, defs: dict[str, tuple[int, str]]) -> str:
    lines = text.splitlines()
    inside = set()
    for first, body in defs.values():
        inside |= set(range(first - 1, first + body.count("\n") + 2))
    return "\n".join(line for n, line in enumerate(lines) if n not in inside)


def _drop_substitutions(line: str) -> str:
    """The line with each `$(...)` and `(...)` subshell's text taken out: what
    runs there changes nothing in the function's shell."""
    out, depth = [], 0
    i = 0
    while i < len(line):
        c = line[i]
        if c == "(" and (depth or line[i - 1 : i] == "$" or _subshell_paren(line, i)):
            depth += 1
        elif c == ")" and depth:
            depth -= 1
            i += 1
            continue
        if not depth:
            out.append(c)
        i += 1
    return "".join(out)


def _subshell_paren(line: str, i: int) -> bool:
    """A `(` that opens a subshell: at a command's start, not `x=(...)` or
    `f()`."""
    before = line[:i].rstrip()
    return before == "" or before.endswith(
        (";", "&&", "||", "|", "(", "then", "do", "else")
    )


def substitutions(line: str) -> list[str]:
    """The text inside each `$(...)` of a line, nested ones too, and each
    backquoted command."""
    found, stack = [], []
    i = 0
    while i < len(line):
        if line.startswith("$(", i):
            stack.append(i + 2)
            i += 2
            continue
        if line[i] == "(":
            stack.append(None)
        elif line[i] == ")" and stack:
            start = stack.pop()
            if start is not None:
                found.append(line[start:i])
        i += 1
    found += re.findall(r"`([^`]*)`", line)
    return found


def pipeline_members(line: str) -> list[str]:
    """Each command of a pipeline on the line, the last one too: bash runs
    every one of them in a subshell."""
    out = []
    for part in re.split(r"&&|\|\||;", _drop_substitutions(line)):
        members = re.split(r"(?<!\|)\|(?!\|)", part)
        if len(members) > 1:
            out += members
    return out


def calls(text: str, name: str) -> bool:
    return (
        re.search(COMMAND + re.escape(name) + r"(?=\s|$|;|\))", text.strip())
        is not None
    )


def recorders(text: str, defs: dict[str, tuple[int, str]]) -> dict[str, list[str]]:
    """Each function that records state, directly or through a function it
    calls in its own shell, with what it records."""
    read_first = readers(top_level(text, defs)).union(
        *(readers(body) for _, body in defs.values())
    )
    found = {name: records(body, read_first) for name, (_, body) in defs.items()}
    found = {name: r for name, r in found.items() if r}
    changed = True
    while changed:
        changed = False
        for name, (_, body) in defs.items():
            if name in found:
                continue
            for callee in list(found):
                if any(
                    calls(_drop_substitutions(line), callee) for line in _code(body)
                ):
                    found[name] = [f"{callee}()"]
                    changed = True
                    break
    return found


def offenders_in(
    text: str, defs: dict[str, tuple[int, str]]
) -> list[tuple[int, str, list[str]]]:
    """(line number, function, what it records) for each call of a
    recording function in a subshell."""
    record = recorders(text, defs)
    out = []
    for n, line in logical_lines(text):
        places = substitutions(line) + pipeline_members(line)
        for name, what in record.items():
            if any(calls(place, name) for place in places):
                out.append((n, name, what))
    return out


def sourced_definitions(path: Path, text: str, by_name: dict[str, Path]) -> dict:
    defs = {}
    for name in SOURCE.findall(text):
        lib = by_name.get(name)
        if lib is not None:
            defs.update(functions(lib.read_text(encoding="utf-8", errors="replace")))
    defs.update(functions(text))
    return defs


def offenders() -> list[str]:
    files = shell_files()
    by_name = {}
    for path in files:
        by_name.setdefault(path.name, path)
    out = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        defs = sourced_definitions(path, text, by_name)
        for n, name, what in offenders_in(text, defs):
            out.append(f"{path.relative_to(ROOT)}:{n}: {name} sets {', '.join(what)}")
    return out


def test_no_function_that_records_state_runs_in_a_subshell():
    found = offenders()
    assert found == [], (
        "each function below sets a global or a trap and is called in $(...), "
        "backquotes or a pipeline, where the change is lost; call it bare and "
        "read the global it sets:\n" + "\n".join(found)
    )


# #59's round-1 extras/docker/ci/checks.sh at ff00bbc6 (on the PR's branch
# only; the PR merged squashed): lines 58, 63-71, 148-156, 234, 246, 357
# and 372 as they were, each function closed after the lines taken from it.
ROUND_1 = """\
COMPOSE_DOWN=()
cleanup() {
    local name each
    for each in "${COMPOSE_DOWN[@]}"; do
        read -r folder file project <<<"$each"
        (cd "$folder" \\
            && { docker compose -p "$project" -f "$file" logs --no-color > "$LOGS/$project-compose.log" 2>&1 || true; } \\
            && docker compose -p "$project" -f "$file" down -v >/dev/null 2>&1) || true
    done
    COMPOSE_DOWN=()
}
compose_up() {
    (cd "$1" && docker compose -p "$3" -f "$2" up -d) >&2
    COMPOSE_DOWN+=("$1 $2 $3")
    local id
    id="$(cd "$1" && docker compose -p "$3" -f "$2" ps -q)"
    [ "$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$id")" = unless-stopped ] \\
        || fail "$2's container has no unless-stopped restart policy"
    printf '%s\\n' "$id"
}
check_timer_compose() {
    id="$(compose_up "$folder" compose.timer.yaml "$PREFIX-timer-compose")"
}
check_photostand_compose() {
    STAND="$(compose_up "$folder" compose.photostand.yaml "$PREFIX-photostand-compose")"
}
"""  # noqa: E501 (the lines as they were)


def test_round_1_checks_sh_is_found():
    """Both `$(compose_up ...)` calls, whose COMPOSE_DOWN entries the
    cleanup never saw."""
    found = offenders_in(ROUND_1, functions(ROUND_1))
    assert found == [
        (22, "compose_up", ["COMPOSE_DOWN+="]),
        (25, "compose_up", ["COMPOSE_DOWN+="]),
    ]


def test_compose_up_called_bare_passes():
    """As master's checks.sh calls it: `compose_up ...` then `$CONTAINER`."""
    text = (
        ROUND_1.replace('id="$(compose_up ', "compose_up ")
        .replace('STAND="$(compose_up ', "compose_up ")
        .replace('-compose")"', '-compose"')
    )
    assert "$(compose_up" not in text
    assert offenders_in(text, functions(text)) == []


def test_each_form_of_record_and_subshell_is_found():
    text = """\
add() { LIST+=("$1"); }
set_it() {
    local mine
    mine=1
    VALUE=$mine
}
arm() {
    trap 'rm -f "$tmp"' EXIT
}
indirect() {
    add "$1"
}
x="$(add a)"
y=`set_it`
z="$(echo "$(arm)")"
indirect b | tee log
w=$( true && indirect c )
echo "$VALUE"
"""
    assert offenders_in(text, functions(text)) == [
        (13, "add", ["LIST+="]),
        (14, "set_it", ["VALUE="]),
        (15, "arm", ["trap"]),
        (16, "indirect", ["add()"]),
        (17, "indirect", ["add()"]),
    ]


def test_each_body_is_read_to_its_own_closing_brace():
    """A brace on the next line, an inner `}` deeper in, and a heredoc's
    lines at column 0 each leave the body going on to its recording line."""
    text = """\
brace_below()
{
    BELOW+=(x)
}
nested() {
    if true; then
        {
            echo a
        }
    fi
    NESTED+=(x)
}
heredoc() {
    cat <<EOF
text
EOF
    DOC+=(x)
}
a="$(brace_below)" b="$(nested)" c="$(heredoc)"
"""
    assert offenders_in(text, functions(text)) == [
        (19, "brace_below", ["BELOW+="]),
        (19, "nested", ["NESTED+="]),
        (19, "heredoc", ["DOC+="]),
    ]


def test_the_safe_forms_pass():
    text = """\
pure() {
    local a b=2 v
    a=$(( b + 1 ))
    [ "$a" = 3 ] && echo "$a"
    ( GLOBAL=1; trap 'x' EXIT )
    v="$(OTHER=1 printf x)"
}
check() {
    OK=1
    jq -e . >/dev/null || OK=0
    [ "$OK" = 1 ] || awk '{on=1}' "$(curl -d "name=$OK")"
}
record() { LIST+=("$1"); }
x="$(pure)"
record a
pure | tee log
echo "$(( 1 + 2 ))"
curl x | check
"""
    assert offenders_in(text, functions(text)) == []


def test_a_function_from_a_sourced_library_is_read(tmp_path):
    lib = tmp_path / "common.sh"
    lib.write_text('remember() { SEEN+=("$1"); }\n')
    script = tmp_path / "suite.sh"
    text = 'source "$(dirname "$0")/common.sh"\nid="$(remember x)"\n'
    defs = sourced_definitions(script, text, {"common.sh": lib})
    assert offenders_in(text, defs) == [(2, "remember", ["SEEN+="])]


def test_the_sweep_reads_each_shell_file_and_its_library(tmp_path, monkeypatch):
    import tests.test_subshell_state as module

    (tmp_path / "lib.sh").write_text('remember() { SEEN+=("$1"); }\n')
    (tmp_path / "a.sh").write_text('. "$(dirname "$0")/lib.sh"\nid="$(remember x)"\n')
    (tmp_path / "b.sh").write_text("source missing.sh\nid=$(other)\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "shell_files", lambda: sorted(tmp_path.glob("*.sh")))
    assert module.offenders() == ["a.sh:2: remember sets SEEN+="]


@pytest.mark.parametrize(
    ("text", "blanked"),
    [
        ("awk '{on=1}' f", "awk '      ' f"),
        ('"x=$V y"', '"  $V  "'),
        ('"$(curl -d "a=1")"', '"$(curl -d "   ")"'),
        ("'$V'", "'  '"),
        ('"a\\"b=1"', '"      "'),
        ("x\\'y=1", "x\\'y=1"),
        ("'a\\' b=1", "'  ' b=1"),
        ("'a\nb'", "' \n '"),
        ('$(echo ")") c=1', '$(echo " ") c=1'),
        ('"$(f) x=1"', '"$(f)    "'),
        ("'$(f) a=1' b", "'        ' b"),
    ],
)
def test_unquoted_blanks_what_quotes_hold(text, blanked):
    assert unquoted(text) == blanked


@pytest.mark.parametrize(
    ("line", "left"),
    [
        ("a=$(f x) b", "a=$ b"),
        ("x=(1 2); y=1", "x=(1 2); y=1"),
        ("( X=1 ); Y=2", "; Y=2"),
        ("true && ( A=1 )", "true && "),
        ("a || ( A=1 )", "a || "),
        ("a | ( A=1 )", "a | "),
        ("then ( A=1 )", "then "),
        ("do ( A=1 )", "do "),
        ("else ( A=1 )", "else "),
        ("( ( A=1 ) B=2 ) C=3", " C=3"),
        ("f() { :; }", "f() { :; }"),
        ("$(a (b) c)d", "$d"),
    ],
)
def test_drop_substitutions_takes_out_each_subshell(line, left):
    assert _drop_substitutions(line) == left


@pytest.mark.parametrize(
    ("line", "found"),
    [
        ('a "$(f x)" b', ["f x"]),
        ('$(f "$(g)")', ["g", 'f "$(g)"']),
        ("$(f (x) y)", ["f (x) y"]),
        ("a `g y`", ["g y"]),
        ("echo (x)", []),
    ],
)
def test_substitutions_finds_each_command_substitution(line, found):
    assert substitutions(line) == found


@pytest.mark.parametrize(
    ("chunk", "read"),
    [
        ('echo "$A"; A=1', {"A"}),
        ('B=1; echo "$B"', set()),
        ('C=""\necho "$C"', {"C"}),
        ("D=()\necho ${D[@]}", {"D"}),
        ("E=''\necho $E", {"E"}),
        ("F=\necho $F", {"F"}),
        ("G=$(x)\necho $G", set()),
        ("echo '$H'", set()),
    ],
)
def test_readers_are_the_names_read_before_set(chunk, read):
    assert readers(chunk) == read


def test_a_local_is_no_record_though_read_elsewhere():
    text = 'f() {\n    local v\n    v=1\n    printf %s "$v"\n}\nx="$(f)"\necho "$v"\n'
    assert offenders_in(text, functions(text)) == []
    unlocal = text.replace("    local v\n", "")
    assert offenders_in(unlocal, functions(unlocal)) == [(5, "f", ["v="])]


def test_a_global_only_its_own_function_reads_first_is_a_record():
    """A log it adds to: its next call reads the value the subshell lost."""
    text = 'note() {\n    NOTES="$NOTES $1"\n}\nn=$(note a)\n'
    assert offenders_in(text, functions(text)) == [(4, "note", ["NOTES="])]
