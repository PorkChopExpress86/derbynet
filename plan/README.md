# Django migration plan tooling

The plan lives in GitHub issues on PorkChopExpress86/derbynet, labeled `django-migration`. The live issue bodies are the source of truth. This folder holds what the issues can't: the owner-decision log and the tools that check and ship the plan.

| File | Purpose |
| --- | --- |
| `HANDOFF.md` | Where to find the plan's state, how the work is run, open questions, and new-machine setup |
| `implement-all-tickets.md` | Prompt for a lead session that works through every `ready-for-agent` ticket in a loop, giving each step to fresh Opus or Sonnet subagents: implement, code review, PR, merge, retro |
| `owner-decisions.md` | Every owner decision, with the issues and ADRs that record it |
| `lint-issues.py` | Checks every plan issue body: style, section order, a ticket's Goal that asserts a cause the body marks unverified, cited paths, `#N` references, and forward-only dependencies with no cycles. Checks that issue state matches the code: no open ticket or spec that a merged PR closes, no `ready-for-agent` with an open dependency, no `blocked` with every dependency closed |
| `bin/pr-wait` | Waits for every check, in every workflow, on a PR's current head (the PR's number or its branch) that `pr-merge` counts and prints one line per check, then the parity-check push checks it leaves out |
| `bin/ci-failures` | Lists each test that failed on a PR's checks, `known` when it also failed on a recent master run (a flake already on master) and `new` otherwise, with a load-run command for a `new` test whose file the PR does not change, and for a job with no test id its failed step and the error line the jobs share, with an `outage:` line when every job died in setup on that line; `pr-wait` prints it on a failure |
| `bin/issue` | Prints an issue's title, body, and comments, because `gh issue view --comments` prints nothing on some gh versions |
| `bin/ci-suites` | Lists each `testing/` suite an issue names and whether `.github/workflows/server.yml` runs it; exits 1 when one is missing |
| `bin/pr-merge` | Merges a PR only if every check on its current head passed, pinned to that head SHA. Refuses one that would close an issue with a `## Still open` section, and one whose area (plan/, .githooks/, server/tools/, .github/, a conftest.py, server/pyproject.toml, server/uv.lock) origin/master changed since its merge base, until `--base-checked` says the combined diff's affected tests ran; then prints each issue the PR closes with its state and exits 1 if one is still open |

## Edit an issue body

```bash
gh issue view N --repo PorkChopExpress86/derbynet --json body --jq .body > /tmp/bodyN.md
# edit /tmp/bodyN.md
python3 -I plan/lint-issues.py --file N=/tmp/bodyN.md
gh issue edit N --repo PorkChopExpress86/derbynet --body-file /tmp/bodyN.md
```

A ticket's Goal states the symptom to make pass, and leaves the mechanism to the first Scope box. When Current PHP behavior or Scope marks an observation or a cause unverified ("not yet verified", "observed once", "not reproduced", "suspect"), the lint flags each Goal line that asserts a cause as fact ("can land", "because", "caused by", "due to"). #282's Goal said CI's run "can land it on one worker" while its Current PHP behavior called the failure "observed once" and its suspect "not yet verified", and the mechanism proved false.

A body for an issue not created yet lints with `--file 0=PATH`, as a new open issue with the labels `--labels kind:ticket,ready-for-agent` names (default `kind:ticket`). `plan/tests/` tests the lint against a fake `gh`; CI runs it.

The lint reads every other issue live, so a reference or dependency is checked against the whole plan. It needs `gh` on `PATH`, or `GH=/path/to/gh`. CI runs it in `.github/workflows/plan-checks.yml`.

The lint counts a PR as merged when GitHub says so, or when GitHub shows it closed and its head commit is on `origin/master`, as when a PR is merged by hand. A PR closes an issue through GitHub's closing references or a `Closes #N`, `Fixes #N` or `Resolves #N` in its body. Two sections, placed anywhere in a body, tell the lint that a state is meant:

- `## Still open` keeps an issue open after its work has merged. Write what is left under it, such as the acceptance boxes still to tick. A ticket that depends on it counts the dependency as done.
- `## Waiting on` keeps `blocked` on a ticket whose dependencies are all closed. Write what it waits on under it, such as an owner decision.

## Ship a PR

```bash
plan/bin/pr-wait 82     # exits 1 if any check pr-merge counts failed
plan/bin/pr-merge 82     # squashes and deletes the branch unless given a method
```
