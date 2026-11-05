// The replay page's ring of recent camera frames (#53), ported from
// website/js/circular-frame-buffer.js.
//
// Each animation frame draws the stream into an offscreen canvas and keeps
// its pixels, at most `fps` frames a second, in a ring that holds
// `length_ms` of video. Frame times are animation-frame timestamps, the
// clock of performance.now().
//
// PHP played the last `length_ms` before recording stopped. A clip is now
// a time window (D18): playback(canvas, {start, end, ...}) shows the frames
// recorded from `start` to `end`, so the page can keep recording for the
// post-roll after the finish and cut the clip's start to the maximum
// length. It returns what it plays: the first and last frame times and the
// frame count, which the page shows in its log and tests read.
'use strict';

// The highest capture rate, as PHP expected of a 60 Hz display.
var REPLAY_MAX_FPS = 60;

function CircularFrameBuffer(stream, length_ms, fps) {
  fps = Math.min(fps || 30, REPLAY_MAX_FPS);
  // A frame comes at most this often. Animation frames land on the
  // display's 16.7 ms ticks, so a little slack keeps 30 fps from dropping to
  // 20.
  var interval_ms = 1000 / fps * 0.9;
  var this_cfb = this;
  var now_msg = (new Date()).toTimeString().split(' ', 1)[0];

  var resizing_callback = false;
  this.on_resize = function (cb) { resizing_callback = cb; };

  // Takes effect for the next recording, not the current one.
  this.set_recording_length = function (ms) { length_ms = ms; };

  var offscreen_video = document.createElement('video');
  // A remote stream may have no size yet; the recording loop fixes it up.
  var stream_settings = stream.getVideoTracks()[0].getSettings();
  offscreen_video.width = stream_settings.width || 0;
  offscreen_video.height = stream_settings.height || 0;
  // Without this, iOS produces NotAllowedError at play().
  offscreen_video.playsInline = true;
  offscreen_video.muted = true;

  var offscreen_canvas = document.createElement('canvas');
  offscreen_canvas.width = offscreen_video.width;
  offscreen_canvas.height = offscreen_video.height;
  var offscreen_context = offscreen_canvas.getContext('2d', {willReadFrequently: true});

  var frames;
  var frame_times;
  // The index in frames[] of the NEXT frame to capture.
  var frame_index = 0;
  // The index of the most recent frame, or -1.
  var last_recorded_frame_index = -1;
  var recording = false;

  offscreen_video.srcObject = stream;
  offscreen_video.play().then(
    function () { console.log('Video play() started'); },
    function (reason) { console.log('Video element play() rejected:', reason); });

  var fps_div = document.getElementById('fps');
  var last_ts = 0;
  function report_fps(ts, play_or_rec) {
    var delta = ts - last_ts;
    last_ts = ts;
    if (delta != 0 && fps_div) {
      var shown = Math.round(1000 / delta);
      fps_div.style.backgroundColor = shown < 30 ? 'red' : (shown < REPLAY_MAX_FPS ? 'yellow' : 'white');
      fps_div.hidden = shown >= REPLAY_MAX_FPS;
      fps_div.textContent = play_or_rec + shown + ' fps';
    }
  }

  function fit_to_stream() {
    var tr = stream.getVideoTracks()[0];
    if (!tr) return;
    var settings = tr.getSettings();
    var did_resize = false;
    if (settings.width && offscreen_canvas.width != settings.width) {
      offscreen_video.width = offscreen_canvas.width = settings.width;
      did_resize = true;
    }
    if (settings.height && offscreen_canvas.height != settings.height) {
      offscreen_video.height = offscreen_canvas.height = settings.height;
      did_resize = true;
    }
    if (did_resize) {
      logstream('Stream resized to ' + settings.width + 'x' + settings.height);
      if (resizing_callback) resizing_callback(offscreen_video.width, offscreen_video.height);
    }
  }

  function recording_callback(ts) {
    report_fps(ts, 'rec ');
    if (!recording) return;

    if (last_recorded_frame_index < 0 || ts - frame_times[last_recorded_frame_index] >= interval_ms) {
      try {
        fit_to_stream();
        if (offscreen_canvas.width > 0 && offscreen_canvas.height > 0 &&
            offscreen_video.readyState >= offscreen_video.HAVE_CURRENT_DATA) {
          offscreen_context.drawImage(offscreen_video, 0, 0);
          frames[frame_index] = offscreen_context.getImageData(
            0, 0, offscreen_canvas.width, offscreen_canvas.height);
          frame_times[frame_index] = ts;
          last_recorded_frame_index = frame_index;
          frame_index = (frame_index + 1) % frames.length;
        }
      } catch (error) {
        console.log('CFB ' + now_msg + ' caught error ' + error.message +
                    ' at frame_index ' + frame_index);
      }
    }

    if (this_cfb === window.g_recorder) {
      requestAnimationFrame(recording_callback);
    } else {
      console.log('g_recorder has changed, so stopping the ' + now_msg + ' recorder.');
      this_cfb.stop_recording();
      offscreen_video.srcObject = null;
    }
  }

  this.start_recording = function () {
    var slots = Math.ceil(length_ms / interval_ms) + 2;
    frames = new Array(slots);
    frame_times = new Array(slots);
    frame_index = 0;
    last_recorded_frame_index = -1;
    if (!recording) {
      recording = true;
      requestAnimationFrame(recording_callback);
    }
  };

  this.stop_recording = function () {
    recording = false;
  };

  this.width = function () { return offscreen_video.width; };
  this.height = function () { return offscreen_video.height; };

  // The recorded frames from `start` to `end`, oldest first.
  function clip_frames(start, end) {
    var found = [];
    if (!frames || last_recorded_frame_index < 0) return found;
    for (var step = frames.length - 1; step >= 0; --step) {
      var i = (last_recorded_frame_index + frames.length - step) % frames.length;
      var t = frame_times[i];
      if (frames[i] && t >= start && t <= end) found.push({image: frames[i], time: t});
    }
    return found;
  }

  // Plays the frames recorded from options.start to options.end on
  // `canvas`, options.repeat times at options.rate percent of real time.
  // on_precanvas(canvas) is called once with the offscreen canvas each frame
  // is drawn on first, on_playback_finished after each showing, and on_done
  // at the end.
  this.playback = function (canvas, options) {
    var clip = clip_frames(options.start, options.end);
    var played = {
      frames: clip.length,
      first: clip.length ? clip[0].time : null,
      last: clip.length ? clip[clip.length - 1].time : null,
    };
    if (clip.length == 0) {
      console.log('No captured frames! (' + now_msg + ')');
      if (options.on_done) options.on_done();
      return played;
    }

    var context = canvas.getContext('2d');
    var pre_canvas = document.createElement('canvas');
    pre_canvas.width = clip[0].image.width;
    pre_canvas.height = clip[0].image.height;
    var pre_context = pre_canvas.getContext('2d');
    if (options.on_precanvas) options.on_precanvas(pre_canvas);

    var scale = Math.min(canvas.width / pre_canvas.width, canvas.height / pre_canvas.height);
    var draw_width = pre_canvas.width * scale;
    var draw_height = pre_canvas.height * scale;
    var draw_x = (canvas.width - draw_width) / 2;
    var draw_y = (canvas.height - draw_height) / 2;

    var rate = options.rate || 100;
    var repeat = options.repeat || 1;
    var rpt = 0;
    var index;
    var shown;
    var playback_start_time;

    function start_showing() {
      index = 0;
      shown = -1;
      playback_start_time = performance.now();
      requestAnimationFrame(playback_callback);
    }

    function playback_callback(ts) {
      report_fps(ts, 'play ');
      var goal = played.first + (ts - playback_start_time) * rate / 100;
      while (index < clip.length - 1 && clip[index + 1].time <= goal) ++index;
      if (goal <= played.last) {
        if (index != shown) {
          try {
            pre_context.putImageData(clip[index].image, 0, 0);
            context.drawImage(pre_canvas, draw_x, draw_y, draw_width, draw_height);
          } catch (e) {
            console.log('Playback draw failed', e);
          }
          shown = index;
        }
        requestAnimationFrame(playback_callback);
        return;
      }
      if (options.on_playback_finished) {
        try {
          options.on_playback_finished(rpt);
        } catch (e) {
          console.log('on_playback_finished failed', e);
        }
      }
      ++rpt;
      if (rpt < repeat) {
        start_showing();
      } else if (options.on_done) {
        options.on_done();
      }
    }

    start_showing();
    return played;
  };
}
