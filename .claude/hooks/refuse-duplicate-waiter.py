#!/usr/bin/env python3
"""PreToolUse hook: refuse a background Bash command while the same command
already runs in the background, and a foreground `until` or `while` loop
that sleeps.

#446, #400, #470 and #48 each started a second background waiter on a run
already being waited on (their retro lists), and each copy later woke the
agent with the same notification. Claude Code runs a background command as
`bash -c '... eval '<command>' < /dev/null ...'`, so a live copy shows up
in /proc/*/cmdline with the command quoted that way. When the eval form
changes, the hook finds nothing and lets every call through.

A foreground loop that sleeps until a file says a run ended is a waiter
too: the Bash tool moves it to the background after about 3 min, where it
is one more waiter (plan/AGENT-BRIEF.md, Evidence). #409, #463, the #499
retro and #500 each typed one on a run's log; #500's was TaskStopped
(#500 retro). A loop inside a quoted string, a heredoc body or a comment
is left alone, as a script being written, and so is a `for` loop. A
`sleep` counts only inside the loop's own `do ... done`, so a sleep after
a `while read` loop is no waiter. A counter loop, whose condition is one
comparison with a literal of at most 60 (`while [ $i -lt 5 ]`), runs as a
`for _ in $(seq 5)` loop does; one joined with `||` or `&&` to a check,
`until grep ... || [ $i -ge 30 ]`, is a waiter (PR #517's review).

A hook matched on `Bash` alone also gets each Monitor command, named
`Bash` and with only its command and description, so the loop rule
refused the Monitor watch its own message names (#501 and #514 retro).
The matcher names Monitor too, and Claude Code then sends a Monitor call
under its own name, which this hook lets through.
"""

import json
import os
import re
import sys

# A loop's header, its `do` after a `;` or a newline as in
# plan/tests/test_stub_loops.py's HEAD.
HEADER = re.compile(r"(?<![\w-])(?:until|while)\b(?P<condition>[^\n;]*)(?:;|\n)\s*do\b")
COUNTER = re.compile(r"\[\s*\S+\s+-(?:lt|le|ge|gt)\s+(\d+)\s*\]")
DO_DONE = re.compile(r"\b(do|done)\b")
MAX_COUNT = 60


def waits(code):
    """Whether `code` holds an `until` or `while` loop whose own body sleeps,
    other than a counter loop."""
    for header in HEADER.finditer(code):
        counter = COUNTER.fullmatch(header["condition"].strip())
        if counter and int(counter[1]) <= MAX_COUNT:
            continue
        depth = 1
        for word in DO_DONE.finditer(code, header.end()):
            depth += 1 if word[1] == "do" else -1
            if depth == 0:
                if re.search(r"\bsleep\b", code[header.end() : word.start()]):
                    return True
                break
    return False


def code_of(command):
    """`command` less its heredoc bodies, quoted strings and comments, as
    .githooks/pre-push reads a shell's script."""
    heredoc = r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2(?=\n|$)"
    command = re.sub(heredoc, r"\3", command, flags=re.S)
    command = re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", command)
    return re.sub(r"(^|\s)#[^\n]*", r"\1", command)


def deny(reason):
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def quoted(command):
    return "eval '" + command.replace("'", "'\"'\"'") + "' < /dev/null"


def running(needle):
    me = os.getuid()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != me:
                continue
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if args[:2] == [b"/bin/bash", b"-c"] and needle in args[2]:
            return pid
    return None


def main():
    call = json.load(sys.stdin)
    if call.get("tool_name") != "Bash":
        return
    tool_input = call.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if tool_input.get("run_in_background") is not True:
        if waits(code_of(command)):
            deny(
                "A foreground until or while loop that sleeps is a waiter, which "
                "the Bash tool moves to the background after about 3 min "
                "(plan/AGENT-BRIEF.md, Evidence; #500 retro). Start the run itself "
                "with run_in_background and wait for its notification, or watch "
                "for the condition with the Monitor tool."
            )
        return
    pid = running(quoted(command).encode())
    if pid is None:
        return
    deny(
        f"The same background command already runs as pid {pid}. "
        "Wait for its notification instead of starting a second "
        "copy; stop that one first if you need to restart it."
    )


if __name__ == "__main__":
    main()
