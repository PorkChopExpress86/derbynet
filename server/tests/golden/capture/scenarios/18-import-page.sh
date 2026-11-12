# PHP's roster import page, website/import-roster.php, driven in a browser
# (#242, for #49): each sample roster in testing/data, read in the encoding it
# is written in and with each column dragged to the field its header names.
# One transcript per file holds the racer.import requests the page sent, in
# order, and racer.list&order=name after them.  The Mac file has CR line ends,
# and the xlsx file's first sheet is the Mac Roman reading of the roster.
# The files are named by their $ROOT/testing paths, so golden_inputs.py
# counts them as inputs of the capture.

import_page() {
  reset_database
  record_import_page "import/$1" "$2" "$3" "$4" "${5:-0}"
}

import_page sample-names "$ROOT/testing/data/sample-names.csv" 10000 firstname,lastname,partition,carnumber
import_page sample-names-Mac "$ROOT/testing/data/sample-names-Mac.csv" 10000 firstname,lastname,partition,carnumber
import_page sample-names-Windows "$ROOT/testing/data/sample-names-Windows.csv" 1252 firstname,lastname,partition,carnumber
import_page sample-names-MS-DOS "$ROOT/testing/data/sample-names-MS-DOS.csv" 437 firstname,lastname,partition,carnumber
import_page sample-names-ascii "$ROOT/testing/data/sample-names-ascii.csv" 65001 first-last,partition,carnumber
import_page sample-names-firstlast "$ROOT/testing/data/sample-names-firstlast.csv" 65001 first-last,partition,carnumber
import_page sample-names-last-comma-first "$ROOT/testing/data/sample-names-last-comma-first.csv" 65001 first-last,partition,carnumber
import_page sample-names-plurals "$ROOT/testing/data/sample-names-plurals.csv" 65001 first-last,partition,carnumber
import_page sample-names-xlsx "$ROOT/testing/data/sample-names.xlsx" 65001 firstname,lastname,partition,carnumber
