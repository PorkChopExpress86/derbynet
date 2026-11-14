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

In the main clone, run `git fetch origin`, then `git worktree add ../derbynet-wt-N -b issue-N-<slug> origin/master`. Note the time, for the summary's wall-clock (step 6.4).

## 1. Implement

Spawn a general-purpose agent with `model: opus`, briefed per plan/AGENT-BRIEF.md for ticket #N in ../derbynet-wt-N on branch issue-N-<slug>. Add to the brief:

- Follow ~/.claude/plugins/cache/claude-plugins-official/mattpocock-skills/*/skills/engineering/implement/SKILL.md, and leave its code-review step to the lead.
- While iterating, run the test files you edited and `tools/source-checks.sh`. At each hand-off, run what `plan/bin/affected-tests` prints for that head, once: the tests the diff can reach, `tools/source-checks.sh` and ruff, with `server/tools/pytest-postgres.sh` only when it says the diff touches database code (started when the SQLite run ends), and the repeats under load at the count it gives. Leave the full suites to CI, which runs them on every push; for a shared conftest, the dependencies, the settings or a workflow the tool prints "CI runs the full suites on both databases (D85)" and only the targeted tests. Run the verify skill for any page or action.php change.
- Commit with a message that references #N, push, and return the commit SHA, what you built, and what origin/master already had.
- Before handing back, TaskStop every Monitor and background run you started, and check `git status`. A leftover one wakes the agent after its handback, and it resends its report: #283's Standards reviewer resent an unchanged one 4 times (memory `subagent-resends-report.md`).

While a subagent runs, read its notifications: when they show the same background wait for over an hour with no new tool uses, list its processes with `ps -eo pid,etime,args` instead of waiting on. #247's implementer waited about 10 h on a probe that had hung, and the lead looked only when the owner asked, after about 6 h. When a subagent resends a report unchanged, TaskStop it without messaging it.

## 2. Code review

1. Open the PR as a draft at the implementer's first push, so CI runs during review: `gh pr create --repo PorkChopExpress86/derbynet --draft --head issue-N-<slug> --title "<title>" --body "Part of #N. Draft: the body comes after review."`, then run `plan/bin/pr-wait <pr>` in the background. Start the review without waiting for CI; a red check joins the review findings in the fix round (D85).
2. Run /mattpocock-skills:code-review on ../derbynet-wt-N with origin/master as the fixed point. The spec is ticket #N, and the standards are CONTRIBUTING.md and plan/AGENT-BRIEF.md. Spawn its Standards reviewer with `model: sonnet` and its Spec reviewer with `model: opus`. A reviewer runs only what confirms or refutes a finding (the test, the `-k` mutant, the server run), never the full suites (owner, 2026-11-11). Before any run it expects to take more than 10 min, a reviewer messages the lead with the finding the run would confirm and waits for an OK (D85; #282's Spec reviewer spent 87 min on suites and load runs, and one 25 min run confirmed its finding). A reviewer that mutates code does it in its own copy (`git worktree add --detach ../derbynet-wt-N-rv<axis> <head>`, removed after), and a reviewer leaves a change it didn't make as it is: #247's Spec reviewer reset a mutation the Standards reviewer had running in the shared worktree. Each reviewer's brief carries Implement's TaskStop line.
3. Rank every finding.
4. Before sending, merge the head with each other open PR's head in a scratch `git worktree add --detach` copy and run `tools/source-checks.sh` there when either PR changes a source check or a shell script; `plan/bin/pr-merge` refuses the pair later, after a CI round. Then send the high and medium findings, the name and link of any failed CI check, and what the trial merge found to the implementer in one message, since its fix-round run starts once they arrive: #61's fix-round run was stopped when #304's pipefail clash came after the review findings, and the clash cost a commit and another run (#61 retro). It reproduces each one with a test, a server run, or the affected tests (owner, 2026-11-11), and reports the ones it can't reproduce (CLAUDE.md). It fixes each reproduced finding with a test that goes red before the fix, reruns what `plan/bin/affected-tests` prints for the new head, mutates only the asserts or rules the round changed (owner, 2026-11-11), and commits with a message that references #N.

## 3. PR

1. Have the implementer merge origin/master into the branch before its fix-round hand-off run (Code review's step 4), so that one run covers the merge too, and push. Have it write the PR body with /mattpocock-skills:pr to a scratchpad file, with the low findings under "Left for later" and `Closes #N` (or the reason the ticket stays open under `## Still open`), and return the path. The body quotes each line `plan/bin/pr-evidence N` lists beside its evidence, or under `## Still open`.
2. Run `plan/bin/pr-evidence N --check <path>`, and send each line it names back to the implementer: #61's body draft left out the reviewer's checklist its timer box asked for (#61 retro). Then run `gh pr edit <pr> --repo PorkChopExpress86/derbynet --body-file <path>` on the draft PR.

## 4. Merge

1. Run `plan/bin/pr-wait <pr>` in the background and wait for its notification.
2. When a check fails, send its name and link to the implementer, which reads the log with `gh run view --log-failed`, fixes the cause, and pushes. Then wait again. An `outage:` line from `plan/bin/ci-failures` is no code failure: see Stop and report.
3. When every check is green, run `gh pr ready <pr> --repo PorkChopExpress86/derbynet`, then `plan/bin/pr-merge <pr>`, which squashes and deletes the branch. It refuses a PR when origin/master moved since the PR's merge base in an area the PR also changes (plan/, .githooks/, server/tools/, .github/, a conftest.py, server/pyproject.toml, server/uv.lock), and prints what `plan/bin/affected-tests` maps the combined diff to: run that on the PR merged with origin/master, then `plan/bin/pr-merge <pr> --base-checked`, or update the branch and wait for CI again. #289 passed CI on a base without #288 and merged after it. It also refuses a PR whose body would close an issue that has a `## Still open` section; change the PR body to `Part of #N`. If it reports the issue still open, close it, or add a `## Still open` section and move the frontier again (Pick a ticket, step 1), because that section unblocks the tickets that depend on it.
4. When the ticket has a screenshot box, post the screenshots (D75). Download the `ui-screenshots` artifact of the passing `ui (sqlite)` run with `gh run download <run> -n ui-screenshots`, into an empty scratchpad folder. Commit the ticket's PNGs to `N/` on the orphan `screenshots` branch, from a fresh clone of that branch alone in the scratchpad: `git clone --single-branch --branch screenshots --depth 1 https://github.com/PorkChopExpress86/derbynet.git <scratchpad>/shots-N`. Make a new one for each ticket, since a clone kept in /tmp did not survive #237's reboot. Commit with the owner's identity (plan/AGENT-BRIEF.md), push with `git push origin screenshots`, and remove the clone. Then comment on the ticket, embedding each image by its `raw.githubusercontent.com` URL at that commit's SHA. Tick the box only when every view it names is shot at both sizes; otherwise name the missing views under `## Still open`.

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
3. If it returned a PR body, open the PR. When the PR adds or changes a tool under plan/bin, server/tools or .githooks, or a CI workflow, spawn the Standards reviewer of Code review's step 2 on it (`model: sonnet`, in its own `git worktree add --detach` copy), with the tool's tests as its spec, and send its high and medium findings to the retro agent before the merge. A PR that changes only docs or memory skips the review. #285, #286, #288 and #289 merged with CI alone. Then merge and reset as in steps 4 and 5.
4. Give a 5-line summary: the ticket PR link and what landed, the low review findings left over, the retro fixes (with PR link) and any proposed ~/.claude/CLAUDE.md change, the low retro findings left over, and the ticket's wall-clock from the time noted at Set up with its biggest step (#283 spent about 2.5 h on full suites and load runs before the owner raised it). Then go back to Pick a ticket with new subagents.

## Pausing mid-ticket

When the loop must stop inside a ticket (a reboot, a stop condition), write `~/.claude/projects/-home-specter-dev-projects-derbynet/resume-N.md` from the implementer's last report, and a memory note pointing at it. After #237's reboot, its file gave a skip count without the skips, which the second implementer guessed wrong in the PR body; listed full-suite commands without a `DERBYNET_TEST_PREFIX`; and called a load run unstarted that had begun (`server/var/load-runs/20261108-120936-2056795`). The file holds:

- the worktree, the branch, the pushed head, and each commit that holds real work;
- each finished step with its result, every skip named (`25 skipped: <test ids or the reason>`);
- each step left, as the exact command to run, with its folder and its `DERBYNET_TEST_PREFIX`;
- each folder under `server/var/load-runs/` or elsewhere that a stopped run left half-written, marked to ignore or remove;
- where the PR body draft lives, outside /tmp;
- what is still running, or "nothing is running".

Delete the file and the memory note once the ticket's PR merges.

## Stop and report

Stop and report when:

- a ticket needs an owner decision, or says a fix belongs in website/ (PHP is frozen);
- CI fails twice in a row for the same cause. When every failed job died in a setup step before any test, with one shared error line (`plan/bin/ci-failures` prints `outage:`), it is an outage, not a code failure: probe the dependency the line names, then work around it in a PR of its own, as #285 put a second apt mirror first (`.github/ubuntu-mirror.sh`), or report it;
- a replay or parity suite would need an expectation edited instead of a narrower fix;
- a golden capture is needed (it needs a regular clone; see server/tests/golden/README.md);
- a retro fix would change a rule in plan/HANDOFF.md, plan/owner-decisions.md, or CLAUDE.md's standing rules (propose it instead);
- no ready ticket is left to build.

The final report lists the skipped tickets and what is waiting on the owner:

- every open `needs-info` issue, for /mattpocock-skills:grill-with-docs, since the plan made these tickets and each waits on a decision;
- every `kind:spec` that is `ready-for-agent`, for /mattpocock-skills:prototype when its question needs running code to answer;
- every open issue outside the `django-migration` label, for /mattpocock-skills:triage.
