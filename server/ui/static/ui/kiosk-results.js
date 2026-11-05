// Results by Racer (#52): #49's results page as a kiosk. The rounds poll
// results/rounds/ every second, which answers 204 while they are unchanged,
// (results-by-racer-update.js:4-17 polls every 1 s), so a new round or
// result shows on the next poll, where PHP reloads the page for a new round.
// A list taller than the screen glides up in a loop; PHP's kiosk moves it up
// a row every 2 s (results-by-racer-scrolling.js:71, racer-results.php:47-48),
// a look the rebuild changes (D10).
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
    // Every second (results-by-racer-update.js:4-17).
    Kiosk.poll(poll, 1000, 'after');
    // The playlist intermission (D12), on the same 1 s.
    Kiosk.poll(function () {
      return Kiosk.action({query: 'poll', values: 'intermission'}).then(function (data) {
        Kiosk.intermission(data.intermission);
      });
    }, 1000, 'after');
    Kiosk.start();
    setInterval(scroll, 40);
  });
}());
