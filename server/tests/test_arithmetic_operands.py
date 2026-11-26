"""Every name a tool script reads as a number holds one the script made or
checked.

Bash drops the whole command on an arithmetic error, `$((10#1abc))` or
`$((MINUTES * 60))` with MINUTES=1m, and goes on with the next one with
no failed status, so neither `set -e` nor an ERR trap sees it (bash 5.2 on
CI's Ubuntu 24.04 and 5.3 here).  #417's round 3 took `1abc.2` in the
line folder as an entry; `$((10#${last%%.*}))` dropped `line_join`, ENTRY
stayed empty, and the run started its command while another held the lock
(#417 retro).  `tools/load-run.sh 1m` dropped its load test and exited 0
under `Report:`.  An integer test on such a value, `[ "$w" -ge "$X" ]`,
prints an error and reads as false, so a loop bounded by it runs on.

A name read in `$(( ))`, `(( ))`, `let`, an array subscript, or beside
`-eq`, `-ne`, `-lt`, `-le`, `-gt` or `-ge` passes when each assignment to it
in the file is a number the script made (digits, `$(( ))`, `$(date +%s)`,
`$SECONDS`, `$!`, `$$`, `$?`, `$#`, `${#...}`) or when the file checks it
for a whole number with no leading zero, which bash reads as octal
(`case "$X" in '' | *[!0-9]* | 0?*)`, or `[[ $X =~ ^(0|[1-9][0-9]*)$ ]]`).  Any
other value read as a number has a row in KNOWN saying why it is one; a row
that matches no read fails, and a row whose reason is a line of code holds
only while REASON_IN_CODE finds that line in the file.  A positional
parameter, `$1` or `${1:-30}`, is read as the name `1`, and passes only when
the file checks it, since no assignment makes it.

The rule reads one line at a time, so an expression split over lines,
`x=$((` then `N + 1` then `))`, is not read.  None of the scripts writes one.

The rule covers the scripts that run or gate this repo's agents' work:
locks, waits, hand-offs, hooks and CI steps.  The PHP suite's scripts under
testing/ and the upstream installers stay as they are.
"""

import re

import pytest

from tests.file_kinds import shell_files
from tests.test_pipefail_pipes import ROOT

SCOPE = (
    ".githooks/",
    ".github/",
    ".claude/skills/verify/bin/",
    "plan/bin/",
    "server/tools/",
    "server/docker/",
    "testing/docker/",
)
# (file, what it reads as a number): why it is one.
KNOWN = {
    ("server/tools/load-lock-line.sh", "${last%%.*}"): (
        "line_scan lists only names ENTRY_NAME matches, ten digits before the dot"
    ),
    ("server/tools/pytest-under-load.sh", "pid"): "$PPID, then `ps -o ppid=`",
    ("server/tools/source-checks.sh", "pid"): "$PPID, then `ps -o ppid=`",
    ("server/docker/ci/checks.sh", "size"): "padded_jpeg's callers pass digits",
    ("server/docker/ci/checks.sh", '$(stat -c %s "$photo")'): "stat's %s is a size",
    ("server/docker/ci/checks.sh", "BASH_REMATCH"): "the =~ above matched ([0-9]+)$",
    ("server/docker/ci/checks.sh", "lines"): "`wc -l` prints a count",
    ("server/docker/ci/guide-commands.sh", "WAIT_SECONDS"): (
        "a bad value drops the wait's loop, and the fail after it runs"
    ),
    (
        "server/docker/ci/guide-commands.sh",
        "before",
    ): "events prints Event.objects.count()",
    ("server/docker/entrypoint.sh", "value"): (
        "=~ ^[0-9]{1,10}$ above it, and each read is 10#$value, so no octal"
    ),
    ("server/docker/entrypoint.sh", "uid"): "checked as `value` by =~ ^[0-9]{1,10}$",
    ("server/docker/entrypoint.sh", "gid"): "checked as `value` by =~ ^[0-9]{1,10}$",
}
NAME = r"[A-Za-z_]\w*"
# A name, or a positional parameter's digit: `$1` reads the command line.
PARAM = rf"(?:{NAME}|\d)"
# A value that is a number whatever the script's input: the whole word.
MADE = re.compile(
    r"""^(["']?)(?:\d+|\$\(\(.*\)\)|\$\(date \+%s%?N?\)|\$SECONDS|\$\{SECONDS\}"""
    r"""|\$[!$?#]|\$\{#[^}]*\})\1$"""
)
SPECIAL = {"SECONDS"}
# `${NAME}` or `${NAME:-7}`: the name itself, or the name with a number for
# its default.  Any other operator, `${x%%.*}`, makes a new value.
PLAIN_EXPANSION = re.compile(rf"\$\{{({PARAM})(?::?-\d+)?\}}|\$({PARAM})")
INTEGER_TEST = re.compile(
    r"""("[^"]*"|'[^']*'|\S+)\s+-(?:eq|ne|lt|le|gt|ge)\s+("[^"]*"|'[^']*'|\S+)"""
)


HEREDOC = re.compile(r"(?<!<)<<-?(?!<)\s*(['\"]?)(\w+)\1")


# The code a KNOWN row's reason stands on.  #417's round 3 read the ticket
# from a glob, `[0-9]*.[0-9]*`, which took `1abc.2` (23ca6006).
REASON_IN_CODE = {
    ("server/tools/load-lock-line.sh", "${last%%.*}"): re.compile(
        r"ENTRY_NAME='\^\[0-9\]\{10\}\\\.\[0-9\]\+\$'\n(?s:.*)"
        r"\[\[ \$\{entry##\*/\} =~ \$ENTRY_NAME \]\] \|\| continue"
    ),
}


def unexplained(name: str, text: str) -> list[tuple[int, str]]:
    """The offenders in file `name` that no KNOWN row explains."""
    return [
        (n, read)
        for n, read in offenders_in(text)
        if (name, read) not in KNOWN
        or (
            (name, read) in REASON_IN_CODE
            and not REASON_IN_CODE[name, read].search(text)
        )
    ]


def _unquoted(line: str) -> str:
    """The line with each single-quoted string's text and closing quote
    taken out: bash expands nothing in one, so `jq '((.a))'` reads no
    number.  A `'` inside double quotes, or after a backslash, opens none."""
    out, single, double, i = [], False, False, 0
    while i < len(line):
        c = line[i]
        if single:
            single = c != "'"
        elif c == "\\":
            out.append(line[i : i + 2])
            i += 1
        else:
            double ^= c == '"'
            single = c == "'" and not double
            out.append(c)
        i += 1
    return "".join(out)


def lines_of(text: str) -> list[tuple[int, str]]:
    """(line number, code) for each line outside a heredoc body: the code
    without its single-quoted strings and its comment."""
    raw = text.splitlines()
    found = []
    ends: list[str] = []
    for i, line in enumerate(raw):
        if ends:
            if line.strip() == ends[0]:
                ends.pop(0)
            continue
        code = re.sub(r"(^|\s)#.*$", r"\1", _unquoted(line))
        ends = [
            m.group(2) for m in HEREDOC.finditer(re.sub(r"(^|\s)#.*$", r"\1", line))
        ]
        found.append((i + 1, code))
    return found


def _closing(text: str, start: int, opener: str, closer: str) -> int:
    """The index just past the `closer` that balances the `opener` at `start`."""
    depth = 0
    i = start
    while i < len(text):
        if text.startswith(opener, i):
            depth += 1
            i += len(opener)
            continue
        if text[i] == "(":
            depth += 1
        elif text.startswith(closer, i) and depth == 1:
            return i + len(closer)
        elif text[i] == ")":
            depth -= 1
        i += 1
    return len(text)


def arithmetic(code: str) -> list[str]:
    """Each arithmetic expression on a line: `$(( ))`, a command `(( ))`, a
    `let`'s arguments, and an indexed array subscript."""
    found = []
    i = 0
    while (start := code.find("((", i)) != -1:
        end = _closing(code, start, "((", "))")
        found.append(code[start + 2 : end - 2])
        i = end
    if m := re.search(r"(?:^|[;&|]\s*|\b(?:then|do|else)\s+)let\s+(.*)", code):
        found.append(m.group(1))
    found += re.findall(rf"\$\{{!?{NAME}\[([^]@*][^]]*)\]", code)
    return found


def _operands_of(expression: str, bare: bool) -> list[str]:
    """The values an expression reads as numbers: `NAME` for a plain
    expansion (or a bare name when `bare`), or the text of any other
    expansion or command substitution."""
    expression = re.sub(
        r"\$\(date \+%s%?N?\)|\$\{#[^}]*\}|\$[!$?#]|\b\d+#", " ", expression
    )
    read = []
    for m in re.finditer(
        r"\$\{[^}]*\}|\$\([^)]*\)?|`[^`]*`|\$" + PARAM + "|" + NAME, expression
    ):
        word = m.group(0)
        plain = PLAIN_EXPANSION.fullmatch(word)
        if plain:
            read.append(plain.group(1) or plain.group(2))
        elif word.startswith(("$", "`")):
            read.append(word)
        elif bare:
            read.append(word)
    return read


def numbers_read(code: str) -> list[str]:
    read = []
    for expression in arithmetic(code):
        read += _operands_of(expression, bare=True)
    stripped = re.sub(r"\$\(\(.*?\)\)", " 0 ", code)
    for m in INTEGER_TEST.finditer(stripped):
        for operand in m.groups():
            read += _operands_of(operand.strip("\"'"), bare=False)
    return read


def _word_after(code: str, start: int) -> str:
    """The shell word that starts at `start`, with `$(...)` kept whole.  A
    space inside double quotes ends it too, which leaves the word with an
    open `"` that MADE never matches."""
    i = start
    while i < len(code):
        if code.startswith("$(", i):
            i = _closing(code, i + 1, "(", ")")
            continue
        if code[i].isspace() or code[i] in ";&|)":
            break
        i += 1
    return code[start:i]


def assignments(text: str) -> dict[str, list[str]]:
    """Each name's assigned values in the file; `None` stands for a value no
    word shows (`read`, a `for` over words).  `n+=x` and `n[2]=x` count as
    the word they assign."""
    found: dict[str, list] = {}
    for _, code in lines_of(text):
        for m in re.finditer(
            rf"(?:^|[\s;&|({{]|\blocal\s|\bexport\s|\breadonly\s)({NAME})(?:\[[^]]*\])?\+?=",
            code,
        ):
            before = code[: m.start(1)].rstrip()
            if before.endswith(("let", "((")):
                continue
            found.setdefault(m.group(1), []).append(_word_after(code, m.end()))
        for m in re.finditer(rf"\bfor\s+({NAME})\s+in\s+(.*?)(?:;|$)", code):
            words = m.group(2).split()
            numbers = all(re.fullmatch(r"\d+", w) for w in words) or m.group(
                2
            ).startswith("$(seq ")
            found.setdefault(m.group(1), []).append("0" if numbers else None)
        for m in re.finditer(r"\bread\s+((?:-\w+\s+(?:\S+\s+)?)*)([^;&|<>]*)", code):
            for name in re.findall(NAME, m.group(2)):
                found.setdefault(name, []).append(None)
        for m in re.finditer(rf"\(\(\s*({NAME})\s*(?:[-+*/%]?=|\+\+|--)", code):
            found.setdefault(m.group(1), []).append("0")
        for m in re.finditer(rf"\blet\s+({NAME})", code):
            found.setdefault(m.group(1), []).append("0")
    return found


def checked(text: str) -> set[str]:
    """The names the file checks for a whole number: digits, and no leading
    zero, since `08` is no octal number (#417's `10#`)."""
    names = set()
    lines = [code for _, code in lines_of(text)]
    for i, code in enumerate(lines):
        if m := re.search(rf"\bcase\s+\"?\$\{{?({PARAM})\}}?\"?\s+in\b", code):
            block = " ".join(lines[i : i + 6])
            if "[!0-9]" in block and "0?*" in block:
                names.add(m.group(1))
        pattern = r"\s*=~\s*['\"]?\^(?:\(0\|)?\[1-9\]"
        for m in re.finditer(rf"\$\{{?({PARAM})\}}?\"?{pattern}", code):
            names.add(m.group(1))
    return names


def offenders_in(text: str) -> list[tuple[int, str]]:
    made = assignments(text)
    safe = checked(text)
    found = []
    for number, code in lines_of(text):
        for read in numbers_read(code):
            if read in safe or read in SPECIAL:
                continue
            values = made.get(read)
            if values and all(v is not None and MADE.match(v) for v in values):
                continue
            found.append((number, read))
    return found


def scripts():
    return [p for p in shell_files() if str(p.relative_to(ROOT)).startswith(SCOPE)]


def test_the_rule_reads_the_tool_scripts():
    names = {str(p.relative_to(ROOT)) for p in scripts()}
    assert {
        "server/tools/load-lock-line.sh",
        "plan/bin/pr-wait",
        ".githooks/pre-push",
    } <= names


@pytest.mark.parametrize("path", scripts(), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_number_a_tool_script_reads_is_one_it_made_or_checked(path):
    name = str(path.relative_to(ROOT))
    found = unexplained(name, path.read_text(encoding="utf-8"))
    assert not found, (
        f"{path.relative_to(ROOT)} reads these as numbers with no check that they"
        f" are: {found}.  A bad value drops the whole command with no failed"
        ' status (#417 retro).  Check each for a whole number (`case "$X" in'
        " '' | *[!0-9]* | 0?*)"
        " usage ;; esac`), or say why it is one in a KNOWN row."
    )


def test_each_known_row_names_a_read_that_is_there():
    reads = {
        (str(p.relative_to(ROOT)), read)
        for p in scripts()
        for _, read in offenders_in(p.read_text(encoding="utf-8"))
    }
    assert set(KNOWN) <= reads, (
        f"KNOWN rows that match no read: {sorted(set(KNOWN) - reads)}"
    )


@pytest.mark.parametrize(
    ("text", "found"),
    [
        # #417 round 3 (23ca6006): a file name from a glob.
        (
            "local count=${#E[@]} last=0\nlast=${E[count - 1]##*/}\n"
            "last=$((10#${last%%.*}))\nX=$((last + 1))\n",
            [(3, "${last%%.*}"), (4, "last")],
        ),
        # tools/load-run.sh before this retro: an argument.
        ('MINUTES="${1:-30}"\nrun --duration "$((MINUTES * 60))"\n', [(2, "MINUTES")]),
        (
            'W=${CLOSE_WAIT:-30}\nwaited=0\n[ "$waited" -ge "$W" ] && break\n',
            [(3, "W")],
        ),
        ('[ "$(grep -c x f)" -gt 0 ]\n', [(1, "$(grep -c x f)")]),
        ("read -r n rest\necho $((n + 1))\n", [(2, "n")]),
        ("(( count++ ))\necho $((count + 1))\n", []),
        (
            "s=${3:-160}\ncase \"$s\" in\n  '' | *[!0-9]* | 0?*) usage ;;\nesac\n"
            "d=$((s + 1))\n",
            [],
        ),
        ("[[ $n =~ ^(0|[1-9][0-9]*)$ ]] || exit 2\necho $((n * 2))\n", []),
        ("[[ $n =~ ^[1-9][0-9]*$ ]] || exit 2\necho $((n * 2))\n", []),
        # `08` passes these, and is no octal number.
        ("[[ $n =~ ^[0-9]+$ ]] || exit 2\necho $((n * 2))\n", [(2, "n")]),
        (
            "s=${3:-160}\ncase \"$s\" in\n  '' | *[!0-9]*) usage ;;\nesac\n"
            "d=$((s + 1))\n",
            [(5, "s")],
        ),
        ('since=$(date +%s)\necho "$(( $(date +%s) - since )) s"\n', []),
        ("started=$SECONDS\necho $((SECONDS - started))\n", []),
        ("n=$(f)\necho $((n + 1))\n", [(2, "n")]),
        ("jq '((.a | length) > 1)' f\n", []),
        ("python3 - <<'EOF'\nre.search(r'((x))', s)\nEOF\necho $((n))\n", [(4, "n")]),
        ('x=$(tr a b <<<"$y")\necho $((x))\n', [(2, "x")]),
        (
            'pid=$PPID\nfor _ in 1 2 3; do\n  [ "${pid:-0}" -gt 1 ] || break\n'
            '  pid=$(ps -o ppid= -p "$pid")\ndone\n',
            [(3, "pid")],
        ),
        (
            "for _ in $(seq 60); do :; done\n"
            "for i in 1 2 3; do echo $((i + 1)); done\n",
            [],
        ),
        ("for f in *.txt; do echo $((f + 1)); done\n", [(1, "f")]),
        ('if [ $# -eq 0 ]; then :; fi\n[ "${#a[@]}" -gt 0 ]\n', []),
        ("let LANE+=1\necho $((LANE))\n", []),
        # A positional parameter is read as the name of its digit.
        ("echo $(( $1 * 60 ))\n", [(1, "1")]),
        ('[ "$1" -gt 5 ]\n', [(1, "1")]),
        ("(( $2 > 3 ))\n", [(1, "2")]),
        ("echo $(( ${1:-30} * 60 ))\n", [(1, "1")]),
        ('case "$1" in *[!0-9]* | 0?*) exit 2 ;; esac\necho $(( $1 * 60 ))\n', []),
        ("[[ $1 =~ ^[1-9][0-9]*$ ]] || exit 2\necho $(( $1 * 60 ))\n", []),
        ('x="a"\n[ "$x" = 1 ]\n', []),
        ('echo "it\'s $((n))"\n', [(1, "n")]),
        ("echo \\' $((n))\n", [(1, "n")]),
        ('echo "a" \'((x))\' "b" $((n))\n', [(1, "n")]),
        ("let n=m+1\n", [(1, "m")]),
        ("[ 3 -eq 3 ] && [ on -eq 1 ]\n", []),
        ('since="$(date +%s)"\necho $((since))\n', []),
        ('n="1"; m=$(f)\necho $((n))\n', []),
        ("n=1; echo $((n))\n", []),
        ("n=$(f a)x\necho $((n))\n", [(2, "n")]),
        ("n=1\nn+=x\necho $((n))\n", [(3, "n")]),
        ("n=1\nn[2]=x\necho $((n))\n", [(3, "n")]),
        ("for i in $(seq 3); do echo $((i)); done\n", []),
        ("n=1\nread -r n\necho $((n))\n", [(3, "n")]),
        ('case "$n" in\n  start) ;;\nesac\necho $((n))\n', [(4, "n")]),
        ('case "$n" in\n  0?*) usage ;;\nesac\necho $((n))\n', [(4, "n")]),
        ('n="1 2"\necho $((n))\n', [(2, "n")]),
    ],
)
def test_offenders_are_the_numbers_read_with_no_check(text, found):
    assert offenders_in(text) == found


def test_a_read_with_no_known_row_is_unexplained():
    assert unexplained("server/tools/x.sh", "read -r n\necho $((n))\n") == [(2, "n")]


def test_the_lock_line_as_round_3_read_it_is_refused():
    """23ca6006's line_join, the glob that took `1abc.2` and all."""
    name = "server/tools/load-lock-line.sh"
    now = (ROOT / name).read_text(encoding="utf-8")
    assert unexplained(name, now) == []
    glob = now.replace('"$LINE"/*; do', '"$LINE"/[0-9]*.[0-9]*; do')
    round_3 = glob.replace("    [[ ${entry##*/} =~ $ENTRY_NAME ]] || continue\n", "")
    assert round_3 != glob != now
    assert [read for _, read in unexplained(name, round_3)] == ["${last%%.*}"]
