// Camera choice for the replay page and the remote camera (#53), from
// website/js/video-device-picker.js.
//
// PHP's picker listed the cameras and opened the chosen one at the window's
// size (replay.php:221-225), and nothing was remembered. The replay setup
// now picks the camera, the resolution and the frame rate explicitly, asks
// for them as exact constraints, and remembers them in this browser's
// localStorage (D9). The remote camera remembers its camera the same way.
'use strict';

var REPLAY_RESOLUTIONS = [[640, 480], [1280, 720], [1920, 1080], [2560, 1440], [3840, 2160]];
var REPLAY_FRAME_RATES = [15, 20, 24, 25, 30, 50, 60];

// The choice saved under `key`, or {}. Storage can be blocked, as in a
// private window; the page then starts from the defaults each time.
function load_camera_choice(key) {
  try {
    var saved = JSON.parse(window.localStorage.getItem(key) || '{}');
    return saved && typeof saved === 'object' ? saved : {};
  } catch (e) {
    return {};
  }
}

function save_camera_choice(key, choice) {
  try {
    window.localStorage.setItem(key, JSON.stringify(choice));
  } catch (e) {
    console.log('Cannot remember the camera choice', e);
  }
}

// The video inputs, as [{deviceId, label}]. Labels are empty until the page
// has opened a camera once.
function video_devices() {
  if (!navigator.mediaDevices) return Promise.resolve([]);
  return navigator.mediaDevices.enumerateDevices().then(function (devices) {
    return devices
      .filter(function (d) { return d.kind == 'videoinput'; })
      .map(function (d, i) {
        return {deviceId: d.deviceId, label: d.label || 'Camera ' + (i + 1), named: Boolean(d.label)};
      });
  });
}

// The video inputs once the browser has granted a camera: before then they
// have no labels and no ids, so this opens a camera once to ask.
function granted_video_devices() {
  return video_devices().then(function (devices) {
    if (!devices.length || devices.some(function (d) { return d.named; })) return devices;
    return navigator.mediaDevices.getUserMedia({video: true, audio: false}).then(function (stream) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      return video_devices();
    }, function () { return devices; });
  });
}

// The id of the remembered camera among `devices`, by its id or, since a
// browser may give a camera a new id in a new session, by its label.
function find_device(devices, choice) {
  var found = devices.filter(function (d) { return d.deviceId == choice.deviceId; })[0] ||
    devices.filter(function (d) { return d.named && d.label == choice.label; })[0];
  return found ? found.deviceId : null;
}

function _within(range, value) {
  return !range || ((range.min === undefined || value >= range.min) &&
                    (range.max === undefined || value <= range.max));
}

// The resolutions a track with these capabilities offers, as [w, h] pairs,
// with the track's own size among them.
function resolution_choices(capabilities, current) {
  capabilities = capabilities || {};
  var found = REPLAY_RESOLUTIONS.filter(function (r) {
    return _within(capabilities.width, r[0]) && _within(capabilities.height, r[1]);
  });
  if (current && current[0] && !found.some(function (r) { return r[0] == current[0] && r[1] == current[1]; })) {
    found.push(current);
    found.sort(function (a, b) { return a[0] * a[1] - b[0] * b[1]; });
  }
  return found;
}

// The frame rates a track with these capabilities offers, with its highest
// rate and its own rate among them.
function frame_rate_choices(capabilities, current) {
  var range = (capabilities || {}).frameRate;
  var found = REPLAY_FRAME_RATES.filter(function (f) { return _within(range, f); });
  [range && range.max, current].forEach(function (f) {
    f = Math.round(f || 0);
    if (f > 0 && f <= REPLAY_FRAME_RATES[REPLAY_FRAME_RATES.length - 1] && found.indexOf(f) < 0) found.push(f);
  });
  return found.sort(function (a, b) { return a - b; });
}

// Replaces `select`'s options with `choices`, each [value, text], keeping
// `selected` chosen when it is among them, else the first.
function fill_select(select, choices, selected) {
  select.textContent = '';
  choices.forEach(function (c) {
    var option = document.createElement('option');
    option.value = c[0];
    option.textContent = c[1];
    if (String(c[0]) === String(selected)) option.selected = true;
    select.appendChild(option);
  });
}

function stop_stream(video) {
  var stream = video.srcObject;
  if (stream) {
    stream.getTracks().forEach(function (track) { track.stop(); });
  }
  video.srcObject = null;
}

// The no-camera warning: the browser gives no camera to a page that is not
// a secure context, so plain http:// links to the https:// address, as
// replay.php:244-247 and camera.php:65-68 do.
function show_no_camera_warning(warning) {
  warning.hidden = false;
  if (window.location.protocol == 'http:') {
    var https_url = 'https://' + window.location.hostname + window.location.pathname;
    var p = document.createElement('p');
    p.append('You may need to switch to ');
    var a = document.createElement('a');
    a.href = https_url;
    a.textContent = https_url;
    a.className = 'font-medium text-accent underline underline-offset-2';
    p.appendChild(a);
    warning.appendChild(p);
  }
}
