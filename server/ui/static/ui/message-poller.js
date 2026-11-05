// The WebRTC message relay between the replay page and the remote camera
// (#53), ported from website/js/message-poller.js without jQuery.
//
// A MessagePoller polls `message.retrieve` every second for the messages
// addressed to its recipient, and sends with `message.send`, as PHP's does
// (message-poller.js:115-137). A message object goes as a PHP form array,
// `message[recipient]=...&message[sdp][type]=...`, the encoding
// jQuery.ajax gave it, so the server stores what PHP's stored.
//
// With `g_websocket_url` set, it uses a WebSocket instead. The page renders
// the URL empty, so v1 always polls; #56 decides the push channel. Messages
// sent while the socket is still opening are flushed with `ws.send` once it
// opens: PHP's flush called `wc.send`, an undefined name, so the first
// message, the viewer's solicitation, was lost (#64 C32,
// ui/tests/browser/test_replay.py).
'use strict';

function logmsg(cl, txt) {
  var log = document.getElementById('log');
  if (!log) return;
  var p = document.createElement('p');
  p.className = cl;
  p.textContent = txt;
  log.appendChild(p);
  while (log.children.length > 20) log.removeChild(log.firstChild);
}

function logsent(txt) {
  console.log('=> ' + txt);
  logmsg('sent', txt);
}
function logrcvd(txt) {
  console.log('<= ' + txt);
  logmsg('rcvd', txt);
}
function logstream(txt) {
  console.log('** ' + txt);
  logmsg('stream', txt);
}

function make_id_string(stem) {
  var alphabet = 'abcdefghjklmnpqrstuvwz0123456789';
  var id = stem;
  for (var k = 0; k < 12; ++k) {
    id += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return id;
}

// `value` as the form fields jQuery.param writes: an object's keys in
// brackets, an array's items by index, null and undefined as empty text.
function php_form_fields(value, prefix, out) {
  out = out || new URLSearchParams();
  if (value !== null && typeof value === 'object') {
    Object.keys(value).forEach(function (key) {
      php_form_fields(value[key], prefix ? prefix + '[' + key + ']' : key, out);
    });
  } else {
    out.append(prefix, value === null || value === undefined ? '' : String(value));
  }
  return out;
}

// POST `fields` to action.php, resolving to the parsed reply.
function post_action(fields) {
  var config = window.g_replay_config || {};
  return fetch(config.action || '/action.php', {
    method: 'POST',
    credentials: 'same-origin',
    body: fields instanceof FormData ? fields : php_form_fields(fields),
  }).then(function (response) { return response.json(); });
}

function MessagePoller(recipient, on_message, topics) {
  topics = topics || [];
  var poller = this;
  if (window.g_websocket_url) {
    var ws = new WebSocket(window.g_websocket_url);
    var closing = false;
    var messages_pending_open = [];

    var init = function () {
      ws.onmessage = function (event) {
        var msg;
        try {
          msg = JSON.parse(event.data);
        } catch (e) {
          console.log('  Unable to parse json message:', event.data);
          return;
        }
        on_message(msg);
      };

      ws.onopen = function () {
        console.log('Websocket opened');
        poller.send_message({subscriber: recipient, topics: topics});
        if (messages_pending_open.length > 0) {
          logstream('websocket opened, sending queued messages');
          while (messages_pending_open.length > 0) {
            ws.send(messages_pending_open.shift());
          }
        }
      };

      ws.onerror = function (event) { console.error('Websocket error', event); };
      ws.onclose = function (event) {
        console.log('Websocket close event', event, ws && ws.readyState);
        if (!closing) {
          // Reconnect after a 10 s cooling-off delay.
          setTimeout(function () {
            console.log('Attempting to re-establish websocket connection');
            ws = new WebSocket(window.g_websocket_url);
            init();
          }, 10000);
        }
      };
    };
    init();

    this.send_message = function (message_json) {
      try {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify(message_json));
        } else if (ws.readyState === WebSocket.CONNECTING) {
          logstream('(awaiting websocket open)');
          messages_pending_open.push(JSON.stringify(message_json));
        } else {
          logstream('send_message: websocket readyState is ' +
                    ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][ws.readyState]);
        }
      } catch (e) {
        console.log('send_message error', e, e.code, e.name, e.message);
      }
    };
    this.close = function () {
      closing = true;
      if (ws) {
        ws.close();
        ws = null;
      }
    };
  } else {
    this.send_message = function (message_json) {
      post_action({action: 'message.send', message: message_json})
        .catch(function (e) { console.log('message.send failed', e); });
    };

    var in_flight = false;
    this.retrieve_messages = function () {
      if (in_flight) return;
      in_flight = true;
      post_action({action: 'message.retrieve', recipient: recipient})
        .then(function (data) {
          (data.messages || []).forEach(function (msg) { on_message(msg); });
        })
        .catch(function (e) { console.log('message.retrieve failed', e); })
        .finally(function () { in_flight = false; });
    };

    var interval = setInterval(function () { poller.retrieve_messages(); }, 1000);

    this.close = function () {
      if (interval) clearInterval(interval);
      interval = null;
    };
  }
}
