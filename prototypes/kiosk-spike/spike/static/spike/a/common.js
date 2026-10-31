// SPIKE #71, candidate A: what every page shares.  htmx fetches
// server-rendered HTML; Alpine.js keeps the little client state a kiosk has
// (paused for replay, how many standings rows are revealed).
var S = window.__spike;

// ?vt=0 turns the View Transitions API off: htmx's swap and settle classes
// and hand-written FLIP do the motion instead (the .no-vt rules).
var USE_VT = !/[?&]vt=0/.test(location.search);
if (!USE_VT) document.documentElement.classList.add('no-vt');

// Runs `change`, then glides each element from where it was to where it is
// now, and drops newly shown ones in.  Resolves when the motion ends.  This
// is what Svelte's animate:flip does.
function flip(elements, change) {
  var els = Array.prototype.slice.call(elements);
  var before = els.map(function (el) { return el.offsetParent ? el.getBoundingClientRect().top : null; });
  return Promise.resolve(change()).then(function () {
    var anims = els.map(function (el, i) {
      if (!el.offsetParent) return null;
      if (before[i] === null) {
        return el.animate([{ transform: 'translateY(-40px) scaleY(.4)', opacity: 0 }, { transform: 'none', opacity: 1 }],
                          { duration: 500, easing: 'ease-out' });
      }
      var dy = before[i] - el.getBoundingClientRect().top;
      return dy ? el.animate([{ transform: 'translateY(' + dy + 'px)' }, { transform: 'none' }],
                             { duration: 500, easing: 'ease-out' }) : null;
    }).filter(Boolean);
    return Promise.all(anims.map(function (a) { return a.finished; }));
  });
}

// Count every request htmx sends, for the request-rate numbers.
document.addEventListener('htmx:beforeSend', function () { S.requests++; });
