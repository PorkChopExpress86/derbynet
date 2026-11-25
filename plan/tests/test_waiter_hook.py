""".claude/hooks/refuse-duplicate-waiter.py refuses a foreground `until` or
`while` loop that sleeps, the waiter #409, #463 and the #494 and #499
retros typed on a run's log, as #500 did (#500 retro).

Run: python3 -I -m unittest discover -s plan/tests
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

import k_patterns  # noqa: F401  a -k that matches no test is refused

HOOK = Path(__file__).resolve().parents[2] / '.claude' / 'hooks' / 'refuse-duplicate-waiter.py'

# The waiters, as the agents typed them, but the last.
WAITERS = (
    'until grep -q "arm B" ~/.cache/derbynet-drafts/409/repro/summary.txt; do sleep 10; done; '
    'cat ~/.cache/derbynet-drafts/409/repro/summary.txt',
    'until grep -q "^cpu8 exit" ~/.cache/derbynet-drafts/463/logs/s1-cpu8.log; do sleep 10; done',
    "until grep -q '^push exit' ~/.cache/derbynet-drafts/retro-499/push1.log; do sleep 5; done; "
    "grep -E '^push exit|FAILED' ~/.cache/derbynet-drafts/retro-499/push1.log | tail -5",
    'until grep -q "^exit " ~/.cache/derbynet-drafts/retro-494/push.log 2>/dev/null; '
    'do sleep 15; done; tail -4 ~/.cache/derbynet-drafts/retro-494/push.log',
    # #500's own was an `until grep ...; do sleep 10; done` on its push log;
    # this is its `while` form, with `do` on a line of its own.
    'while ! grep -q "^exit" push.log\ndo\n  sleep 10\ndone',
)


def decision(command, background=False):
    """The hook's decision for a Bash call: 'deny', or None to let it run."""
    call = {'tool_name': 'Bash',
            'tool_input': {'command': command, 'run_in_background': background}}
    done = subprocess.run([sys.executable, '-I', str(HOOK)], input=json.dumps(call),
                          capture_output=True, text=True, check=True)
    if not done.stdout:
        return None
    return json.loads(done.stdout)['hookSpecificOutput']['permissionDecision']


class ForegroundWaiter(unittest.TestCase):
    def test_each_waiter_typed_in_the_foreground_is_refused(self):
        for command in WAITERS:
            with self.subTest(command=command):
                self.assertEqual(decision(command), 'deny')

    def test_a_waiter_on_a_heredoc_s_first_line_is_refused(self):
        """The rest of the line that opens a heredoc runs, as its body does not."""
        command = "cat > note <<'EOF'; until grep -q x log; do sleep 5; done\nbody\nEOF"
        self.assertEqual(decision(command), 'deny')

    def test_the_same_loop_in_the_background_runs(self):
        for command in WAITERS:
            with self.subTest(command=command):
                self.assertIsNone(decision(command, background=True))

    def test_a_loop_that_does_not_sleep_a_for_loop_and_a_loop_in_a_string_run(self):
        for command in (
            'while read -r line; do echo "$line"; done < list.txt',
            'for _ in $(seq 30); do curl -sf localhost && break; sleep 1; done',
            "python3 -c 'while True:\n    time.sleep(1)'",
            "cat > w.sh <<'EOF'\nuntil grep -q done log; do sleep 5; done\nEOF\nchmod +x w.sh",
            'git commit -m "until the run is done, sleep"',
            'sleep 1',
            # A word, an option or a comment is no loop header.
            'git log --until=yesterday; for i in 1 2; do sleep 1; done',
            '# wait until the server is up\nfor _ in $(seq 3); do curl -sf x && break; sleep 1; done',
            None,
        ):
            with self.subTest(command=command):
                self.assertIsNone(decision(command))


if __name__ == '__main__':
    unittest.main()
