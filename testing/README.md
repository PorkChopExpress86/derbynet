# DerbyNet test suite

The tests are shell scripts that drive a running DerbyNet server over HTTP, plus
a few Puppeteer scripts in `puppeteer/`.  `exercise-one-database.sh` runs all of
them.  The `php-suite` GitHub Actions workflow runs the suite against the PHP
server image on every pull request and every push to master.

## Run the suite on Linux

You need `docker` and `bash`.  From the root of a git checkout, run:

    testing/docker/run-suite.sh

The script does everything inside containers, and takes a while (a first run
also downloads and builds the images).  It

1. builds a harness image with the test tools,
2. builds the gitignored files the PHP pages need (`website/inc/banner.inc`,
   `website/timer.php`, and so on),
3. builds the server image from `installer/docker/Dockerfile` and starts it with
   a fresh data volume,
4. raises the PHP and nginx timeouts, which `test-extended-scheduling.sh` needs,
5. runs `test-face-detection.sh`, `js-syntax-check.sh`, and
   `exercise-one-database.sh http://localhost ez`, and
6. removes the server container and its volume.

A successful run prints the line

    <<<<<<<<<<<<<<<<<<<<<<<< Test run complete >>>>>>>>>>>>>>>>>>>>>>>>

as the last line of the suite's output.  Any failure stops the run with
`TEST FAILURE` and the last response received.  `testing/output.curl`,
`testing/debug.curl`, and `testing/suite.log` hold the details.

Name the steps to run some of them, e.g. `run-suite.sh harness assets server`
to leave a server running, and then `run-suite.sh suite`; `run-suite.sh logs`
prints the server's log, and `run-suite.sh clean` removes the server.  Set
`DERBYNET_TEST_PREFIX` to change the prefix (`derbynet-test`) on the names of
the images, container, and volume.

Run from a regular checkout, not a `git worktree`: the containers can't see the
repository's git directory, and the build needs it.

To see what PHP does with one script, from a regular checkout or a worktree,
build the images once with `run-suite.sh harness assets server` and then run

    testing/docker/php-scratch.sh test-points-racing.sh

It starts a throwaway PHP server, creates the event, runs the script against
it, and removes the server. Pass `--no-event` for a script that sets the event
up itself, such as `exercise-one-database.sh`. A fresh PHP server has no event,
so a script that logs in first fails with `parse error: Invalid numeric literal`
until `setup.nodata` has created one.

A server database can't be reused: setup (`ez-new=testdb`) fails with
`already_exists` the second time, so each run needs a fresh data volume.  The
script always makes one.

## Run individual scripts from the host

The scripts need `bash`, `curl`, `jq`, `perl`, `xmllint` (Debian and Ubuntu
package `libxml2-utils`, used only for the XML responses), `md5sum`, and
ImageMagick's `identify` and `convert` (`test-photo-manipulations.sh` measures
photos with them).  The Puppeteer tests and `js-syntax-check.sh`
also need `node`: run `npm ci` in `testing/puppeteer` and
`export NODE_PATH=$PWD/testing/puppeteer/node_modules`.

Start a server that listens on a port of localhost:

    DERBYNET_TEST_PORT=8080 testing/docker/run-suite.sh harness assets server timeout

Then give the script the server's URL, for example

    bash testing/exercise-one-database.sh http://localhost:8080 ez

Stop the server with `testing/docker/run-suite.sh clean`.

## Write a test that works against any server

The suite is meant to gate more than one server, so a test checks behavior
through JSON and stable keys, and not through what the PHP server happens to
render or number:

- To look at a page, use `curl_page`.  It checks the status and the error text
  and doesn't parse the HTML.  `page_text` gives a page's visible text.
- To check standings, read the workbook that `export-json.php` serves with
  `standings_sheet`, `standings_row` and `expect_row`.
- Which pages each role can open is `fixtures/role-pages.json`, and
  `test-permissions.sh` holds every server to it.
- Never write a database ID.  Look it up by a stable key with `racerid_of`
  (car number), `racerid_named`, `roundid_of` (class and round), `classid_of`,
  `rankid_of`, `partitionid_of` and `awardid_of`, or take it from the response
  that created the thing.  `normalize_ids` replaces every ID in a response with
  a label, to compare it with a golden response.
- `check-no-literal-ids.sh` fails when a script contains a literal ID.  An
  exception goes in `check-no-literal-ids.allowlist`, with its reason.

The suite also runs with the IDs shifted.  `DERBYNET_SHIFT_IDS=1
testing/docker/run-suite.sh` makes the server's SQLite tables never reuse an ID,
the way Django's don't, and creates and deletes throwaway rows after every
database reset (`shift-ids.sh`), so that nothing created in the suite gets its
usual ID.

`test-ab-initio-setup.sh` is PHP only.  Set `DERBYNET_BACKEND=django` to skip it.

`test-photo-manipulations.sh` checks how the renders of a photo with EXIF
orientation 6 come out.  The PHP server ignores the tag (#73) and won't be
fixed, so by default the script expects PHP's sideways renders.  With
`DERBYNET_BACKEND=django` it expects upright ones.

`test-reschedule.sh` checks PHP's charts by default.  With
`DERBYNET_BACKEND=django` it expects the charts and car numbers of Django,
which keeps the running and staged heats (D34, Q20), and the `notonroster`
refusals of a racer who is not on the round's roster (D39, Q25): the 17
previews that fit a Lions racer into White's Wolves, and one latecomer that
`partition=2` puts in a new partition.  Its stress-loop latecomers join White's
Wolves instead, so Django fits them in, and the charts are checked.  It reads its
standings from `export-json.php`, since Django does not serve
`query=standings`.

## Dev and demo scripts

These are not part of the suite, and run against the PHP or the Django server.
Each takes the base URL first.

- `demo.sh` runs an unattended kiosk demo until stopped: it resets the
  database, imports and checks in the roster, races heats, and moves every
  kiosk through the check-in, slideshow, on-deck, now-racing, results and award
  pages.  `demo-multi.sh` does the same with three kiosks, `main`, `aux` and
  `ondeck`, each on its own page.
- `setup-basic.sh` and `setup-basic-no-photos.sh` start an event named
  `testdb` with the suite's roster, with or without photos.
  `setup-single-group-operation.sh` sets up one group of 14 racers, checked
  in, with the first heat racing.  `checkin-all.sh` checks in every racer.
- `run-scout-heats.sh` races every first round of `import-roster.sh`'s roster.
  `test-aggregate-rounds.sh` and `test-aggregate-classes.sh` run it.
- `send-snapshot.sh <url> <file>` puts a snapshot back.
  `post-timer-log.sh <url> < text` appends to the timer log as the timer.
- `simulate-replay.sh <url> [seconds]` polls `replay.message` the way
  `replay.php` does, and prints each replay command.
- `polling-loop.sh` polls `poll.coordinator` nonstop, for load.

CI runs the ones that end on their own against Django, in
`.github/workflows/server.yml`.  `server/legacy_api/tests/test_testing_scripts.py`
checks that PHP has every endpoint a script in `testing/` names and that Django
serves it or defers it to a backlog ticket, that every script a script runs is
there, and that each parses.

## Compare two runs

`common.sh` appends every request and its response to `output.curl`, next to
the scripts.  Run the same scripts against two servers from two copies of
`testing/`, then compare the transcripts from `server/`:

    uv run python -m tools.transcript_diff --labels php,django php/testing/output.curl django/testing/output.curl

It pairs the exchanges in order, numbers database ids by first appearance,
masks clocks, cache breakers and session ids, and prints each difference with
its endpoint, request and JSON or XML path.  It exits 1 on any difference.
The docstring of `server/tools/transcript_diff.py` lists what it normalizes.

## Test photos

`data/carphotos/pattern-o6.jpg` stands in for a phone photo taken in portrait:
four colored quarters, stored turned 90 degrees, with EXIF orientation 6 to
turn them back.  `data/carphotos/make-pattern-o6.py` made it.  To make it
again, run the script with Pillow 11.0.0, which gives the same bytes:

    docker run --rm -v "$PWD/testing/data/carphotos:/d" python:3.12-slim \
        sh -c 'pip install -q pillow==11.0.0 && python /d/make-pattern-o6.py /d/pattern-o6.jpg'
    git status testing/data/carphotos     # prints no change

Check the tag with a reader that prints its number, such as
`exiftool -n -Orientation` or Pillow's `getexif()[0x112]`.  `file` prints
`orientation=upper-right` for tag 6.

## macOS

`suite-local-mac.sh` runs the suite against a DerbyNet installed on the local
machine.
