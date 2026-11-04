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

  // Row height and font follow the lane count and the window, so a name
  // fits its row (now-racing-adjust-fonts.js).
  function fit() {
    if (columns) return;
    var rows = lanes().length;
    if (!rows) return;
    var height = board.getBoundingClientRect().height / (rows + 0.6);
    board.style.setProperty('--row-h', height + 'px');
    board.style.setProperty('--row-font', Math.max(14, Math.min(height * 0.42, window.innerWidth * 0.04)) + 'px');
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
      values: (columns ? 'best-times,' : '') + 'current-heat,heat-results,precision,racers,timer-trouble,current-reschedule',
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
      best(data);
      return results(data).then(function () { lineup(data); });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    board = document.querySelector('[data-now-racing]');
    if (!board) {
      // No lane count yet: try again in 5 s, as PHP reloads.
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
    Kiosk.poll(poll, 500, 'after');
    Kiosk.start();
  });
}());
