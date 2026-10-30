# Django migration plan tooling

The plan lives in GitHub issues on PorkChopExpress86/derbynet, labeled `django-migration`. The live issue bodies are the source of truth. This folder holds what the issues can't: the owner-decision log and the tools that check and ship the plan.

| File | Purpose |
| --- | --- |
| `HANDOFF.md` | Where the migration stands, what comes next, how the work is run, and new-machine setup |
| `owner-decisions.md` | Every owner decision, with the issues and ADRs that record it |
| `lint-issues.py` | Checks every plan issue body: style, section order, cited paths, `#N` references, and forward-only dependencies with no cycles. Checks that issue state matches the code: no open ticket or spec that a merged PR closes, no `ready-for-agent` with an open dependency, no `blocked` with every dependency closed |
| `bin/pr-wait` | Waits for every check on a PR's current head and prints one line per check |
| `bin/issue` | Prints an issue's title, body, and comments, because `gh issue view --comments` prints nothing on some gh versions |
| `bin/pr-merge` | Merges a PR only if every check on its current head passed, pinned to that head SHA, then prints each issue the PR closes with its state and exits 1 if one is still open |

## Edit an issue body

```bash
gh issue view N --repo PorkChopExpress86/derbynet --json body --jq .body > /tmp/bodyN.md
# edit /tmp/bodyN.md
python3 -I plan/lint-issues.py --file N=/tmp/bodyN.md
gh issue edit N --repo PorkChopExpress86/derbynet --body-file /tmp/bodyN.md
```

The lint reads every other issue live, so a reference or dependency is checked against the whole plan. It needs `gh` on `PATH`, or `GH=/path/to/gh`. CI runs it in `.github/workflows/plan-checks.yml`.

The lint counts a PR as merged when GitHub says so, or when GitHub shows it closed and its head commit is on `origin/master`, as when a PR is merged by hand. A PR closes an issue through GitHub's closing references or a `Closes #N`, `Fixes #N` or `Resolves #N` in its body. Two sections, placed anywhere in a body, tell the lint that a state is meant:

- `## Still open` keeps an issue open after a merged PR closes it. Write what is left under it, such as the acceptance boxes still to tick.
- `## Waiting on` keeps `blocked` on a ticket whose dependencies are all closed. Write what it waits on under it, such as an owner decision.

## Ship a PR

```bash
plan/bin/pr-wait 82     # exits 1 if any check failed
plan/bin/pr-merge 82 --squash
```
