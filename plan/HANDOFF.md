# Handoff: state of the Django migration

Written on 2026-10-24, before the owner moved to a new computer. It records what is done, what comes next, how the work is run, and the setup that lives outside this repo. Everything else lives in GitHub issues (labeled `django-migration`, roadmap #2), in `plan/owner-decisions.md`, and in the ADRs in `server/docs/adr/`.

## Where the work stands

| Milestone | State |
| --- | --- |
| M0 Parity harness and decisions | Done. Epic #3 stays open only for #64, the PHP defect tracker, which closes as Django fixes each row. |
| M1 Django skeleton | Done and closed (#10). Exit met: Django imports a real past-event database (#14) and answers `role.login` byte for byte like PHP (#16). |
| M2 Racing engine | Started. Spec #20 and ticket #21 are done. #22 to #27 are open. |
| M3 to M6 | Not started. |

Master is at `e2179a0d` (#94). Every ticket in M0 and M1 merged through a PR that passed CI and an independent review.

### What the Django server has today (`server/`)

- Project scaffold, env-driven settings, SQLite in WAL mode by default, Postgres through `DATABASE_URL` (#11, PR #85).
- No Django users or admin. Roles live in the session (D22, ADR-16, PR #86).
- Domain models for every event-scoped table, the legacy RaceInfo key registry, `check_invariants()` with a `cross-event` check, `new_event()`, and `manage.py dump_event` (#13, PR #87).
- The `/action.php` dispatcher with PHP-compatible JSON and XML encoders, handler registry, permission checks, and ActionLog (#18, PR #88).
- `manage.py import_derbynet` for old DerbyNet SQLite files (schema 1 to 10) and snapshots, crash-safe, never touching the source (#14, PR #89, hardened in #91 / PR #92).
- Role logins, permissions, `/mcheckin.php` tokens, hardened `session.write`, the Origin check, and `manage.py import_roles` (#16, PR #90).
- The PHP schedule generator tables as a generated Python module, checked against PHP (#21, PR #94).

### Test and CI tooling

- `testing/` is the PHP shell suite, now backend-agnostic, with no literal IDs, and run twice in CI with shifted IDs (#5, #6).
- `server/tests/golden/` holds fixtures captured from PHP, checked in CI by the `golden-fixtures` workflow (#7). `deviations.json` (D25) will list approved differences.
- `.github/workflows/`: `php-suite`, `golden-fixtures`, `server` (SQLite and Postgres 17), and `plan-checks` (ADR path check plus the issue lint).
- `plan/lint-issues.py` lints every plan issue body live from GitHub.
- `plan/bin/pr-wait` and `plan/bin/pr-merge` wait for checks and merge only the head that passed.
- `.claude/skills/verify/` is a project skill that launches an isolated DerbyNet on port 8090 and drives it with the Playwright MCP.

## What comes next

### M2, in dependency order

1. **#22 Heat schedule generation** and **#25 Rounds, rosters, follow-on rounds**. They are independent and can run in parallel.
   - #22 must widen the no-Django test (comment on #22).
   - #22 must not merge any `deviations.json` entry before reading D25.
2. **#26 Standings and speed awards** comes after #25. **#23 Master schedule** comes after #22.
3. **#24 Rescheduling** comes after #22 and #23.
4. **#27 Heat state machine, results, playlist auto-advance** comes last. It builds the timer audit service.
   - The audit never delays the timer (D26). See the comment on #27.
   - It owns the lock-read-write concurrency test.
5. Close epic #19.

Spec #20 (closed) is the contract for all of M2: module map, quirks Q1 to Q17, locking rules, race-day flow (D11, D12, D27, D28).

### Then

- M3 legacy API parity (#28 epic) starts with #63, the database reset that every shell suite needs. It ends at #37, the parity gate where the full suite runs against Django.
- M4 manager UI (#38) starts with spec #39 and #40.
- M5 displays and replay (#51) starts with the kiosk framework spike #71. Kiosk push is required (D21, ADR-15).
- M6 packaging and cutover (#57).

## Open questions for the owner

- **Which role the timer laptop logs in as** (comment on #50). Spec #15 moves `timer.settings` behind `CONTROL_RACE`, so a Timer-only login loses the browser timer's settings button. #50 lists two fixes.
- **Raspberry Pi 4 timings** are unmeasured: login cost (#16) and generator table load (#21). The owner runs a regular server (D21), so these are low priority.
- **The Electron desktop timer** likely sends `Origin: null`, which the Origin check refuses. It is untested. PHP sends no CORS header either. Test it only if the Electron build is ever used.

## How the work is run

These are the owner's standing rules. They are also stored as Claude memory files on the old machine (see the setup section).

- **Fork only.** All issues and PRs go to `PorkChopExpress86/derbynet`. Contacting upstream `jeffpiazza/derbynet` needs the owner's explicit approval.
- **The PHP server is frozen.** No fixes under `website/` (D12). Defects are fixed only in Django, and #64 tracks them.
- **One PR per ticket, then an independent review before merge.** The reviewer is a separate agent that the author did not choose. It reproduces findings, or labels them unverified.
- **Merges need the owner's approval for each PR.** An earlier approval never covers new PRs. Merge with `plan/bin/pr-merge`.
- **Owner decisions go in `plan/owner-decisions.md`** (D1 to D34 so far), in the same change that edits the issues they touch.
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

- `~/.claude/pstack-models.md` holds the pstack model sheet. Recreate it with `/pstack:setup-pstack`. Current choices:
  - `feature, refactoring: sonnet`
  - `strongest judgment: opus`
  - `how explorer` and `reflect tooling`: `haiku`
  - `why investigators` and `swarm workers`: `sonnet`
  - every other role: `opus`
  - `default effort: high`
  - `session hook: on`
- `~/.claude/CLAUDE.md` includes that sheet (`@~/.claude/pstack-models.md`) and a sudo rule. The rule runs every `sudo` command as `SUDO_ASKPASS=$HOME/.local/bin/agy-askpass sudo -A <command>`, so the password goes into a popup and never into chat. The helper scripts are documented in `~/SUDO_ASKPASS_SETUP.md` on the old machine; copy that file too.
- Project memory lives in `~/.claude/projects/<project-path>/memory/`. It holds three notes: the git identity and push command, merge approval for each PR, and that a review finding counts as confirmed only once reproduced. All three are restated in this file. Copy the folder, or let them be relearned.
- The plugins `pstack` (marketplace `michael-denyer/pstack-claude`) and `mattpocock-skills` are enabled in `~/.claude/settings.json`.
- Optional: a notes vault at `/mnt/c/Users/Blake/vault/ClaudeMemory` is referenced from `~/.claude/CLAUDE.md`.

### Local state that does not need to move

- The Docker container `derbynet-local` (port 8080) runs the PHP server with a throwaway test event ("Pinewood Test"). Recreate it if wanted: `testing/docker/run-suite.sh harness assets`, then build `installer/docker/Dockerfile`. It ran with `-p 8080:80 -v derbynet-local-data:/var/lib/derbynet`.
- The stopped container `m0spike-pg` is left over from the #9 prototype and can be deleted.
- `.claude/worktrees/`, `.claude/verify-evidence/`, and `.playwright-mcp/` are local only and git-ignored.
