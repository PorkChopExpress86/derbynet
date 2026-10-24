<?php
// Writes a copy of a schema-10 database as an older DerbyNet would have made it.
// build.sh runs it under the server image's PHP CLI, from a copy of website/
// whose expected_schema_version() returns <version>:
//
//   php downgrade.php <v10 database> <new database> <version>
//
// schema.inc gives the tables at <version>, except for the scripts it includes
// whatever the version: those tables and columns are dropped here, as
// update-schema.inc adds them.  The rows come from the v10 database, column by
// column, less the aggregate groups that the version cannot store.  Before version 10 check-in times live in Events, so each checked-in
// racer gets an EVENT_CHECKIN row.  Version 1 has no `schema` key, mixed-case
// GPRM column names, a 50-character value, and a RaceChart row with no racer.

error_reporting(E_ALL);

list(, $source, $target, $version) = $argv;
$version = (int) $version;
if (expected_schema_version_of_tree() != $version) {
  fwrite(STDERR, "inc/schema_version.inc must expect version $version\n");
  exit(1);
}

$db = new PDO('sqlite:'.$target);
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$db->setAttribute(PDO::ATTR_CASE, PDO::CASE_LOWER);
$dbtype = 'sqlite';

function sql_file_path($stem) {
  return 'sql'.DIRECTORY_SEPARATOR.'sqlite'.DIRECTORY_SEPARATOR.$stem.'.inc';
}

function expected_schema_version_of_tree() {
  require_once('inc/schema_version.inc');
  return expected_schema_version();
}

foreach (include(sql_file_path('schema')) as $stmt) {
  $db->exec($stmt);
}

// What update-schema.inc adds at each version, newest first.
$added = array(
  10 => array('DROP TABLE ActionHistory'),
  9 => array('DROP TABLE EventLaneDetails',
             'ALTER TABLE Events DROP COLUMN finishid'),
  8 => array('DROP TABLE Partitions',
             'DROP TABLE ConstituentClasses',
             'ALTER TABLE RegistrationInfo DROP COLUMN partitionid',
             'ALTER TABLE RegistrationInfo DROP COLUMN note'),
  7 => array('DROP TABLE TimerSettings'),
  6 => array('DROP TABLE BallotAwards', 'DROP TABLE Voters', 'DROP TABLE Votes'),
  4 => array('DROP TABLE MessageQueue', 'DROP TABLE SceneKiosk',
             'DROP TABLE Scenes', 'DROP TABLE Playlist'),
  );
foreach ($added as $since => $statements) {
  if ($version < $since) {
    foreach ($statements as $stmt) {
      $db->exec($stmt);
    }
  }
}

$db->exec('ATTACH DATABASE '.$db->quote($source).' AS src');

$tables = $db->query("SELECT name FROM main.sqlite_master WHERE type = 'table'")
             ->fetchAll(PDO::FETCH_COLUMN);
foreach ($tables as $table) {
  $mine = $db->query("SELECT name FROM pragma_table_info('$table', 'main')")
             ->fetchAll(PDO::FETCH_COLUMN);
  $theirs = $db->query("SELECT name FROM pragma_table_info('$table', 'src')")
               ->fetchAll(PDO::FETCH_COLUMN);
  $columns = implode(', ', array_intersect($mine, $theirs));
  $db->exec("DELETE FROM main.$table");
  if ($columns) {
    $db->exec("INSERT INTO main.$table($columns) SELECT $columns FROM src.$table");
  }
}

// Aggregates need Classes.constituents (version 3) or Classes.rankids
// (version 5), so an older database has none.
$aggregates = array();
if ($version < 3) {
  $aggregates[] = "constituents <> ''";
}
if ($version < 5) {
  $aggregates[] = "rankids <> ''";
}
if ($aggregates) {
  $where = 'classid IN (SELECT classid FROM src.Classes WHERE '.implode(' OR ', $aggregates).')';
  foreach (array('Awards', 'Playlist', 'Rounds', 'Classes') as $table) {
    if (in_array($table, $tables)) {
      $db->exec("DELETE FROM main.$table WHERE $where");
    }
  }
}

// Before partitions, a subgroup named "* Not specified" was the default one,
// which after_update_schema() renames.
if ($version < 8) {
  $db->exec("UPDATE Ranks SET rank = '* Not specified'"
            .' WHERE rankid = (SELECT MAX(rankid) FROM Ranks)');
}

$db->exec("DELETE FROM RaceInfo WHERE itemkey = 'schema'");
if ($version > 1) {
  $db->exec("INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('schema', '$version')");
}

if ($version < 10) {
  $db->exec('INSERT INTO Events(tstamp, action, racerid)'
            ." SELECT datetime(checkin_time / 1000, 'unixepoch', 'localtime'), 1, racerid"
            .' FROM src.RegistrationInfo WHERE checkin_time IS NOT NULL'
            .' ORDER BY checkin_time, racerid');
}

if ($version == 1) {
  $db->exec("UPDATE RaceInfo SET itemvalue = '".str_repeat('x', 50)."'"
            ." WHERE itemkey = 'supergroup-label'");
  if ($db->query("SELECT COUNT(*) FROM RaceInfo WHERE itemkey = 'supergroup-label'")
         ->fetchColumn() == 0) {
    $db->exec("INSERT INTO RaceInfo(itemkey, itemvalue)"
              ." VALUES('supergroup-label', '".str_repeat('x', 50)."')");
  }
  $db->exec('INSERT INTO RaceChart(classid, roundid, heat, lane, racerid)'
            .' SELECT classid, roundid, MAX(heat) + 1, 1, NULL FROM RaceChart'
            .' WHERE roundid = (SELECT MIN(roundid) FROM RaceChart)');
  $renames = array('RegistrationInfo' => array('racerid' => 'RacerID',
                                               'carnumber' => 'CarNumber',
                                               'firstname' => 'FirstName',
                                               'lastname' => 'LastName'),
                   'RaceInfo' => array('itemkey' => 'ItemKey', 'itemvalue' => 'ItemValue'),
                   'Classes' => array('classid' => 'ClassID', 'class' => 'Class'),
                   'Awards' => array('awardid' => 'AwardID', 'awardname' => 'AwardName'));
  foreach ($renames as $table => $columns) {
    foreach ($columns as $from => $to) {
      $db->exec("ALTER TABLE $table RENAME COLUMN $from TO $to");
    }
  }
}

$db->exec('DETACH DATABASE src');
?>
