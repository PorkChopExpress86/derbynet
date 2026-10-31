// SPIKE #71, candidate A: the standings reveal.
document.addEventListener('alpine:init', function () {
  Alpine.data('standings', function () {
    return {
      paused: false,
      stepping: false,
      key: null,
      total: 0,
      revealed: 0,
      target: 0,
      auto: false,
      init: function () {
        var self = this;
        S.onReplay(function (p) { self.paused = p; if (!p) self.tick(); });
      },
      // poll.kiosk's parameters, delivered by htmx as an HX-Trigger event:
      // {key: catalog key, reveal: rows to show from the bottom, or "all"}.
      apply: function (params) {
        var self = this;
        var key = params.key || '';
        var load = key === this.key ? Promise.resolve()
          : htmx.ajax('GET', '/spike/a/standings/table?key=' + encodeURIComponent(key),
                      { target: '#table', swap: 'innerHTML' }).then(function () {
              self.key = key;
              self.revealed = 0;
              self.total = document.querySelectorAll('#table .srow').length;
            });
        load.then(function () {
          self.auto = params.reveal === 'all';
          self.target = self.auto ? self.total : Math.min(Number(params.reveal) || 0, self.total);
          if (self.target < self.revealed) self.revealed = self.target;
          self.tick();
        });
      },
      // Reveal one row inside a view transition: the new row drops in and the
      // rows below it glide down (each has a view-transition-name).  With
      // ?vt=0, flip() does the same motion with the Web Animations API.
      tick: function () {
        var self = this;
        if (this.paused || this.stepping || this.revealed >= this.target) return;
        this.stepping = true;
        S.mark('reveal', this.revealed + 1);
        var step = function () { self.revealed++; return self.$nextTick(); };
        var done = !USE_VT ? flip(document.querySelectorAll('#table .srow'), step)
          : document.startViewTransition ? document.startViewTransition(step).finished : step();
        // One reveal at a time: stay "stepping" through the pause between
        // rows, so a poll or a replay-ended cannot start a second chain.
        done.then(function () {
          setTimeout(function () { self.stepping = false; self.tick(); }, self.auto ? 1500 : 0);
        });
      },
      shows: function (fromBottom) { return fromBottom <= this.revealed; },
    };
  });
});
