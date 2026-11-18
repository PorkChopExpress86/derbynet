"""No shell function that records state for the script runs in a subshell:
inside `$(...)`, backquotes or a pipeline.

The subshell gets a copy of the script's variables and traps, and its
changes end with it. #59's round-1 `extras/docker/ci/checks.sh` ran
`id="$(compose_up ...)"`, and compose_up's `COMPOSE_DOWN+=(...)` went with
the subshell, so `cleanup` took no Compose project down and a
`t59-timer-compose` container was left (#59 review, retro).

A function records state when, outside a quoted string and outside a
subshell of its own, it appends to a variable it has not declared `local`,
sets a trap, assigns a global that code outside it reads before setting, or
calls a function that does. A function that prints its result for `$(...)`
takes its state from its caller instead, or the caller runs it bare and
reads the global it sets, as checks.sh's compose_up now does.

The rule reads the script's text, not a bash parse: a function read across
files is found only through a `source` or `.` line naming a .sh file by its
basename, and a call is a name in command position.
"""

import re
from pathlib import Path

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


def records(body: str, read_elsewhere: set[str]) -> list[str]:
    """What the body changes in its caller's shell, in order: each append to
    a global (`NAME+=`, `NAME[i]=`), each trap it sets, and each global it
    assigns that code elsewhere reads (`read_elsewhere`). A global set and
    read only inside functions that each set it first, such as common.sh's
    OK, is scratch, and a subshell loses nothing by it."""
    local = locals_of(body)
    out = []
    for line in _code(unquoted(body)):
        line = _drop_substitutions(line)
        for m in ASSIGNMENT.finditer(line):
            name = m.group(1)
            append = line[m.end() - 2] in "+]"
            if name not in local and (append or name in read_elsewhere):
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
        read |= {n for n in READ.findall(line) if n not in assigned}
        code = _drop_substitutions(line)
        for m in ASSIGNMENT.finditer(code):
            if re.match(r"[^\s;]*", code[m.end() :]).group() not in EMPTY:
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
        if c == "(" and (
            depth or (i and line[i - 1] == "$") or _subshell_paren(line, i)
        ):
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
        if line.startswith("$((", i):
            stack.append(None)
            i += 3
            continue
        if line.startswith("$(", i):
            stack.append(i + 2)
            i += 2
            continue
        if line[i] == "(" and stack:
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
    reads = {name: readers(body) for name, (_, body) in defs.items()}
    outside = readers(top_level(text, defs))
    found = {}
    for name, (_, body) in defs.items():
        elsewhere = outside.union(*(r for other, r in reads.items() if other != name))
        found[name] = records(body, elsewhere)
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
        if DEFINITION.match(line):
            continue
        places = substitutions(line) + pipeline_members(line)
        for name, what in record.items():
            if any(calls(place, name) for place in places):
                out.append((n, name, what))
    return out


def sourced_definitions(path: Path, text: str, by_name: dict[str, Path]) -> dict:
    defs = {}
    for name in SOURCE.findall(text):
        lib = by_name.get(name)
        if lib is not None and lib != path:
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
