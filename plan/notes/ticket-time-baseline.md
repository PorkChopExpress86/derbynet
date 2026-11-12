# Ticket time baseline: #282 and #283 (2026-11-11)

Where a ticket's wall-clock time went, from the implementer's start to the merged PR, measured for the perf branch of `/diagnosing-bugs` (measure first, fix second). Nothing here changes code or rules. Section 7 lists inputs for `/grill-with-docs`, not decisions.

**Sources.**
- The lead's session log, `4216c3f5-a6ed-4fa9-b4d3-b3652b7241da.jsonl` (called "lead" below).
- Its subagent logs under `subagents/`: implementer #282 `a482304`, implementer #283 `ad30b82`, Standards #282 `af1ddbb`, Spec #282 `a20eda1`, Standards #283 `a63c51f`, Spec #283 `a26e0bb`.
- The run outputs those agents left in the session scratchpad. Pytest's own "in Ns" line gives a run's length; a file's last write gives its end.
- `journalctl` on this machine, for the suspend entries and wake times.
- `gh run list` / `gh pr view` for PRs #284, #285, #287 and #288.
- Timed runs on master `4ea65152`, 2026-11-12, 8 cores, `-n auto`.

All times are UTC on 2026-11-11 unless they say otherwise. This machine's journal is UTC−6: its local 07:22 is 13:22 UTC.

**Critical path** means the step delayed the merge. A step marked n ran beside one that did.

## 1. Tables

### #282 (PR #284): from the owner's approval at 11:46 to the merge at 17:22, 336 min

| Step | Start | End | Min | Critical path | Source |
|---|---|---|---|---|---|
| Draft tickets A and B, lint, show the owner (lead) | 05:58 | 06:00 | 2 | y | lead, Bash 05:58:23–05:59:23 |
| Wait for the owner's approval | 06:00 | 11:46 | 346 | owner (not counted) | lead, USER "I approve" 11:46:06 |
| Create the issues, add the worktree, brief the implementer | 11:46 | 11:47 | 1 | y | lead 11:46:10–11:47:02 |
| Implementer: reproduce red, diagnose (pytest 9.2 bump), report to the lead | 11:47 | 11:51 | 4 | y | a482304 SendMessage 11:51:12 |
| Implementer: fix, regression test, guard, mutations | 11:51 | 12:01 | 10 | y | a482304 11:52–12:01 |
| Full non-browser suite, SQLite | 12:01 | 12:09 | 8.0 | y | a482304 bg "Full non-browser suite on SQLite" |
| Full browser suite, SQLite | 12:09 | 12:22 | 12.9 | y | a482304 bg; pytest "in 772.46s" |
| Full non-browser suite, Postgres | 12:22 | 12:33 | 10.9 | y | a482304 bg |
| Full browser suite, Postgres (1 replay flake, red) | 12:33 | 12:49 | 15.6 | y | a482304 bg |
| Replay 10× without load; red head pushed by mistake; version check | 12:49 | 12:54 | 5 | y | a482304 12:50–12:54 |
| Rerun the Postgres browser suite at the final head | 12:54 | 13:10 | 15.9 | y | a482304 bg "Rerun Postgres browser suite at final head" |
| Push and hand back | 13:10 | 13:11 | 1 | y | a482304 push 13:10:49, handback 13:11:36 |
| Lead edits the issue body, starts reviewers | 13:11 | 13:12 | 1 | y | lead 13:11:40–13:12:25 |
| Standards review (sonnet) | 13:12 | 13:17 | 5 | n | af1ddbb handback 13:16:42 |
| Spec review (opus): its own checks and collections | 13:12 | 13:22 | 10 | y | a20eda1 13:12–13:21 |
| **Machine suspended.** Spec's full non-browser suite (9.2.0, then 9.1.1) was running and never finished; Spec stopped it at 14:01 | 13:22 | 14:01 | 39 | y | journal: suspend 07:22:44 local, "slept 37m41s" at 08:00:25; `nb92.txt` reached 19%; a20eda1 TaskStop 14:01:34 |
| Spec: collection-order check | 14:01 | 14:03 | 2 | y | a20eda1 14:01–14:03 |
| Spec: test_replay 5× under load on Postgres, pytest 9.2.0 then 9.1.1 | 14:03 | 14:55 | 52.4 | y | `rv/load92.out` "1 failed, 204 passed … in 1719.72s"; `rv/load911.out` "205 passed … in 1425.01s" |
| Spec: remote-camera test 25× under load on Postgres, both versions | 14:56 | 15:21 | 25 | y | `rv/cam92.out` "125 passed … in 758.37s"; `rv/cam911.out` |
| Spec hands back; lead relays 5 findings | 15:21 | 15:23 | 2 | y | a20eda1 15:22:36; lead SendMessage 15:23:04 |
| Fix round: 5 findings, edits and ticket command | 15:23 | 15:32 | 9 | y | a482304 15:25–15:31 |
| Fix round: full browser suite, SQLite | 15:32 | 15:46 | 14.4 | y | a482304 bg |
| Fix round: full browser suite, Postgres | 15:46 | 16:02 | 15.5 | y | a482304 bg |
| Push, PR body, hand back; PR created | 16:02 | 16:06 | 4 | y | a482304 handback 16:06:13; PR #284 created 16:06:27 |
| CI attempt 1 (apt mirror outage, 10 jobs red) | 16:06 | 16:18 | 12 | y | run 43054997286 a1; lead notify 16:18:09 |
| CI attempt 2, same outage | 16:18 | 16:30 | 12 | y | lead rerun 16:18:30, notify 16:29:50 |
| Wait for the owner (stop condition) | 16:30 | 16:41 | 11 | y | lead USER 16:41:08 |
| CI attempt 3, same outage; diagnosis | 16:41 | 16:53 | 12 | y | lead notify 16:52:34 |
| Mirror fix PR #285, its CI, merge | 16:53 | 17:10 | 17 | y | PR #285 created 16:55:24, merged 17:10:02 |
| CI on the merged head | 17:10 | 17:22 | 12 | y | run 43065535338, 11 min |
| pr-merge | 17:22 | 17:22 | 0 | y | PR #284 merged 17:22:27 |
| Retro (after the merge) | 17:24 | 17:47 | 23 | n | a8376e8 |

**Review findings and the fix rounds they caused.**
- Standards: 5 min, its own targeted runs only.
- Spec: 130 min, 87 of them test runs, plus the 39 min suspended.
- Spec's 5 findings came back together and cost one fix round of 43 min. 30 of those minutes were both full browser suites, rerun because `conftest.py` changed.

### #283 (PR #287): from the implementer's start at 18:01 to the merge at 00:01 on 11-12, 360 min

| Step | Start | End | Min | Critical path | Source |
|---|---|---|---|---|---|
| Lead adds the worktree, briefs the implementer | 18:01 | 18:01 | 0 | y | lead 18:01:17–18:01:32 |
| PHP evidence (php-scratch), 4 option probes on 2 fonts, question to the lead | 18:01 | 18:17 | 16 | y | ad30b82 SendMessage 18:17:26 |
| Lead's ruling (option A) | 18:17 | 18:17 | 0.2 | y | lead SendMessage 18:17:37 |
| Build option A; mutation round 1 (32 mutants) | 18:17 | 18:43 | 26 | y | ad30b82 bg "Run mutation rounds on both fonts" 18:27–18:37; retro 10.0 min |
| 25-count load run, default font | 18:44 | 18:54 | 9.5 | y | ad30b82 bg; retro 9:28 |
| 25-count load run, DejaVu | 18:54 | 19:04 | 9.6 | y | ad30b82 bg; retro 9:31 |
| Gap message to the lead; answer | 19:05 | 19:05 | 0.2 | y | ad30b82 19:05:28; lead 19:05:42 |
| Fix the gaps; mutation round 2 (36 mutants) | 19:05 | 19:19 | 14 | y | ad30b82 bg "Mutation round 2" 19:07–19:19; retro 11.2 min |
| `-n 0`, full and browser suites on SQLite and Postgres | 19:19 | 20:20 | 60.4 | y | ad30b82 bg; retro 9:27 / 17:10 / 12:44 / 19:32 |
| 25-count load runs on the final file, both fonts | 20:20 | 21:00 | 39.5 | y | ad30b82 bg; retro 20:25 + 18:51 |
| Push, hand back | 21:00 | 21:02 | 2 | y | push 21:00:58, handback 21:02:49 |
| Lead starts reviewers | 21:02 | 21:03 | 1 | y | lead 21:03:25 |
| Standards review (sonnet) | 21:03 | 21:13 | 10 | n | a63c51f first handback 21:12:54; 4 resends to 21:14:28 |
| Spec review (opus) | 21:03 | 21:16 | 13 | y | a26e0bb handback 21:16:02 |
| Lead relays 7 findings (6 Standards, 1 Spec) | 21:16 | 21:16 | 0.3 | y | lead SendMessage 21:16:23; ad30b82 retro items 1–7 |
| Fix round: edits, mutation round 3 (45 mutants) | 21:16 | 21:49 | 33 | y | ad30b82 bg "Mutation round 3" 21:20–21:49; retro 26.8 min |
| Final: 25-count load run, default font | 21:50 | 22:02 | 12.1 | y | `f-load-noto.out` "150 passed in 723.88s" |
| Final: 25-count load run, DejaVu | 22:02 | 22:13 | 11.2 | y | `f-load-dejavu.out` "150 passed in 674.08s" |
| Final: `-n 0` names test, then this file | 22:13 | 22:14 | 1.4 | y | `f-n0.log` "in 81.73s" |
| Final: full non-browser suite, SQLite (pytest 9:22) | 22:14 | 22:17 | 3 | y | `f-full.log` "in 562.49s", last write 23:27 |
| **Machine suspended** during that suite | 22:17 | 23:20 | 63 | y | journal: suspend 16:17:18 local, "slept 1h3m23s" at 17:20:40 |
| Final: the rest of that suite | 23:20 | 23:27 | 6 | y | `f-full.log` |
| Final: full browser suite, SQLite | 23:27 | 23:44 | 16.6 | y | `f-browser.log` "in 997.86s", last write 23:44:06 |
| Final: Postgres full suite, stopped under the new rules | 23:44 | 23:46 | 2 | y | `final.log` "Terminated"; TaskStop 23:46:08 |
| Owner's testing question and the new rules (PR #288) | 22:01 | 23:45 | 104 | overlaps the rows above | lead USER 22:01:29, 23:45:38 |
| Push, PR body, hand back; PR created | 23:46 | 23:47 | 1 | y | push 23:46:41; PR #287 created 23:47:49 |
| CI (first time green) | 23:47 | 00:01 | 14 | y | run 43122537615, 13 min |
| pr-merge, D75 screenshots, issue boxes | 00:01 | 00:03 | 2 | y | merged 00:01:41; lead 00:02–00:03 |
| Retro (after the merge) | 00:04 | 00:23 | 18 | n | a1a031f |

**Review findings and the fix rounds they caused.**
- Standards: 10 min and 6 findings.
- Spec: 13 min and 1 finding.
- The reviews themselves were cheap. The 7 findings cost one fix round of 151 min:
  - 33 min of edits and mutation round 3;
  - 23 min of load runs;
  - 27 min of suites;
  - 63 min suspended.

### Waiting on a run with nothing else to do

Gaps of more than 5 min between an agent's own turns, while its background run went on (from the subagent logs):

| Agent | Idle on its own runs | Of its active minutes |
|---|---|---|
| Implementer #282 | 85 min (12:09–12:49, 12:54–13:10, 15:32–16:02) | 127 (84 build + 43 fix) |
| Spec #282 | 111 min (13:22–14:01, 14:09–14:55, 14:56–15:21) | 130 |
| Implementer #283 | ~285 min (18:45–19:04, 19:07–20:20, 20:21–21:00, 21:21–21:49, 21:51–23:46) | 331 (181 build + 151 fix) |
| Standards and Spec #283, Standards #282 | 0 | — |

The agents spent most of their time waiting on serial runs. They didn't run the runs alongside other work, or alongside each other.

## 2. Top steps by critical-path minutes

| Rank | Step | #282 | #283 | Total | Share |
|---|---|---|---|---|---|
| 1 | Local full suites | 93 | 89 (60 + 29 at the final head) | 182 | 28% / 25% |
| 2 | Repeats: load runs and mutation rounds | 0 by the implementer | load 82, mutation ~54 | 136 | — / 38% |
| 3 | Machine suspend while tests ran | 39 | 63 | 102 | 12% / 18% |
| 4 | Reviewer test runs | 87 (Spec) | 13 (whole review) | 100 | 26% / 4% |
| 5 | CI | 76 (64 of it the outage, now fixed) | 14 | 90 | 23% / 4% |
| — | Writing code, design, diagnosis | ~30 | ~45 | ~75 | 9% / 13% |
| — | The lead's own time between steps | ~5 | ~3 | ~8 | 1–2% |

- **Test runs and CI, not counting the suspends:** 256 of #282's 336 min (76%) and 239 of #283's 360 min (66%).
- **The suspends add 39 and 63 min.** Both happened while an agent was waiting on a run.
- **The lead was not a bottleneck.** Rulings and relays took seconds: the 18:17 ruling came 11 s after the question.
- **Diagnosis was fast:** #282 went red and its cause was named in 4 min.
- **The owner's waits are outside the loop:** 5 h 46 min for ticket approval overnight, and 11 min at the outage stop.

## 3. What the new testing rules (PR #288, merged 2026-11-12 00:31 UTC) would have saved — estimates

All of #282 and #283 ran before PR #288. These figures are estimates from the measured runs, with no new runs of the old heads.

- **#282, about 60 min of 336.**
  - `affected-tests` still says "everything on both databases" for this diff (`pyproject.toml`, `uv.lock`, a shared `conftest.py`). So the implementer's suites (93 min) stay.
  - The saving is the reviewer rule ("run only what confirms a finding"). Spec's 87 min of runs shrink to the one run that confirmed its finding, at most the 25 min camera run.
  - It adds 25 repeats of `test_harness.py` on Postgres (gap), and a DejaVu run of 11 files. A comparable 8-file DejaVu run measured 2.2 min.
  - Separately, #285's mirror and #290's outage rule remove the 64 + 11 min the outage cost.
- **#283, about 140 min of 360.** Measured on master today, PR #287's scoped commands take:

  | Command | Min |
  |---|---|
  | non-browser | 0.6 |
  | browser | 4.0 |
  | browser, DejaVu | 2.2 |
  | 5-repeat load, one font | 2.3 |
  | `source-checks.sh` | 0.9 |

  Applied to the old rows:
  - suites 89 → about 16, at two heads;
  - load runs 82 → about 14, at three heads.

  Mutation rounds (~54 min) barely change, because `-k` on the target test is the same file they already ran.
- **Neither rule touches the suspends** (102 min).

## 4. The slowest tests and CI jobs

Measured on master `4ea65152`, SQLite, `--durations=30`, under `-n auto` on 8 cores.

- **Full non-browser suite:** 8068 passed in 10:07 (609 s wall).
- **Full browser suite:** 578 passed, 79 skipped, in 16:27 (989 s wall).

Under xdist, a test's "setup" time includes its worker's database setup. So the long setups below partly measure per-worker setup, not the test's own fixture.

| Non-browser, slowest | s | Browser, slowest | s |
|---|---|---|---|
| setup `tests/test_transcript_diff_live.py::test_two_runs_with_shifted_ids_match` | 68.4 | `test_kiosk_screens_tool.py::test_overlapping_text_and_text_cut_with_an_ellipsis_fail_their_pages` | 32.5 |
| `ui/tests/test_poll_signatures.py::…[/kiosks/board/]` | 51.8 | `test_smoke.py::test_every_registry_page_opens_without_errors` (8 cases) | 21.4–29.0 each, ~207 total |
| setup `tests/test_load_test_live.py::test_each_client_type_polls_at_its_rate[poll.kiosk]` | 47.4 | `test_checkin.py` (weigh-in screenshots, station sync, owner flow; 5 cases) | 18.2–27.4 each |
| `ui/tests/test_poll_signatures.py::…[/dashboard/live/]` | 41.4 | `test_kiosk_thumbnails.py` (4 cases) | 21.4–26.2 each |
| `racing/engine/tests/test_schedule_parity.py::test_reference_port_matches_php_to_60_cars[2]` | 35.4 | `test_dashboard.py::test_the_race_day_runs_through_both_finals` (2) | 18.1–25.3 |
| setup `racing/engine/tests/test_reschedule_parity.py::test_every_recorded_reschedule_is_replayed` | 32.2 | `test_awards.py::test_judging_parity_grid_…` | 24.3 |
| setup `test_reschedule_parity.py::test_php_inputs_give_the_php_reply[…042…]` | 28.2 | `test_groups.py` (2) | 21.2–22.6 |
| `legacy_api/tests/test_race_control_parity.py::test_the_schedule_scenario_matches_php` | 17.6 | `test_kiosks.py::test_kiosk_dashboard_parity` (2) | 20.6–21.0 |
| next 22 | 9–17 each | next 14 | 18–20 each |

**CI's slowest jobs** (last green master run, all in parallel):

| Job | Min |
|---|---|
| `ui (postgres)` | 11 |
| `exercise-one-database.sh (postgres)` | 11 |
| `pytest (postgres)` | 10 |
| `ui (sqlite)` | 9 |
| `exercise-one-database.sh (sqlite)` | 8 |

A PR waits 11–14 min on CI: #284 took 11 min, #287 took 13 min. CI runs the whole check in 11–14 min. The same check locally, serial, took 48–60 min.

## 5. Gaps

- **25 repeats of `test_harness.py` on Postgres** (what `affected-tests` now asks of #284's diff): not run. #282 never ran it, and section 3 leaves it out.
- **#284's 11-file DejaVu run:** not run. It is estimated from the 8-file run (2.2 min).
- **Old heads under the new rules:** the section 3 figures apply today's master timings to the old diffs. They are not runs of `a5c80519` / `d7511b90`.
- **Mutation rounds:** the minutes are pytest's own times from the implementer's retro (10.0, 11.2, 26.8 min) plus uv startup per mutant. That startup was not measured separately.
- **Postgres suites today:** not rerun for this baseline. The Postgres figures are from the #282/#283 logs (non-browser 10.9–12.7 min, browser 15.5–19.5 min).
- **Idle minutes:** these come from gaps between an agent's turns, so a short read during a run is counted as active.
- **Where the run logs are:** the session scratchpad, which goes away with the session. The numbers above are copied from them.

## 6. The machine sleeps during runs

The journal shows three suspends while agents were running tests:
- 13:19–13:22 UTC: several short ones;
- 13:22–14:00 UTC: 37 min 41 s;
- 22:17–23:20 UTC: 63 min 23 s.

The background runs froze with the machine. Pytest's own clock (monotonic) left the sleep out, so `f-full.log` says 9:22 for a suite that took 73 min of wall clock. Nothing in the loop noticed.

## 7. Candidates (inputs for `/grill-with-docs`, not decisions)

1. **Local full suites (182 min over both tickets).**
   - (a) Run the full suites once, at the final head only. While iterating, run just the changed test file. Both tickets reran full suites after every commit.
   - (b) For diffs where `affected-tests` says "everything" (shared conftest, `pyproject.toml`, `uv.lock`), push and let CI's parallel 11-minute run be the full check, instead of a 48–60 min serial local run. That would mean changing the owner's rule.
   - (c) Where a local full run is still wanted, start SQLite and Postgres at once with separate prefixes, not one after another.
2. **Repeats (#283: load 82 min, mutation ~54 min).**
   - (a) PR #288 already cut load runs on deterministic tests to 5 per font, measured at 2.3 min per font.
   - (b) Run each mutation round in one pytest process with `-n`, or mutate only the asserts changed since the last round. Round 3's 45 mutants took 26.8 min, at 18–43 s each.
3. **The machine suspends during runs (102 min).**
   - (a) Wrap long runs in `systemd-inhibit --what=sleep`, in `pytest-under-load.sh`, `pytest-postgres.sh` and the loop's run commands.
   - (b) The owner turns off suspend on AC power while the loop runs.
   - (c) Either way, a loop check that compares wall clock with pytest's own time would catch the next one.
4. **Reviewer test runs (#282 Spec: 87 min).**
   - (a) PR #288's reviewer rule covers it. Check on the next ticket that a reviewer's runs stay under 25 min.
   - (b) A reviewer asks the lead before any run over 10 min, naming the finding it would confirm.
5. **Slow fixtures, if tests become the main cost.**
   - Live and parity setups take 30–68 s each: `test_transcript_diff_live`, `test_load_test_live`, `test_reschedule_parity`.
   - `test_smoke`'s 8 registry-page cases take ~207 s of browser time.
   - These are candidates for `/improve-codebase-architecture` or a CI-only marker.
