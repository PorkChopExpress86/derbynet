#! /bin/bash
#
# The please-check-in kiosk's classids filter (#231, D83).  An empty list lists
# every class, a list of class ids lists those classes, and [0], which names no
# class, lists nobody: PHP pastes the list into `classid IN (...)` only when it
# is not empty (website/kiosks/please-check-in.kiosk:101-102).  An import gives
# a kiosk whose classes are all gone [0] for that reason.
#
# Needs import-roster.sh first.  Adolfo Asher races in Lions & Tigers and Angelo
# Alas in White's Wolves.

BASE_URL=$1
set -e -E -o pipefail
source `dirname $0`/common.sh

LIONS=$(classid_of "Lions & Tigers")

PAGE=$(mktemp)
trap 'rm -f "$PAGE"' EXIT

check_in() {
    curl_page "kiosk.php?page=kiosks/please-check-in.kiosk&parameters=$(jq -rn --arg v "$1" '$v|@uri')" \
        | page_text
}

check_in '{"classids":[]}' > "$PAGE"
expect_count "Asher," 1 < "$PAGE"
expect_count "Alas," 1 < "$PAGE"

check_in "{\"classids\":[$LIONS]}" > "$PAGE"
expect_count "Asher," 1 < "$PAGE"
expect_count "Alas," 0 < "$PAGE"

check_in '{"classids":[0]}' > "$PAGE"
expect_count "Asher," 0 < "$PAGE"
expect_count "Alas," 0 < "$PAGE"
