Work through the ready tickets one at a time, in a loop, until none are left or you hit a stop condition.

You are the lead. Keep your own context small, so the loop can run many tickets: give each ticket to fresh subagents, and read only their reports and the `plan/bin/pr-wait` output, never diffs, test logs or CI logs. Load `SendMessage` with ToolSearch if it is deferred; you use it to send follow-up work to a subagent that still has its context. No agent uses the fable model.

PICK A TICKET
- Run `gh issue list --repo PorkChopExpress86/derbynet --label ready-for-agent --state open`. Take the lowest-numbered `kind:ticket` that the roadmap (#2) orders first. Read it with `plan/bin/issue N`.
- Skip a ticket whose unticked items are all listed under `## Still open` and wait on another open ticket: its work has already merged. Skip `kind:spec` issues too. List every skipped issue, with the reason, in your final report.

SET UP
- In the main clone, run `git fetch origin`, then `git worktree add ../derbynet-wt-N -b issue-N-<slug> origin/master`. Leave the main clone's own branch alone.

1. IMPLEMENT (Opus subagent)
Spawn a general-purpose agent with `model: opus`. Brief it per plan/AGENT-BRIEF.md, and tell it to:
- work in ../derbynet-wt-N on branch issue-N-<slug>;
- list what origin/master already has for #N (`git log origin/master --grep '#N\b'`, `server/legacy_api/registry.py`, existing handlers and services), and build only what is missing;
- read ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/implement/SKILL.md and follow it, using the tdd skill at the seams and running single test files often and the full suite once at the end, but skip its code-review step;
- run `server/tools/pytest-postgres.sh` on the affected tests, and the verify skill for any UI or action.php change;
- commit with a message that references #N, push the branch, and return the commit SHA, what it built, and what origin/master already had.

2. CODE REVIEW (lead runs it, reviewers are subagents)
- Run /mattpocock-skills:code-review on ../derbynet-wt-N with origin/master as the fixed point. The spec is ticket #N. The standards are CONTRIBUTING.md and plan/AGENT-BRIEF.md.
- Spawn its Standards reviewer with `model: sonnet` and its Spec reviewer with `model: opus`.
- Rank every finding high, medium, or low.
- Send the high and medium findings to the implementer with SendMessage. Tell it to:
  - reproduce each one with a test, the suite, or a server run, and drop any it can't reproduce, saying why (CLAUDE.md);
  - fix each reproduced finding, with a test that failed before the fix where behavior changes;
  - rerun the affected tests, then the full suite;
  - commit with a message that references #N.

3. PR
- Have the implementer, by SendMessage:
  - merge origin/master into the branch and rerun the affected tests;
  - push the branch;
  - write the PR body with /mattpocock-skills:pr to a file in the scratchpad, listing the low findings under a "Left for later" line, with `Closes #N` (or with it left out and the reason given, if the ticket must stay open under `## Still open`);
  - return the file's path.
- Open the PR: `gh pr create --repo PorkChopExpress86/derbynet --head issue-N-<slug> --body-file <path>`.

4. MERGE
- Run `plan/bin/pr-wait <pr>` in the background and wait for its notification.
- If a check fails, send the failing check's name and link to the implementer by SendMessage. Tell it to read the log with `gh run view --log-failed`, fix the cause, push, and report back. Then wait again.
- Once every check is green, run `plan/bin/pr-merge <pr> --squash --delete-branch`. If it reports the issue still open, close it or explain why it stays open.

5. RESET
- Run `git worktree remove ../derbynet-wt-N`, `git branch -D issue-N-<slug>`, and `git worktree prune`. Then `git fetch origin`, and confirm origin/master contains the merge commit.

6. RETRO
- Spawn a general-purpose agent with `model: sonnet` to gather the facts. Give it the implementer's reports, the review findings, and every CI failure. Point it at this session's transcript, and its subagents' transcripts, under ~/.claude/projects/-home-specter-dev-projects-derbynet/. Have it return a list of mistakes and slow spots: failed CI runs, reruns, wrong guesses about the code, slow searches, and review findings a check could have caught first. Each item needs evidence.
- Spawn a general-purpose agent with `model: opus` to act on that list. Tell it to:
  - read ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/retro/SKILL.md and follow it;
  - rank each candidate high, medium, or low, and fix every high and medium one, preferring a deterministic check (a test, ruff rule, pre-push hook, or CI step) over prose, and keeping CLAUDE.md to navigation pointers;
  - make repo fixes in `git worktree add ../derbynet-wt-retro-N -b retro-N origin/master`, run the affected checks, commit, push, and return a PR body written with /mattpocock-skills:pr;
  - write memory files under ~/.claude/projects/-home-specter-dev-projects-derbynet/memory/ directly;
  - never widen permissions in any settings file, and only name any change it would make to ~/.claude/CLAUDE.md;
  - return the low findings, and open no branch if nothing is high or medium.
- If it returned a PR body, open the PR, then wait, merge and reset as in steps 4 and 5.
- Give a 4-line summary: the ticket PR link and what landed, the low review findings left over, the retro fixes made (with PR link) and any ~/.claude/CLAUDE.md change proposed, and the low retro findings left over. Then go back to PICK A TICKET with new subagents.

STOP AND REPORT IF
- a ticket needs an owner decision, or says a fix belongs in website/ (PHP is frozen);
- CI fails twice in a row for the same cause;
- a replay or parity suite would need an expectation edited instead of a narrower fix;
- a golden capture is needed (it needs a regular clone; see server/tests/golden/README.md);
- a retro fix would change a rule in plan/HANDOFF.md, plan/owner-decisions.md, or CLAUDE.md's standing rules (propose it, don't apply it);
- no ready ticket is left to build.
Never force-push, never touch upstream jeffpiazza/derbynet, and never edit an issue body without running plan/lint-issues.py first.
