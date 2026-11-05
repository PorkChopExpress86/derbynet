// The remote camera's side of the WebRTC signaling (#53), ported from
// website/js/camera-signaling.js. The camera is the recipient
// `camera-replay`; each viewer that solicits it gets a ViewClient, which
// offers the camera's stream to that viewer.
//
// Create one ViewClientManager and call its setstream() with the stream to
// send. The ICE servers come from the `replay_ice_servers` setting
// (`replay_rtc_configuration()` in viewer-signaling.js's place, defined here
// too since the camera page loads only this file).
'use strict';

function camera_rtc_configuration() {
  var config = window.g_replay_config || {};
  return {iceServers: config.ice_servers || []};
}

function camera_sent(viewer, key) {
  logsent(viewer + ': ' + key);
}
function camera_received(viewer, key) {
  logrcvd(viewer + ': ' + key);
}

function ice_candidate_key(candidate) {
  return 'ice ' + candidate.protocol + ' ' + candidate.address + ':' + candidate.port +
    ' (' + candidate.type + ')';
}

// A ViewClient is a remote viewer that wants the camera's stream.
function ViewClient(recipient, poller) {
  var pc = new RTCPeerConnection(camera_rtc_configuration());

  pc.onicecandidate = function (event) {
    if (event.candidate) {
      camera_sent(recipient, ice_candidate_key(event.candidate));
      poller.send_message({recipient: recipient,
                           type: 'ice-candidate',
                           from: 'camera-replay',
                           candidate: event.candidate.toJSON()});
    }
  };

  pc.onnegotiationneeded = function () {
    logstream('onnegotiation');
    pc.setLocalDescription()
      .then(function () {
        camera_sent(recipient, 'offer');
        poller.send_message({recipient: recipient,
                             type: 'offer',
                             from: 'camera-replay',
                             sdp: pc.localDescription.toJSON()});
      })
      .catch(failed);
  };

  this.connection = pc;

  var senders = [];
  this.setstream = function (stream) {
    // A new stream replaces the tracks of the one before, so a viewer keeps
    // one video track when the camera reopens at a new size.
    senders.forEach(function (sender) { pc.removeTrack(sender); });
    senders = stream.getTracks().map(function (track) {
      return pc.addTrack(track, stream);
    });
  };

  function failed(e) { logstream('Signaling failed: ' + e); }

  this.on_message = function (msg) {
    if (msg.type == 'answer') {
      camera_received(recipient, 'answer');
      // An answer to an offer a newer one replaced is refused, and the
      // viewer answers the newer one.
      pc.setRemoteDescription(new RTCSessionDescription(msg.sdp)).catch(failed);
    } else if (msg.type == 'ice-candidate') {
      var candidate = new RTCIceCandidate(msg.candidate);
      camera_received(recipient, ice_candidate_key(candidate));
      pc.addIceCandidate(candidate).catch(failed);
    } else if (msg.type == 'solicitation') {
      // The viewer nags every 5 s until it connects. PHP's camera logged
      // the nag as unrecognized, so a viewer that lost its connection never
      // got a new offer; this one offers again once the last offer is
      // settled.
      if (pc.connectionState != 'connected' && pc.signalingState == 'stable') {
        this.read_ideal(msg);
        this.convey_offer();
      }
    } else {
      logrcvd('UNRECOGNIZED ' + JSON.stringify(msg));
      console.error('Unrecognized message for camera:', msg);
    }
  };

  var ideal = {width: 0, height: 0, frameRate: 0};
  this.on_solicitation = function (msg) {
    logrcvd('solicitation');
    this.read_ideal(msg);
    // With a stream, adding its tracks already asked for an offer
    // (onnegotiationneeded); PHP offered twice, and two offers in one poll
    // raced at the viewer.
    if (senders.length == 0) this.convey_offer();
  };
  this.read_ideal = function (msg) {
    if (msg.ideal) {
      // Sent as form fields, so each number arrives as text.
      ideal = {width: Number(msg.ideal.width) || 0,
               height: Number(msg.ideal.height) || 0,
               frameRate: Number(msg.ideal.frameRate) || 0};
    }
  };
  this.ideal = function () { return ideal; };

  this.convey_offer = function () {
    pc.createOffer()
      .then(function (offer) { return pc.setLocalDescription(offer); })
      .then(function () {
        camera_sent(recipient, 'offer');
        poller.send_message({recipient: recipient,
                             type: 'offer',
                             from: 'camera-replay',
                             sdp: pc.localDescription.toJSON()});
      })
      .catch(failed);
  };
}

// Owns the ViewClients and hands each message to its viewer's client.
function ViewClientManager(on_add_client_callback) {
  var dispatcher = {};  // viewer id => ViewClient
  var current_stream = null;

  var poller = new MessagePoller(
    'camera-replay',
    function (msg) {
      if (Object.prototype.hasOwnProperty.call(dispatcher, msg.from)) {
        dispatcher[msg.from].on_message(msg);
      } else if (msg.type != 'solicitation') {
        console.log('Received non-solicitation message from unknown sender: ', msg);
      } else {
        logrcvd('Solicitation received from ' + msg.from);
        var client = new ViewClient(msg.from, poller);
        if (current_stream) client.setstream(current_stream);
        dispatcher[msg.from] = client;
        client.on_solicitation(msg);
        on_add_client_callback();
      }
    });

  this.each_client = function (cb) {
    Object.keys(dispatcher).forEach(function (k) { cb(dispatcher[k]); });
  };

  this.setstream = function (stream) {
    current_stream = stream;
    this.each_client(function (vc) { vc.setstream(stream); });
  };

  // The largest size any viewer solicited.
  this.ideal = function () {
    var ideal = {width: 0, height: 0, frameRate: 0};
    this.each_client(function (vc) {
      var i = vc.ideal();
      ideal.width = Math.max(ideal.width, i.width);
      ideal.height = Math.max(ideal.height, i.height);
      ideal.frameRate = Math.max(ideal.frameRate, i.frameRate);
    });
    return ideal;
  };
}
