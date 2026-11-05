// The remote replay camera (#53), ported from website/camera.php's script.
//
// A phone opens this page and its camera becomes `camera-replay`, which the
// replay page can choose as its Remote Camera. Each viewer that solicits it
// says the size it wants, and the camera reopens at the largest size asked
// as ideal values (camera.php:23-55). The chosen camera is remembered in
// this browser (D9).
'use strict';

var g_replay_config = JSON.parse(document.getElementById('replay-config').textContent);
// Empty in v1, so the page polls; #56 decides the push channel.
var g_websocket_url = g_replay_config.websocket_url;
var CAMERA_DEVICE_KEY = 'derbynet.camera.device';

function camera_el(id) { return document.getElementById(id); }

var g_client_manager = null;

function on_device_selection() {
  var preview = camera_el('preview');
  stop_stream(preview);
  var device_id = camera_el('device-picker').value;
  if (!device_id) return;
  var label = camera_el('device-picker').selectedOptions[0].textContent;
  save_camera_choice(CAMERA_DEVICE_KEY, {deviceId: device_id, label: label});
  var video = {deviceId: {exact: device_id}};
  var ideal = g_client_manager.ideal();
  if (ideal.width) video.width = {ideal: ideal.width};
  if (ideal.height) video.height = {ideal: ideal.height};
  if (ideal.frameRate) video.frameRate = {ideal: ideal.frameRate};
  navigator.mediaDevices.getUserMedia({video: video, audio: false}).then(function (stream) {
    preview.srcObject = stream;
    g_client_manager.setstream(stream);
    var settings = stream.getVideoTracks()[0].getSettings();
    logstream('Video stream is ' + settings.width + 'x' + settings.height);
    camera_el('stream-size').textContent = settings.width + ' × ' + settings.height;
    return build_device_picker(false);
  }, function (error) {
    logstream('The camera did not start: ' + error.name);
    camera_el('camera-error').hidden = false;
    camera_el('camera-error').textContent = 'The camera did not start (' + error.name + ').';
  });
}

// Lists the cameras, keeping the current or remembered one chosen. With
// `open`, opens the chosen one.
function build_device_picker(open) {
  var picker = camera_el('device-picker');
  return granted_video_devices().then(function (devices) {
    var current = picker.value || find_device(devices, load_camera_choice(CAMERA_DEVICE_KEY));
    fill_select(picker, devices.map(function (d) { return [d.deviceId, d.label]; }), current);
    if (open) on_device_selection();
  });
}

document.addEventListener('DOMContentLoaded', function () {
  var preview_switch = camera_el('show-preview');
  preview_switch.addEventListener('change', function () {
    camera_el('preview').hidden = !preview_switch.checked;
  });

  if (!navigator.mediaDevices) {
    show_no_camera_warning(camera_el('no-camera-warning'));
    return;
  }
  // A new viewer may want a bigger stream.
  g_client_manager = new ViewClientManager(on_device_selection);
  camera_el('device-picker').addEventListener('change', on_device_selection);
  navigator.mediaDevices.ondevicechange = function () { build_device_picker(true); };
  build_device_picker(true);
});
