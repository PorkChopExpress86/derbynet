// Results by Racer (#52): #49's results page as a kiosk. The rounds poll
// results/rounds/ every second, which answers 204 while they are unchanged,
// so a new round or result shows on the next poll. A list taller than the
// screen scrolls up slowly in a loop, as racer-results.php's kiosk does.
(function () {
  'use strict';

  var root, rounds;
  var offset = 0;
  var sig = '';

  function poll() {
    var url = root.dataset.url + (sig ? '?' + new URLSearchParams({sig: sig}) : '');
    return Kiosk.fragment(url).then(function (html) {
      if (html === null) return;
      rounds.innerHTML = html;
      var found = rounds.querySelector('#results-sig');
      sig = found ? found.value : '';
    });
  }

  function scroll() {
    if (Kiosk.paused) return;
    var room = root.clientHeight;
    var height = rounds.scrollHeight;
    if (height <= room) {
      offset = 0;
    } else {
      offset += 1;
      if (offset > height - room + room / 3) offset = 0;
    }
    rounds.style.transform = 'translateY(' + (-offset) + 'px)';
  }

  document.addEventListener('DOMContentLoaded', function () {
    root = document.querySelector('[data-results]');
    rounds = root.querySelector('[data-rounds]');
    var found = rounds.querySelector('#results-sig');
    sig = found ? found.value : '';
    Kiosk.poll(poll, 1000, 'after');
    Kiosk.start();
    setInterval(scroll, 40);
  });
}());
