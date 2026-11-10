// Now Racing (#52): the rows page (now-racing.kiosk) and the columnar pages
// (now-racing-columnar*.kiosk), rebuilt from now-racing.js,
// now-racing-columnar.js, now-racing-poller.js and now-racing-overlay.js.
//
// The page asks `query=poll` 500 ms after each reply. A new lineup slides in
// lane by lane; once every car of the heat has a result the places land in
// place order, the winner's lane glows, and the board holds the results for
// `now-racing-linger-ms` before it takes the next lineup. Inside replay.php
// the board holds while a replay shows and starts the linger when it ends.
(function () {
  'use strict';

  var board, columns, lingerMs;
  var shown = {roundid: 0, heat: 0, results: 0};
  var lingerUntil = 0;
  var holding = false;
  var animate = true;
  var racers = 0;

  function okToChange() {
    return !holding && Date.now() > lingerUntil;
  }

  function lanes() {
    return board.querySelectorAll('[data-lane]');
  }

  function lane(n) {
    return board.querySelector('[data-lane="' + n + '"]');
  }

  function set(el, selector, text) {
    var found = el.querySelector(selector);
    if (found) found.textContent = text;
    return found;
  }

  function overlay(which) {
    document.querySelectorAll('[data-overlay]').forEach(function (el) {
      el.hidden = el.dataset.overlay !== which;
    });
  }

  // Whether a lane's names (name, car name, note) run past its row's foot,
  // or hold a word wider than their box. They are measured with words kept
  // whole (kiosk.css `.k-fitting`), so the font shrinks for a long word, as
  // PHP's does for a table too wide (now-racing-adjust-fonts.js:53-61), and
  // a word breaks only at the floor.
  function overflows() {
    board.classList.add('k-fitting');
    var over = Array.prototype.some.call(lanes(), function (el) {
      var names = el.querySelector('.k-names');
      if (!names) return false;
      if (names.offsetTop + names.offsetHeight > el.clientHeight) return true;
      return Array.prototype.some.call(names.children, function (line) {
        return line.scrollWidth > line.clientWidth;
      });
    });
    board.classList.remove('k-fitting');
    return over;
  }

  function laneFont() {
    var first = lanes()[0];
    return first ? parseFloat(getComputedStyle(first).fontSize) : 0;
  }

  // The row height follows the lane count and the window. The board's one
  // lane font starts from the row height and the window, and steps down by
  // 0.9 while a lane's names run past their row or hold a word wider than
  // their box (overflows(), above), as PHP's FontAdjuster shrinks every cell
  // until the table fits (now-racing-adjust-fonts.js:42-66). PHP shrinks on a
  // 75 ms timer; this runs only for a new lineup, a photo that loads, or a
  // resize (#250). The loop stops once the computed font stops falling, so
  // kiosk.css's `.k-lane` clamp holds the bounds (server/README.md, the kiosk
  // pages; D10, ADR-10), where PHP's loop sets no minimum. At the floor, a
  // word wider than its box breaks, and a name with more lines than its row
  // holds is cut at the row's foot (test_kiosk_names.py).
  function fit() {
    if (columns) return;
    var rows = lanes().length;
    if (!rows) return;
    var height = board.getBoundingClientRect().height / (rows + 0.6);
    board.style.setProperty('--row-h', height + 'px');
    board.style.setProperty('--row-font', Math.min(height * 0.42, window.innerWidth * 0.04) + 'px');
    var size = laneFont();
    while (overflows()) {
      board.style.setProperty('--row-font', size * 0.9 + 'px');
      var smaller = laneFont();
      if (smaller >= size) return;  // held by the clamp's floor
      size = smaller;
    }
  }

  function photo(slot, url, alt) {
    if (!slot) return;
    var img = slot.querySelector('img');
    if (!url) {
      slot.textContent = '';
      return;
    }
    if (!img) {
      img = document.createElement('img');
      img.alt = alt || '';
      // A photo narrows its lane's names, so they are fitted again.
      img.addEventListener('load', fit);
      slot.appendChild(img);
    }
    if (img.getAttribute('src') !== url) img.setAttribute('src', url);
  }

  function clearLanes() {
    lanes().forEach(function (el) {
      ['[data-car]', '[data-name]', '[data-carname]', '[data-note]', '[data-time]', '[data-speed]', '[data-place]'].forEach(function (s) {
        var found = el.querySelector(s);
        if (found) {
          found.textContent = '';
          found.classList.remove('k-shown');
        }
      });
      photo(el.querySelector('[data-photo]'), '');
      photo(el.querySelector('[data-car-photo]'), '');
      el.classList.remove('k-new', 'k-p1', 'k-p2', 'k-p3');
      var entry = el.querySelector('[data-entry]');
      if (entry) entry.classList.remove('k-new');
    });
  }

  function lineup(data) {
    var current = data['current-heat'] || {};
    if (data.hasOwnProperty('timer-trouble')) {
      overlay('timer');
    } else if (!current.now_racing && okToChange()) {
      overlay('paused');
    } else if (data['current-reschedule']) {
      overlay('reschedule');
    } else {
      overlay('');
    }
    var test = board.querySelector('[data-timer-test]');
    if (test) {
      test.hidden = !data.hasOwnProperty('timer-test');
      lanes().forEach(function (el) { el.hidden = !test.hidden; });
    }

    var results = (data['heat-results'] || []).length;
    if (results > shown.results) shown.results = results;
    if (!okToChange()) return;

    var isNew = current.roundid != shown.roundid || current.heat != shown.heat || results < shown.results;
    shown = {roundid: current.roundid, heat: current.heat, results: results};

    if (current['number-of-heats']) {
      var name = current['class'];
      var title;
      if (current.use_master_sched) {
        title = 'Heat ' + current.masterheat;
        if (name) title += ': ' + name + (current.round > 1 ? ', round ' + current.round : '');
      } else {
        title = (name ? name + ', ' : '') + 'Heat ' + current.heat + ' of ' + current['number-of-heats'];
      }
      set(document, '[data-banner-title]', title);
    }

    var list = data.racers || [];
    if (isNew && list.length > 0) {
      animate = true;
      racers = list.length;
      clearLanes();
      list.forEach(function (r, i) {
        var el = lane(r.lane);
        if (!el) return;
        set(el, '[data-car]', r.carnumber);
        set(el, '[data-name]', r.name);
        set(el, '[data-carname]', r.carname || '');
        set(el, '[data-note]', r.note || '');
        photo(el.querySelector('[data-photo]'), r.photo, '');
        photo(el.querySelector('[data-car-photo]'), r.photo2, '');
        var moving = el.querySelector('[data-entry]') || el;
        moving.style.setProperty('--i', i);
        moving.classList.remove('k-new');
        void moving.offsetWidth;
        moving.classList.add('k-new');
      });
      fit();
    } else {
      list.forEach(function (r) {
        var el = lane(r.lane);
        if (!el) return;
        photo(el.querySelector('[data-photo]'), r.photo, '');
        photo(el.querySelector('[data-car-photo]'), r.photo2, '');
      });
    }
  }

  function wait(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  // The results of the heat shown. Resolves once the places have landed.
  function results(data) {
    var found = data['heat-results'] || [];
    if (!found.length) return Promise.resolve();
    var precision = data.hasOwnProperty('precision') ? data.precision : 3;
    var byPlace = found.slice().sort(function (a, b) { return a.place - b.place; });
    var land = animate && found.length >= racers;
    found.forEach(function (hr) {
      var el = lane(hr.lane);
      if (!el) return;
      var time = set(el, '[data-time]', Number.parseFloat(hr.time).toFixed(precision));
      if (time && land) time.classList.add('k-shown');
      if (hr.speed !== '' && hr.speed !== undefined) {
        var speed = set(el, '[data-speed]', hr.speed);
        if (speed && land) speed.classList.add('k-shown');
      }
      if (!land) set(el, '[data-place]', columns ? '' : hr.place);
    });
    if (!land) return Promise.resolve();
    animate = false;
    var chain = Promise.resolve();
    byPlace.forEach(function (hr, i) {
      chain = chain.then(function () {
        var el = lane(hr.lane);
        if (!el) return;
        var place = set(el, '[data-place]', columns ? i + 1 : hr.place);
        if (place) place.classList.add('k-shown');
        if (hr.place <= 3) el.classList.add('k-p' + hr.place);
        return wait(400);
      });
    });
    return chain.then(function () { lingerUntil = Date.now() + lingerMs; });
  }

  function best(data) {
    var box = document.querySelector('[data-best]');
    if (!box || !data['best-times']) return;
    var tiles = box.querySelectorAll('div');
    data['best-times'].forEach(function (b, i) {
      var tile = tiles[i];
      if (!tile) {
        tile = document.createElement('div');
        tile.innerHTML = '<span></span><b></b>';
        box.appendChild(tile);
      }
      tile.firstChild.textContent = b.carnumber;
      tile.lastChild.textContent = b.finishtime;
    });
  }

  function request() {
    var query = {
      query: 'poll',
      values: (columns ? 'best-times,' : '') + 'current-heat,heat-results,precision,racers,timer-trouble,current-reschedule,intermission',
      roundid: shown.roundid,
      heat: shown.heat,
    };
    if (columns) {
      var width = Math.round(window.innerWidth / Math.max(1, lanes().length));
      query['head-size'] = width + 'x' + Math.floor(1 + 0.24 * window.innerHeight);
      query['car-size'] = width + 'x' + Math.floor(1 + 0.20 * window.innerHeight);
    } else {
      var h = parseFloat(board.style.getPropertyValue('--row-h')) || 0;
      query['row-height'] = Math.floor(h * 0.8);
    }
    return query;
  }

  function poll() {
    return Kiosk.action(request()).then(function (data) {
      Kiosk.intermission(data.intermission);
      best(data);
      return results(data).then(function () { lineup(data); });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    board = document.querySelector('[data-now-racing]');
    if (!board) {
      // No lane count yet: reload in 5 s, as now-racing.kiosk:32-41 does.
      setTimeout(function () { location.reload(); }, 5000);
      return;
    }
    columns = board.dataset.layout === 'columns';
    lingerMs = Number(board.dataset.lingerMs) || 10000;
    fit();
    window.addEventListener('resize', fit);
    Kiosk.onReplay = function (paused) {
      holding = paused;
      if (!paused) lingerUntil = Date.now() + lingerMs;
    };
    // 500 ms after each reply (now-racing-poller.js:33-43).
    Kiosk.poll(poll, 500, 'after');
    Kiosk.start();
  });
}());
