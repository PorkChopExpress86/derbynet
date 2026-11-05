// The replay page's side of the remote camera (#53), ported from
// website/js/viewer-signaling.js. The viewer solicits the camera
// `camera-replay` with the size it wants, answers the camera's offer, and
// hands the received track to `stream_cb`.
//
// The ICE servers come from the `replay_ice_servers` setting
// (`g_replay_config.ice_servers`); PHP hardcodes Google's STUN server, and
// an empty setting means host candidates only, for a LAN with no internet.
'use strict';

function replay_rtc_configuration() {
  var config = window.g_replay_config || {};
  return {iceServers: config.ice_servers || []};
}

// viewer_id identifies this viewer. ideal is the size the viewer wants:
// width, height and, from the setup screen, frameRate.
function RemoteCamera(viewer_id, ideal, stream_cb) {
  var pc = new RTCPeerConnection(replay_rtc_configuration());

  // Messages are handled one at a time: an offer's answer is set before the
  // next offer is read, as two offers can come in one poll's reply.
  var handled = Promise.resolve();
  var poller = new MessagePoller(viewer_id, function (msg) {
    handled = handled.then(function () { return on_message(msg); })
      .catch(function (e) { logstream('Signaling failed: ' + e); });
  });

  function on_message(msg) {
      if (msg.type == 'offer') {
        logrcvd('offer');
        if (!pc) {
          pc = new RTCPeerConnection(replay_rtc_configuration());
        }
        pc.onnegotiationneeded = function () {
          logstream('NEGOTIATION NEEDED');
        };
        pc.onicecandidate = function (event) {
          if (event.candidate) {
            logsent('ice-candidate');
            poller.send_message({recipient: 'camera-replay',
                                 type: 'ice-candidate',
                                 from: viewer_id,
                                 candidate: event.candidate.toJSON()});
          }
        };
        pc.ontrack = function (event) {
          var wh = event.track.getSettings();
          logstream('ontrack with ' + wh.width + 'x' + wh.height +
                    ' (' + event.streams.length + ' streams)');
          var s = new MediaStream();
          s.addTrack(event.track);
          stream_cb(s);
        };

        return pc.setRemoteDescription(new RTCSessionDescription(msg.sdp))
          .then(function () { return pc.createAnswer(); })
          .then(function (answer) { return pc.setLocalDescription(answer); })
          .then(function () {
            logsent('answer');
            poller.send_message({recipient: 'camera-replay',
                                 type: 'answer',
                                 from: viewer_id,
                                 sdp: pc.localDescription.toJSON()});
          });
      } else if (msg.type == 'ice-candidate') {
        if (!pc) {
          pc = new RTCPeerConnection(replay_rtc_configuration());
          logstream('UNEXPECTED ICE CANDIDATE');
        }
        if (msg.from != 'camera-replay') {
          console.log('ICE candidate from unknown sender ' + msg.from);
          return;
        }
        logrcvd('ice-candidate');
        return pc.addIceCandidate(new RTCIceCandidate(msg.candidate));
      } else if (msg.type == 'solicitation') {
        // A broadcast solicitation, meant for the camera.
      } else {
        console.error('Unrecognized message ', msg);
      }
  }

  var pcstate = '';
  var nag = setInterval(function () {
    if (pc) {
      if (pc.connectionState != pcstate) {
        logstream('State ' + pc.connectionState);
        pcstate = pc.connectionState;
      }
      if (pc.connectionState == 'disconnected') {
        pc.close();
        pc = null;
      }
    }

    if (pc == null || pc.connectionState != 'connected') {
      logsent('solicitation (nag) ' + (pc == null ? 'disconnected' : pc.connectionState) +
              ' ' + ideal.width + 'x' + ideal.height);
      poller.send_message({recipient: 'camera-replay', type: 'solicitation',
                           from: viewer_id, ideal: ideal});
    }
  }, 5000);

  logsent('solicitation ' + ideal.width + 'x' + ideal.height);
  poller.send_message({recipient: 'camera-replay', type: 'solicitation',
                       from: viewer_id, ideal: ideal});

  this.close = function () {
    logstream('------------ closed -----------------');
    if (poller) poller.close();
    poller = null;
    clearInterval(nag);
    if (pc) pc.close();
    pc = null;
  };
}
