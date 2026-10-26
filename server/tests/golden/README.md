# Golden fixtures from the PHP server

These files record what the PHP server produces today. The Django engine (M2) and the legacy API (M3) are diffed against them. The fixtures record PHP as it behaves, quirks included. Which quirks the port keeps is a decision for #20, not for this tool.

Do not edit a fixture by hand. Regenerate them all with one command.

## Regenerate

You need `docker` and `bash`, and a regular git clone (not a `git worktree`, because the server image build reads the git directory).

    server/tests/golden/capture.sh

The script builds the harness and server images by calling the steps of `testing/docker/run-suite.sh` (`harness`, `assets`, `server`, `timeout`), starts a server with an empty data volume, captures, and removes the server. Then `git status server/tests/golden` shows what changed. Rerunning on the same commit changes nothing.

    server/tests/golden/capture.sh --check     # also fail if the capture differs from what git has
    server/tests/golden/capture.sh --engine-only
    server/tests/golden/capture.sh --http-only

`DERBYNET_TEST_PREFIX` (default `derbynet-golden`) names the images, container and volume.

The `golden-fixtures` workflow runs `capture.sh --check` on every pull request and push to master that touches `website/`, `installer/docker/`, `testing/` or this directory.

The committed fixtures were captured against the image built from `installer/docker/Dockerfile`. `capture.sh` writes what that image held to `provenance.txt`: the PHP major and minor version, `TZ`, and the extensions the PHP CLI (engine capture) and php-fpm (HTTP capture) load. `--check` compares it like any fixture, so CI fails when the image it builds moves to another PHP minor version or loads other extensions, and someone recaptures. The PHP patch level and the Alpine release are printed to the capture log, not written to the file. The Dockerfile takes whatever patch release Alpine publishes, so comparing them would turn CI red on a routine Alpine update with no change to the code. A patch release that changes a response still fails `--check` through the fixture it changes.

The file leaves out the commit SHA and the image digest on purpose. Both change on every commit and every image build, even when PHP and every fixture stay the same, so `--check` would always fail. The commit that captured the fixtures is the one that last changed them (`git log -1 -- server/tests/golden/engine server/tests/golden/http`).

The Dockerfile starts `FROM alpine:3.23` and runs `apk upgrade`, so the PHP it gets is whatever Alpine publishes that day, and CI always builds from scratch. A local Docker layer cache can hold an older build. If the capture log shows an older PHP than CI's log, refresh the base image and rebuild without the cache, then capture again:

    docker pull alpine:3.23
    docker build --no-cache -t derbynet-golden-server -f installer/docker/Dockerfile .

## What is here

| Path | Contents | Made by |
|---|---|---|
| `engine/ordered/manifest-lanes-N.tsv` | One line per `make_ordered_schedule()` case: id, heat count, SHA-256 of the canonical record | `capture/capture-engine.php` |
| `engine/ordered/full/lNN-cCCC.json` | The full record for the representative subset | same |
| `engine/rotation/` | The same for `make_rotation_schedule()` | same |
| `engine/out-of-range.json` | Seven lanes, fewer cars than lanes, more than 200 cars; PHP errors are recorded | same |
| `engine/scheduler-data/lanes-N.json` | `$all_scheduler_data`, one car count per line, plus `sha256.txt` of the whole `json_encode()` | same |
| `engine/unused-lanes.json` | `spread_across_unused_lanes()` for every mask that leaves two lanes or more on 6 and 8 lanes | same |
| `engine/ordinals.json` | `ordinal()` and `nth_fastest()` for 0 to 120 (and `T0` to `T120`) | same |
| `engine/lane-bias.json`, `engine/critical-f-value.json` | `lane_bias_analysis()` over fixed time sets for 2 to 8 lanes, the `history.php` sentences, and `critical_f_value()` | same |
| `engine/summary.txt` | The case counts the tool printed | same |
| `provenance.txt` | PHP major and minor version, `TZ` and PHP extensions of the image the fixtures were captured against | `capture.sh` |
| `http/**/*.http` | HTTP transcripts: request, status, content type, `Location`, `Set-Cookie`, `Cache-Control`, body | `capture/capture-http.sh` and its `scenarios/` |
| `http/**/*.by-car.json` | Standings ladders with racer IDs replaced by car number and name | `standings_by_car` in `capture-http.sh` |

ADR-12 makes tests ID-agnostic, so a port should not compare row IDs. Transcripts carry the IDs PHP assigned on an empty database, so they are reproducible, but the `step-N` state snapshots and the `.by-car.json` standings are keyed by car number, name, class, rank and partition. Use those where IDs would get in the way.

## Engine fixtures

`capture-engine.php` runs under `php` with `website/` as the working directory. `json_out()` and `json_failure()` are stubs that record what the engine reported. `read_raceinfo()` is the real function from `inc/data.inc`, reading an in-memory SQLite database that has only the `RaceInfo` and `RaceChart` tables. A PHP warning or exception is recorded in the case, never raised.

A case record is canonical JSON with this key order and no spaces: `{"failure":null,"nheats":N,"heats":[[0,1],...]}`, plus `"warnings"` and `"error"` when PHP raised any. `failure` is the code `json_failure()` was given (`cant-fulfill`). The manifest hash is `sha256(record)`. Case ids are `ordered/<lanes>/<cars>/<n_times_per_lane>/<max_runs_per_car>` and `rotation/<lanes>/<cars>/<n_times_per_lane>`.

The sweep:

- `make_ordered_schedule()` for 2, 3, 4, 5, 6 and 8 lanes, every car count from the lane count to 200, every `n_times_per_lane` from 1 up to and including the first `cant-fulfill`, with `max_runs_per_car` 0 and 1.
- One lane, every car count from 1 to 200, 1 to 6 runs and the `cant-fulfill` at 7, with `max_runs_per_car` 0.
- `make_rotation_schedule()` for 2 to 8 lanes (including 7), every car count from the lane count to 200, 1 to 3 runs.

Nothing is sampled in the manifests. The full records are committed for a representative subset: every lane count, car counts from the lane count to the lane count plus 10, and 17, 23, 50, 100 and 200. Any other full record is regenerated on demand by calling `ordered_record()` in `capture-engine.php`.

The sweep takes about 20 minutes on 8 cores, because `make_ordered_schedule()` is cubic in the car count. The sweep workers run under the PHP JIT, which is 3.7 times faster; the tool recomputes a sample of cases without the JIT and exits non-zero if a hash differs. See [Known risks](#known-risks) for how small that sample is.

Counts printed by the tool and recorded in `engine/summary.txt`: see that file. At the time of writing they were:

    ordered cases: 12624
    ordered full outputs: 990
    rotation cases: 4116
    out-of-range cases: 69
    scheduler-data entries: 1178
    unused-lane masks: 304
    ordinals cases: 363
    lane-bias cases: 140
    critical-f-value cases: 1242

The `history.php` sentences are mirrored in `history_sentences()` in the capture, and the capture refuses to run if `history.php` stops containing them. The HTTP capture also records the real `history.php` lane-bias block in `http/standings/edge-cases-history-lane-bias.html`. The mirror is a copy, not PHP's output; see [Known risks](#known-risks).

## HTTP fixtures

A transcript is a sequence of exchanges in the format `capture/transcript.mjs` documents. The helper in `capture-http.sh` is `capture <name> <request>`. Timer transcripts send a `User-Agent`. A file upload, such as `snapshot.put`, sets `CAPTURE_UPLOAD`: its `request-upload:` line names the uploaded file, which is a fixture beside the transcript. Scenarios live in `capture/scenarios/`, run in order, and share a shell. Two things drive them:

- `capture` calls, for the cases written for this tool.
- `record_script`, which runs a script from `testing/` unmodified through `capture/recorder.mjs`, a recording proxy. It records the exchanges named for each script, and for the group scripts a snapshot of the state after each step.

Files, by directory (`find http -type f` lists them all):

- `http/envelope/` Dispatcher envelope (#18): `before-setup.http`, `dispatcher.http`
- `http/kiosks/` Kiosk polling (#34): `pages.http`, `poll-kiosk.http`
- `http/login/` role.login, role.list and the XML stubs (#16): `role-login.http`
- `http/master-schedule/` Master schedule with half weights (#23): `half-weights.http`
- `http/poll/` Poll payloads at fixed points: `race-day-polls.http`
- `http/race-day/` The owner race day: playlist, top-4 and top-8 finals: `outlaws.http`, `playlist-queue.http`, `scouts.http`
- `http/replay/` Replay command sequence (#27, #34): `command-sequence.http`
- `http/schedule/` schedule.generate per round, then the schedule poll: `cant-fulfill-4-lanes.http`, `max-runs-per-car-1-4-lanes.http`, `more-than-200-cars.http`, `normal-4-lanes.http`, `rotation-schedule-4-lanes.http`, `seven-lanes.http`, `two-lanes.http`, `two-runs-per-lane-4-lanes.http`, `unused-lane-mask-33-6-lanes.http`
- `http/setup/` The setup action: `ez-new.http`
- `http/standings/` Standings and awards under every setting, as left by each script and on the edge-case roster; the award settings the matrix leaves out (`award-settings.http`); quirks Q7 and Q13 of #20, with the snapshot the Q13 transcript uploads: `award-settings.http`, `edge-cases-all-heats.by-car.json`, `edge-cases-all-heats.http`, `edge-cases-history-lane-bias.html`, `edge-cases-setup.http`, `edge-cases-six-heats-of-nine.by-car.json`, `edge-cases-six-heats-of-nine.http`, `q13-drop-slowest.http`, `q13-drop-slowest.snapshot.xml`, `q7-tie-after-earlier-trophy.http`, `suite-aggregate-classes.by-car.json`, `suite-aggregate-classes.http`, `suite-aggregate-nonracing.by-car.json`, `suite-aggregate-nonracing.http`, `suite-aggregate-rounds.by-car.json`, `suite-aggregate-rounds.http`, `suite-basic-racing-after-new-rounds.by-car.json`, `suite-basic-racing-after-new-rounds.http`, `suite-basic-racing-and-awards.by-car.json`, `suite-basic-racing-and-awards.http`, `suite-master-schedule.by-car.json`, `suite-master-schedule.http`, `suite-playlist.by-car.json`, `suite-playlist.http`, `suite-points-racing.by-car.json`, `suite-points-racing.http`, `suite-standing-by-rank.by-car.json`, `suite-standing-by-rank.http`, `suite-subgroup-ntrophies.by-car.json`, `suite-subgroup-ntrophies.http`
- `http/suite/` Suite scripts run unmodified through the recording proxy: finalists and concerns (#26), per-step group state (#25), replay commands, reschedules: `aggregate-classes.http`, `aggregate-nonracing.http`, `aggregate-rounds.http`, `basic-racing.http`, `den-changes-basic.http`, `master-schedule.http`, `new-rounds-basic.http`, `new-rounds-master.http`, `partitions.http`, `playlist.http`, `points-racing.http`, `racing-groups.http`, `reschedule.http`, `reschedule.omitted.txt`, `single-group-operation.http`, `standing-by-rank.http`, `subgroup-ntrophies.http`
- `http/timer/` Timer transcripts, every case in the ticket: `before-login.http`, `competing-timers.http`, `finished-all-zeroes-and-not-racing.http`, `flags.http`, `handshake-and-idle-heartbeat.http`, `malfunction.http`, `race-staging-and-results.http`, `remote-start.http`, `reverse-lanes.http`, `unconfirmed-timer.http`

## Normalization

`capture/normalize.mjs` is the only place that masks a volatile value, and it masks nothing else. It changes only the volatile token, so the rest of each body is byte for byte what the server sent. `capture.sh` runs `normalize.mjs --check-readme` before it captures anything, and stops if a rule below is missing here.

| Rule | What it masks |
|---|---|
| `timecheck` | The server clock in the `role.login` response, `"timecheck"`, becomes `"<timecheck>"`. |
| `last-contact` | A non-zero `"last-contact"` or `"last_contact"` (timer, replay or kiosk contact, epoch seconds) becomes `"<epoch>"`. |
| `timer-contact-age` | `CONNECTED (N sec.)` becomes `CONNECTED (<n> sec.)`, and a kiosk `"age"` becomes `"<seconds>"`. |
| `award-duration` | The `"duration"` in `query=award.current` becomes `"<seconds>"`. |
| `set-cookie-value` | The value of every `Set-Cookie` header becomes `<value>`, so `PHPSESSID=0f3c...; path=/` is recorded as `PHPSESSID=<value>; path=/`. The cookie name and every attribute are kept, because they are the cookie contract (#16). |
| `reschedule-timing` | The seconds in `schedule.reschedule`'s `"timing"` and trace become `"<seconds>"`, and the exchange gets a `note: deterministic` line. A reschedule whose request took half its search deadline or longer is left out and listed in a `.omitted.txt`, and so is every later exchange in that transcript when the omitted one changed the schedule. See [Reschedules](#reschedules). |
| `completed-timestamp` | A `"YYYY-MM-DD HH:MM:SS"` string in a JSON body (when a heat result was written, `RaceChart.completed`) becomes `"<timestamp>"`. |
| `export-results-order` | The Results sheet of `export-json.php` is listed `ORDER BY completed, heat, lane`, and `completed` has one-second resolution, so rows swap places from run to run. The rule first checks, on the unmasked `Completed` values, that PHP listed every row in that order (a heat not yet raced, with `Completed` null, comes first), and fails the capture if not. Then it sorts the rows by class, round, heat, lane. It also fails the capture if an `export-json.php` response has a Results sheet it does not recognize, so it can never pass rows through unsorted. |

A transcript keeps these response headers: `Content-Type`, `Location`, every `Set-Cookie` (value masked) and `Cache-Control`. Values the capture does not need to mask because it never records them: other response headers (`Date`, `Expires`, `Pragma`, `X-Powered-By`, `Server`), and photo bytes (issue #8).

The committed order of the Results rows is therefore not PHP's: which rows share a second, and so the order across heats and rounds, changes from run to run, and the fixture cannot pin it. What PHP guarantees, and what a port must keep, is `ORDER BY completed, heat, lane` with null first. A port's harness that runs its export through `normalize.mjs` gets the same check.

Fixed by construction: the server image sets `TZ=America/New_York`, so `RaceChart.completed` is a local-time string wherever it is captured. Race times come from a function of car number and lane, never from a clock.

## Reschedules

`ScheduleAdjuster` in `website/ajax/action.schedule.reschedule.inc` searches once per run per lane. Each search has a deadline of `intdiv(10, n_runs_per_lane)` seconds and stops once `time() > deadline`, so a search cut off by its deadline ran for more than that many seconds. A cut-off search returns the best proposal found so far, which depends on the machine.

The response cannot show whether that happened. `"run-N-timed-out"` is also set when a search stops on a perfect score, and `"timing"` counts whole seconds. So the recording proxy measures each request's wall time, which bounds every search the request ran, and `normalize.mjs` keeps an exchange only when that time is under half the per-run deadline. Kept exchanges get the note `deterministic: the request took under 1/2 of its N s search deadline`. The capture log prints one `reschedule` line per exchange with its time in milliseconds, so a CI run shows how close each one came.

An omitted reschedule that was not a dry run wrote the schedule that every later call in `test-reschedule.sh` starts from, so the rest of that transcript is left out too.

The 50 calls in `test-reschedule.sh` took these wall times on the capture machine (8 cores, WSL2). The first column is the run whose fixtures are committed; the second is the range over that run, an earlier full capture, and three runs of `test-reschedule.sh` alone:

| Exchanges | Committed run | Range over 5 runs | Deadline | Kept |
|---|---|---|---|---|
| 001 to 038 | 58 ms to 2.1 s (the slowest are 016 to 021, which reschedule a whole round with `trace=1`) | the same, over the two full captures | 10 s, or 5 s for 010 to 015 | yes |
| 039, the first 4-lane latecomer, not a dry run | 4.0 s | 2.1 s to 4.0 s | 10 s | yes |
| 040 to 044 | 2.0 s to 2.4 s | 0.65 s to 2.4 s | 10 s | yes |
| 045, the first 6-lane latecomer, not a dry run | 12.2 s, cut off by its deadline | 11.5 s to 12.2 s | 10 s | no |
| 046 to 050 | under 1.1 s, but they start from the schedule 045 wrote | | 10 s | no |

Exchange 039 sets the margin. It usually takes about 2.2 s; the 4.0 s run was a full capture while the host was busy with other containers. At 2.2 s a runner has to be 2.3 times slower for 039 to cross the 5 s line and be omitted, which `--check` reports as a changed `reschedule.http` and `reschedule.omitted.txt`, and 4.5 times slower for its search to reach the deadline. At 4.0 s those margins are 1.25 and 2.5 times. The CI log prints the same `reschedule` lines, so a failure there shows the time that crossed the line. `http/suite/reschedule.omitted.txt` lists the omitted exchanges and why.

## Things the fixtures show about PHP

These are recorded as found and are not fixed here:

- `critical_f_value()` returns nothing for 8 lanes (`df1` = 7 is past the end of the table), so `lane_bias_analysis()` reports a critical value of 0.000 for an 8-lane track.
- `make_ordered_schedule()` has no table for 7 lanes or for more than 200 cars, and raises a `TypeError`. `schedule.generate` answers with status 500 and an empty body.
- `schedule.generate` reports success when the schedule cannot be made (`cant-fulfill`), because `json_success()` overwrites the failure.
- `action.php?query[]=poll` answers 500 with an empty body.
- `timer-message` with `IDENTIFIED&nlanes=4` (what the suite sends) does not set the lane count, which the server reads from `lane_count`.
- `schedule.reschedule` reports `"run-N-timed-out": true` both when a search reached its deadline and when it stopped early on a perfect score (`$score == 0`, which a `null` score also passes). Exchanges 004, 009 and 010 of `http/suite/reschedule.http` carry the flag for the second reason.
- A `FINISHED` for the wrong heat answers `<failure code='wrong-heat'>` and then `<success/>`.
- With `one-trophy-per`, a tied rung whose first racer already holds a trophy gets a speed award with `racerid` null, and the other racer in the rung gets none (#20 Q7, `http/standings/q7-tie-after-earlier-trophy.http`).
- Reading the standings writes `scoring=1` to RaceInfo when the legacy `drop-slowest` key is set and `scoring` is not (#20 Q13, `http/standings/q13-drop-slowest.http`).
- `lane_bias_analysis()`'s second loop, `foreach ($lanes as $lane)`, writes through the reference the first loop left on the last lane, so the sums of squares count the second-to-last lane twice and leave out the last lane (`engine/lane-bias.json`, #20 Q24).
- Time scoring over heats that record places only gives a racer a NULL score. `StandingsTracker::AddEntry` files it under rung -1, and with scoring 0 or 2 `compute_all_standings()` then throws a `TypeError`, so `query=standings` and `query=award.list` answer 500 with an empty body (`http/standings/suite-points-racing.http` 004, 006, 012, 014). With scoring 1 the ladders come back keyed from `"-1"` (008, 010). Django gives a racer with no score no place (#20 Q23).
- A tie of more than 26 racers runs the award key letter past `z`, and past 31 racers `chr()` makes a key that is not UTF-8, so `json_encode()` fails and `award.list` answers only a newline (`http/standings/suite-aggregate-classes.http` 025 and 027). Django names those awards `aa`, `ab`, and on (#20 Q22).

## Known risks

These are limits of the capture that are documented, not fixed.

**The JIT cross-check is a sample.** The sweep workers run with `opcache.jit=tracing`. Afterwards `jit_cross_check()` in `capture-engine.php` recomputes 50 of the 12,624 ordered cases in the parent process, without the JIT, and compares their hashes with the manifest. The sample is every lane count (and 1 lane) with 40 and 61 cars, `n_times_per_lane` 1, 3 and 6, and both `max_runs_per_car` values. A JIT miscompile outside that sample, at 150 cars for example, is written into the fixtures, and CI cannot see it, because CI captures with the same JIT. The parent process runs without the JIT only because the image's CLI settings leave `opcache.enable_cli` at `0` and `opcache.jit` at `disable`. If the image ever turns opcache on for the CLI, both sides run the JIT and the check compares the JIT with itself.

To widen the sample, add car counts or `n_times_per_lane` values to the loops in `jit_cross_check()`. To check every case, run the sweep without the JIT and compare it with git. It takes about 3.7 times as long as the normal engine capture:

    server/tests/golden/capture.sh --engine-only --keep-server
    docker exec -w /var/www/html derbynet-golden-server \
        php -d serialize_precision=-1 /tmp/golden-capture/capture-engine.php /tmp/golden-out --no-jit
    rm -rf server/tests/golden/engine
    docker cp derbynet-golden-server:/tmp/golden-out/engine server/tests/golden/engine
    git status --porcelain server/tests/golden/engine     # prints nothing if the JIT agrees
    DERBYNET_TEST_PREFIX=derbynet-golden testing/docker/run-suite.sh clean

**The lane-bias `sentences` are a copy of `history.php`.** The `sentences` field in `engine/lane-bias.json` is built by `history_sentences()` in `capture-engine.php`, which repeats `history.php`'s wording and its `sprintf("%5.3f", ...)` formats. Its guard only checks that `history.php` still contains the five phrases in `HISTORY_PHRASES`, such as `"F statistic is "`. A change to a number format, or to the `df1=` and `df2=` part, passes the guard, and the `sentences` then describe text PHP no longer prints. Only one page is captured from PHP itself, `http/standings/edge-cases-history-lane-bias.html`, on 2 lanes. It catches such a change for that case, but not for the 8-lane `0.000` critical value, which only the copy records. When `history.php`'s lane-bias paragraph changes, update `history_sentences()` to match it.

**Some captures depend on the server's whole-second clock.** A stall of a second or more on a loaded runner, such as a paused php-fpm worker, can change these fixtures, and `--check` then fails with no code change:

- `http/timer/competing-timers.http`. `action.timer-message.inc` treats a second timer as competing only while `$last_contact + 1 >= time()`. The scenario in `capture/scenarios/03-timer.sh` sends five timer messages from two `User-Agent` values, and each must arrive within about a second of the one before, or a `<competing>` reply turns into a plain one.
- `http/replay/command-sequence.http` and the replay exchanges in `http/suite/basic-racing.http`. The server forgets a replay client that has not polled for more than `REPLAY_TIMEOUT_LIMIT` (10 s, `website/inc/replay.inc`), and greets it again with `HELLO`. A gap over 10 s between polls changes the command list. `09-replay.sh` sleeps 11 s on purpose to record the greeting. `record_script` in `capture-http.sh` waits for the replay client to time out before every recorded script, so each one starts with no replay client, and `test-basic-racing.sh`'s first poll is always greeted with `HELLO`. Before this wait, `new-rounds-basic.http` recorded the replay as connected on a fast CI runner and as not connected on the 8-core capture machine.
- The suite transcripts that carry `"timer-state"`, such as `http/suite/new-rounds-basic.http`. The server counts the timer as connected until 60 s after its last message (`website/inc/timer-state.inc`). `new-rounds-basic.http` records it as `CONNECTED`, because `test-new-rounds.sh` starts less than 60 s after `test-basic-racing.sh` sent its last timer message. A machine slow enough to stretch `test-awards.sh` and the standings captures between them past 60 s records `NOT CONNECTED`.
- `http/suite/reschedule.http`, which [Reschedules](#reschedules) covers: exchange 039 moves into the omitted list if it takes 5 s or more, which a loaded runner can reach.

If one of these fails in CI, rerun the job before you treat the diff as real. A real change reproduces.

## Adding a case

A ticket that needs a case this tool lacks adds it here: a `capture` line in a scenario, or a new file in `capture/scenarios/`, or a case in `capture-engine.php`. Then rerun `capture.sh` and commit the new fixtures with the change.

## Deviations

`deviations.json` lists each place where Django differs from these fixtures on purpose (owner decision D25, #20 "How fixtures record decisions"). `capture.sh` never touches it or `deviations/`. Each entry has these keys:

| Key | Value |
|---|---|
| `quirk` | A quirk id from #20 whose decision is Fix, such as `Q1` |
| `fixture` | The fixture path, relative to this directory |
| `cases` or `exchanges` | Engine case ids, or transcript exchange names as their `###` line gives them |
| `reason` | One line |
| `expected` | Django's output for those items, under `deviations/<quirk>/`: canonical engine records keyed by case id, or a transcript that holds only the named exchanges |

A parity test compares a listed item with its expected output and fails if Django's output equals PHP's. It compares every other item with PHP. `racing/engine/tests/test_schedule_parity.py` does this for `engine/out-of-range.json`, and `racing/engine/tests/test_lane_bias.py` for `engine/lane-bias.json` and `engine/critical-f-value.json`, whose Q9 and Q24 entries and expected records it also writes when run as `uv run python -m racing.engine.tests.test_lane_bias`. `racing/engine/tests/test_standings_parity.py` and `racing/engine/tests/test_speed_awards_parity.py` do it for the `query=standings`, `query=award.list` and `export-json.php` exchanges in `http/standings/`; the second writes their Q7, Q22 and Q23 entries and expected exchanges when run as `uv run python -m racing.engine.tests.test_speed_awards_parity`. The other legacy API transcripts get their runner with their handlers (#31). `tests/test_deviations.py` checks the list itself: a Fix quirk, items that exist, none listed twice, and expected files that hold exactly the listed items.
