# Briefing an agent on a ticket

A delegate reads `CLAUDE.md` on its own. A brief names the ticket and what is specific to it, and leaves out what this file and `CLAUDE.md` already say.

## Rules for every agent

- Never use the fable model, and never spawn a subagent on it. Use opus for the hardest work.
- Work in its own git worktree, on a branch named `issue-<n>-<slug>`. Check `git branch --show-current` before any pull or merge in the main clone, because other sessions use it.
- Commit with `git -c user.name=PorkChopExpress86 -c user.email=<owner email> commit`, and end each message with the `Co-Authored-By` line the session gives. Push the branch, and leave the PR and the merge to the lead.
- Edit no issue body and no owner decision. Put proposed ticks in the final report.

## Django handlers

- A handler under `server/legacy_api/handlers/` is a thin adapter. It parses `ctx.args`, calls a service, and encodes the result. The guard in `server/legacy_api/tests/test_registry.py` fails a handler that writes through the ORM.
- A service that changes roster, inspection, lane, or result data takes `core.services.lock_race_state(event_id)` first inside `transaction.atomic()`. A handler never reads race state before a service call and acts on it after.
- A record that must survive a refusal is written after the atomic block, because the rollback removes it.
- Every endpoint needs a `LEAK_REQUESTS` entry in `server/legacy_api/tests/test_event_leak.py`.
- A deliberate difference from PHP goes in `server/tests/golden/deviations.json` (D25), and in the endpoint's `divergences` in the registry.

## Evidence

- A claim counts once a test or a real server run reproduces it.
- Run the shell suites in `testing/` against a live Django server the way `.github/workflows/server.yml` does. Add a suite to that workflow once it passes on SQLite and Postgres with no stand-in.
- Run the full server suite once at the end, in the background, and read its exit code before pushing.
- Golden captures from PHP need a regular clone, not a worktree. Ask the lead to run `server/tests/golden/capture.sh`, narrowed with `GOLDEN_SCENARIOS`.
- Before the PR, merge `origin/master` into the branch and rerun the affected tests.
