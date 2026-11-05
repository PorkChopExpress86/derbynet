// Records a stream with MediaRecorder (#53), ported unchanged in behavior
// from website/js/video-capture.js. The replay page records the first
// showing of a clip from its offscreen canvas and uploads it.
//
// VideoCapture(stream) starts recording; stop(cb) stops it and calls cb
// with the recorded Blob, or null when nothing was recorded.
'use strict';

function VideoCapture(stream) {
  var recorder = null;
  var recordedChunks = [];

  try {
    recorder = new MediaRecorder(stream, {});
  } catch (e) {
    console.log('Exception while creating MediaRecorder: ' + e);
    this.stop = function (cb) { if (cb) cb(null); };
    return;
  }

  recorder.ondataavailable = function (event) {
    if (event.data.size > 0) {
      recordedChunks.push(event.data);
    }
  };

  recorder.start();

  this.stop = function (cb) {
    if (recorder.state == 'recording') {
      if (cb) {
        recorder.onstop = function () {
          if (recordedChunks.length > 0) {
            var all = new Blob(recordedChunks, {type: recordedChunks[0].type});
            recordedChunks = [];
            cb(all);
          } else {
            cb(null);
          }
        };
      }
      recorder.stop();
    } else if (cb) {
      cb(null);
    }
  };
}
