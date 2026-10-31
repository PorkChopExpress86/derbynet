// SPIKE #71: shared by both candidates.  (1) The replay handshake: replay.php
// posts 'replay-started' and 'replay-ended' into the kiosk iframe; the page
// pauses every running animation and stops starting new ones until the replay
// ends.  (2) Instrumentation the Playwright scripts read: window.__spike.log
// holds {ev, t} marks on one clock (epoch ms) across browser instances.
(function () {
  var S = (window.__spike = window.__spike || { log: [], requests: 0, paused: false });
  S.now = function () { return performance.timeOrigin + performance.now(); };
  S.mark = function (ev, detail) { S.log.push({ ev: ev, t: S.now(), detail: detail }); };
  S.listeners = [];
  S.onReplay = function (fn) { S.listeners.push(fn); };
  window.addEventListener('message', function (e) {
    if (e.data === 'replay-started') {
      S.paused = true;
      document.getAnimations().forEach(function (a) { a.pause(); });
      document.documentElement.classList.add('replay-paused');
    } else if (e.data === 'replay-ended') {
      S.paused = false;
      document.getAnimations().forEach(function (a) { if (a.playState === 'paused') a.play(); });
      document.documentElement.classList.remove('replay-paused');
    } else {
      return;
    }
    S.mark(S.paused ? 'paused' : 'resumed');
    S.listeners.forEach(function (fn) { fn(S.paused); });
  });
})();
