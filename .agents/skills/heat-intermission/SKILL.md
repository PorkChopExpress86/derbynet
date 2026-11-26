---
name: heat-intermission
description: Formally verify DerbyNet's current-heat state machine, result services, and playlist operator-gate intermission (D27, D28, D32, D135, D136, D141, D143, D144) with TLA+/TLC. Use before or after changing server/racing/engine/heat_state.py, server/racing/services/heats.py, server/racing/services/results.py, server/racing/services/playlist.py, or server/racing/services/result_import.py.
---

# Heat Lifecycle & Playlist Intermission Verification

Checks every reachable interleaving of DerbyNet's heat transitions, result writes/clears/restores and imports, reruns/reinstates, and the playlist operator-gate intermission using TLA+ and TLC.

## Coverage & sync commit

- **Last verified commit:** `603cf1fa678ac7da614e1eb6f3c4c87ac5d68338`
- **Covered files and functions:**
  - `server/racing/engine/heat_state.py`: `State`, `Chart.allowed`, `Chart.round_complete`, `TABLE`, `decide`, `_RACING_ON_EXTRAS`
  - `server/racing/services/heats.py`: `transition`, `select_heat`, `set_racing`, `rerun`, `reinstate`, `advance`, `rebuild_waiting_final`, `results_locked`, `locked_failure`
  - `server/racing/services/results.py`: `record_timer_result`, `timer_result_locked`, `record_manual_result`, `discard_heat`, `restore_results`
  - `server/racing/services/playlist.py`: `start_playlist`, `round_ended`, `_after_round`, `_activate`, `start_waiting_step`, `skip_waiting_step`, `rebuild_waiting_round`, `_waiting_final`, `rebuild_fed_finals`, `_clear_changed_final`, `_waits`, `close_intermission_on_result`, `set_step_waits`
  - `server/racing/services/result_import.py`: `import_result`

Before trusting a pass, check whether covered code changed since the sync commit:

```sh
git diff --stat 603cf1fa678ac7da614e1eb6f3c4c87ac5d68338 -- \
  server/racing/engine/heat_state.py \
  server/racing/services/heats.py \
  server/racing/services/results.py \
  server/racing/services/playlist.py \
  server/racing/services/result_import.py
```

Move the sync commit forward **only** after confirming that `.agents/skills/heat-intermission/HeatIntermission.tla` still describes the code at the new commit, and only to a commit on master: `plan/tests/test_skill_sync_commits.py` fails a sha that is not on `origin/master`. A PR's branch commits never reach master, since `pr-merge` squashes, so a ticket PR leaves the sync commit as it is and a follow-up PR names the squash commit after the merge (#499 retro).

## Properties checked

| Invariant | Promise | Harm if broken | Baseline at the last verified commit |
| --- | --- | --- | --- |
| `RacingHeatAllowed` | While racing mode is on, the current heat exists and has no results yet (`Chart.allowed`). | Timer stages or scores a heat that already has results, overwriting or corrupting scores. | **PASS** (190,080 distinct states checked; failed before #499, when `restore_results` left `racing=True` on a scored heat) |
| `WaitingFinalMatchesStandings` | While the final has not raced, the waiting final's roster matches Round 1's current standings: with the intermission open (D27, D135), and with it closed once the final is made for a step that waits, unless a Round 1 change kept its roster (D141). The ghost `kept` marks a change made while the final had a result (D136) or while its step did not wait (PHP). A rerun of a Round 1 heat is such a change (D144). | The wrong kids race in the final after a Round 1 result change. | **PASS** (190,080 distinct states checked; failed before #511 on `SelectHeat` + `ManualWriteR1`, a result written by hand on Round 1 with the intermission closed, and fails again with `Rebuilt`'s D141 branch taken out; with `Rerun`'s rebuild taken out it fails on `TimerFinishR1` x2 + `Rerun` (D144). Failed before #501 on traces that wrote a result to the waiting final while the intermission was open, such as `ManualWriteR2` + `DiscardHeat` + `Rerun`. D136 closes the intermission on that result.) |
| `NoIntermissionAfterFinalRaces` | Once any heat of the final has a result, the intermission for that final is closed (D136). | Intermission stays open after the final has started scoring; `skip_waiting_step` can skip an already-raced final. | **PASS** (190,080 distinct states checked; failed before #501, when `record_manual_result` on the paused waiting final left the intermission open. With the close taken out of `ManualWriteR2`, of `RestoreResults` or of `ImportR2` (D143, #519) it fails again.) |
| `LockedFeederProtected` | Once any heat of the final has a result, no caller without `CONTROL_RACE` can alter Round 1's results (D32). | A volunteer without race-control permission alters qualifying heats after the final has begun. | **PASS** (190,080 distinct states checked) |
| `NoRacingDuringIntermission` | Racing never runs with the intermission open (#514), outside the timer test, which the model leaves out (#515). | The dashboard shows the intermission over a race in progress, and still offers Skip. | **PASS** (190,080 distinct states checked; failed before #514, when the playlist's racing-on for a step that does not wait left the intermission open. With the close taken out of `RoundOneEnded`'s racing-on branch it fails again, on a trace that ends in `NextNoResult` by `advance`. No page reaches that trace: the dashboard's Next and `heat.select` call `select_heat`, which closes the intermission first.) |

TLC also checks `NoDeadlock` across all 190,080 states (**PASS**).

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

`/statechart` installs TLC with mise, which this machine lacks (`spawn mise ENOENT`, hit by #500, #501 and #514). Run `check.sh` once, to cache the jar, then give `/statechart` that jar and Java, with `--out` a folder that does not exist yet:

```sh
TLA2TOOLS=$HOME/.local/share/tla2tools/tla2tools-1.7.4.jar JAVA=java \
  <statechart skill dir>/scripts/statechart .agents/skills/heat-intermission/HeatIntermission.tla \
  --config .agents/skills/heat-intermission/HeatIntermission.cfg \
  --view .agents/skills/heat-intermission/HeatIntermission.view.mjs --out <new folder>
```

## Telling a real bug from a modeling error

When TLC reports a counterexample:

1. Read the counterexample as a sequence of service calls (`record_timer_result`, `record_manual_result`, `import_result`, `select_heat`, `advance`, `set_racing`, `rerun`, `reinstate`, `discard_heat`, `restore_results`, `start_waiting_step`, `skip_waiting_step`, `set_step_waits`).
2. Replay that exact sequence against the real Django services in `server/` with `uv run pytest` (see `server/racing/tests/test_result_changes_rebuild_the_final.py` for the `two_step_playlist` fixture pattern).
3. If the real Django services reproduce the violation, it is a **real bug** — do not weaken the invariant or the model.
4. If the real Django services reject a step or behave differently from the model, the model has drifted — update `HeatIntermission.tla` to match the real code and re-run.

## Updating the model after intentional code changes

1. Update the affected action(s) in `.agents/skills/heat-intermission/HeatIntermission.tla` so each action still matches the exact guards, state writes, and `rebuild_waiting_final` / `locked_failure` calls in the covered Python files.
2. Run `./.agents/skills/heat-intermission/check.sh` and inspect with `/statechart`.
3. Confirm every newly passing invariant is still able to fail by temporarily reverting the guard/fix in the model and verifying TLC catches it.
4. Update the baseline table once the model matches the code. Leave **Last verified commit** and the `.tla` header's sync commit as they are until the PR merges, then name its squash commit in both, and in the `git diff --stat` above, in a follow-up PR.

## Scope & what is NOT covered

- **Bounds:** One group with a 2-step playlist (Round 1 with 2 heats, Round 2 Final with 1 heat, which waits until `set_step_waits` switches that off), abstract Round 1 results `{"none", "v1", "v2"}`, and a cut that picks 2 racers or more, or one (D28), and changes at any time. `continue_racing` on Round 1's step is either value each time the round ends.
- **Assumptions:** Each service call executes atomically under `lock_race_state(event.id)` (as enforced by `transaction.atomic()` + `SELECT ... FOR UPDATE`).
- **Not modeled:** A final of more than one heat, so a half-raced final that its round's second ending must not make wait again (`_waits`; `server/racing/tests/test_a_result_closes_the_intermission.py` covers it), a step's `scene_at_finish`, a cut that picks no racer, whose empty roster `_activate` writes again where the model has a step that does not wait keep its roster, the timer test (#515), an import onto Round 1, which rebuilds the waiting final as a result written by hand does (D141) but checks no `locked_failure`, onto a heat Round 1's two heats cannot hold, an import onto a final with no chart or onto the final heat racing is on for, master schedule interleaving, `EmptiedHeat` retention/release, `schedule.reschedule` / `close_lanes` snapshot retries, `delete_results(round=None)` whole-event clears, and multi-group aggregate finals.
