<?php
// Writes php_encoding.json: what PHP's json_encode() and is_numeric() give for
// the inputs below.  The encoder tests compare legacy_api/encoding.py with it.
// Regenerate with the command in legacy_api/tests/test_encoding.py.

$flags = JSON_PRETTY_PRINT | JSON_NUMERIC_CHECK;

$strings = [
  '007', ' 5', '5 ', '+5', '-0', '.5', '5.', '1.50', '1e3', '1E3', '0x1A',
  'NaN', 'INF', '9223372036854775807', '9223372036854775808',
  '-9223372036854775808', '-9223372036854775809', '12345678901234567890',
  '', ' ', '1e', '1e+', '1.5e3', '.5e1', '5.e1', '+.5', '-.5e-3', "\t5\n",
  "\v5\f", '1e25', '1e400', '0.00001', '0.0001', '1e16', '1e17', '3.0',
  '2.50E+2', '00', '0000.0', '-0.0', '123.456e-7', 'abc', '5 5', '- 5', '0b1',
  '1_000', '٣', '.', '+', '.e1', '0.1', '1e-4',
];

$floats = [
  '1e25', '0.1', '0.3', '1e-5', '1e-4', '0.0001', '2.5e-4', '1e16', '1e17',
  '-0.0', '100.0', '1.5', '123456789.125', '5e-324', '1.7976931348623157e308',
  '-1234.5', '0.5',
];

$values = [
  '{"007":"x"}', '{"5":"x"}', '[]', '{}', '{"a":[],"b":{}}',
  '{"s":"a/b","u":"é😀","c":"\u0001\b\f\n\r\t\"\\\\\u007f"}',
  '[1,"2",[3,{"k":"4"}],true,false,null]', '{"n":-0.0,"x":1.0}',
];

$cases = [];
foreach ($strings as $s) {
  $cases[] = ['kind' => 'string', 'input' => $s,
              'is_numeric' => is_numeric($s), 'json' => json_encode($s, $flags)];
}
foreach ($floats as $s) {
  $cases[] = ['kind' => 'float', 'input' => $s, 'json' => json_encode((float) $s, $flags)];
}
foreach ($values as $s) {
  $cases[] = ['kind' => 'value', 'input' => $s, 'json' => json_encode(json_decode($s), $flags)];
}

echo json_encode(['php' => PHP_MAJOR_VERSION.'.'.PHP_MINOR_VERSION,
                  'serialize_precision' => ini_get('serialize_precision'),
                  'cases' => $cases],
                 JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE), "\n";
