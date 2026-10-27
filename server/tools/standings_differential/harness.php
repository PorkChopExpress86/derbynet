<?php
// Runs the real PHP standings, finalists and speed-award code on events read
// from a JSON Lines file, one event per line, and writes one JSON line of
// results per event.  Run from the website root:
//
//   php84 harness.php <events.jsonl> <results.jsonl>
//
// Each event is loaded into a fresh in-memory SQLite database built from
// sql/sqlite/schema.inc, the schema PHP installs.  Then StandingsOracle,
// add_speed_awards, the three calculate_roster_* builders, topological_sort
// and ajax/query.award.current.inc run on it unchanged.  json_out() and
// json_failure(), which action.php defines, are stood in for, and PHP
// warnings are recorded with the results.

ini_set('memory_limit', '2G');
ini_set('serialize_precision', '-1');
error_reporting(E_ALL);
$_SERVER['DERBYNET_CONFIG_DIR'] = '/nonexistent-differential-config';

$GLOBALS['warnings'] = array();
set_error_handler(function($errno, $errstr, $errfile, $errline) {
  $GLOBALS['warnings'][] = basename($errfile).':'.$errline.': '.$errstr;
  return true;
});

$GLOBALS['json_out'] = array();
$GLOBALS['failure'] = null;
function json_out($key, $value) { $GLOBALS['json_out'][$key] = $value; }
function json_failure($code, $description) { $GLOBALS['failure'] = $code; }

function fresh_db() {
  $db = new PDO('sqlite::memory:');
  $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
  $GLOBALS['db'] = $db;
  $GLOBALS['dbtype'] = 'sqlite';
  $GLOBALS['labels'] = false;
  foreach (include('sql/sqlite/schema.inc') as $sql) {
    $db->exec($sql);
  }
}

// data.inc reads $db as it loads, and the schema script needs sql-script.inc,
// which needs data.inc.
$db = new PDO('sqlite::memory:');
require_once('inc/data.inc');
require_once('inc/sql-script.inc');
fresh_db();
require_once('inc/standings.inc');
require_once('inc/awards.inc');
require_once('inc/roster.inc');
require_once('inc/aggregate_round.inc');
require_once('inc/classes.inc');
require_once('inc/photo-config.inc');
require_once('inc/name-mangler.inc');

function load_event($ev) {
  fresh_db();
  $db = $GLOBALS['db'];
  foreach ($ev['settings'] as $key => $value) {
    write_raceinfo($key, $value);
  }
  $db->exec("INSERT INTO Partitions(partitionid, name, rankid, sortorder) VALUES(1, 'Default', 0, 1)");
  $classes = $db->prepare('INSERT INTO Classes(classid, class, constituents, rankids, durable, ntrophies, sortorder)'
                          .' VALUES(?, ?, ?, ?, 1, ?, ?)');
  $cc = $db->prepare('INSERT INTO ConstituentClasses(antecedent, dependent) VALUES(?, ?)');
  foreach ($ev['Classes'] as $c) {
    $classes->execute(array($c['classid'], $c['class'], implode(',', $c['constituents']),
                            implode(',', $c['rankids']), $c['ntrophies'], $c['sortorder']));
    foreach ($c['constituents'] as $ante) {
      $cc->execute(array($ante, $c['classid']));
    }
  }
  $ranks = $db->prepare('INSERT INTO Ranks(rankid, rank, classid, ntrophies, sortorder) VALUES(?, ?, ?, ?, ?)');
  foreach ($ev['Ranks'] as $r) {
    $ranks->execute(array($r['rankid'], $r['rank'], $r['classid'], $r['ntrophies'], $r['sortorder']));
  }
  $racers = $db->prepare('INSERT INTO RegistrationInfo(racerid, carnumber, carname, lastname, firstname,'
                         .' classid, rankid, partitionid, passedinspection, exclude)'
                         .' VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?, ?)');
  foreach ($ev['RegistrationInfo'] as $r) {
    $racers->execute(array($r['racerid'], $r['carnumber'], $r['carname'], $r['lastname'], $r['firstname'],
                           $r['classid'], $r['rankid'], $r['passedinspection'], $r['exclude']));
  }
  $round_class = array();
  $rounds = $db->prepare('INSERT INTO Rounds(roundid, round, classid, charttype, phase) VALUES(?, ?, ?, 0, 0)');
  foreach ($ev['Rounds'] as $r) {
    $rounds->execute(array($r['roundid'], $r['round'], $r['classid']));
    $round_class[$r['roundid']] = $r['classid'];
  }
  $roster = $db->prepare('INSERT INTO Roster(roundid, classid, racerid) VALUES(?, ?, ?)');
  foreach ($ev['Roster'] as $r) {
    $roster->execute(array($r['roundid'], $round_class[$r['roundid']], $r['racerid']));
  }
  $chart = $db->prepare('INSERT INTO RaceChart(classid, roundid, heat, lane, racerid, finishtime, finishplace)'
                        .' VALUES(?, ?, ?, ?, ?, ?, ?)');
  foreach ($ev['RaceChart'] as $r) {
    $chart->execute(array($round_class[$r['roundid']], $r['roundid'], $r['heat'], $r['lane'], $r['racerid'],
                          $r['finishtime'], $r['finishplace']));
  }
}

// [[key, value], ...] keeps PHP's key order through JSON.
function pairs($map) {
  $out = array();
  foreach ($map as $k => $v) {
    $out[] = array($k, $v);
  }
  return $out;
}

function int_or_null($v) {
  return $v === null ? null : (int) $v;
}

function roster_record($result) {
  list($finalists, $concerns) = $result;
  return array('finalists' => $finalists, 'concerns' => $concerns);
}

// The included file assigns $key, so the requested key needs another name.
function current_award($requested_key) {
  global $db;
  write_raceinfo('current_award_key', $requested_key);
  $GLOBALS['json_out'] = array();
  $GLOBALS['failure'] = null;
  try {
    include('ajax/query.award.current.inc');
  } catch (Throwable $t) {
    return array('key' => $requested_key, 'failure' => null, 'current' => null, 'racerid' => null,
                 'error' => get_class($t).': '.$t->getMessage());
  }
  $current = isset($GLOBALS['json_out']['current']) ? $GLOBALS['json_out']['current'] : null;
  return array('key' => $requested_key,
               'failure' => $GLOBALS['failure'],
               'current' => $current === null ? null
                   : array('key' => $current['key'], 'awardname' => $current['awardname'],
                           'carnumber' => $current['carnumber']),
               'racerid' => isset($racerid) ? $racerid : null);
}

function run_event($ev) {
  load_event($ev);
  $out = array();

  $oracle = new StandingsOracle();
  $rows = array();
  foreach ($oracle->result_summary as $r) {
    $rows[] = array('roundid' => $r['roundid'], 'racerid' => $r['racerid'], 'base' => $r['base'],
                    'score' => $r['score'], 'avg' => $r['avg'], 'best' => $r['best'], 'worst' => $r['worst'],
                    'nonfinal' => isset($r['nonfinal']), 'outcome' => pairs($r['outcome']));
  }
  $out['rows'] = $rows;
  $out['ladders'] = pairs($oracle->all_standings);
  $out['catalog_counts'] = pairs($oracle->catalog_counts);
  $out['catalog'] = $oracle->standings_catalog();
  list($classmap, $rankmap) = $oracle->class_and_rank_keys();
  $out['class_keys'] = pairs($classmap);
  $out['rank_keys'] = pairs($rankmap);

  $aggregates = aggregate_classes();
  $out['topological_order'] = array_map(function($c) { return (int) $c['classid']; },
                                        topological_sort($aggregates));

  $awards = array();
  $out['reserved_overall'] = add_speed_awards($awards);
  $out['awards'] = array_map(function($a) {
    return array('key' => $a['awardkey'], 'name' => $a['awardname'], 'place' => $a['place'],
                 'sort' => $a['sort'], 'racerid' => $a['racerid'],
                 'classid' => int_or_null($a['classid']), 'rankid' => int_or_null($a['rankid']));
  }, $awards);

  $finalists = array();
  foreach ($ev['requests'] as $req) {
    if ($req['kind'] == 'follow_on') {
      $res = calculate_roster_follow_on_round($req['classid'], $req['bucketed'], $req['limit']);
    } else if ($req['kind'] == 'classes') {
      $res = calculate_roster_from_classes($req['classids'], $req['bucketed'], $req['limit']);
    } else {
      $res = calculate_roster_from_subgroups($req['rankids'], $req['bucketed'], $req['limit']);
    }
    $finalists[] = roster_record($res);
  }
  $out['finalists'] = $finalists;

  $keys = array();
  foreach ($awards as $a) {
    $keys[] = $a['awardkey'];
  }
  foreach ($ev['extra_award_keys'] as $k) {
    $keys[] = $k;
  }
  $out['current'] = array_map('current_award', array_values(array_unique($keys)));
  return $out;
}

$in = fopen($argv[1], 'r');
$outf = fopen($argv[2], 'w');
while (($line = fgets($in)) !== false) {
  if (trim($line) == '') {
    continue;
  }
  $ev = json_decode($line, true);
  $GLOBALS['warnings'] = array();
  try {
    $result = run_event($ev);
  } catch (Throwable $t) {
    $result = array('error' => get_class($t).': '.$t->getMessage().' at '.basename($t->getFile()).':'.$t->getLine());
  }
  $result['id'] = $ev['id'];
  $result['warnings'] = $GLOBALS['warnings'];
  fwrite($outf, json_encode($result, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION)."\n");
}
fclose($outf);
