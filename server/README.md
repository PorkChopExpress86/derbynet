# DerbyNet Django server

This directory holds the Django port of the DerbyNet server. The PHP server in `website/` keeps running until the cutover release (ADR-01, ADR-13). The architecture decisions are in [`docs/adr/`](docs/adr/README.md).

## Set up

Install [uv](https://docs.astral.sh/uv/). It installs Python 3.14 for you if the host lacks it. From this directory, run:

```sh
uv sync
```

`uv sync` creates `.venv/` with the locked versions from `uv.lock`, including the development tools.

## Test and lint

```sh
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python manage.py makemigrations --check --dry-run
```

The `server` GitHub Actions workflow runs these four commands on Python 3.14, once on SQLite and once on Postgres. To run the tests on Postgres locally, set `DATABASE_URL`, for example `DATABASE_URL=postgres://postgres:postgres@localhost:5432/derbynet uv run pytest`.

Model tests build their data with the factory_boy factories in `<app>/tests/factories.py`. `EventFactory` creates an event through `core.services.new_event()`, so each event comes with its settings, state rows, and the six legacy scenes.

## Schedule generator tables

`racing/engine/scheduling/generator_tables.py` holds the PHP schedule generator tables (`website/inc/generators/*.inc`) as data. Do not edit it. Regenerate it, and commit the result, with:

```sh
uv run python tools/convert_generators.py
uv run python tools/convert_generators.py --check   # exits 1 if the module is out of date; CI runs this
```

`racing.engine.scheduling.generators.scheduler_data(nlanes, ncars)` returns `{"generators": [...], "permutation_sets": [...]}` or `None` when no table covers the counts. The module loads on the first call. `racing/engine/tests/test_generator_tables.py` compares the tables with the PHP dump in `tests/golden/engine/scheduler-data/`.

## Schedule generation

`racing.services.schedule.generate_schedule(round, n_times_per_lane)` replaces a round's chart and returns its number of heats. `unschedule(round)` deletes it. On failure `generate_schedule` raises `ScheduleFailure` with a `code` of `nlanes`, `results-exist`, `too-few-racers`, `cant-fulfill` or `busy`, and the old chart stays. `cant-fulfill` covers fewer than one run per lane and every request the generator tables cannot meet. Under normal scheduling on two or more lanes, that is more runs per lane than the tables give, 7 lanes, more than 8 lanes, or more than 200 racers. One lane (up to 6 runs per lane), rotation scheduling (any number of runs), and `max-runs-per-car` (exactly one run) do not need a table, so they schedule any racer and lane count. PHP reports success on these requests and writes an empty round, or answers 500. `racing.engine.scheduling.rough.max_runs_per_lane(nlanes, nracers)` tells a form how many runs per lane the tables give.

The engine modules in `racing/engine/scheduling/` are ported from `website/inc/schedule_*.inc`. `ordered.py` keeps the literal port of PHP's greedy reorder as `reference_ordered_schedule`, and `make_ordered_schedule` makes the same choices in integer arithmetic. The parity tests compare both with the PHP fixtures:

```sh
uv run pytest racing/engine/tests/test_schedule_parity.py -m pr     # every pull request, about a minute
uv run pytest racing/engine/tests/test_schedule_parity.py -m full   # the whole sweep, in the schedule-parity workflow
```

A plain `uv run pytest` runs the `pr` grid and skips `full`.

To time the largest case, 200 racers on 6 lanes with 6 runs per lane (1200 heats):

```sh
uv run python -m timeit -n 3 -r 3 -s 'from racing.engine.scheduling.ordered import make_ordered_schedule' 'make_ordered_schedule(6, 200, 6, 0)'
```

## Master schedule

`generate_schedule` and `unschedule` renumber the event's master heats in the same transaction as the chart, through `racing.services.master.reassign_master_heats(event)`. As in PHP, this runs whether `use-master-sched` is on or off. Raced heats keep their numbers. The unraced heats are numbered from 1 above the highest raced number, interleaved across the rounds of each round level by weight. The engine is `racing/engine/scheduling/master.py`. `assign_master_heats` does the numbering, and `master_target` gives the heat that `next-up`, `next` and `prev` land on. `racing/engine/tests/test_master_parity.py` checks the numbering against `tests/golden/http/master-schedule/half-weights.http`.

## The current heat and results

`racing/engine/heat_state.py` is the current-heat state machine, ported from `autoadvance.inc`, `racing-state.inc` and the `heat.*`, `result.write` and timer `FINISHED` handlers. `TABLE` holds every transition as data: an event, a guard, and the steps it runs in order. `decide(state, event, chart)` returns the final state, the effects in PHP's order (replay commands are observable), and the first failure. A step can decide a follow-on event, as PHP's handlers call one another, so one call covers a timer result, its advance, and the racing-off and playlist hook that end a round. `racing/engine/results.py` parses posted lanes, mirrors them for `reverse-lanes`, tests for an all-zero result (every lane up to the lane count, Q17), and merges posted results over stored ones with PHP's `empty()` rule and place derivation (ties share a place, 1, 1, 3). A manual time that is not a number counts as not posted (Q16). From the timer, a car with no time, a zero, or a time that is not a number did not finish and gets `DID_NOT_FINISH`, 9.999 s, so the heat counts as run and places from the times (D42). When no scheduled lane has a time above zero, no car finished, and the heat stops racing as an all-zero result does (`is_false_start`).

`racing.services.heats.transition(state, event)` carries out one decision on the locked `RaceState`. It reads the chart and the held emptied heats, writes the state, sends replay commands through `racing.services.replay.send` (rows in the same transaction, only while a replay client polled in the last 10 s), applies the racing scene, and runs the playlist hook last. The services each open their own transaction and lock `RaceState` first:

- `racing.services.heats`: `select_heat`, `set_racing`, `advance`, `rerun`, `reinstate`, `cancel_running_round`.
- `racing.services.results`: `record_timer_result(event, fields)` returns `recorded`, `notracing`, `all-zero`, `wrong-heat` or `nocurrentround`; `record_manual_result(event, fields, expected=None)` refuses with `wrong-heat` when the heat the race coordinator was shown has moved (D19); `delete_results(event, round=None, heat=None)`.
- `racing.services.playlist`: `apply_due_scene(event, now)` shows a finished step's scene once its due time, 10 s after the round ends, has passed. The kiosk poll and a periodic check call it.

Rules the services keep:

- Only `next` runs the playlist hook, and a move to another round sets the current group (Q2, C4 in #64).
- A rerun saves each lane's time and place in `RaceState.last_heat`, and a reinstate brings both back (Q4, C6 in #64). A last heat imported from PHP keeps PHP's one column and reinstates it as PHP does.
- `next` lands on a held emptied heat, which the race crew runs or skips by hand (D39). Once the current heat moves to another round, an emptied heat with no heat after it in its own round is let go, so `next-up` never returns to it (D40). A rerun or a new chart that keeps the current round is no move, and the staged heat, the next heat on the track, always stays.
- A rerun, a reinstate, a manual result, a result delete or a timer result on the round that ended before an open intermission rebuilds the waiting final in the same transaction (D27), until a heat of the final has a result.
- Once a heat of a final has a result, a caller without `CONTROL_RACE` gets `results-locked` for a manual result, a delete, a rerun or a reinstate on a round the final was chosen from, or a combined round of several groups was drawn from (D32, D41). The timer is never refused.
- `unschedule` of the current round cancels it, and under the master schedule each chart change except a reschedule (Q6) moves to `next-up`.
- `racing.services.audit.write` records the timer and heat audit (D19) after the race-state transaction, in its own durable transaction. It waits at most 1 s for the database, and on a timeout or any database error logs a warning and skips the records, so it never delays the timer (D26).

`racing/services/tests/test_timer_transcripts.py` replays the timer and replay transcripts of `tests/golden/http/` through the services, `racing/tests/test_playlist_script.py` replays `testing/test-playlist.sh` and compares the race state at each of its polls with `http/suite/playlist-states.http`, `racing/services/tests/test_heat_state_parity.py` replays `capture/scenarios/13-heat-state.sh` (Q4, Q11, Q16, D42), and `racing/services/tests/test_heat_concurrency.py` runs the lock under real threads on SQLite and on Postgres.

## Rescheduling

`racing.services.reschedule.reschedule(round, racer_id=None, dry_run=False)` adjusts a round that has started. It fits a late racer into the remaining heats, or with no `racer_id` every passed racer of the roster who has no heat. It also turns departed racers into byes and squeezes out heats whose racers can fill byes elsewhere. A late racer joins only this way: the race coordinator previews with `dry_run=True`, then presses Add to schedule, which makes the same call without it (D32). The dry run changes nothing, racing state included (Q5). The call returns the reply's `chart` strings. It fails with `RescheduleFailure` and code `busy`, `nochart`, `alreadyscheduled` (the racer already has heats in the round, as after a second press of Add to schedule) `nlanes` (the chart uses lanes past the lane count), `noracer` (`racer_id` is not a racer of the round's event) or `notonroster` (the racer is not on the round's roster, such as a racer of another group or one not picked for a final, D39). `racing.services.rounds.adjustments(event)` lists the unscheduled and departed racers of each round, for the poll's `adjustments` field.

The search is `racing/engine/scheduling/adjuster.py`, a port of PHP's `ScheduleAdjuster` that keeps its algorithm and loop order, the heat-index-0 quirk (Q3) included. PHP stops each search on the clock. The service passes a `DeadlineBudget` with PHP's deadlines, and tests pass a `NodeBudget`, which stops after a number of proposals and so always gives the same chart. The search runs on a snapshot, outside any transaction. The write then rechecks the snapshot under the `RaceState` lock, tries once more if a result or the roster changed, and then fails with `busy` (Q15). `RaceState.reschedule_started_at` keeps a second reschedule of the event out for up to 120 s.

Django differs from PHP in three ways (D34, Q21 and Q25 in #20).

- The running heat, the one `RaceState` names, and the staged heat keep their racers, lanes and heat numbers. The staged heat is the next unraced heat after the current heat in the same round. Under the master schedule it is the unraced heat with the next master number, which can be in another round, and it keeps its master number too, so `next` still lands on it (D39). A heat with a result in any lane has run, so a partly run current heat is not the running heat, and the staged heat is the next heat with no result. Under the master schedule the running heat also keeps its master number, so it stays first. A late racer goes into later heats, or into heats added at the end, and no heat before them is squeezed out. A departed racer leaves both heats too: its lane runs empty, and every other car keeps its lane (D37). When every car of one of them leaves, the heat keeps its number with no lanes (D39). A Django-only `racing.models.EmptiedHeat` row holds its round, heat number, master number and `held_at` until the race passes it, which is once a later heat of its own round gets a result whose `completed` is at or after `held_at`. Which round is current does not matter, and results written before the heat emptied and master renumbering never pass it, so under the master schedule a heat of a round that does not run again stays held with its number. PHP's `write-heat-results.inc` sets `completed` on every result write, and so does every result writer in `racing.services.results` and `racing.services.heats`; a rerun and a result delete clear it. A result with no `completed`, as in some imported databases, never passes a held heat. Every reader goes through `racing.services.master.held_emptied_heats(event)`, which ignores passed rows. The race position reads it, and every reschedule of its round keeps it like the running and staged heats. `reassign_master_heats` numbers it as an unraced heat and deletes passed rows. `master_index(event)`, the index `master_target` reads, lists it as a heat with no result, so `next` lands on it and moves on from it. The heat state machine reads the same index (see "The current heat and results"). `generate_schedule` and `unschedule` drop the round's rows. A committed squeeze in the current round still turns racing off, and a current heat that has already run follows its new number.
- PHP adds up to one heat per usable lane for a racer who does not fit, but never searches with the last one, so it can answer `nochart`. Django searches with it. Every chart PHP does produce stays the same.
- PHP fits in any racer it is given. Django refuses a racer who is not on the round's roster with `notonroster` (D39, Q25). That changes the 17 previews in `test-reschedule.sh` that fit a Lions racer into White's Wolves.

`racing/engine/tests/test_reschedule_parity.py` replays each reschedule PHP recorded, rebuilding the chart it started from with `racing/engine/tests/reschedule_replay.py`, and compares the chart and the `timing.internal` keys. `racing/services/tests/test_reschedule_replay.py` runs the same scripts against the database and the real service and compares them with the same expected replies, so the replay's copy of the service's rules cannot drift. After a change to the adjuster, regenerate the Q20, Q21 and Q25 deviation entries, and commit them:

```sh
uv run python tools/reschedule_deviations.py
uv run python tools/reschedule_deviations.py --check   # exits 1 if they are out of date
```

## Race control and the racing polls

`legacy_api/handlers/race_control.py` serves `heat.select`, `heat.rerun`, `heat.reinstate`, `result.write`, `result.delete`, `schedule.generate`, `schedule.unschedule`, `schedule.reschedule`, `roster.new` and `roster.delete` over the services above, and `query=poll.coordinator`, `query=poll` and `query=poll.results` over `racing/services/polls.py` (#31). The services take the `RaceState` lock and read the current heat under it, so no handler reads race state before it calls one; `result.delete`'s `current` round and heat are `racing.services.results.CURRENT`, resolved under the lock.

- `racing.services.polls.coordinator(event)` builds the coordinator payload, PHP's ten keys from `query.poll.coordinator.inc`. `query=poll.coordinator` serves it, the ten actions append it as PHP's `require` does, and the new dashboard (#41) reads the same function. `poll.coordinator` is anonymous, as in PHP and #31, where #17's table asks for a login: `run_heat` in `testing/common.sh` polls it as the Timer role, whose empty password counts as no login. `heat.select` and `heat.rerun` refuse a caller without `CONTROL_RACE` inside the handler and still append the payload, as PHP does.
- `racing.services.polls.POLL_VALUES` maps each `query=poll` value key to the one function that answers it, so the key list is data. `awards` adds nothing, as in PHP, and `race-structure` adds four top-level keys.
- `ondeck` lists every heat lane of the event outside the master schedule, because PHP's `json_ondeck()` never narrows its query (C25). It is kept for parity and is a known cost for a large event, which #52 should measure: the kiosks poll it about every half second.
- `query=poll.results` lists the current round's results (or `roundid`'s) and every result written in the last 300 s. `polls.results_cutoff(now)` is the one place the window is computed, on aware datetimes, so the server's time zone never splits it. It now has a PHP capture, `http/poll/poll-results.http`, which matches but for R8 and R9: PHP writes a space before its JSON, and with `details` repeats each column under its position as PDO fetches it.
- `schedule.reschedule` answers `busy` while another reschedule of the event holds `RaceState.reschedule_started_at`, or for 120 s after one that died without clearing it. It checks that before the round, as PHP checks its marker first, so a `roundid` that names no round is `busy` then and `nochart` otherwise (`reschedule.refuse_missing_round`).
- `schedule.generate` fails in PHP's order: `nlanes`, `results-exist`, `too-few-racers`, then `cant-fulfill`. A `roundid` that names no round still gets the lane check first (`schedule.refuse_missing_round`). PHP's `trace` field adds a `trace` key to the reply; Django drops it (R6).
- Fixes and differences from PHP are recorded on each endpoint's `divergences` in the registry, for the parity gate (#37): `roster.new` needs `CONTROL_RACE` (S17) and fails with `invalid-roundid` (C24), `result.delete` has no `sql` key (C23), and the R3 and R5 to R9 differences #31 proposes for the owner to confirm.

`legacy_api/tests/test_race_control_parity.py` replays `capture/scenarios/04-schedules.sh`, `05-master-schedule.sh`, `11-reschedule-quirks.sh`, `16-poll-results.sh` and the polls and awards of `06-race-day.sh` through these endpoints, with ids mapped by first appearance (`legacy_api/tests/php_http.py`). The capture's timer messages go through `timer-message` from a Timer login, and its playlist steps (#34) through the services. `WRITE_DEVIATIONS=1 uv run pytest legacy_api/tests/test_race_control_parity.py` rewrites its deviation entries. `legacy_api/tests/test_race_control_concurrency.py` posts two `schedule.reschedule` calls, and `heat.select` with `result.write`, at once on real transactions.

## Closed lanes

`racing/services/lanes.py` lets the race coordinator close a damaged lane and reopen it (#108, D36, D37, D45). PHP has no lane closure.

- `close_lanes(event, lanes, scoring=None)` adds the lanes to `unused-lane-mask`, so every later round and every late racer fitted in leaves them out. Every round with a chart and a heat left to run, started or not, and every interleaved round under the master schedule, is rescheduled on the open lanes (D45), and each one that was racing (a result, or the running or staged heat) gets a `racing.models.LaneClosure`. A round only scheduled has no time to drop, so it gets none. All of them go through one reschedule guard, snapshot, recheck and retry (Q15), and every chart is written in one transaction. The rounds to close are read again under the lock, so a round scheduled or finished meanwhile makes the closure search again. `ScheduleAdjuster.close_lanes(mask)` empties the closed lanes of every unraced heat except the running and the staged heat, drops a heat left with no car, and squeezes out heats of byes. A full chart then still gives every racer the round's runs per lane in each open lane. A racer left with no run in an open lane, as in an abbreviated round, gets one run on an open lane, in the first bye after the staged heat or in a heat added at the end. That counts the cars whose only run to come was in the closed lane. A closure never fits in a late racer. With no round left to run it writes a `LaneClosure` with no round and changes only later rounds.
- The standings drop every result of a round in a lane closed for that round, reopened or not, and keep the rows as they are. Under points racing the other cars of the heat move up a place for each dropped car ahead of them (`racing/engine/standings/closed_lanes.py`), and every round's points count the lanes its chart uses less the lanes closed in it, so first place on 4 lanes with one closed or unused is worth 3. PHP counts `lane-count` whatever lanes are unused (D46, Q26 in #20). The finalist cut reads the same standings.
- With `scoring`, the event's `scoring` setting changes, and so does `Round.scoring` of each round being closed that has a result. Before that, every finished round with no `Round.scoring` keeps the old value there. A round with no result follows the event setting, and so takes the new scoring (D45), as every round does in PHP.
- `reopen_lanes(event, lanes)` clears the lanes from the mask, only while no round has been scheduled since they closed (`generate_schedule` records the first one). Otherwise it fails with `round-scheduled` and changes nothing. A round with a `LaneClosure` keeps the lane closed for its remaining heats and keeps its dropped times. `Round.chart_mask` holds the lanes a closure took the round's current chart off, and a reschedule keeps off them. `generate_schedule` clears it, so with no closure a reschedule reads the mask in force, as PHP does. So a reopened lane, on the dashboard or on the settings page, is used only by charts made after the reopen (D37), and a new chart keeps only the closures of lanes still closed. `poll_lanes` lists a lane only while it is closed for later rounds or for the remaining heats of a round it closed in.
- `poll_lanes(event)` is the dashboard's poll field (#41): each lane closed now, the rounds it closed in, whether it can still be reopened, and the scoring in force.

## Groups, rounds and the playlist

`roster/services/groups.py` holds the partition, group (legacy class) and subgroup (legacy rank) services behind `action.partition.*`, `action.class.*` and `action.rank.*`, and `form_groups_by_rule(event, rule)` for the three group formation rules. `racing/services/rounds.py` holds `prepare_new_round`, `write_roster(round, finalists)`, `delete_round_roster`, `round_counts` and `ready_aggregate_groups`. `racing/services/playlist.py` runs the playlist: `start_playlist`, `round_ended`, and the operator gate. When a round ends and the next step has `waits_for_operator`, the next round is scheduled and selected, racing stays off, and `RaceState.waiting_entry` and `ended_round` hold the intermission until `start_waiting_step(event)` starts it or `skip_waiting_step(event, finalists_for)` passes over it to the next step (D28). `rebuild_waiting_round` makes a waiting round's roster and chart again after a result in the round that feeds it changes (D27). A domain failure raises `GroupFailure`, `RoundFailure` or `PlaylistFailure` with PHP's code. Every service that changes a roster takes the `RaceState` lock first, as `generate_schedule` needs.

Owner decision D31 sets two rules where PHP behaves otherwise.

- A real rule change deletes every round, chart, aggregate link, group award and playlist step, and is refused with `results-exist` once any heat has a result. Applying the rule already in force writes nothing and succeeds, even once results exist.
- Partition names are unique per event with case ignored. Adding or renaming a partition to a name that matches another partition in any case is refused with `name-taken` (an exact match on `partition.add` keeps PHP's `already-exists`). `find_or_create_partition` returns the partition that matches, so a racer imported with `LIONS` joins `Lions`. The services compare `Partition.name_key`, Python's `casefold()`, because SQLite's `lower()` folds only ASCII letters. The importer renames a legacy partition that matches an earlier one in any case.

A final with fewer than 2 racers, zero included, gets D28's "The final needs at least 2 racers." from `start_waiting_step` (D34). Only a waiting round that was deleted during the intermission, and so is remade empty, says "The waiting round has no racers in its roster."

The finalists of a follow-on or aggregate round come from the standings (see below), so `write_roster` and the playlist services take them as input. `racing/engine/groups/aggregates.py` orders the aggregate groups (Q8), computes what each draws from, and finds the aggregates ready for a roster.

`legacy_api/handlers/timer.py` serves the timer protocol over `racing/services/timer.py` (#32): `timer-message` (`HELLO`, `IDENTIFIED`, `STARTED`, `FINISHED`, `HEARTBEAT`, `MALFUNCTION`, `FLAGS`), `timer.assign-flag`, `timer.assign-port`, `timer.assign-device`, `timer.remote-start`, `timer.test`, `query=timer.settings`, `query=timer.log` and `query=poll.timer.test`. `legacy_api.views.post_timer_log` serves `post-timer-log.php` at the root and under `/derbynet/`.

- `timer.handle_message` runs one message in one transaction that locks `RaceState`, then `TimerStatus`, and builds the reply as text in PHP's child order: the message's own children, then `<abort />`, `<heat-ready/>`, the pending `<assign-*/>` with a `<query/>`, `<remote-start/>`, `<remote-log/>`, and the racing-off `<abort/>`. A `FINISHED` calls `results.timer_result_locked`, the same write, replay and advance as `record_timer_result`, inside that transaction.
- `timer.TimerState` is PHP's `TimerState`: the 60 s contact timeout, the 60 s malfunction, `unhealthy` and `troubled`. Reading it writes nothing. The timeout is written, and logged as `timer.*overdue`, by the next timer message or by `timer.check_contact`. `polls.timer_state` reads the same class.
- Every message, and every change of the timer's state, is written to the audit (`AuditEvent` 500 and 501) after the transaction, with the timer results and malfunctions PHP records (D19, D26). A failed audit write never changes the reply.
- A queued remote start is taken with one `UPDATE ... WHERE remote_start_requested`, so one `<remote-start/>` goes out per `timer.remote-start`, however many heartbeats arrive together.
- A staged heat of the timer test is a `TimerStatus` staged heat with no round. The timer test moves the current heat through `heat_state.TimerTestStart` and `TimerTestEnd`.
- Hardening from #15: the `timer.*` actions and queries need `CONTROL_RACE` (S21), and `post-timer-log.php` needs `TIMER_MESSAGE` from the session (S22). The log file is opened only when its stored path names a `timer-*.log` in the event's logs folder. `IDENTIFIED` still overwrites the lane count with no check against the schedules (C26, kept for parity; #50).

`legacy_api/tests/test_timer_parity.py` replays `capture/scenarios/03-timer.sh` and matches every timer transcript byte for byte. `testing/race-with-derby-timer.sh` races a round with the real `derby-timer.jar` and its simulated timer, in the `server` workflow.

`legacy_api/handlers/groups.py` serves `class.add`, `class.edit`, `class.delete`, `class.order`, `query=class.list`, the five `rank.*` actions and the six `partition.*` actions over these services (#30). `roster/services/structure.py` builds the `classes` list (`json_all_classes()`), the `partitions` list that `poll` will serve (#31), and the `partition.*structure` entry for the action log. The services also log `class.*create`, `round.*create`, `rank.*create`, `partition.*create`, `class.*delete` and `rank.*delete` with PHP's keys, in the transaction of the change, so `history.php`'s replay reads new events as it reads imported ones. Every ordering endpoint writes the posted order and renumbers the rows 1 to n in one transaction, where PHP's duplicate cleanups crash or loop (C16). `partition.move` accepts only `classid` or `rankid` as `from_group_field` (S9), and refuses an unknown partition or target with `no-such-partition`, `bad-group` or `bad-from-group`. `class.list` needs a login (D5). `legacy_api/tests/test_groups_parity.py` replays the group scripts' recordings through these endpoints and compares `class.list` with PHP's after every step.

`roster/tests/test_group_scenarios.py` and `racing/tests/test_round_scenarios.py` replay the group and round scripts of the shell suite through these services, using `roster/tests/php_replay.py`, and compare the state with what PHP recorded. PHP rebuilds the groups, with new ids, when a script applies the rule in force, so the replay checks that Django changes nothing and then rebuilds them itself, so later requests find the rows they name.

## Standings and speed awards

`racing/engine/standings/` ports PHP's standings (`website/inc/standings.inc`), finalist cut (`website/inc/roster.inc`), speed awards (`website/inc/awards.inc`), award names (`website/inc/ordinals.inc`) and lane bias test (`website/inc/lane-bias.inc`) as pure Python. `oracle.Standings(event)` gives the result rows, best first, each with its places, and the ladders keyed as PHP keys them: `st-r<round>`, `st-rr<round>-<subgroup>`, `st-q<group>` and `st-ff`. Scores follow PHP's SQLite rules on every database: `math.fsum` for `SUM`, NULLs left out as SQL leaves them, a single heat scoring as itself under drop-slowest (Q12), and names compared as `NOCASE`, which folds ASCII letters only.

`racing/services/standings.py` reads an event with a few ORM queries. `standings_snapshot(event)` returns the standings, and its `speed_awards`, computed from the rows as they are. It caches nothing: reading the rows is most of the time (8.8 ms of 11.6 ms for 200 racers and 1200 results on SQLite, measured with `tools/time_standings.py` on an i7-10510U laptop), so a cache would save little even for the award kiosk's 0.5 s `award.current` poll. The ladders and orders it returns are tuples and read-only mappings. `current_award(event)` resolves `RaceState.current_award_key` for speed awards. `racing/services/lane_bias.py` holds `lane_bias(event)`.

`racing/services/rounds.py` holds `new_round_roster(source, top, bucketed, extras)`, the service behind `roster.new`. The source is `FollowOn(round)`, `FromAggregate(group)` or `NewAggregate(name, groups, subgroups)`; a new aggregate becomes the `full-field-calc` group, as in PHP. In one transaction it makes the round or clears its roster, cuts the standings, and enters the qualifiers, then the extra racers the race coordinator allowed in (D32), which never take a qualifier's place. When the roster would be empty it fails with `no_racers` and changes nothing. `playlist_finalists(extras)` is the `finalists_for` the playlist services take, with each final's extras keyed by its group and round number, so no other final gets them. It returns the racers with the cut's concerns, for the intermission to show (D28). Both also enter the racers `racing.services.finals` allowed into the round, read from their `FinalRequest` rows at every build, so `roster.new`, the playlist activation and the D27 rebuild all keep them.

`racing/services/finals.py` owns the allowed racers of a final and the finalist suggestions (#103, D32). A racer who passed check-in after the group's round 1 ended, with no round 1 result, asks for a place with `request_final_place(racer)`, which makes a `racing.models.FinalRequest` keyed on the group and sets the racer not eligible for speed awards (D33). A racer whose round 1 still has unraced heats is refused with `late-racer`, and joins round 1 with Add to schedule instead. The race coordinator calls `allow_final_request` or `decline_final_request`, and can still allow a declined request. An allowed racer goes into the next round the group races in, its round 2 final (#20) or a combined round drawn from it, whichever was made first and has heats left to run (D44). A racer allowed after the final has run goes into a later combined round, or into the next round built. A request is spent on the first round it puts the racer on: `FinalRequest.placed_round` names that round, every rebuild of it keeps the racer, and no later round takes the racer. Declining clears it. A combined round that draws from subgroups takes only racers of those subgroups. Allowed racers follow the qualifiers in the order they were allowed. A request stops counting once its racer moves to another group or the racer's check-in is reverted (D36), and allowing such a request is refused with `wrong-group` or `not-checked-in`. Allowing into a final that already has a roster adds the racer to it with no heats, so the race coordinator fits the racer in with a reschedule preview and Add to schedule; allowing never writes a chart. Every final keeps the cut it was built with, from `roster.new` or its playlist step, in `Round.cut_top` and `Round.cut_bucketed` (D43). `finalist_suggestions(final)` compares that cut of the round before the final with the final's roster less its allowed racers, and returns the racers to add and to remove without changing anything; it never names a racer with an award setting. A round made without a stored cut, as before D43, fails with `no-cut`. `add_to_final` and `remove_from_final` act on a suggestion. An added racer keeps its award settings and has no heats until Add to schedule, and a removed racer's unraced heats become byes at the next reschedule. `poll_finals(event)` is the dashboard's poll field (#41): the open and declined requests that still apply, and the suggestions of each final with a raced heat, or `"failure": "no-cut"` for a final with no stored cut.

Award eligibility follows owner decisions D33, D34 and D35:

- A racer has two award settings. `Racer.exclude` is "not eligible for any award", and `Racer.not_eligible_for_speed_awards` is new. The legacy API reads and writes `exclude` only.
- Every award unit lists the partitions its awards can go to: the pack, each group (aggregates included), each subgroup, and later each judged award (#33). `awards.models.AwardPartition` stores a list only where it is narrowed, and `awards/services.py` reads and sets them. No stored list means every partition, those added later too; a narrowed list keeps exactly its partitions; deleting a partition deletes it from every list, and a list left empty goes back to all partitions. The legacy API never writes the lists, and only `award.winner` reads them.
- A racer who cannot win a ladder's speed awards keeps the standings rows, marked in `Row.not_eligible` with the reason, and takes no place and adds no `T` mark there. Every other place, ladder, speed award and finalist is then what PHP gives with that racer removed. A racer whose partition a unit's list leaves out is marked only in the ladder that unit's awards read, and keeps every other place. The `full-field-calc` group's award ladder is the pack's.
- A cut skips any racer who cannot win the speed awards of the group the new round is for, and takes the next racer. It reads that group's list, never the pack's. `racing/engine/standings/eligibility.py` holds the one rule the ladders, the awards and the cut share.

`racing/engine/tests/test_standings_parity.py`, `test_speed_awards_parity.py`, `test_ordinals.py` and `test_lane_bias.py` compare the engine with every PHP standings, award and engine fixture, and `racing/tests/test_finalists.py` replays every `roster.new` and playlist cut of the shell suite. To time `standings_snapshot()` and the speed awards on a 200-racer event with 1200 results:

```sh
export DATABASE_URL=sqlite:////tmp/standings-timing.sqlite3
uv run python manage.py migrate -v 0
uv run python -m tools.time_standings
```

## Awards, judging and presentation

`legacy_api/handlers/awards.py` serves `award.edit`, `award.delete`, `award.order`, `award.adhoc`, `award.winner`, `award.import`, `award.present`, `award.calc-rule`, `award.xbs`, `query=award.current` and `query=award.list` (#33). The writes are in `awards/editing.py`, which takes the `RaceState` lock first and fails with `AwardFailure` and PHP's code, and the replies in `awards/listing.py`, which builds `json_out_awards()` and `award.current`.

- `award.winner` keeps PHP's checks for every judged award: `ineligible` for a racer not eligible for any award (`exclude`), then `wrong-class` and `wrong-subgroup`, an aggregate checked through its groups and its own subgroups. A racer who is not eligible for speed awards can still win a judged award (D33). A racer whose partition the award's partition list leaves out is `ineligible` too (D35).
- `query=award.current` answers anonymously, for the award kiosk (D5), and resolves a `speed-` key through `racing.services.standings.current_award`. It caches nothing, as the standings section above explains. `query=award.list` needs a login.
- The ballot endpoints (`ballot.open`, `query=ballot.get`, `vote.cast`, `query=ballot.results`) are deferred to #54 (D7), listed in `legacy_api.registry.DEFERRED` for the coverage check of #37. `award.edit` still takes `ballot_depth`.
- Differences from PHP, for the owner to confirm, are A1 to A5 in `deviations.json`: a pack-wide award lists `classid` and `rankid` 0, never PHP's null (A1); a `class_and_rank`, `full-field-calc` or award key PHP would store unchecked is refused (A2, A4, A5); and `award.present` with no parameters answers success (A3).

`legacy_api/tests/test_awards_parity.py` replays `capture/scenarios/17-awards.sh` through these endpoints, and `WRITE_DEVIATIONS=1` rewrites its entries.

## Standings differential test against PHP

`tools/standings_differential/` runs the real PHP standings, finalists and speed-award code and the Django engine in `racing/engine/standings/` on the same seeded random events, and compares every result row, ladder, catalog entry, class and rank key, finalist cut, speed award and `award.current` answer. It needs Docker and a local image with `php84` and the website at `/var/www/html`. From this directory, run:

```sh
uv run python -m tools.standings_differential [IMAGE] --controls
```

`IMAGE` defaults to `derbynet-local`. The run makes 2000 random events and 200 events for each targeted batch in `generate.py`, takes about two minutes, and exits 1 if any comparison differs. `--controls` also breaks the engine in a dozen seeded ways and checks that the comparison reports each. Each PHP run is a `docker run --rm` container named after `--name-prefix`, with no volume. pytest does not run the tool against PHP, because CI has no PHP image.

The comparison counts three deliberate differences apart, Q7, Q8 and Q22 (#20). A key whose place ends in two or more letters, such as `speed-1ab`, names the 28th award of a tie in Django, where PHP reads its last letter only. It counts as Q22 only when Django answers with the award its letters name: as in PHP, a key counts the awards of its place that match its ids, every group's and subgroup's when it has none. An event counts as Q7 only when a model of PHP's award loop, changed only in its tie indexing, gives Django's awards one way and PHP's the other. A `current_award_key` with an oddly written number, such as `speed-02` or `speed-2- 1`, is compared like any other: the engine reads it with PHP 8's comparison rules. A key that makes PHP's `award.current` throw, such as `speed-x1`, is recorded as naming no award, which is what the engine answers. `racing/engine/tests/test_differential_controls.py` runs every seeded fault in CI against the engine's own answers.

## Inspect an event

The server has no Django admin (ADR-16). To see what one event holds, print its rows for every event-scoped model as JSON:

```sh
uv run python manage.py dump_event <slug>
```

The command only reads.

## Import a past DerbyNet event

`import_derbynet` loads one event from the PHP server as a new event. The source is an event folder's `derbynet.sqlite3`, at any schema version from 1 to 10, or a file saved from `action.php?query=snapshot.get&photos`. The importer copies a SQLite source, with its `-wal` and `-journal` files, to a private temporary folder and reads the copy read-only, so the source folder is never written and can be read-only. A source with an unfinished write in its `-journal` is refused. Stop the PHP server before importing its database.

```sh
uv run python manage.py import_derbynet /path/to/2024/pack42/derbynet.sqlite3 --report report.json
```

| Option | Effect |
|---|---|
| `--event NAME` | The new event's name. Default: the source's folder name, `pack42` above. |
| `--photos-dir DIR`, `--car-photos-dir DIR` | Where to look for photos first. Then the source's `racers/` and `cars/` folders. |
| `--replace` | Replace the event of the same name. Its rows go, and after the commit its `racers/`, `cars/`, `videos/` and `logs/` folders go too. The new event starts with empty `videos/` and `logs/`. Without `--replace`, an existing name is refused. |
| `--time-zone ZONE` | The IANA time zone the PHP server kept local times in, such as `America/Chicago`. Default: this server's `TIME_ZONE`. The report records it. |
| `--accept-violations` | Keep the import when `check_invariants()` finds violations. Without it, they roll the import back. |
| `--report PATH` | Write the full report as JSON. A summary always goes to stdout. The path cannot be the source or its `-wal`, `-shm` or `-journal` file. |
| `--force-report` | Overwrite an existing `--report` file. Without it, an existing file is refused before the import starts. |

The report counts the rows read and imported per legacy table, and lists every row dropped and every value changed, with the reason.

The import copies photos into a new folder, `events/<name>.import-<token>` under `DERBYNET_DATA_DIR`, and points the event's photo, video and log directories at it in the same transaction as the rows. A killed import leaves either the old event with its folders or the new event with the new folder. The next import removes what a killed one left behind and lists it in the report under `recovered`. With `--replace`, the old event's folders are removed after the commit.

The tests read fixtures that `transfer/tests/fixtures/legacy/build.sh` made with the PHP server.

## Run both servers

From the repository root, first build the files the PHP pages need, as step 2 of the top-level `README.md` says (`ant generated`). Then run:

```sh
docker compose -f server/compose.yaml up
```

Compose starts three services. They share no database.

- `php` runs the `jeffpiazza/derbynet_server` image with `website/` mounted read-only, on port 8080.
- `php-setup` creates an event database for `php` on the first start, then exits. Without one, PHP redirects every request to `setup.php`.
- `django` runs `manage.py migrate` and then `manage.py runserver 0.0.0.0:8000` with `server/` mounted, so code edits reload.

Check that both answer:

```sh
curl -s 'http://localhost:8080/action.php?query=role.list'
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/health/
```

These variables change the defaults:

| Variable | Default | Effect |
|---|---|---|
| `DERBYNET_PHP_PORT` | `8080` | Host port for `php` |
| `DERBYNET_DJANGO_PORT` | `8000` | Host port for `django` |
| `DERBYNET_WEBSITE_DIR` | `../website` | The PHP document root to mount, relative to `server/` |

`docker compose -f server/compose.yaml down -v` stops the servers and deletes their data.

## Point a test script at either server

The scripts in `testing/` take the server's base URL as their first argument. Pass the PHP server or the Django server:

```sh
testing/login-coordinator.sh http://localhost:8080
testing/login-coordinator.sh http://localhost:8000
```

The Django server serves `/action.php` with PHP's dispatch rules and envelope. Each module in `legacy_api/handlers/` registers its endpoints on import, and every other name answers `unrecognized` until M3 ports it. The steps of `.github/workflows/server.yml` list the shell suites that run against Django. A suite is added there once it passes on SQLite and Postgres with no stand-in.

### Run the shell suites against Django

The suites reset the database, seed slides, and check the database invariants through endpoints that a real race must not expose. Set `DERBYNET_ENABLE_TEST_ENDPOINTS=1` on the Django server that a suite runs against. With it set, `test.setup` exists and `database.check` answers anyone. Without it, `test.setup` answers `unrecognized` and `database.check` needs `SET_UP`. The CI job `server` sets it for its shell-suite steps.

A new server has no event, and `reset-database.sh` needs one to reset. Create an empty active event first. The command does nothing when an event exists:

```sh
cd server
export DERBYNET_ENABLE_TEST_ENDPOINTS=1
uv run python manage.py migrate
uv run python manage.py create_test_event
uv run python manage.py runserver 127.0.0.1:8000 &
../testing/reset-database.sh http://127.0.0.1:8000
DBCHECK=1 ../testing/reset-database.sh http://127.0.0.1:8000
```

`DBCHECK=1` makes `testing/common.sh` call `database.check` before every POST. The script needs `curl`, `jq` and `xmllint`.

## Legacy handlers

A handler lives in `legacy_api/handlers/` and registers under its exact PHP name with `@action(...)` or `@query(...)` from `legacy_api/registry.py`. Each declares its `permission`, a `Perm` from `accounts/permissions.py` or an `Access` value. The dispatcher checks it against the session's `role` and `permissions` keys before the handler runs. A handler resolves every id from a request with `ctx.resolve(Model, raw_id)`, which finds rows of the active event only, and writes through services, never the ORM. Each new endpoint adds a request to `LEAK_REQUESTS` in `legacy_api/tests/test_event_leak.py`. An endpoint whose reply differs from PHP's on purpose lists each difference in its `divergences`, as `<id>: <what>`, where the id is a row of #64, an owner decision or a quirk of #20.

## Roles and logins

Roles are rows of `accounts.Role`, with hashed passwords and PHP's permission bits (ADR-07, ADR-16). A new database starts with PHP's five default roles and passwords, which `testing/default.passwords` lists. A login stores the role name and its bits in the session. A session with no login holds the bits of the anonymous role, the one named `''`. Before the first event exists, every session also holds `SET_UP`. `Access.LOGGED_IN` admits only a role that has a password. Anyone can log in as `Timer`, because its password is empty, so a timer handler requires `TIMER_MESSAGE` instead. A logout clears the whole session.

A session keeps the bits it got at login. A role edit or an `import_roles` run does not change a session that is already logged in, until that session expires after 24 hours. PHP behaves the same way.

To keep the roles and passwords of a PHP install, import its `config-roles.inc`. The import replaces every role:

```sh
uv run python manage.py import_roles /path/to/local/config-roles.inc --dry-run
uv run python manage.py import_roles /path/to/local/config-roles.inc
```

`--dry-run` prints each role and its bit mask and writes nothing. When the file holds PHP that the command cannot read, it runs `php` instead, if `php` is installed. That executes the file as PHP, with `--dry-run` too, so import only a file you trust.

Pages log in at `login/` and log out with a POST to `logout/`. A page view declares its permission with `@page_requires(Perm.X)` from `accounts/views.py`, which sends a visitor without it to `login/?next=<page>`. A mobile check-in link is `mcheckin.php?tok=<make_mcheckin_token()>`. It grants check-in, revert and photo upload to the phone's session, and expires after 60 minutes.

A POST to `/action.php` that carries an `Origin` header for another site answers `notauthorized`. The server's own origin and the origins in `DERBYNET_CSRF_TRUSTED_ORIGINS` pass. Hardware clients send no `Origin`.

Behind a proxy that ends TLS, the server takes its own scheme from the `X-Forwarded-Proto` header. If the proxy does not send that header, the server thinks it serves `http`, and every browser POST to `/action.php` answers `notauthorized`, logins included. Set the proxy to send `X-Forwarded-Proto: https`, or set `DERBYNET_CSRF_TRUSTED_ORIGINS` to the public origin, such as `https://derby.example`.

## Settings

`derbynet/settings.py` reads every install-level setting from the environment.

| Variable | Default | Effect |
|---|---|---|
| `DERBYNET_DATA_DIR` | `server/var/data` | Root for event folders and media |
| `DERBYNET_CONFIG_DIR` | `server/var/config` | Holds `secret_key` and other install-level files |
| `DATABASE_URL` | `sqlite:///<DERBYNET_DATA_DIR>/derbynet.sqlite3` | Parsed with `dj-database-url` |
| `DERBYNET_URL_PREFIX` | empty | Sets `FORCE_SCRIPT_NAME`, for example `/derbynet` |
| `DERBYNET_TIME_ZONE` | `TZ` when it is an IANA name, else `UTC` | Sets `TIME_ZONE`, with `USE_TZ = True`. An invalid value raises `ImproperlyConfigured` |
| `DERBYNET_DEBUG` | `0` | Sets `DEBUG`. Only `0` and `1` are accepted |
| `DERBYNET_ALLOWED_HOSTS` | `*` | Comma-separated. LAN installs are reached by bare IP address |
| `DERBYNET_CSRF_TRUSTED_ORIGINS` | empty | Comma-separated extra origins for the new UI behind a proxy |
| `DERBYNET_LOCK_SETTINGS` | `0` | Sets `LOCK_SETTINGS`. `settings.write` then refuses the photo, car photo, and video folders, as PHP's `settings.lock` file did for a hosted install |
| `DERBYNET_ENABLE_TEST_ENDPOINTS` | `0` | Registers `test.setup` and lets anyone call `database.check`. Only the shell suites set it |
| `DJANGO_SECRET_KEY` | unset | When unset, the server generates a key into `<DERBYNET_CONFIG_DIR>/secret_key` with mode 0600 on first start, and reuses it after that |

SQLite runs in WAL mode with `synchronous=NORMAL`, `IMMEDIATE` transactions, and a 20 second busy timeout (ADR-05). WAL writes `-wal` and `-shm` files next to the database, so keep `DERBYNET_DATA_DIR` on a writable local disk. A database on a network share can be corrupted. A Postgres `DATABASE_URL` is used as `dj-database-url` parses it, with no extra options.

## Setup, snapshots, preferences and exports (#36)

- `query=stats` needs a login, and gives `race_info` to `SET_UP` only (D16). `racing/services/stats.py` counts.
- `export-json.php` is `racing.services.workbook.export_all()`: Roster, Results, one Standings sheet per catalog entry, and Awards. It encodes without numeric coercion (`encode_php_json(numeric_check=False)`), so a car name "111" stays text. `racing/tests/test_workbook_parity.py` checks the Standings and speed-award sheets against every PHP capture.
- `preferences.upload` and `preferences.php` are `roster/services/preferences.py`. The file needs `SET_UP` and leaves out `ballot_password` (S4). An upload is not one transaction: a bad line leaves the lines before it applied, as in PHP.
- `database.purge` is `racing/services/purge.py`. `purge=schedules` also renumbers master heats, as every chart change does.
- `result.import` is `racing/services/result_import.py`, one transaction per line.
- `snapshot.get` writes the event under the legacy table and column names, numbered from 1 (`transfer/snapshot.py`). `snapshot.put` hands the file to the importer, which replaces the active event. A round trip loses `ntrophies`, `note`, `checkin_time` and what the legacy schema has no column for.
- `setup.nodata` makes or opens an event (`core/setup_event.py`). `file.directory.nodata`, `file.stat` and `file.directory.new.nodata` stay under the data folder.
- `legacy_api/history_formatter.py` turns the action log into the sentences of `history.php`. `tests/data/history/` holds two PHP events' rows and the text PHP printed for them.
