// SPIKE #71, candidate A: now racing.
var LINGER_MS = 4000;  // now_racing_linger_ms: hold results on screen

// What the board shows, sent with each poll.  `hold` asks the server not to
// move to the next lineup yet (the linger after results, or after a replay).
var holdUntil = 0;
function boardState() {
  var b = document.querySelector('[data-board]');
  var hold = Date.now() < holdUntil ? 1 : '';
  return b ? { roundid: b.dataset.roundid, heat: b.dataset.heat, nresults: b.dataset.nresults,
               hold: hold, vt: USE_VT ? '' : '0' } : {};
}

document.addEventListener('alpine:init', function () {
  Alpine.data('nowRacing', function (delay) {
    return {
      paused: false,
      init: function () {
        var self = this, board = this.$refs.board;
        S.onReplay(function (p) {
          self.paused = p;
          // replay-ended: start the linger, as now-racing.js does.
          if (!p) holdUntil = Date.now() + LINGER_MS;
        });
        // Paused for a replay: skip this poll but keep the chain going, as
        // Poller.suspended does.
        board.addEventListener('htmx:confirm', function (e) {
          if (self.paused) {
            e.preventDefault();
            self.queue(delay);
          }
        });
        board.addEventListener('htmx:afterRequest', function () { self.queue(delay); });
        board.addEventListener('htmx:afterSwap', function () {
          var b = board.querySelector('[data-board]');
          if (b.hasAttribute('data-new-heat')) S.mark('lineup', b.dataset.roundid + '/' + b.dataset.heat);
          if (Number(b.dataset.nresults) > 0) {
            S.mark('results', b.dataset.roundid + '/' + b.dataset.heat);
            holdUntil = Date.now() + LINGER_MS;
          }
        });
      },
      queue: function (ms) {
        var board = this.$refs.board;
        clearTimeout(this.timer);
        this.timer = setTimeout(function () { htmx.trigger(board, 'next-poll'); }, ms);
      },
    };
  });
});
