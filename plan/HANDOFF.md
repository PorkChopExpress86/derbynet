# Handoff: state of the Django migration

Written on 2026-10-24, before the owner moved to a new computer, and brought up to date on 2026-10-28. It records what is done, what comes next, how the work is run, and the setup that lives outside this repo. Everything else lives in GitHub issues (labeled `django-migration`, roadmap #2), in `plan/owner-decisions.md`, and in the ADRs in `server/docs/adr/`.

## Where the work stands

| Milestone | State |
| --- | --- |
| M0 Parity harness and decisions | Done. Epic #3 stays open only for #64, the PHP defect tracker, which closes as Django fixes each row. |
| M1 Django skeleton | Done and closed (#10). Exit met: Django imports a real past-event database (#14) and answers `role.login` byte for byte like PHP (#16). |
| M2 Racing engine | Done and closed (#19). Spec #20 and tickets #21 to #27 are merged, with the follow-on work for lane closure (#108), allowed racers (#110), and finalist suggestions. |
| M3 Legacy API parity | In progress (#28). #63, #29, #30, and #31 are merged. #32 to #36, #107, and the parity gate #37 are open. |
| M4 to M6 | Not started. |

Master is at `e0455614` (#120). Every ticket merged through a PR that passed CI and an independent review.

### What the Django server has today (`server/`)

- Project scaffold, env-driven settings, SQLite in WAL mode by default, Postgres through `DATABASE_URL` (#11, PR #85).
- No Django users or admin. Roles live in the session (D22, ADR-16, PR #86).
- Domain models for every event-scoped table, the legacy RaceInfo key registry, `check_invariants()` with a `cross-event` check, `new_event()`, and `manage.py dump_event` (#13, PR #87).
- The `/action.php` dispatcher with PHP-compatible JSON and XML encoders, handler registry, permission checks, and ActionLog (#18, PR #88).
- `manage.py import_derbynet` for old DerbyNet SQLite files (schema 1 to 10) and snapshots, crash-safe, never touching the source (#14, PR #89, hardened in #91 / PR #92).
- Role logins, permissions, `/mcheckin.php` tokens, hardened `session.write`, the Origin check, and `manage.py import_roles` (#16, PR #90).
- The PHP schedule generator tables as a generated Python module, checked against PHP (#21, PR #94).
- The racing engine: schedules, master schedule, rescheduling, rounds and rosters, group formation, standings and speed awards, the heat state machine, and lane closure (#22 to #27, #108, #110).
- Legacy `/action.php` endpoints so far: the database reset, settings, and test endpoints (#63); racers, check-in, and roster import (#29); classes, ranks, and partitions (#30); schedule, heat, result, round, and the racing polls (#31). `server/README.md` lists them.
- `deviations.json` lists every approved difference from PHP, and the endpoint registry carries the same list in its `divergences` field for the parity gate (#37).

### Test and CI tooling

- `testing/` is the PHP shell suite, now backend-agnostic, with no literal IDs, and run twice in CI with shifted IDs (#5, #6).
- `server/tests/golden/` holds fixtures captured from PHP, checked in CI by the `golden-fixtures` workflow (#7). `deviations.json` (D25) lists approved differences.
- `.github/workflows/`: `php-suite`, `golden-fixtures`, `server` (SQLite and Postgres 17), and `plan-checks` (ADR path check plus the issue lint).
- `plan/lint-issues.py` lints every plan issue body live from GitHub.
- `plan/bin/pr-wait` and `plan/bin/pr-merge` wait for checks and merge only the head that passed.
- `.claude/skills/verify/` is a project skill that launches an isolated DerbyNet on port 8090 and drives it with the Playwright MCP.

## What comes next

### M3, in dependency order

The shell suites in `testing/` run against Django one by one as their endpoints land. A suite goes into `.github/workflows/server.yml` once it passes on SQLite and Postgres with no stand-in.

1. **#32 timer-message XML protocol** and **#33 awards, judging, balloting**. They are independent. Most suites run heats through `timer-message`, so #32 unlocks the most suite boxes on #29, #30, and #31.
2. **#36 settings, setup, snapshot, preferences, history** and **#34 kiosks, scenes, playlist, replay**. #36 adds `export-json.php` and `preferences.upload`, which several suites read.
3. **#35 photos, video, slides** (with #73 and #74).
4. **#107** updates `testing/test-reschedule.sh` for D34, and #5 decides whether its PHP-chart step is marked known-failing.
5. **#37 parity gate**: the full suite against Django in CI, with hardware clients unchanged.

The acceptance boxes left open on #29, #30, and #31 are suite criteria that wait for the tickets above.

### After M3

- M4 manager UI (#38) starts with spec #39 and #40.
- M5 displays and replay (#51) starts with the kiosk framework spike #71. Kiosk push is required (D21, ADR-15).
- M6 packaging and cutover (#57).

## Open questions for the owner

- **The Electron desktop timer** likely sends `Origin: null`, which the Origin check refuses. It is untested. PHP sends no CORS header either. Test it only if the Electron build is ever used.

## How the work is run

These are the owner's standing rules. They are also stored as Claude memory files on the old machine (see the setup section).

- **Fork only.** All issues and PRs go to `PorkChopExpress86/derbynet`. Contacting upstream `jeffpiazza/derbynet` needs the owner's explicit approval.
- **The PHP server is frozen.** No fixes under `website/` (D12). Defects are fixed only in Django, and #64 tracks them.
- **One PR per ticket, then an independent review before merge.** The reviewer is a separate agent that the author did not choose. It reproduces findings, or labels them unverified.
- **Merge a PR when its checks pass on the current head and its review is clean.** The owner gave standing approval on 2026-10-28, so no per-PR ask. Use `plan/bin/pr-wait` and `plan/bin/pr-merge`, which pin the merge to the head that passed. Anything else that is hard to reverse still needs a confirmation.
- **Owner decisions go in `plan/owner-decisions.md`** (D1 to D56 so far), in the same change that edits the issues they touch.
- **Edit issue bodies through the lint.** Fetch the body, edit it, run `plan/lint-issues.py --file N=PATH`, then `gh issue edit`. See `plan/README.md`.

## Setting up a new computer

The repo carries the code and the plan. The items below live outside it and must be recreated.

### Tools

- `docker`: every PHP build and test runs in containers through `testing/docker/run-suite.sh`, so the host needs no PHP, Java, or Ant.
- `gh` (GitHub CLI). Log in with `gh auth login`, and make sure the token has the `repo` and `workflow` scopes.
- `uv`, for `server/` (`uv sync`, `uv run pytest`). Python 3.14.
- `jq`.
- For the Playwright MCP browser, run `npx -y playwright install --with-deps chrome` once, with sudo.
- Git commits use the owner's identity through `-c` flags, so the global git config is never changed: `git -c user.name=PorkChopExpress86 -c user.email=<owner email> commit`. Every commit also ends with a `Co-Authored-By` line for Claude.

### Claude Code configuration (in `~/.claude/`, not in this repo)

- `~/.claude/CLAUDE.md` holds a root-command rule: run root commands as `agent-sudo <command>`, which asks for approval in a desktop popup so no password goes into chat.
- Project memory lives in `~/.claude/projects/<project-path>/memory/`. It holds three notes: the git identity and push command, merge approval for each PR, and that a review finding counts as confirmed only once reproduced. All three are restated in this file. Copy the folder, or let them be relearned.
- The plugin `mattpocock-skills` is enabled in `~/.claude/settings.json`.
- Optional: a notes vault at `/mnt/c/Users/Blake/vault/ClaudeMemory` is referenced from `~/.claude/CLAUDE.md`.

### Local state that does not need to move

- The Docker container `derbynet-local` (port 8080) runs the PHP server with a throwaway test event ("Pinewood Test"). Recreate it if wanted: `testing/docker/run-suite.sh harness assets`, then build `installer/docker/Dockerfile`. It ran with `-p 8080:80 -v derbynet-local-data:/var/lib/derbynet`.
- The stopped container `m0spike-pg` is left over from the #9 prototype and can be deleted.
- `.claude/worktrees/`, `.claude/verify-evidence/`, and `.playwright-mcp/` are local only and git-ignored.
