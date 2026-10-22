<?php

$php_file = $argv[1];
$directory = $argv[2];  // e.g., 'data/headshots';

require_once($php_file);

$start = microtime(true);
$detector = new FaceDetector();

$files = array();

$dh = @opendir($directory);
while (($filename = readdir($dh)) !== false) {
  if (preg_match('/jpg/i', $filename) && is_file($directory.DIRECTORY_SEPARATOR.$filename)) {
    $files[] = $directory.DIRECTORY_SEPARATOR.$filename;
  }
}
closedir($dh);

// This placeholder is "No Image Available" text, not a headshot, so the
// detector must find no face in it.
$expect_no_face = array('no-photo.jpg');

echo count($files)." photos to scan.\n";
$fail_count = 0;
foreach ($files as $file) {
  $image = imagecreatefromjpeg($file);
  $detection_start_time = microtime(true);
  $box = $detector->detect_face($image);
  $detection_end_time = microtime(true);
  imagedestroy($image);
  $elapsed = $detection_end_time - $detection_start_time;
  if (in_array(basename($file), $expect_no_face)) {
    if ($box) {
      ++$fail_count;
      echo "** Unexpected face in ".$file." in ".$elapsed."s\n";
    } else {
      echo "   No face, as expected, in ".$file." in ".$elapsed."s\n";
    }
  } else if ($box) {
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