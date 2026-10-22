<?php
// Returns {standings: {<stkey>: [[racerid, ...], ...], ...}}
//
// Each ladder gives the racerids for first place, second place, and so on, as
// described in inc/standings.inc.  A tie yields several racerids in one rung
// and empty rungs after it.

require_once('inc/standings.inc');

$oracle = new StandingsOracle();
// An object even when empty, so the JSON has the same shape either way.
json_out('standings', (object) $oracle->all_standings);
?>
