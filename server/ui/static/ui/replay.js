// The replay page (#53), ported from website/replay.php's script.
//
// The page records the camera into a ring of frames, shows a kiosk page in
// a full-window frame, and replays each heat when the server says it
// finished. It polls `replay.message` every 250 ms (replay.php:42-61) and
// handles the six commands of racing/services/replay.py:
//   HELLO; TEST, REPLAY and RACE_STARTS `<skipback> <showings> <rate>`;
//   START `<class>_Round<n>_Heat<NN>`, which names the next clip; CANCEL.
// RACE_STARTS replays `skipback` ms after the gate opens unless a REPLAY
// comes first, and a REPLAY after such a timeout replay is ignored until the
// next START (replay.php:111-158).
//
// The clip window (D18): when the finish arrives, the page keeps recording
// for the post-roll, then plays from `skipback` before the finish to the
// post-roll after it, its start cut so it never runs longer than the
// maximum. PHP stopped recording at the finish and played `skipback` ms.
// Playback starts the operator's delay after the finish, once the post-roll
// is recorded. A finish that arrives while a clip is still recording,
// waiting or playing is ignored, where PHP started a second playback over
// the first.
//
// Playback posts `replay-started` and `replay-ended` to the kiosk frame,
// which pauses the kiosk page meanwhile (ui/static/ui/kiosk.js). With
// `upload-videos` set and MediaRecorder present, the first showing is
// recorded and uploaded as `<START name>.mkv` through `video.upload`.
'use strict';

var g_replay_config = JSON.parse(document.getElementById('replay-config').textContent);
// Empty in v1, so the page polls; #56 decides the push channel.
var g_websocket_url = g_replay_config.websocket_url;
var g_upload_videos = g_replay_config.upload_videos;
var g_video_name_root = '';
// After a timeout replay that RACE_STARTS set, REPLAY is ignored until the
// next START.
var g_preempted = false;
var g_remote_poller = null;
var g_recorder = null;
var g_replay_timeout = 0;
var g_replay_options = {
  count: g_replay_config.showings,
  rate: g_replay_config.rate,
  length: g_replay_config.skipback_ms,
};
// What the page did with the last clip, for its log and the browser tests.
var g_last_clip = null;

var CAMERA_CHOICE_KEY = 'derbynet.replay.camera';
var REMOTE = 'remote';

function replay_el(id) { return document.getElementById(id); }

function set_replay_state(state) {
  replay_el('replay').dataset.replayState = state;
}

// The clip's length (D18): skipback plus post-roll, at most the maximum.
function clip_length_ms() {
  return Math.max(0, Math.min(g_replay_options.length + g_replay_config.post_roll_ms,
                              g_replay_config.max_clip_ms));
}

// ----- replay commands -----------------------------------------------------

var g_poll_in_flight = false;

function poll_once_for_replay() {
  if (g_poll_in_flight) return;
  g_poll_in_flight = true;
  post_action({action: 'replay.message', status: 0, 'finished-replay': 0})
    .then(function (data) {
      (data.replay || []).forEach(handle_replay_message);
    })
    .catch(function (e) { console.log('replay.message failed', e); })
    .finally(function () { g_poll_in_flight = false; });
}

function listen_for_replay_messages() {
  if (g_websocket_url) {
    new MessagePoller(make_id_string('replay-'),
                      function (msg) { handle_replay_message(msg.cmd); },
                      ['replay-commands']);
    // Poll every 5 s too, so the server still counts the page as connected.
    setInterval(poll_once_for_replay, 5000);
  } else {
    setInterval(poll_once_for_replay, 250);
  }
}

function parse_replay_options(cmdline) {
  var fields = cmdline.split(' ');
  g_replay_options.length = parseInt(fields[1]);
  g_replay_options.count = parseInt(fields[2]);
  g_replay_options.rate = parseInt(fields[3]);
  if (g_recorder) g_recorder.set_recording_length(clip_length_ms());
}

function handle_replay_message(cmdline) {
  logrcvd(cmdline);
  if (cmdline.startsWith('HELLO')) {
    // Nothing to do.
  } else if (cmdline.startsWith('TEST')) {
    parse_replay_options(cmdline);
    on_replay();
  } else if (cmdline.startsWith('START')) {
    g_video_name_root = cmdline.substr(6).trim();
    g_preempted = false;
  } else if (cmdline.startsWith('REPLAY')) {
    parse_replay_options(cmdline);
    if (!g_preempted) {
      clearTimeout(g_replay_timeout);
      on_replay();
    }
  } else if (cmdline.startsWith('CANCEL')) {
    clearTimeout(g_replay_timeout);
  } else if (cmdline.startsWith('RACE_STARTS')) {
    parse_replay_options(cmdline);
    clearTimeout(g_replay_timeout);
    g_replay_timeout = setTimeout(function () {
      g_preempted = true;
      logstream('Replay after the RACE_STARTS timeout');
      on_replay();
    }, g_replay_options.length);
  } else {
    console.log('Unrecognized replay message: ' + cmdline);
  }
}

// ----- the clip --------------------------------------------------------------

function on_replay() {
  clearTimeout(g_replay_timeout);
  g_replay_timeout = 0;
  if (!g_recorder) {
    logstream('No camera, so no replay');
    return;
  }
  var state = replay_el('replay').dataset.replayState;
  if (state == 'post-roll' || state == 'waiting' || state == 'playing') {
    logstream('A clip is already under way; this finish is ignored');
    return;
  }
  // Read now: a START may come in before the clip plays.
  var root = g_video_name_root;
  var upload = g_upload_videos;
  var finish = performance.now();
  var post_roll = g_replay_config.post_roll_ms;
  var delay = Math.max(0, (replay_el('delay').valueAsNumber || 0) * 1000);
  var clip = {
    root: root,
    finish: finish,
    end: finish + post_roll,
    start: finish + post_roll - clip_length_ms(),
  };
  g_last_clip = clip;
  set_replay_state('post-roll');
  setTimeout(function () {
    g_recorder.stop_recording();
    set_replay_state('waiting');
    setTimeout(function () { start_playback(clip, upload); },
               Math.max(0, delay - post_roll));
  }, post_roll);
}

function announce_to_interior(msg) {
  replay_el('interior').contentWindow.postMessage(msg, '*');
}

function upload_video(root, blob) {
  if (!blob) return;
  logsent('Uploading video ' + root + '.mkv');
  var form = new FormData();
  form.append('video', blob, root + '.mkv');
  form.append('action', 'video.upload');
  post_action(form).then(function (data) {
    console.log('video upload', data.outcome);
  }, function (e) { console.log('video upload failed', e); });
}

function start_playback(clip, upload) {
  var playback = replay_el('playback');
  playback.width = window.innerWidth;
  playback.height = window.innerHeight;

  set_replay_state('playing');
  announce_to_interior('replay-started');
  replay_el('playback-background').hidden = false;

  var vc = null;
  var played = g_recorder.playback(playback, {
    start: clip.start,
    end: clip.end,
    repeat: g_replay_options.count,
    rate: g_replay_options.rate,
    on_precanvas: function (pre_canvas) {
      if (upload && clip.root != '') {
        vc = new VideoCapture(pre_canvas.captureStream());
      } else if (!upload) {
        console.log('No video upload planned.');
      } else {
        console.log('No video root name specified; will not upload.');
      }
    },
    on_playback_finished: function () {
      // The first showing is the upload.
      if (vc) {
        vc.stop(function (blob) { upload_video(clip.root, blob); });
        vc = null;
      }
    },
    on_done: function () {
      replay_el('playback-background').hidden = true;
      announce_to_interior('replay-ended');
      g_recorder.start_recording();
      set_replay_state(replay_el('replay-setup').hidden ? 'watching' : 'setup');
    },
  });
  clip.first = played.first;
  clip.last = played.last;
  clip.frames = played.frames;
  logstream('Clip ' + played.frames + ' frames, ' +
            (played.frames ? Math.round(played.last - played.first) : 0) + ' ms');
}

// ----- the camera ------------------------------------------------------------

var g_choice = load_camera_choice(CAMERA_CHOICE_KEY);

function on_stream_ready(stream, fps) {
  replay_el('waiting-for-remote').hidden = true;
  g_recorder = new CircularFrameBuffer(stream, clip_length_ms(), fps);
  g_recorder.start_recording();
  replay_el('preview').srcObject = stream;
}

function show_stream_info(text) {
  var info = replay_el('recording-stream-info');
  info.hidden = !text;
  replay_el('recording-stream-size').textContent = text || '';
}

function camera_error(text) {
  var p = replay_el('camera-error');
  p.hidden = !text;
  p.textContent = text || '';
}

function chosen_size() {
  var parts = (replay_el('resolution-picker').value || '').split('x');
  return {width: parseInt(parts[0]) || 0, height: parseInt(parts[1]) || 0,
          frameRate: parseInt(replay_el('frame-rate-picker').value) || 0};
}

function fill_constraint_pickers(capabilities, settings) {
  var size = [settings.width || 0, settings.height || 0];
  fill_select(replay_el('resolution-picker'),
              resolution_choices(capabilities, size).map(function (r) {
                return [r[0] + 'x' + r[1], r[0] + ' × ' + r[1]];
              }),
              size[0] + 'x' + size[1]);
  var rate = Math.round(settings.frameRate || 0);
  fill_select(replay_el('frame-rate-picker'),
              frame_rate_choices(capabilities, rate).map(function (f) { return [f, f + ' fps']; }),
              rate);
}

// Each camera opened counts up, so a camera that opens after the operator
// has already chosen another is closed again, not shown.
var g_open_count = 0;
var g_camera_request = Promise.resolve();

function close_camera() {
  ++g_open_count;
  show_stream_info('');
  stop_stream(replay_el('preview'));
  if (g_remote_poller) {
    g_remote_poller.close();
    g_remote_poller = null;
  }
  g_recorder = null;
}

function open_remote_camera() {
  replay_el('waiting-for-remote').hidden = false;
  show_stream_info('');
  // No capabilities to read from a camera not yet connected: offer every
  // size, which the camera page asks of its own camera as ideal values.
  fill_select(replay_el('resolution-picker'),
              REPLAY_RESOLUTIONS.map(function (r) { return [r[0] + 'x' + r[1], r[0] + ' × ' + r[1]]; }),
              (g_choice.width || 1280) + 'x' + (g_choice.height || 720));
  fill_select(replay_el('frame-rate-picker'),
              REPLAY_FRAME_RATES.map(function (f) { return [f, f + ' fps']; }),
              g_choice.frameRate || 30);
  var ideal = chosen_size();
  save_camera_choice(CAMERA_CHOICE_KEY, g_choice = {deviceId: REMOTE, width: ideal.width,
                                                    height: ideal.height, frameRate: ideal.frameRate});
  var id = make_id_string('viewer-');
  logstream('Viewer id is ' + id);
  g_remote_poller = new RemoteCamera(id, ideal, function (stream) {
    on_stream_ready(stream, ideal.frameRate);
    // -1x-1 says a stream came before its first frame, as replay.php's does.
    var size = stream.getVideoTracks()[0].getSettings();
    show_stream_info(size.width ? size.width + 'x' + size.height : '-1x-1');
    g_recorder.on_resize(function (w, h) { show_stream_info(w + 'x' + h); });
  });
}

// Opens the chosen camera with exact constraints. A camera that cannot
// give the saved size or rate says so and opens at its own.
function open_local_camera(device_id, exact) {
  var opening = g_open_count;
  var video = {deviceId: {exact: device_id}};
  if (exact && g_choice.width && g_choice.height) {
    video.width = {exact: g_choice.width};
    video.height = {exact: g_choice.height};
  }
  if (exact && g_choice.frameRate) {
    video.frameRate = {exact: g_choice.frameRate};
  }
  // One camera opens at a time: a camera still opening at its old size
  // makes a second request for a new size fail as overconstrained.
  var request = g_camera_request.then(function () {
    if (opening != g_open_count) return null;
    return navigator.mediaDevices.getUserMedia({video: video, audio: false});
  });
  var handled = request.then(function (stream) {
    if (!stream) return;
    if (opening != g_open_count) {
      stream.getTracks().forEach(function (track) { track.stop(); });
      return;
    }
    var track = stream.getVideoTracks()[0];
    var settings = track.getSettings();
    var capabilities = track.getCapabilities ? track.getCapabilities() : {};
    fill_constraint_pickers(capabilities, settings);
    g_choice = {deviceId: device_id, label: track.label, width: settings.width, height: settings.height,
                frameRate: Math.round(settings.frameRate || 0)};
    save_camera_choice(CAMERA_CHOICE_KEY, g_choice);
    on_stream_ready(stream, g_choice.frameRate);
    show_stream_info(settings.width + 'x' + settings.height + ', ' + g_choice.frameRate + ' fps');
    // Labels show once the browser has granted a camera.
    return refresh_device_picker();
  }, function (error) {
    if (opening != g_open_count) return;
    if (exact) {
      camera_error('This camera cannot record at ' + g_choice.width + ' × ' + g_choice.height +
                   ', ' + g_choice.frameRate + ' fps (' + error.name + '). Choose another size.');
      // Not returned: the fallback waits for this request to settle.
      open_local_camera(device_id, false);
      return;
    }
    camera_error('The camera did not start (' + error.name + ').');
  });
  // The stream of a stale request is stopped before the next one opens.
  g_camera_request = handled.catch(function () {});
  return handled;
}

function on_device_selection() {
  close_camera();
  camera_error('');
  var device_id = replay_el('device-picker').value;
  if (device_id == REMOTE) {
    open_remote_camera();
  } else if (device_id) {
    if (device_id != g_choice.deviceId) g_choice = {deviceId: device_id};
    open_local_camera(device_id, true);
  }
}

function on_constraint_selection() {
  var size = chosen_size();
  g_choice = {deviceId: replay_el('device-picker').value, width: size.width,
              height: size.height, frameRate: size.frameRate};
  save_camera_choice(CAMERA_CHOICE_KEY, g_choice);
  on_device_selection();
}

function refresh_device_picker() {
  var picker = replay_el('device-picker');
  return video_devices().then(function (devices) {
    var choices = devices.map(function (d) { return [d.deviceId, d.label]; });
    choices.push([REMOTE, 'Remote Camera']);
    var current = picker.value || g_choice.deviceId;
    fill_select(picker, choices, current);
    return devices;
  });
}

function build_device_picker() {
  var ready = navigator.mediaDevices ? granted_video_devices() : Promise.resolve([]);
  return ready.then(refresh_device_picker).then(function (devices) {
    var picker = replay_el('device-picker');
    var known = find_device(devices, g_choice);
    if (g_choice.deviceId == REMOTE) {
      picker.value = REMOTE;
    } else if (known) {
      g_choice.deviceId = picker.value = known;
    } else {
      picker.value = devices.length ? devices[0].deviceId : REMOTE;
    }
    on_device_selection();
  });
}

// ----- setup and running -----------------------------------------------------

function on_resize() { on_setup(); }

function on_proceed() {
  replay_el('interior').src = replay_el('inner_url').value;
  if (replay_el('go-fullscreen').checked && window.screenfull && screenfull.enabled) {
    // Going full screen resizes the window; that resize is not the
    // operator's, so it does not bring the setup back.
    window.removeEventListener('resize', on_resize);
    screenfull.request();
    setTimeout(function () { window.addEventListener('resize', on_resize); }, 2000);
  }
  replay_el('replay-setup').hidden = true;
  replay_el('interior').hidden = false;
  replay_el('click-shield').hidden = false;
  document.documentElement.classList.add('replay-running');
  if (replay_el('replay').dataset.replayState == 'setup') set_replay_state('watching');
}

function on_setup() {
  replay_el('click-shield').hidden = true;
  replay_el('interior').hidden = true;
  replay_el('replay-setup').hidden = false;
  document.documentElement.classList.remove('replay-running');
  if (replay_el('replay').dataset.replayState == 'watching') set_replay_state('setup');
}

document.addEventListener('DOMContentLoaded', function () {
  listen_for_replay_messages();
  window.addEventListener('resize', on_resize);

  if (typeof window.MediaRecorder == 'undefined') {
    g_upload_videos = false;
    replay_el('user_agent').textContent = navigator.userAgent;
    replay_el('recorder-warning').hidden = false;
  }

  replay_el('device-picker').addEventListener('change', on_device_selection);
  replay_el('resolution-picker').addEventListener('change', on_constraint_selection);
  replay_el('frame-rate-picker').addEventListener('change', on_constraint_selection);
  replay_el('proceed').addEventListener('click', on_proceed);

  var shield = replay_el('click-shield');
  shield.addEventListener('click', function (e) { e.preventDefault(); on_setup(); });
  // A right-click replays the last few seconds now.
  shield.addEventListener('contextmenu', function (e) { e.preventDefault(); on_replay(); });

  if (!navigator.mediaDevices) {
    show_no_camera_warning(replay_el('no-camera-warning'));
  } else {
    // A camera unplugged mid-race brings the setup back, as replay.php's
    // ondevicechange does through build_device_picker's not-found callback.
    navigator.mediaDevices.ondevicechange = function () {
      var before = replay_el('device-picker').value;
      refresh_device_picker().then(function () {
        if (replay_el('device-picker').value != before) {
          on_setup();
          on_device_selection();
        }
      });
    };
  }
  build_device_picker();
});
