// SPIKE #71, candidate A: the intermission leaders.  htmx chains the panels;
// this only holds the next one back while a replay shows.
document.addEventListener('alpine:init', function () {
  Alpine.data('leaders', function () {
    return {
      paused: false,
      pending: null,
      init: function () {
        var self = this;
        S.onReplay(function (p) {
          self.paused = p;
          if (!p && self.pending) { var el = self.pending; self.pending = null; htmx.trigger(el, 'resume'); }
        });
        this.$el.addEventListener('htmx:confirm', function (e) {
          if (self.paused) { e.preventDefault(); self.pending = e.detail.elt; }
        });
        this.$el.addEventListener('htmx:afterSwap', function () { S.mark('panel'); });
      },
    };
  });
});
