#! /bin/bash
#
# Runs the tests that read only the source, not a database or a page: the PHP
# cites and claims, the QUIRK_DECISIONS records, copied helpers, the rules in
# services, read-then-act views, browser tab closes, one-shot box reads (#237
# retro), empty 200s a browser test
# answers (#224 retro), one reader of legacy ids in transfer (#231
# retro), the registry, tests that
# flush the seeded rows, frozen exception classes, and the parity gate's
# entries for the suites server.yml runs, fixtures only xdist provides (#220
# retro), a test that starts Playwright in the pytest process (#239 retro),
# prose that calls a clamped font by its viewport term (#283 retro),
# the first entry of a folder listing taken as it comes (#312 retro),
# the role page lists of testing/test-permissions.sh against the page gates
# (#319 retro), a fake clock paused at its install time and a browser test
# that takes no fixture (#317 retro),
# and plan/tests, the plan tools' tests
# that plan-checks.yml runs (#218 retro).  #238 found eleven guards left out
# (the browser fixture order among them, which only the full suite caught);
# tests/test_source_checks.py now fails a test module that imports `ast` or
# walks a tree with `rglob` and is neither listed here nor exempted.  They
# took 16 min at a load average of 34 (#311's pr-merge run), and
# .githooks/pre-push runs them.  #48 found five of them failing
# one full-suite round at a time (#48 retro); run this after each batch of
# edits.
#
# Usage: server/tools/source-checks.sh [pytest argument ...]

set -e -o pipefail

# Under `timeout`, tests/test_pytest_under_load.py's runs of that script see
# the timeout above them, are refused, and fail: #282 lost a round to it.
# These refuse it too; they took 16 min at a load average of 34 (#311).
pid=$PPID
while [ "${pid:-0}" -gt 1 ]; do
  if [ "$(ps -o comm= -p "$pid" || true)" = timeout ]; then
    echo "source-checks.sh: it runs under timeout, which" \
         "tests/test_pytest_under_load.py's tests would take for their own." \
         "It took 16 min at a load average of 34 (#311): run it with no timeout." >&2
    exit 2
  fi
  pid=$(ps -o ppid= -p "$pid" | tr -d ' ') || pid=1
done

cd "$(dirname "$0")/.."
# plan/bin reads the parity gate and the workflows; #222 broke a plan test.
# Both halves run whatever the first one says, and the script exits with the
# last failure's status: #312 stopped at a failing plan test and needed a
# second scratch merge to learn the pytest half passed (#312 retro).
status=0
python3 -I -m unittest discover -s ../plan/tests </dev/null || status=$?
env -u DATABASE_URL uv run --locked pytest -q -p no:cacheprovider \
  tests/test_php_claims.py \
  tests/test_php_cite_lines.py \
  tests/test_clamp_claims.py \
  tests/test_deviations.py \
  tests/test_parity_gate_ci.py \
  tests/test_pytest_under_load.py \
  tests/test_no_xdist_fixtures.py \
  tests/test_playwright_in_process.py \
  tests/test_transactional_tests.py \
  tests/test_docker_checks.py \
  tests/test_readme_compose.py \
  tests/test_pipefail_pipes.py \
  tests/test_listing_order.py \
  tests/test_plan_source_checks.py \
  tests/test_role_pages_fixture.py \
  transfer/tests/test_id_readers.py \
  ui/tests/test_no_copied_helpers.py \
  ui/tests/test_rules_in_services.py \
  ui/tests/test_read_then_act.py \
  ui/tests/test_browser_tab_close.py \
  ui/tests/test_browser_one_shot_reads.py \
  ui/tests/test_browser_empty_fulfill.py \
  ui/tests/test_browser_missing_allowance.py \
  ui/tests/test_browser_clock_pause.py \
  ui/tests/test_browser_source_only.py \
  ui/tests/test_php_libraries.py \
  legacy_api/tests/test_registry.py \
  core/tests/test_read_only_guard.py \
  core/tests/test_exception_classes.py \
  tests/test_source_checks.py \
  tests/test_management_commands.py \
  tests/test_golden_marker.py \
  ui/tests/test_browser_fixture_order.py \
  ui/tests/test_photo_urls.py \
  ui/tests/test_phone_grids.py \
  ui/tests/test_race_day_screens.py \
  ui/tests/test_relative_urls.py \
  ui/tests/test_vendored.py \
  ui/tests/test_settings_bounds.py \
  ui/tests/test_htmx.py::test_no_reply_replaces_an_element_a_trigger_listens_to \
  ui/tests/test_htmx.py::test_no_control_queues_behind_a_poll \
  media/tests/test_repository_paths.py \
  racing/engine/tests/test_no_django.py \
  legacy_api/tests/test_testing_scripts.py \
  "$@" || status=$?
exit "$status"
