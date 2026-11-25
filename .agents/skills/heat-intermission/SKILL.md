---
name: heat-intermission
description: Formally verify DerbyNet's current-heat state machine, result services, and playlist operator-gate intermission (D27, D28, D32, D135) with TLA+/TLC. Use before or after changing server/racing/engine/heat_state.py, server/racing/services/heats.py, server/racing/services/results.py, or server/racing/services/playlist.py.
---

# Heat Lifecycle & Playlist Intermission Verification

Checks every reachable interleaving of DerbyNet's heat transitions, result writes/clears/restores, reruns/reinstates, and the playlist operator-gate intermission using TLA+ and TLC.

## Coverage & sync commit

- **Last verified commit:** `7c3cd879a90246a83044ae86ece6206935892679`
- **Covered files and functions:**
  - `server/racing/engine/heat_state.py`: `State`, `Chart.allowed`, `Chart.round_complete`, `TABLE`, `decide`, `_RACING_ON_EXTRAS`
  - `server/racing/services/heats.py`: `transition`, `select_heat`, `set_racing`, `rerun`, `reinstate`, `rebuild_waiting_final`, `results_locked`, `locked_failure`
  - `server/racing/services/results.py`: `record_timer_result`, `timer_result_locked`, `record_manual_result`, `discard_heat`, `restore_results`
  - `server/racing/services/playlist.py`: `start_playlist`, `round_ended`, `_after_round`, `_activate`, `start_waiting_step`, `skip_waiting_step`, `rebuild_waiting_round`, `_clear_changed_final`

Before trusting a pass, check whether covered code changed since the sync commit:

```sh
git diff --stat 7c3cd879a90246a83044ae86ece6206935892679 -- \
  server/racing/engine/heat_state.py \
  server/racing/services/heats.py \
  server/racing/services/results.py \
  server/racing/services/playlist.py
```

Move the sync commit forward **only** after confirming that `.agents/skills/heat-intermission/HeatIntermission.tla` still describes the code at the new commit, and only to a commit on master: `plan/tests/test_skill_sync_commits.py` fails a sha that is not on `origin/master`. A PR's branch commits never reach master, since `pr-merge` squashes, so a ticket PR leaves the sync commit as it is and a follow-up PR names the squash commit after the merge (#499 retro).

## Properties checked

| Invariant | Promise | Harm if broken | Baseline at the last verified commit |
| --- | --- | --- | --- |
| `RacingHeatAllowed` | While racing mode is on, the current heat exists and has no results yet (`Chart.allowed`). | Timer stages or scores a heat that already has results, overwriting or corrupting scores. | **PASS** (36,971 distinct states checked; failed before #499, when `restore_results` left `racing=True` on a scored heat) |
| `WaitingFinalMatchesStandings` | While an intermission is open and the final has not raced, the waiting final's roster matches Round 1's current standings (D27, D135). | The wrong kids race in the final after a Round 1 result change. | **FAIL**, only through #501's path (real bug: `ManualWriteR2` + `DiscardHeat` + `Rerun`, a result on the waiting final that leaves the intermission open, which D136 closes). The `select_heat` + `rerun` + `timer_result` trace is gone since #500, where `round_ended` rebuilds the unraced final (D135). |
| `NoIntermissionAfterFinalRaces` | Once any heat of the final has a result, the intermission for that final is closed (D28). | Intermission stays open after the final has started scoring; `skip_waiting_step` can skip an already-raced final. | **FAIL** (real bug: `record_manual_result` on paused waiting final does not close intermission) |
| `LockedFeederProtected` | Once any heat of the final has a result, no caller without `CONTROL_RACE` can alter Round 1's results (D32). | A volunteer without race-control permission alters qualifying heats after the final has begun. | **PASS** (36,971 distinct states checked) |

TLC also checks `NoDeadlock` across all 36,971 states (**PASS**).

## How to run

Run the standalone check (uses Java 11+ and pinned `tla2tools.jar` `v1.7.4`, cached under `~/.local/share/tla2tools/`):

```sh
./.agents/skills/heat-intermission/check.sh
```

To check a single invariant:

```sh
./.agents/skills/heat-intermission/check.sh LockedFeederProtected
```

### Reading results with `/statechart`

Always inspect TLA+ results using the pstack `/statechart` skill against:
- Spec: `.agents/skills/heat-intermission/HeatIntermission.tla`
- Config: `.agents/skills/heat-intermission/HeatIntermission.cfg`
- View: `.agents/skills/heat-intermission/HeatIntermission.view.mjs`

`/statechart` reports each invariant violation as its shortest step-by-step path in plain words and renders a Mermaid statechart of the transitions.

## Telling a real bug from a modeling error

When TLC reports a counterexample:

1. Read the counterexample as a sequence of service calls (`record_timer_result`, `record_manual_result`, `select_heat`, `set_racing`, `rerun`, `reinstate`, `discard_heat`, `restore_results`, `start_waiting_step`, `skip_waiting_step`).
2. Replay that exact sequence against the real Django services in `server/` with `uv run pytest` (see `server/racing/tests/test_result_changes_rebuild_the_final.py` for the `two_step_playlist` fixture pattern).
3. If the real Django services reproduce the violation, it is a **real bug** — do not weaken the invariant or the model.
4. If the real Django services reject a step or behave differently from the model, the model has drifted — update `HeatIntermission.tla` to match the real code and re-run.

## Updating the model after intentional code changes

1. Update the affected action(s) in `.agents/skills/heat-intermission/HeatIntermission.tla` so each action still matches the exact guards, state writes, and `rebuild_waiting_final` / `locked_failure` calls in the covered Python files.
2. Run `./.agents/skills/heat-intermission/check.sh` and inspect with `/statechart`.
3. Confirm every newly passing invariant is still able to fail by temporarily reverting the guard/fix in the model and verifying TLC catches it.
4. Update the baseline table once the model matches the code. Leave **Last verified commit** and the `.tla` header's sync commit as they are until the PR merges, then name its squash commit in both, and in the `git diff --stat` above, in a follow-up PR.

## Scope & what is NOT covered

- **Bounds:** One group with a 2-step playlist (Round 1 with 2 heats, waiting Round 2 Final with 1 heat) and abstract Round 1 results `{"none", "v1", "v2"}`.
- **Assumptions:** Each service call executes atomically under `lock_race_state(event.id)` (as enforced by `transaction.atomic()` + `SELECT ... FOR UPDATE`).
- **Not modeled:** Master schedule interleaving, `EmptiedHeat` retention/release, `schedule.reschedule` / `close_lanes` snapshot retries, `timer-test` mode, `delete_results(round=None)` whole-event clears, and multi-group aggregate finals.
