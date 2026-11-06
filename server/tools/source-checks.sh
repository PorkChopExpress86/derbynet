#! /bin/bash
#
# Runs the tests that read only the source, not a database or a page: the PHP
# cites and claims, the QUIRK_DECISIONS records, copied helpers, the rules in
# services, read-then-act views, browser tab closes, the registry, and the
# parity gate's entries for the suites server.yml runs.  They take a few
# seconds, and .githooks/pre-push runs them.  #48 found five of them failing
# one full-suite round at a time (#48 retro); run this after each batch of
# edits.
#
# Usage: server/tools/source-checks.sh [pytest argument ...]

set -e -o pipefail

cd "$(dirname "$0")/.."
exec env -u DATABASE_URL uv run --locked pytest -q -p no:cacheprovider \
  tests/test_php_claims.py \
  tests/test_php_cite_lines.py \
  tests/test_deviations.py \
  tests/test_parity_gate_ci.py \
  tests/test_pytest_under_load.py \
  ui/tests/test_no_copied_helpers.py \
  ui/tests/test_rules_in_services.py \
  ui/tests/test_read_then_act.py \
  ui/tests/test_browser_tab_close.py \
  ui/tests/test_php_libraries.py \
  legacy_api/tests/test_registry.py \
  core/tests/test_read_only_guard.py \
  "$@"
