<?php
// Captures what the PHP scheduling, ordinal and lane-bias code produces, with
// no server and no HTTP.  Run from the website/ directory:
//
//   php -d serialize_precision=-1 capture-engine.php <outdir> [options]
//
// capture.sh runs this inside the server image.  Options:
//   --workers=N    processes for the schedule sweep (default: the CPU count)
//   --no-jit       leave the PHP JIT off in the sweep workers
//   --max-cars=N   a smoke-test limit, never used for committed fixtures
//
// The scheduling functions are free of database access.  Two things they reach
// for are stood in for: json_out() and json_failure(), which action.php defines
// for the HTTP path and which here record what the engine reported, and
// read_raceinfo(), which comes from the real inc/data.inc over an in-memory
// SQLite database that holds just the RaceInfo and RaceChart tables.
//
// Every case is recorded as a canonical JSON record.  The manifests hold its
// SHA-256, and a representative subset also gets the full record on disk.  See
// server/tests/golden/README.md for the formats.

ini_set('memory_limit', '2G');
error_reporting(E_ALL);

const LANE_COUNTS = array(2, 3, 4, 5, 6, 8);
const MAX_CARS = 200;
const ROTATION_LANES = array(2, 3, 4, 5, 6, 7, 8);
const ROTATION_RUNS = array(1, 2, 3);

// ---------------------------------------------------------------------------
// Harness

$GLOBALS['json_out'] = array();
$GLOBALS['failure_code'] = null;
$GLOBALS['warnings'] = array();

function json_out($key, $value) {
  $GLOBALS['json_out'][$key] = $value;
}

function json_failure($code, $description) {
  $GLOBALS['failure_code'] = $code;
}

set_error_handler(function($errno, $errstr, $errfile) {
  $GLOBALS['warnings'][] = basename($errfile).': '.$errstr;
  return true;
});

// In-memory database for the two code paths that read the database.
$_SERVER['DERBYNET_CONFIG_DIR'] = '/nonexistent-golden-config';
$db = new PDO('sqlite::memory:');
$db->exec('CREATE TABLE RaceInfo (raceinfoid INTEGER PRIMARY KEY, itemkey VARCHAR(20) NOT NULL,'
          .' itemvalue VARCHAR(200))');
$db->exec('CREATE TABLE RaceChart (resultid INTEGER PRIMARY KEY, classid INTEGER, roundid INTEGER NOT NULL,'
          .' heat INTEGER NOT NULL, lane INTEGER NOT NULL, racerid INTEGER, chartnumber INTEGER,'
          .' finishtime DOUBLE NULL, finishplace INTEGER, points INTEGER, completed TIMESTAMP,'
          .' ignoretime TINYINT(1) DEFAULT 0, masterheat INTEGER DEFAULT 0)');

require_once('inc/data.inc');
require_once('inc/schedule_rough.inc');
require_once('inc/schedule_ordered.inc');
require_once('inc/schedule_rotation.inc');
require_once('inc/schedule_utils.inc');
require_once('inc/ordinals.inc');
require_once('inc/lane-bias.inc');

// Canonical JSON: what the manifests hash.  Integers and strings only, except
// the lane-bias floats, which serialize_precision=-1 prints shortest-round-trip.
function cj($v) {
  return json_encode($v, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE | JSON_PRESERVE_ZERO_FRACTION);
}

// Runs $fn, collecting what it reports.  Never throws: a PHP error is a fixture.
function observe($fn) {
  $GLOBALS['json_out'] = array();
  $GLOBALS['failure_code'] = null;
  $GLOBALS['warnings'] = array();
  $error = null;
  $value = null;
  try {
    $value = $fn();
  } catch (Throwable $t) {
    $error = get_class($t).': '.$t->getMessage();
  }
  return array($value, $GLOBALS['failure_code'], $GLOBALS['warnings'], $error);
}

function ordered_record($nl, $nc, $nt, $max_runs) {
  list($heats, $failure, $warnings, $error) = observe(function() use ($nl, $nc, $nt, $max_runs) {
    return make_ordered_schedule($nl, $nc, $nt, $max_runs, false);
  });
  $rec = array('failure' => $failure, 'nheats' => is_array($heats) ? count($heats) : null,
               'heats' => is_array($heats) ? $heats : null);
  if ($warnings) { $rec['warnings'] = $warnings; }
  if ($error !== null) { $rec['error'] = $error; }
  return $rec;
}

function rotation_record($nl, $nc, $nt) {
  list($heats, $failure, $warnings, $error) = observe(function() use ($nl, $nc, $nt) {
    return make_rotation_schedule($nl, $nc, $nt, false);
  });
  $out = $GLOBALS['json_out'];
  $rec = array('failure' => $failure, 'nheats' => isset($out['nheats']) ? $out['nheats'] : null,
               'ncars' => isset($out['ncars']) ? $out['ncars'] : null,
               'heats' => isset($out['heats']) ? $out['heats'] : null);
  if ($heats !== (isset($out['heats']) ? $out['heats'] : null)) {
    $rec['returned_differs_from_json_out'] = true;
  }
  if ($warnings) { $rec['warnings'] = $warnings; }
  if ($error !== null) { $rec['error'] = $error; }
  return $rec;
}

// Records with a "heats" list get one heat per line, so a diff shows which heat
// changed.  The result is still valid JSON.
function write_cases($path, $cases) {
  $parts = array();
  foreach ($cases as $key => $rec) {
    $heats = null;
    if (isset($rec['heats']) && is_array($rec['heats'])) {
      $heats = $rec['heats'];
      unset($rec['heats']);
    }
    $s = cj((string) $key).': {"meta": '.cj($rec);
    if ($heats !== null) {
      $s .= ', "heats": ['."\n".implode(",\n", array_map('cj', $heats))."\n]";
    }
    $parts[] = $s.'}';
  }
  write_file($path, "{\n".implode(",\n", $parts)."\n}\n");
}

function write_file($path, $contents) {
  @mkdir(dirname($path), 0777, true);
  if (file_put_contents($path, $contents) === false) {
    fwrite(STDERR, "cannot write $path\n");
    exit(1);
  }
}

function write_json_lines($path, $obj) {
  // One top-level key per line.
  $lines = array();
  foreach ($obj as $k => $v) {
    $lines[] = cj((string) $k).': '.cj($v);
  }
  write_file($path, "{\n".implode(",\n", $lines)."\n}\n");
}

// The representative subset that gets full outputs: every lane count, car
// counts from the lane count to the lane count plus 10, and 17, 23, 50, 100, 200.
function is_representative($nl, $nc) {
  return ($nc >= $nl && $nc <= $nl + 10) || in_array($nc, array(17, 23, 50, 100, 200));
}

// ---------------------------------------------------------------------------
// Sweep: make_ordered_schedule() and make_rotation_schedule()

// Each task is (lanes, cars, max_runs list) and is run for every n_times from 1
// to and including the first cant-fulfill.
function sweep_tasks($max_cars) {
  $tasks = array();
  foreach (LANE_COUNTS as $nl) {
    for ($nc = $nl; $nc <= $max_cars; ++$nc) {
      $tasks[] = array('ordered', $nl, $nc, array(0, 1));
    }
  }
  // testing/test-extended-scheduling.sh schedules one lane.
  for ($nc = 1; $nc <= $max_cars; ++$nc) {
    $tasks[] = array('ordered', 1, $nc, array(0));
  }
  return $tasks;
}

// Rough cost, for handing the big tasks out first.
function task_cost($task) {
  list($kind, $nl, $nc) = $task;
  $runs = $nl == 2 ? 12 : 6;
  return $nc * $nc * $nc * $runs * $runs * $runs;
}

// Runs one task; returns array('rows' => [[id, nheats, sha256]], 'full' => cases or null)
function run_ordered_task($nl, $nc, $max_runs_list) {
  $rows = array();
  $full = array();
  foreach ($max_runs_list as $m) {
    for ($nt = 1; ; ++$nt) {
      $rec = ordered_record($nl, $nc, $nt, $m);
      $rows[] = array("ordered/$nl/$nc/$nt/$m", $rec['nheats'], hash('sha256', cj($rec)));
      if (is_representative($nl, $nc)) {
        $full["t$nt-m$m"] = $rec;
      }
      if ($rec['failure'] !== null || isset($rec['error']) || $nt >= 40) {
        // Reached cant-fulfill (recorded above) or an error.
        break;
      }
    }
  }
  return array('rows' => $rows, 'full' => is_representative($nl, $nc) ? $full : null);
}

function worker_main($outdir, $worker, $max_cars) {
  $tasks = sweep_tasks($max_cars);
  usort($tasks, function($a, $b) { return task_cost($b) <=> task_cost($a); });
  @mkdir("$outdir/.work/claims", 0777, true);
  @mkdir("$outdir/.work/parts", 0777, true);
  foreach ($tasks as $task) {
    list($kind, $nl, $nc, $ms) = $task;
    $id = "$kind-$nl-$nc";
    if (!@mkdir("$outdir/.work/claims/$id")) {
      continue;
    }
    $result = run_ordered_task($nl, $nc, $ms);
    file_put_contents("$outdir/.work/parts/$id.json", json_encode($result));
  }
}

function cpu_count() {
  $n = (int) @shell_exec('nproc 2>/dev/null');
  return $n > 0 ? $n : 4;
}

function run_workers($outdir, $workers, $jit, $max_cars) {
  $procs = array();
  $flags = array('-d', 'serialize_precision=-1', '-d', 'memory_limit=2G');
  if ($jit) {
    array_push($flags, '-d', 'opcache.enable_cli=1', '-d', 'opcache.jit=tracing', '-d', 'opcache.jit_buffer_size=128M');
  }
  for ($w = 0; $w < $workers; ++$w) {
    $cmd = array_merge(array(PHP_BINARY), $flags,
                       array(__FILE__, $outdir, "--worker=$w", "--max-cars=$max_cars"));
    $p = proc_open($cmd, array(0 => array('file', '/dev/null', 'r'), 1 => STDOUT, 2 => STDERR), $pipes);
    if (!is_resource($p)) {
      fwrite(STDERR, "cannot start worker\n");
      exit(1);
    }
    $procs[] = $p;
  }
  foreach ($procs as $p) {
    $status = proc_close($p);
    if ($status != 0) {
      fwrite(STDERR, "sweep worker failed with status $status\n");
      exit(1);
    }
  }
}

function collect_sweep($outdir) {
  $parts = glob("$outdir/.work/parts/*.json");
  $rows = array();
  $full = array();
  foreach ($parts as $part) {
    $r = json_decode(file_get_contents($part), true);
    foreach ($r['rows'] as $row) {
      $rows[] = $row;
    }
    if ($r['full'] !== null) {
      preg_match('/ordered-(\d+)-(\d+)\.json$/', $part, $m);
      $full[sprintf('l%02d-c%03d', $m[1], $m[2])] = $r['full'];
    }
  }
  // Sort numerically by lanes, cars, runs, max_runs.
  usort($rows, function($a, $b) {
    $pa = array_map('intval', array_slice(explode('/', $a[0]), 1));
    $pb = array_map('intval', array_slice(explode('/', $b[0]), 1));
    return $pa <=> $pb;
  });
  ksort($full);
  return array($rows, $full);
}

function write_ordered_outputs($outdir, $rows, $full) {
  foreach (array(1, 2, 3, 4, 5, 6, 8) as $nl) {
    $lines = array();
    foreach ($rows as $row) {
      if (explode('/', $row[0])[1] == $nl) {
        $lines[] = implode("\t", array($row[0], $row[1] === null ? '-' : $row[1], $row[2]));
      }
    }
    write_file("$outdir/engine/ordered/manifest-lanes-$nl.tsv", implode("\n", $lines)."\n");
  }
  foreach ($full as $name => $cases) {
    write_cases("$outdir/engine/ordered/full/$name.json", $cases);
  }
}

// ---------------------------------------------------------------------------
// Rotation schedules, which are cheap and need no workers.

function capture_rotation($outdir) {
  $rows = array();
  $full_by_file = array();
  foreach (ROTATION_LANES as $nl) {
    for ($nc = $nl; $nc <= MAX_CARS; ++$nc) {
      foreach (ROTATION_RUNS as $nt) {
        $rec = rotation_record($nl, $nc, $nt);
        $rows[] = array("rotation/$nl/$nc/$nt", $rec['nheats'], hash('sha256', cj($rec)));
        if (is_representative($nl, $nc)) {
          $full_by_file[sprintf('l%02d-c%03d', $nl, $nc)]["t$nt"] = $rec;
        }
      }
    }
  }
  $lines = array();
  foreach ($rows as $row) {
    $lines[] = implode("\t", array($row[0], $row[1] === null ? '-' : $row[1], $row[2]));
  }
  write_file("$outdir/engine/rotation/manifest.tsv", implode("\n", $lines)."\n");
  ksort($full_by_file);
  foreach ($full_by_file as $name => $cases) {
    write_cases("$outdir/engine/rotation/full/$name.json", $cases);
  }
  return count($rows);
}

// ---------------------------------------------------------------------------
// What normal scheduling does outside the tables: 7 lanes, fewer cars than
// lanes, and more than 200 cars.

function capture_out_of_range($outdir) {
  $cases = array();
  $add = function($kind, $nl, $nc, $nt) use (&$cases) {
    $key = "$kind/$nl/$nc/$nt";
    $cases[$key] = $kind == 'ordered' ? ordered_record($nl, $nc, $nt, 0)
                                       : rotation_record($nl, $nc, $nt);
  };
  foreach (array(1, 2, 3, 4, 5, 6, 7, 8) as $nl) {
    $car_counts = array(201, 202, 250, 400);
    if ($nl == 7) {
      $car_counts = array_merge(range(1, 12), array(17, 23, 50, 100, 200), $car_counts);
    } else {
      $car_counts = array_merge(range(1, $nl - 1), $car_counts);
      if ($nl == 1) { $car_counts = array(201, 202); }
    }
    foreach ($car_counts as $nc) {
      $add('ordered', $nl, $nc, 1);
    }
  }
  write_cases("$outdir/engine/out-of-range.json", $cases);
  return count($cases);
}

// ---------------------------------------------------------------------------
// The generator tables, so the Python port can compare its own.

function capture_scheduler_data($outdir) {
  global $all_scheduler_data;
  $n = 0;
  foreach ($all_scheduler_data as $nl => $table) {
    if (!$table) { continue; }
    $lines = array();
    foreach ($table as $nc => $entry) {
      $lines[] = cj((string) $nc).': '.cj($entry);
      ++$n;
    }
    write_file("$outdir/engine/scheduler-data/lanes-$nl.json", "{\n".implode(",\n", $lines)."\n}\n");
  }
  $all = cj($all_scheduler_data);
  write_file("$outdir/engine/scheduler-data/sha256.txt", hash('sha256', $all)."  json_encode(\$all_scheduler_data)\n");
  return $n;
}

// ---------------------------------------------------------------------------
// spread_across_unused_lanes() and count_one_bits()

function capture_unused_lanes($outdir) {
  global $db;
  $cases = array();
  foreach (array(6, 8) as $nl) {
    for ($mask = 0; $mask < (1 << $nl); ++$mask) {
      $unused = count_one_bits($mask);
      if ($nl - $unused < 2) { continue; }
      $in_use = $nl - $unused;
      $chart = array();
      for ($h = 0; $h < 3; ++$h) {
        $heat = array();
        for ($l = 0; $l < $in_use; ++$l) {
          $heat[] = 100 * ($h + 1) + $l + 1;
        }
        $chart[] = $heat;
      }
      $db->exec("DELETE FROM RaceInfo WHERE itemkey = 'unused-lane-mask'");
      $db->exec("INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('unused-lane-mask', '$mask')");
      list($wide, $failure, $warnings, $error) = observe(function() use (&$chart) {
        return spread_across_unused_lanes($chart);
      });
      $rec = array('unused_lanes' => $unused, 'narrow' => $chart, 'wide' => $wide);
      if ($warnings) { $rec['warnings'] = $warnings; }
      if ($error !== null) { $rec['error'] = $error; }
      $cases["lanes=$nl mask=$mask"] = $rec;
    }
  }
  $db->exec("DELETE FROM RaceInfo WHERE itemkey = 'unused-lane-mask'");
  write_json_lines("$outdir/engine/unused-lanes.json", $cases);
  return count($cases);
}

// ---------------------------------------------------------------------------
// ordinal() and nth_fastest()

function capture_ordinals($outdir) {
  $cases = array();
  for ($n = 0; $n <= 120; ++$n) {
    foreach (array($n, 'T'.$n) as $arg) {
      list($v, , $w, $e) = observe(function() use ($arg) { return ordinal($arg); });
      $rec = array('ordinal' => $v);
      if ($w) { $rec['warnings'] = $w; }
      if ($e !== null) { $rec['error'] = $e; }
      $cases['ordinal('.cj($arg).')'] = $rec;
    }
    list($v, , $w, $e) = observe(function() use ($n) { return nth_fastest($n, 'Den'); });
    $rec = array('nth_fastest' => $v);
    if ($w) { $rec['warnings'] = $w; }
    if ($e !== null) { $rec['error'] = $e; }
    $cases["nth_fastest($n, \"Den\")"] = $rec;
  }
  write_json_lines("$outdir/engine/ordinals.json", $cases);
  return count($cases);
}

// ---------------------------------------------------------------------------
// lane_bias_analysis() and critical_f_value()

// The sentences history.php builds around lane_bias_analysis().  This mirrors
// history.php; capture_lane_bias() refuses to run if history.php stops saying
// these words.
const HISTORY_PHRASES = array(
  "There isn't enough evidence to assess lane bias.",
  "The track lanes appear to be biased, with 90% confidence.",
  "There is no evidence of significant lane bias.",
  "F statistic is ",
  "Critical value for F statistic is ",
);

function history_sentences($bias) {
  if (empty($bias)) {
    return array("There isn't enough evidence to assess lane bias.");
  }
  $s = array();
  $s[] = $bias['biased'] ? "The track lanes appear to be biased, with 90% confidence."
                         : "There is no evidence of significant lane bias.";
  $s[] = "F statistic is ".sprintf("%5.3f", $bias['f-statistic'])." with df1=".$bias['df1']." and df2=".$bias['df2'];
  $s[] = "Critical value for F statistic is ".sprintf("%5.3f", $bias['critical-value']);
  return $s;
}

// Deterministic finish times: each lane's j-th run.  The "noise" term is
// arithmetic on small integers, so the same on every machine.
function lane_times($kind, $nl, $runs, $lane, $j) {
  $noise = (($j * 37 + $lane * 11 + $nl * 5) % 97) / 1000.0;
  switch ($kind) {
    case 'even':       return 3.0 + $noise;
    case 'biased':     return 3.0 + 0.12 * ($lane - 1) + $noise;
    case 'one-slow':   return 3.0 + ($lane == 1 ? 0.2 : 0.0) + $noise;
    case 'identical':  return 3.25;
    case 'with-dnf':   return ($j % 5 == 4) ? 9.9999 : 3.0 + 0.05 * ($lane - 1) + $noise;
  }
  return 3.0;
}

function lane_bias_case($kind, $nl, $runs) {
  global $db;
  $db->exec('DELETE FROM RaceChart');
  $db->exec('DELETE FROM RaceInfo');
  $db->exec("INSERT INTO RaceInfo(itemkey, itemvalue) VALUES('lane_count', '$nl')");
  $stmt = $db->prepare('INSERT INTO RaceChart(roundid, heat, lane, racerid, finishtime, completed)'
                       .' VALUES(1, :heat, :lane, :racer, :t, :completed)');
  for ($j = 0; $j < $runs; ++$j) {
    for ($lane = 1; $lane <= $nl; ++$lane) {
      $stmt->execute(array(':heat' => $j + 1, ':lane' => $lane, ':racer' => $lane,
                           ':t' => lane_times($kind, $nl, $runs, $lane, $j),
                           ':completed' => '2026-01-01 00:00:00'));
    }
  }
  ob_start();
  list($bias, , $w, $e) = observe(function() { return lane_bias_analysis(false); });
  $echoed = ob_get_clean();
  $rec = array('result' => $bias, 'sentences' => history_sentences($bias));
  if ($echoed !== '') { $rec['echoed'] = $echoed; }
  if ($w) { $rec['warnings'] = $w; }
  if ($e !== null) { $rec['error'] = $e; }
  ob_start();
  list(, , $w2, $e2) = observe(function() { return lane_bias_analysis(true); });
  $rows = ob_get_clean();
  $rec['rows_html'] = $rows;
  if ($w2) { $rec['rows_warnings'] = $w2; }
  if ($e2 !== null) { $rec['rows_error'] = $e2; }
  return $rec;
}

function capture_lane_bias($outdir) {
  $history = file_get_contents('history.php');
  foreach (HISTORY_PHRASES as $phrase) {
    if (strpos($history, $phrase) === false) {
      fwrite(STDERR, "history.php no longer contains \"$phrase\"; update history_sentences() in capture-engine.php\n");
      exit(1);
    }
  }
  $cases = array();
  foreach (range(2, 8) as $nl) {
    foreach (array('even', 'biased', 'one-slow', 'identical', 'with-dnf') as $kind) {
      foreach (array(1, 2, 6, 30) as $runs) {
        $cases["lanes=$nl kind=$kind runs=$runs"] = lane_bias_case($kind, $nl, $runs);
      }
    }
  }
  write_json_lines("$outdir/engine/lane-bias.json", $cases);

  $f = array();
  $df2s = array_merge(range(0, 130), array(150, 500, 999, 1000, 9999998, 9999999, 10000000));
  foreach (range(0, 8) as $df1) {
    foreach ($df2s as $df2) {
      list($v, , $w, $e) = observe(function() use ($df1, $df2) { return critical_f_value($df1, $df2); });
      $rec = array('value' => $v);
      if ($w) { $rec['warnings'] = $w; }
      if ($e !== null) { $rec['error'] = $e; }
      $f["critical_f_value($df1, $df2)"] = $rec;
    }
  }
  write_json_lines("$outdir/engine/critical-f-value.json", $f);
  return array(count($cases), count($f));
}

// ---------------------------------------------------------------------------
// The sweep runs under the JIT for speed.  Recompute some cases without it and
// confirm the hashes agree.

function jit_cross_check($outdir, $max_cars) {
  $manifest = array();
  foreach (array(1, 2, 3, 4, 5, 6, 8) as $nl) {
    foreach (file("$outdir/engine/ordered/manifest-lanes-$nl.tsv", FILE_IGNORE_NEW_LINES) as $line) {
      list($id, , $hash) = explode("\t", $line);
      $manifest[$id] = $hash;
    }
  }
  $checked = 0;
  foreach (array_merge(LANE_COUNTS, array(1)) as $nl) {
    foreach (array(min($max_cars, 40), min($max_cars, 61)) as $nc) {
      if ($nc < $nl) { continue; }
      foreach (array(0, 1) as $m) {
        if ($nl == 1 && $m == 1) { continue; }
        foreach (array(1, 3, 6) as $nt) {
          $id = "ordered/$nl/$nc/$nt/$m";
          if (!isset($manifest[$id])) { continue; }
          $h = hash('sha256', cj(ordered_record($nl, $nc, $nt, $m)));
          if ($h !== $manifest[$id]) {
            fwrite(STDERR, "JIT and non-JIT disagree for $id\n");
            exit(1);
          }
          ++$checked;
        }
      }
    }
  }
  return $checked;
}

// ---------------------------------------------------------------------------
// Entry point

function main($argv) {
  $outdir = null;
  $workers = cpu_count();
  $jit = true;
  $max_cars = MAX_CARS;
  $worker = null;
  foreach (array_slice($argv, 1) as $a) {
    if (preg_match('/^--workers=(\d+)$/', $a, $m)) { $workers = (int) $m[1]; }
    else if ($a == '--no-jit') { $jit = false; }
    else if (preg_match('/^--max-cars=(\d+)$/', $a, $m)) { $max_cars = (int) $m[1]; }
    else if (preg_match('/^--worker=(\d+)$/', $a, $m)) { $worker = (int) $m[1]; }
    else if ($a[0] != '-') { $outdir = $a; }
    else { fwrite(STDERR, "unknown option $a\n"); exit(2); }
  }
  if ($outdir === null) {
    fwrite(STDERR, "usage: php capture-engine.php <outdir> [--workers=N] [--no-jit] [--max-cars=N]\n");
    exit(2);
  }
  if ($worker !== null) {
    worker_main($outdir, $worker, $max_cars);
    return;
  }

  $outdir = rtrim($outdir, '/');
  exec('rm -rf '.escapeshellarg("$outdir/engine").' '.escapeshellarg("$outdir/.work"));
  @mkdir($outdir, 0777, true);

  $t0 = microtime(true);
  run_workers($outdir, $workers, $jit, $max_cars);
  list($rows, $full) = collect_sweep($outdir);
  write_ordered_outputs($outdir, $rows, $full);
  exec('rm -rf '.escapeshellarg("$outdir/.work"));
  $checked = $jit ? jit_cross_check($outdir, $max_cars) : 0;

  $counts = array();
  $counts['ordered cases'] = count($rows);
  $counts['ordered full outputs'] = array_sum(array_map('count', $full));
  $counts['rotation cases'] = capture_rotation($outdir);
  $counts['out-of-range cases'] = capture_out_of_range($outdir);
  $counts['scheduler-data entries'] = capture_scheduler_data($outdir);
  $counts['unused-lane masks'] = capture_unused_lanes($outdir);
  $counts['ordinals cases'] = capture_ordinals($outdir);
  list($counts['lane-bias cases'], $counts['critical-f-value cases']) = capture_lane_bias($outdir);

  $lines = array();
  foreach ($counts as $k => $v) {
    $lines[] = "$k: $v";
  }
  write_file("$outdir/engine/summary.txt", implode("\n", $lines)."\n");
  fwrite(STDERR, implode("\n", $lines)."\n");
  fwrite(STDERR, "jit cross-check: $checked cases agree without the JIT\n");
  fwrite(STDERR, sprintf("engine capture took %.0fs\n", microtime(true) - $t0));
}

main($argv);
