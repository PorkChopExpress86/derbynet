Work through the ready tickets in a loop, one at a time or side by side (Several tickets at once), until no ready ticket is left or a stop condition hits.

You are the **lead**. Keep your context small enough to run many tickets: each ticket gets fresh subagents, and you read only their reports and the `plan/bin/pr-wait` output. Brief subagents with context pointers (the ticket number, file paths, commit SHAs), not copied text. Load `SendMessage` with ToolSearch if it is deferred; it sends follow-up work to a subagent that still holds its context.

Steps 2 and 6 rank findings this way:

- **high**: wrong behavior, a difference from PHP that no divergence records, lost data, a locking or permission gap, or a missing spec requirement.
- **medium**: a breach of CONTRIBUTING.md or plan/AGENT-BRIEF.md, a missing test, or a mistake a check could have caught.
- **low**: a smell or a style judgement.

## Pick a ticket

1. Move the **frontier**: run `python3 -I plan/lint-issues.py`, and for each `blocked, but every dependency is closed` finding run `gh issue edit N --repo PorkChopExpress86/derbynet --remove-label blocked --add-label ready-for-agent`.
2. Run `gh issue list --repo PorkChopExpress86/derbynet --label ready-for-agent --state open`. Take the lowest-numbered `kind:ticket` that the roadmap (#2) orders first, and read it with `plan/bin/issue N`.
3. Skip a ticket whose unticked items all sit under `## Still open` waiting on another open ticket, since its work has merged. Skip `kind:spec` issues too. Note each skip and its reason for the final report.

## Several tickets at once

Run tickets side by side only when `plan/bin/ticket-overlap` names no path their Scope and References share; a pair it names waits for the first to merge. In the session retro of 2026-11-24, #499 ran beside #494, and #500 and #501 waited on #499 for `server/racing/services` and the heat-intermission model. Once each draft PR is open, `plan/bin/pr-overlap <pr>` (Code review, step 1) checks the files the PRs change.

- Each ticket gets its own worktree and its own `DERBYNET_TEST_PREFIX`.
- Hand-off runs share the one load lock (`tools/hold-load-lock.sh`), so the second ticket's hand-off queues behind the first.
- Owner decisions that unblock `needs-info` tickets land first, in a small PR of their own. After it merges, edit each issue; `plan/lint-issues.py` then wants a `## Still open` section on each.

## Set up

In the main clone, run `git fetch origin`, then `git worktree add ../derbynet-wt-N -b issue-N-<slug> origin/master`. Note the time, for the summary's wall-clock (step 6.4).

## 1. Implement

Spawn a general-purpose agent with `model: opus`, briefed per plan/AGENT-BRIEF.md for ticket #N in ../derbynet-wt-N on branch issue-N-<slug>. Add to the brief:

- Follow ~/.claude/plugins/cache/mattpocock/mattpocock-skills/*/skills/engineering/implement/SKILL.md, and leave its code-review step to the lead.
- While iterating, run the test files you edited and `tools/source-checks.sh`. At each hand-off, run what `plan/bin/affected-tests` prints for that head, once: the tests the diff can reach, `tools/source-checks.sh` and ruff, with `server/tools/pytest-postgres.sh` only when it says the diff touches database code (started when the SQLite run ends), and the repeats under load at the count it gives. Repeats the ticket asks of a test file the tool gives none, such as a flake ticket's 25 of a file that is no browser test, which D85, D101 and D122 leave without a count, run locally through `tools/pytest-under-load.sh -n 4 --count N FILE`, `--flake-ticket` first for a flake ticket, with the log pasted in the PR, and CI load runs none of them (#529 retro). Leave the full suites to CI, which runs them on every push; for a shared conftest, the dependencies, the settings or a workflow the tool prints "CI runs the full suites on both databases (D85)" and only the targeted tests. When the hand-off pushes the head it ran on and the push log shows `pre-push: tools/source-checks.sh`, that run is the hand-off's, and the push log its evidence; a push whose hook ran only plan/tests leaves the source-checks run to the hand-off (D112, D129). Master's later commits that touch nothing the affected tests read need no merge, push or rerun (D113). Run the verify skill for any page or action.php change.
- Hand off once `tools/docstring_claims.py --check` on the PR body draft exits 0, and each `tools/mutate.py --listed` round ends in its tally line (`N listed: ...`). #500 ran `--check` after its push and hand-off had started, found three claims with no row, and stopped both (#500 retro). A round that printed `STOPPED` is unfinished: #494's stopped at 5 of 37, and the 12 survivors it never ran surfaced only after review started (#494 retro).
- Commit with a message that references #N, and push at the first commit without waiting for the hand-off run, so the draft PR's CI and the review run beside it. Before that first commit, run `git add -N` on each new file (the tool reads `git diff`, which skips untracked files), then `tools/operand_mutants.py --base origin/master` from server/, which takes seconds, and give each condition it lists a test that tells its mutant apart, or delete the code no outcome depends on: the lead opens the draft PR at the first push and starts review there (Code review, step 1), so review can start before any mutation round. #511's reviewers ran on e79689b9 minutes before its round 1, which left 16 of 41 alive, among them a raced-step loop's four; review raised the loop, and 2dd9bed9 deleted it (#509 and #511 retros). Return the commit SHA, what you built, what origin/master already had, the path of the PR body draft under `~/.cache/derbynet-drafts/N/`, with its claims `<details>` block and the PHP output it quotes, and each hand-off run's exit code: the PR is marked ready (step 4) only once each is 0.
- Write every log (a push, a hand-off run, a mutate round) under `~/.cache/derbynet-drafts/N/`, beside the body draft, not in the session scratchpad your instructions name: the lead's subagents all share that one. #519's `push1.log` overwrote #509's there, so #509's implementer took its push for done while its pre-push hook still ran, the commit lock then refused its next commit, and the hook's log was lost as D112 evidence (#509 retro).
- Before handing back, TaskStop every Monitor and background run you started, by its task id, and check `git status`. A `pkill -f` pattern also matches the processes of your own pre-push hook and shell: #511's `pkill -f "wt-511/server.*pytest"` killed the pytest inside 9aff42a9's hook and the agent's shell (exit 144), and threw away 11 min of hook run. A leftover one wakes the agent after its handback, and it resends its report: #283's Standards reviewer resent an unchanged one 4 times (memory `subagent-resends-report.md`).

Leave the `Co-Authored-By` line out of a brief: each agent's own session instructions give it the line that names the model it runs on. #414's brief carried the lead's Opus line to a Sonnet implementer, and its PR's commits name both models (#414 retro). No check can catch it, since git never learns which model wrote a commit.

While a subagent runs, read its notifications: when they show the same background wait for over an hour with no new tool uses, list its processes with `ps -eo pid,etime,args` instead of waiting on. #247's implementer waited about 10 h on a probe that had hung, and the lead looked only when the owner asked, after about 6 h. When a subagent resends a report unchanged, TaskStop it without messaging it.

Hand the implementer's PR body draft path to both reviewers in Code review, since the draft PR's body is a placeholder until step 3: #307's Standards reviewer ran `docstring_claims.py --check` on the placeholder and sent back what the draft already held.

## 2. Code review

1. Open the PR as a draft at the implementer's first push, so CI runs during review: `gh pr create --repo PorkChopExpress86/derbynet --draft --head issue-N-<slug> --title "<title>" --body "Part of #N. Draft: the body comes after review."`, then run `plan/bin/pr-wait <pr>` in the background, and `plan/bin/pr-overlap <pr>`. Send each PR it names to the implementer with its files, so the two tickets share one helper and agree on which PR merges first: #424 had been open 4 h when #433's draft opened, and both built a `STOP_SENDING` stub in the browser conftest (#347 retro). Start the review without waiting for CI; a red check joins the review findings in the fix round (D85).
2. Run /mattpocock-skills:code-review on ../derbynet-wt-N with origin/master as the fixed point. The spec is ticket #N, and the standards are CONTRIBUTING.md and plan/AGENT-BRIEF.md. Spawn its Standards reviewer with `model: sonnet` and its Spec reviewer with `model: opus`. A reviewer runs only what confirms or refutes a finding (the test, a `server/tools/mutate.py` mutant against that test's node id, the server run), never the full suites (owner, 2026-11-11). Before any run it expects to take more than 10 min, a reviewer messages the lead with the finding the run would confirm and waits for an OK (D85; #282's Spec reviewer spent 87 min on suites and load runs, and one 25 min run confirmed its finding). A reviewer that mutates code does it in its own copy (`git worktree add --detach ../derbynet-wt-N-rv<axis> <head>`, removed after), and a reviewer leaves a change it didn't make as it is: #247's Spec reviewer reset a mutation the Standards reviewer had running in the shared worktree. Each reviewer's brief carries Implement's TaskStop line.
3. Rank every finding.
4. Before sending, run `plan/bin/pr-overlap <pr>` again, and merge the head with each other open PR's head in a scratch `git worktree add --detach` copy and run `tools/source-checks.sh` there when either PR changes a source check or a shell script; `plan/bin/pr-merge` runs them on each PR merged with origin/master too, but only at merge time, after a CI round (#311). Then send the high and medium findings, the name and link of any failed CI check, and what the trial merge found to the implementer in one message, since its fix-round run starts once they arrive: #61's fix-round run was stopped when #304's pipefail clash came after the review findings, and the clash cost a commit and another run (#61 retro). It reproduces each one with a test, a server run, or the affected tests (owner, 2026-11-11), and reports the ones it can't reproduce (CLAUDE.md). It fixes each reproduced finding with a test that goes red before the fix, reruns what `plan/bin/affected-tests` prints for the new head, mutates only the asserts or rules the round changed (owner, 2026-11-11), and commits with a message that references #N.

## 3. PR

1. Have the implementer merge origin/master into the branch before its fix-round hand-off run (Code review's step 4), so that one run covers the merge too, and push. Have it finish the PR body draft with /mattpocock-skills:pr, with the low findings under "Left for later" and `Closes #N`, or `Part of #N` with the reason the ticket stays open under `## Still open` (`pr-evidence` refuses a body with neither: #444 named #434 in prose only, and the merge left it open), and return the path. The body fills in each `- <key>: ` line `plan/bin/pr-evidence N` prints with its evidence, or lists the key under `## Still open`.
2. Run `plan/bin/pr-evidence N --check <path> --pr <pr>` (`--pr` gives the PR's head, at which a `- CI load:` run must be and each pasted hand-off header (a line that starts `HEAD` or `head` and its sha, or `cwd` or `worktree` with them later on) must name it, or a commit before a merge at it (the tool passes any such commit, so it is for you to know D113 let the branch skip the rerun), or carry `[before run]`, and must not say its run read uncommitted edits, #427; add `--flake-ticket` for a flake ticket, whose `- CI load:` run must be the one dispatched with `-f flake_ticket=true`, named `CI load at <SHA> (flake ticket)`, which a flake ticket on a file that is no browser test has none of, since `ci-load plan` refuses a plan with no load command; add `--closes M` for each other issue the PR closes on purpose, since it refuses a closing keyword for any other issue outside code: #421's body quoted `Closes #399.` in prose, #415 retro), and send each line it names back to the implementer, since a key with no evidence after it, or with "Not done", is no evidence: #61's body draft left out the reviewer's checklist its timer box asked for (#61 retro). Then run `gh pr edit <pr> --repo PorkChopExpress86/derbynet --body-file <path>` on the draft PR.

## 4. Merge

1. Run `plan/bin/pr-merge <pr> --areas-only`, which needs no CI. When it refuses, update the branch (`gh pr update-branch <pr>`, or merge origin/master in the worktree and push) before the wait: #427's PR 476 waited a CI round, then pr-merge refused it for #398's `plan/owner-decisions.md` (#427 retro). Then run `plan/bin/pr-wait <pr>` in the background and wait for its notification.
2. When a check fails, send its name and link to the implementer, which reads the log with `gh run view --log-failed`, fixes the cause, and pushes. Then wait again. An `outage:` line from `plan/bin/ci-failures` is no code failure: see Stop and report.
3. When every check is green and the implementer's last hand-off run reported exit 0 for each command, run `gh pr ready <pr> --repo PorkChopExpress86/derbynet`, then `plan/bin/pr-merge <pr>`, which squashes and deletes the branch. It refuses a PR when origin/master moved since the PR's merge base in an area the PR also changes (plan/, .githooks/, server/tools/, .github/, a conftest.py, server/pyproject.toml, server/uv.lock), and prints what `plan/bin/affected-tests` maps the combined diff to: run that on the PR merged with origin/master, then `plan/bin/pr-merge <pr> --base-checked`, or update the branch and wait for CI again. #289 passed CI on a base without #288 and merged after it. Before that rule, when one side changed a source check and the other changed code it reads, it runs `server/tools/source-checks.sh` on the PR merged with origin/master in a temporary worktree, which took 16 min at a load average of 34, and refuses on a conflict or a failing check, or when origin/master moved during the run; so when that rule may fire, run `plan/bin/pr-merge` with `run_in_background` and wait for its notification; `--base-checked` skips only the area rule, and a pair of head and master SHAs that passed is not run again (#311). `plan/bin/pr-merge <pr> --dry-run` runs every check and stops before the merge. It also refuses a PR whose body would close an issue that has a `## Still open` section; change the PR body to `Part of #N`. If it reports the issue still open, close it, or add a `## Still open` section and move the frontier again (Pick a ticket, step 1), because that section unblocks the tickets that depend on it.
4. When the ticket has a screenshot box, post the screenshots (D75). Download the `ui-screenshots` artifact of the passing `ui (sqlite)` run with `gh run download <run> -n ui-screenshots`, into an empty scratchpad folder. Commit the ticket's PNGs to `N/` on the orphan `screenshots` branch, from a fresh clone of that branch alone in the scratchpad: `git clone --single-branch --branch screenshots --depth 1 https://github.com/PorkChopExpress86/derbynet.git <scratchpad>/shots-N`. Make a new one for each ticket, since a clone kept in /tmp did not survive #237's reboot. Commit with the owner's identity (plan/AGENT-BRIEF.md), push with `git push origin screenshots`, and remove the clone. Then comment on the ticket, embedding each image by its `raw.githubusercontent.com` URL at that commit's SHA. Tick the box only when every view it names is shot at both sizes; otherwise name the missing views under `## Still open`.

## 5. Reset

Run `git worktree remove ../derbynet-wt-N`, `git branch -D issue-N-<slug>`, and `git worktree prune`. Run `git fetch origin`, and confirm origin/master holds the merge commit.

## 6. Retro

1. Ask the implementer, which holds the primary source, to list every mistake and slow spot in this ticket's run, each with its evidence: failed CI runs, reruns, wrong guesses about the code, slow searches, and review findings a check could have caught first. Add any CI failure it doesn't list.
2. Spawn a general-purpose agent with `model: opus` and give it that list. It follows ~/.claude/plugins/cache/mattpocock/mattpocock-skills/*/skills/engineering/retro/SKILL.md, ranks each candidate, and fixes every high and medium one:
   - It prefers a deterministic check (a test, ruff rule, pre-push hook, or CI step) to prose, and keeps CLAUDE.md to navigation pointers.
   - Repo fixes go in `git worktree add ../derbynet-wt-retro-N -b retro-N origin/master`. It runs the affected checks, commits, pushes, and returns a PR body written with /mattpocock-skills:pr.
   - When the ticket's PR changed a skill's model under `.agents/skills`, the retro PR moves that skill's sync commit to the squash commit, as its SKILL.md says, after rerunning the skill's check. heat-intermission's stayed at 7c3cd879 through #500, #501 and #514 (#501 and #514 retro).
   - Memory files go straight into ~/.claude/projects/-mnt-samsung-dev-derbynet/memory/.
   - Permissions in settings files stay as they are, and a change to ~/.claude/CLAUDE.md is only proposed.
   - It returns the low findings, and creates no branch when nothing is high or medium.
3. If it returned a PR body, run `plan/bin/pr-evidence --check <path> --head "$(git -C <worktree> rev-parse HEAD)"` on it (a hand-off header in the body must name that head, or the commit before a merge at it, or carry `[before run]`: #427) and send each line it names back (#411's retro body quoted `Closes #59` in prose), then open the PR. When the PR adds or changes a tool under plan/bin, server/tools or .githooks, or a CI workflow, spawn the Standards reviewer of Code review's step 2 on it (`model: sonnet`, in its own `git worktree add --detach` copy), with the tool's tests as its spec, and send its high and medium findings to the retro agent before the merge. A PR that changes only docs or memory skips the review. #285, #286, #288 and #289 merged with CI alone. Then merge and reset as in steps 4 and 5.
4. Give a 5-line summary: the ticket PR link and what landed, the low review findings left over, the retro fixes (with PR link) and any proposed ~/.claude/CLAUDE.md change, the low retro findings left over, and the ticket's wall-clock from the time noted at Set up with its biggest step (#283 spent about 2.5 h on full suites and load runs before the owner raised it). Then go back to Pick a ticket with new subagents.

## Pausing mid-ticket

When the loop must stop inside a ticket (a reboot, a stop condition), write `~/.claude/projects/-mnt-samsung-dev-derbynet/resume-N.md` from the implementer's last report, and a memory note pointing at it. After #237's reboot, its file gave a skip count without the skips, which the second implementer guessed wrong in the PR body; listed full-suite commands without a `DERBYNET_TEST_PREFIX`; and called a load run unstarted that had begun (`server/var/load-runs/20261108-120936-2056795`). The file holds:

- the worktree, the branch, the pushed head, and each commit that holds real work;
- each local commit no remote has, as `git -C <worktree> log --oneline HEAD --not --remotes` prints it (on a branch with no upstream too): #366's merge of master (743255ea) was left unpushed across the 2026-11-18 reboot, and only its resume file's line brought it back;
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
