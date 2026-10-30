# Handoff: state of the Django migration

Written on 2026-10-24, before the owner moved to a new computer, and trimmed on 2026-10-30 to what GitHub cannot say: how the work is run, the open questions, and the setup that lives outside this repo. The plan itself lives in GitHub issues, in `plan/owner-decisions.md`, and in the ADRs in `server/docs/adr/`.

## Where the work stands

GitHub holds the live state, so this file copies none of it.

- The roadmap #2 lists the milestones M0 to M6 and their epics.
- `gh issue list --repo PorkChopExpress86/derbynet --label django-migration --state open` lists what is left. A ticket labeled `ready-for-agent` has every dependency closed or marked `## Still open`, which `plan/lint-issues.py` checks.
- `git log origin/master --grep '#N\b'` lists what has landed for ticket N, and `server/legacy_api/registry.py` lists every legacy endpoint Django serves.
- #64 tracks the PHP defects, and epic #3 stays open until its last row is fixed.

The shell suites in `testing/` run against Django one by one as their endpoints land. A suite goes into `.github/workflows/server.yml` once it passes on SQLite and Postgres with no stand-in.

### Test and CI tooling

- `testing/` is the PHP shell suite, now backend-agnostic, with no literal IDs, and run twice in CI with shifted IDs (#5, #6).
- `server/tests/golden/` holds fixtures captured from PHP, checked in CI by the `golden-fixtures` workflow (#7). `deviations.json` (D25) lists approved differences.
- `.github/workflows/`: `php-suite`, `golden-fixtures`, `server` (SQLite and Postgres 17), and `plan-checks` (ADR path check plus the issue lint).
- `plan/lint-issues.py` lints every plan issue body live from GitHub, and checks issue state against merged PRs and dependencies.
- `plan/bin/pr-wait` and `plan/bin/pr-merge` wait for checks and merge only the head that passed.
- `.claude/skills/verify/` is a project skill that launches an isolated DerbyNet on port 8090 and drives it with the Playwright MCP.

## Open questions for the owner

- **The Electron desktop timer** likely sends `Origin: null`, which the Origin check refuses. It is untested. PHP sends no CORS header either. Test it only if the Electron build is ever used.

## How the work is run

These are the owner's standing rules. They are also stored as Claude memory files on the old machine (see the setup section).

- **Fork only.** All issues and PRs go to `PorkChopExpress86/derbynet`. Contacting upstream `jeffpiazza/derbynet` needs the owner's explicit approval.
- **The PHP server is frozen.** No fixes under `website/` (D12). Defects are fixed only in Django, and #64 tracks them.
- **One PR per ticket, then an independent review before merge.** The reviewer is a separate agent that the author did not choose. It reproduces findings, or labels them unverified.
- **Merge a PR when its checks pass on the current head and its review is clean.** The owner gave standing approval on 2026-10-28, so no per-PR ask. Use `plan/bin/pr-wait` and `plan/bin/pr-merge`, which pin the merge to the head that passed. Anything else that is hard to reverse still needs a confirmation.
- **Owner decisions go in `plan/owner-decisions.md`** in the same change that edits the issues they touch.
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
