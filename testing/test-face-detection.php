<?php

$php_file = $argv[1];
$directory = $argv[2];  // e.g., 'data/headshots';

require_once($php_file);

$start = microtime(true);
$detector = new FaceDetector();

// The detector gives up after 6 seconds, which a slow machine reaches on some
// headshots.  Allow more here, so a result doesn't depend on the machine's speed.
$time_limit = new ReflectionProperty('FaceDetector', 'max_time_allowed_sec');
$time_limit->setValue($detector, (int) (getenv('FACE_DETECTION_TIME_LIMIT') ?: 60));

$files = array();

$dh = @opendir($directory);
while (($filename = readdir($dh)) !== false) {
  if (preg_match('/jpg/i', $filename) && is_file($directory.DIRECTORY_SEPARATOR.$filename)) {
    $files[] = $directory.DIRECTORY_SEPARATOR.$filename;
  }
}
closedir($dh);

// This placeholder is "No Image Available" text, not a headshot, so the
// detector must find no face in it.  The detector also searches these two
// headshots to the end without finding the face (about 16 seconds each).  The
// Django port does its own detection (#35), so they record PHP's result.
$expect_no_face = array('no-photo.jpg', 'Cub-1612.jpg', 'head-A.jpg');

echo count($files)." photos to scan.\n";
$fail_count = 0;
foreach ($files as $file) {
  $image = imagecreatefromjpeg($file);
  $detection_start_time = microtime(true);
  $box = $detector->detect_face($image);
  // detect_face reports a miss on a downscaled image as a zero-size box, not
  // false (#74).  Autocrop skips a zero-size box, so it counts as no face.
  $found = $box && $box['width'] > 0;
  $detection_end_time = microtime(true);
  imagedestroy($image);
  $elapsed = $detection_end_time - $detection_start_time;
  if (in_array(basename($file), $expect_no_face)) {
    if ($found) {
      ++$fail_count;
      echo "** Unexpected face in ".$file." in ".$elapsed."s\n";
    } else {
      echo "   No face, as expected, in ".$file." in ".$elapsed."s\n";
    }
  } else if ($found) {
    echo "   Identified ".$file." in ".$elapsed."s\n";
  } else {
    ++$fail_count;
    echo "** Failed for ".$file." in ".$elapsed."s\n";
  }
}
$end_time = microtime(true);
echo "Net: $fail_count failed out of ".count($files)."\n";
echo "Total time ".($end_time - $start)."s\n";
exit($fail_count > 0 ? 1 : 0);

?>