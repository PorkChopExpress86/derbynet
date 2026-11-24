#!/usr/bin/env python3
"""PreToolUse hook: refuse a background Bash command while the same command
already runs in the background.

#446, #400, #470 and #48 each started a second background waiter on a run
already being waited on (their retro lists), and each copy later woke the
agent with the same notification. Claude Code runs a background command as
`bash -c '... eval '<command>' < /dev/null ...'`, so a live copy shows up
in /proc/*/cmdline with the command quoted that way. When the eval form
changes, the hook finds nothing and lets every call through.
"""

import json
import os
import sys


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
    if tool_input.get("run_in_background") is not True:
        return
    command = tool_input.get("command") or ""
    pid = running(quoted(command).encode())
    if pid is None:
        return
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"The same background command already runs as pid {pid}. "
                    "Wait for its notification instead of starting a second "
                    "copy; stop that one first if you need to restart it."
                ),
            }
        },
        sys.stdout,
    )


if __name__ == "__main__":
    main()
