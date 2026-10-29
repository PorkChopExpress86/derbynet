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
- A deliberate difference from PHP goes in the endpoint's `divergences` in the registry, citing a quirk id or an owner decision (`D48`). A difference that a recorded golden fixture shows also goes in `server/tests/golden/deviations.json` (D25).
- A new Django-only endpoint is added to `DJANGO_ONLY` in `server/legacy_api/tests/test_registry.py`. A new event-scoped model needs a row in `server/core/tests/populated.py`. Run `pytest core/tests legacy_api/tests/test_registry.py legacy_api/tests/test_event_leak.py` before the full suite, because these fail only there otherwise.
- Replay suites hold PHP's state after each script step: `roster/tests/test_group_scenarios.py`, `legacy_api/tests/test_groups_parity.py`, `racing/tests/test_round_scenarios.py`, `racing/tests/test_finalists.py`. Run them before changing a behavior they cover. A fix they break needs a narrower condition or a recorded deviation, never an edited expectation.

## Evidence

- A claim counts once a test or a real server run reproduces it. That covers a sentence in `GLOSSARY.md`, `plan/owner-decisions.md` or a docstring that says what the server does. Name the test or the code that shows it. D57 first said a restore left the open event alone, and the importer replaces it.
- Run the shell suites in `testing/` against a live Django server the way `.github/workflows/server.yml` does. Add a suite to that workflow once it passes on SQLite and Postgres with no stand-in.
- Run the full server suite once at the end, in the background, and read its exit code before pushing.
- Golden captures from PHP need a regular clone, not a worktree. Ask the lead to run `server/tests/golden/capture.sh`, narrowed with `GOLDEN_SCENARIOS`.
- Before the PR, merge `origin/master` into the branch and rerun the affected tests.
