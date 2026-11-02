Work through the ready tickets one at a time, in a loop, until none are left or you hit a stop condition.

PICK A TICKET
- Run `gh issue list --repo PorkChopExpress86/derbynet --label ready-for-agent --state open`. Take the lowest-numbered ticket that the roadmap (#2) orders first. Read it with `plan/bin/issue N`.
- Before building, list what origin/master already has for it (`git log origin/master --grep '#N\b'`, `server/legacy_api/registry.py`, existing handlers and services), as plan/AGENT-BRIEF.md says. Build only what is missing.

SET UP
- In the main clone (run-suite.sh needs a regular clone): `git switch master && git pull --ff-only`, then `git switch -c issue-N-<slug>`.

1. IMPLEMENT
- Read ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/implement/SKILL.md and follow it for ticket #N. It says: use the tdd skill at the seams, and run single test files often and the full suite once at the end. Skip its code-review step; step 2 does that after the commit.
- Follow plan/AGENT-BRIEF.md (the "Django handlers" and "Evidence" sections). Also run `server/tools/pytest-postgres.sh` on the affected tests, and run the verify skill for any UI or action.php change.
- Commit with a message that references #N.

2. CODE REVIEW
- Run /mattpocock-skills:code-review with origin/master as the fixed point. The spec is ticket #N. The standards are CONTRIBUTING.md and plan/AGENT-BRIEF.md.
- Rank every Standards and Spec finding high, medium, or low. A finding counts as real only once you reproduce it with a test, the suite, or a server run (CLAUDE.md); drop any you can't reproduce, and say so.
- Fix every reproduced high and medium finding, adding a test that failed before the fix where behavior changes. List the low ones in the PR body; don't fix them.
- Rerun the affected tests, then the full suite. Commit again with a message that references #N.

3. PR
- Merge origin/master into the branch, rerun the affected tests, and push.
- Write the body with /mattpocock-skills:pr. Include `Closes #N` (or leave it out and say why, if the ticket must stay open under `## Still open`).
- Open the PR with `gh pr create --repo PorkChopExpress86/derbynet`.

4. MERGE
- Run `plan/bin/pr-wait <pr>`. If a check fails, read its log, fix the cause, push, and wait again.
- Once every check is green, run `plan/bin/pr-merge <pr> --squash --delete-branch`. If it reports the issue still open, close it or explain why it stays open.

5. RESET
- Run `git switch master && git pull --ff-only`, `git branch -D issue-N-<slug>`, and `git worktree prune`. Confirm `git status` is clean and HEAD equals origin/master.

6. RETRO
- Read ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/retro/SKILL.md and follow it for this ticket's run (steps 1 to 4 above). Instead of only presenting the candidates, rank each high, medium, or low, and fix every high and medium one.
- Look hardest at your own mistakes: failed CI runs, tests you had to rerun, wrong guesses about the code, slow searches, and anything the code review caught that a check could have caught first. Prefer a deterministic check (a test, ruff rule, pre-push hook, or CI step) over prose. Keep CLAUDE.md to navigation pointers.
- Fixes to repo files go on a branch `retro-N` from fresh master. Run the affected checks, commit, push, open a PR with /mattpocock-skills:pr, then run pr-wait and `pr-merge --squash --delete-branch`, then reset to master as in step 5.
- Fixes outside the repo (memory files under ~/.claude/projects/.../memory/) can be written directly. Never widen permissions in any settings file. Name any change you would make to ~/.claude/CLAUDE.md in the summary and leave it for the owner.
- If there are no high or medium findings, open no PR.
- Give a 4-line summary: the ticket PR link and what landed, the low review findings left over, the retro fixes made (with PR link), and the low retro findings left over. Then go back to PICK A TICKET.

STOP AND REPORT IF
- a ticket needs an owner decision, or says a fix belongs in website/ (PHP is frozen);
- CI fails twice in a row for the same cause;
- a replay or parity suite would need an expectation edited instead of a narrower fix;
- a retro fix would change a rule in plan/HANDOFF.md, plan/owner-decisions.md, or CLAUDE.md's standing rules (propose it, don't apply it);
- no ready-for-agent tickets remain.
Never force-push, never touch upstream jeffpiazza/derbynet, and never edit an issue body without running plan/lint-issues.py first.
