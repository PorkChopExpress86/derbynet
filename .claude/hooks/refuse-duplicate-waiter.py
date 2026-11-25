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
(#500 retro). A loop inside a quoted string or a heredoc body is left
alone, as a script being written, and so is a `for` loop.
"""

import json
import os
import re
import sys

FOREGROUND_WAIT = re.compile(
    r"\b(?:until|while)\b.*?\bdo\b.*?\bsleep\b.*?\bdone\b", re.S
)


def code_of(command):
    """`command` less its heredoc bodies and quoted strings, as
    .githooks/pre-push reads a shell's script."""
    heredoc = r"<<-?\s*(['\"]?)(\w+)\1([^\n]*)\n.*?\n\s*\2(?=\n|$)"
    command = re.sub(heredoc, r"\3", command, flags=re.S)
    return re.sub(r"'[^']*'|\"(?:\\.|[^\"\\])*\"", "''", command)


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
        if FOREGROUND_WAIT.search(code_of(command)):
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
