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

The committed fixtures were captured against the image built from `installer/docker/Dockerfile` (Alpine 3.23, PHP 8.4, `TZ=America/New_York`).

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
| `http/**/*.http` | HTTP transcripts: request, status, content type, body | `capture/capture-http.sh` and its `scenarios/` |
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

The sweep takes about 20 minutes on 8 cores, because `make_ordered_schedule()` is cubic in the car count. The sweep workers run under the PHP JIT, which is 3.7 times faster; the tool recomputes sampled cases without the JIT and exits non-zero if a hash differs.

Counts printed by the tool and recorded in `engine/summary.txt`: see that file. At the time of writing they were:

    COUNTS

The `history.php` sentences are mirrored in `history_sentences()` in the capture, and the capture refuses to run if `history.php` stops containing them. The HTTP capture also records the real `history.php` lane-bias block in `http/standings/edge-cases-history-lane-bias.html`.

## HTTP fixtures

A transcript is a sequence of exchanges in the format `capture/transcript.mjs` documents. The helper in `capture-http.sh` is `capture <name> <request>`. Timer transcripts send a `User-Agent`. Scenarios live in `capture/scenarios/`, run in order, and share a shell. Two things drive them:

- `capture` calls, for the cases written for this tool.
- `record_script`, which runs a script from `testing/` unmodified through `capture/recorder.mjs`, a recording proxy. It records the exchanges named for each script, and for the group scripts a snapshot of the state after each step.

FILES

## Normalization

`capture/normalize.mjs` is the only place that masks a volatile value, and it masks nothing else. It changes only the volatile token, so the rest of each body is byte for byte what the server sent. `normalize.mjs --check-readme` fails the capture if a rule below is missing here.

| Rule | What it masks |
|---|---|
| `timecheck` | The server clock in the `role.login` response, `"timecheck"`, becomes `"<timecheck>"`. |
| `last-contact` | A non-zero `"last-contact"` or `"last_contact"` (timer, replay or kiosk contact, epoch seconds) becomes `"<epoch>"`. |
| `timer-contact-age` | `CONNECTED (N sec.)` becomes `CONNECTED (<n> sec.)`, and a kiosk `"age"` becomes `"<seconds>"`. |
| `award-duration` | The `"duration"` in `query=award.current` becomes `"<seconds>"`. |
| `reschedule-timing` | The seconds in `schedule.reschedule`'s `"timing"` and trace become `"<seconds>"`, and the exchange gets a `note: deterministic` line. If a search hit its deadline the capture stops instead of recording it, because that result is not deterministic. |
EXTRA_RULES

Values the capture does not need to mask because it never records them: session cookies (only `Set-Cookie`-independent fields, status, content type and `Location` are kept), other response headers, and photo bytes (issue #8).

Fixed by construction: the server image sets `TZ=America/New_York`, so `RaceChart.completed` is a local-time string wherever it is captured. Race times come from a function of car number and lane, never from a clock.

## Things the fixtures show about PHP

These are recorded as found and are not fixed here:

- `critical_f_value()` returns nothing for 8 lanes (`df1` = 7 is past the end of the table), so `lane_bias_analysis()` reports a critical value of 0.000 for an 8-lane track.
- `make_ordered_schedule()` has no table for 7 lanes or for more than 200 cars, and raises a `TypeError`. `schedule.generate` answers with status 500 and an empty body.
- `schedule.generate` reports success when the schedule cannot be made (`cant-fulfill`), because `json_success()` overwrites the failure.
- `action.php?query[]=poll` answers 500 with an empty body.
- `timer-message` with `IDENTIFIED&nlanes=4` (what the suite sends) does not set the lane count, which the server reads from `lane_count`.
- A `FINISHED` for the wrong heat answers `<failure code='wrong-heat'>` and then `<success/>`.

## Adding a case

A ticket that needs a case this tool lacks adds it here: a `capture` line in a scenario, or a new file in `capture/scenarios/`, or a case in `capture-engine.php`. Then rerun `capture.sh` and commit the new fixtures with the change.
