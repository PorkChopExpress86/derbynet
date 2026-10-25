# DerbyNet: PHP to Django migration

This is the owner's fork, PorkChopExpress86/derbynet. Work, issues, and PRs stay on this fork: pass `--repo PorkChopExpress86/derbynet` to every `gh` call. Contacting the upstream jeffpiazza/derbynet needs the owner's explicit approval.

The PHP server under `website/` is frozen: fix every defect in the Django port (#64 lists them), never in PHP.

## Where things are

- Plan: GitHub issues labeled `django-migration`, roadmap #2. Owner decisions and plan tooling: `plan/README.md`.
- Where the work stands, what is next, and new-machine setup: `plan/HANDOFF.md`.
- Architecture decisions: `server/docs/adr/README.md`.
- PHP test suite, and how to run it in Docker: `testing/README.md`.
- Golden fixtures captured from PHP: `server/tests/golden/README.md`.

## Running things

- Builds and tests run in containers through `testing/docker/run-suite.sh`; the host has no PHP, Java, or Ant. Run it from a regular clone, not a git worktree.
- Give each concurrent run its own `DERBYNET_TEST_PREFIX`, and remove that prefix's containers, images, and volumes afterwards.
- Wait for and merge PRs with `plan/bin/pr-wait` and `plan/bin/pr-merge`, which pin the merge to the head SHA that passed.

## Reviews

A review finding counts as confirmed only after it is reproduced: run the test script, the suite, or the `verify` skill against a real server. Report any finding checked only by reading code as unverified.
