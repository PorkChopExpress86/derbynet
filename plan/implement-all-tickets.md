Work through the ready tickets one at a time, in a loop, until no ready ticket is left or a stop condition hits.

You are the **lead**. Keep your context small enough to run many tickets: each ticket gets fresh subagents, and you read only their reports and the `plan/bin/pr-wait` output. Brief subagents with context pointers (the ticket number, file paths, commit SHAs), not copied text. Load `SendMessage` with ToolSearch if it is deferred; it sends follow-up work to a subagent that still holds its context.

Steps 2 and 6 rank findings this way:

- **high**: wrong behavior, a difference from PHP that no divergence records, lost data, a locking or permission gap, or a missing spec requirement.
- **medium**: a breach of CONTRIBUTING.md or plan/AGENT-BRIEF.md, a missing test, or a mistake a check could have caught.
- **low**: a smell or a style judgement.

## Pick a ticket

1. Move the **frontier**: run `python3 -I plan/lint-issues.py`, and for each `blocked, but every dependency is closed` finding run `gh issue edit N --repo PorkChopExpress86/derbynet --remove-label blocked --add-label ready-for-agent`.
2. Run `gh issue list --repo PorkChopExpress86/derbynet --label ready-for-agent --state open`. Take the lowest-numbered `kind:ticket` that the roadmap (#2) orders first, and read it with `plan/bin/issue N`.
3. Skip a ticket whose unticked items all sit under `## Still open` waiting on another open ticket, since its work has merged. Skip `kind:spec` issues too. Note each skip and its reason for the final report.

## Set up

In the main clone, run `git fetch origin`, then `git worktree add ../derbynet-wt-N -b issue-N-<slug> origin/master`.

## 1. Implement

Spawn a general-purpose agent with `model: opus`, briefed per plan/AGENT-BRIEF.md for ticket #N in ../derbynet-wt-N on branch issue-N-<slug>. Add to the brief:

- Follow ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/implement/SKILL.md, and leave its code-review step to the lead.
- Run `server/tools/pytest-postgres.sh` on the affected tests, and the verify skill for any page or action.php change.
- Commit with a message that references #N, push, and return the commit SHA, what you built, and what origin/master already had.

## 2. Code review

1. Run /mattpocock-skills:code-review on ../derbynet-wt-N with origin/master as the fixed point. The spec is ticket #N, and the standards are CONTRIBUTING.md and plan/AGENT-BRIEF.md. Spawn its Standards reviewer with `model: sonnet` and its Spec reviewer with `model: opus`.
2. Rank every finding.
3. Send the high and medium findings to the implementer. It reproduces each one with a test, the suite, or a server run, and reports the ones it can't reproduce (CLAUDE.md). It fixes each reproduced finding with a test that goes red before the fix, reruns the affected tests and then the full suite, and commits with a message that references #N.

## 3. PR

1. Have the implementer merge origin/master into the branch, rerun the affected tests, and push. Have it write the PR body with /mattpocock-skills:pr to a scratchpad file, with the low findings under "Left for later" and `Closes #N` (or the reason the ticket stays open under `## Still open`), and return the path.
2. Run `gh pr create --repo PorkChopExpress86/derbynet --head issue-N-<slug> --body-file <path>`.

## 4. Merge

1. Run `plan/bin/pr-wait <pr>` in the background and wait for its notification.
2. When a check fails, send its name and link to the implementer, which reads the log with `gh run view --log-failed`, fixes the cause, and pushes. Then wait again.
3. When every check is green, run `plan/bin/pr-merge <pr> --squash --delete-branch`. If it reports the issue still open, close it or say why it stays open.

## 5. Reset

Run `git worktree remove ../derbynet-wt-N`, `git branch -D issue-N-<slug>`, and `git worktree prune`. Run `git fetch origin`, and confirm origin/master holds the merge commit.

## 6. Retro

1. Ask the implementer, which holds the primary source, to list every mistake and slow spot in this ticket's run, each with its evidence: failed CI runs, reruns, wrong guesses about the code, slow searches, and review findings a check could have caught first. Add any CI failure it doesn't list.
2. Spawn a general-purpose agent with `model: opus` and give it that list. It follows ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/retro/SKILL.md, ranks each candidate, and fixes every high and medium one:
   - It prefers a deterministic check (a test, ruff rule, pre-push hook, or CI step) to prose, and keeps CLAUDE.md to navigation pointers.
   - Repo fixes go in `git worktree add ../derbynet-wt-retro-N -b retro-N origin/master`. It runs the affected checks, commits, pushes, and returns a PR body written with /mattpocock-skills:pr.
   - Memory files go straight into ~/.claude/projects/-home-specter-dev-projects-derbynet/memory/.
   - Permissions in settings files stay as they are, and a change to ~/.claude/CLAUDE.md is only proposed.
   - It returns the low findings, and creates no branch when nothing is high or medium.
3. If it returned a PR body, open the PR, then merge and reset as in steps 4 and 5.
4. Give a 4-line summary: the ticket PR link and what landed, the low review findings left over, the retro fixes (with PR link) and any proposed ~/.claude/CLAUDE.md change, and the low retro findings left over. Then go back to Pick a ticket with new subagents.

## Stop and report

Stop and report when:

- a ticket needs an owner decision, or says a fix belongs in website/ (PHP is frozen);
- CI fails twice in a row for the same cause;
- a replay or parity suite would need an expectation edited instead of a narrower fix;
- a golden capture is needed (it needs a regular clone; see server/tests/golden/README.md);
- a retro fix would change a rule in plan/HANDOFF.md, plan/owner-decisions.md, or CLAUDE.md's standing rules (propose it instead);
- no ready ticket is left to build.

The final report lists the skipped tickets and what is waiting on the owner:

- every open `needs-info` issue, for /mattpocock-skills:grill-with-docs, since the plan made these tickets and each waits on a decision;
- every `kind:spec` that is `ready-for-agent`, for /mattpocock-skills:prototype when its question needs running code to answer;
- every open issue outside the `django-migration` label, for /mattpocock-skills:triage.
