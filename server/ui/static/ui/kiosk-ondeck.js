// On Deck (#52), rebuilt from ondeck.js. `query=poll` for ondeck, rounds,
// current-heat and current-reschedule every second. One row per heat, a
// cell per lane in its lane color with byes filled in, a divider per round
// outside the master schedule. The running heat and the next one are
// marked, and the chart glides to keep the running heat near the top. With
// show-cars-on-deck each car's photo shows, and a tap opens it large.
(function () {
  'use strict';

  var root, table, modal;
  var nlanes, colors, photos;
  var drawn = '';

  function cell(tag, text, cls) {
    var el = document.createElement(tag);
    if (text !== undefined && text !== null) el.textContent = text;
    if (cls) el.className = cls;
    return el;
  }

  function photoUrl(render, photo) {
    return root.dataset.photoBase + '/' + render + '/' + photo.basename + '/' + photo['cache-breaker'];
  }

  function draw(data) {
    var current = data['current-heat'] || {};
    var deck = data.ondeck || {chart: []};
    var interleaved = current.use_master_sched;
    var signature = JSON.stringify([deck, current.roundid, current.heat, interleaved]);
    if (signature === drawn) return;
    var first = drawn === '';
    drawn = signature;

    var names = {};
    (data.rounds || []).forEach(function (r) { names[r.roundid] = r.name; });
    var body = document.createElement('tbody');
    var cols = document.createElement('colgroup');
    if (interleaved) cols.appendChild(cell('col')).style.width = '6vw';
    cols.appendChild(cell('col')).style.width = interleaved ? '14vw' : '9vw';
    var row = null;
    var lane = 0;
    var roundid = 0;
    var heat = 0;

    function byes(upTo) {
      while (lane < upTo) {
        lane += 1;
        row.appendChild(cell('td', 'Bye', 'k-bye')).dataset.color = colors[lane - 1] || 'none';
      }
    }

    deck.chart.forEach(function (c) {
      if (c.roundid !== roundid || c.heat !== heat) {
        if (c.roundid !== roundid && !interleaved) {
          var divider = body.appendChild(cell('tr'));
          var th = divider.appendChild(cell('th', names[c.roundid] || '', 'k-divider'));
          th.colSpan = nlanes + 1;
        }
        if (row) byes(nlanes);
        row = body.appendChild(cell('tr'));
        row.dataset.heat = c.roundid + '_' + c.heat;
        lane = 0;
        if (interleaved) row.appendChild(cell('th', c.masterheat));
        row.appendChild(cell('th', (interleaved ? (names[c.roundid] || '') + ', ' : '') + 'Heat ' + c.heat));
        roundid = c.roundid;
        heat = c.heat;
      }
      byes(c.lane - 1);
      var td = row.appendChild(cell('td'));
      td.dataset.color = colors[c.lane - 1] || 'none';
      td.appendChild(cell('div', c.carnumber, 'k-car'));
      td.appendChild(cell('div', c.name, 'k-racer'));
      if (c.result) td.appendChild(cell('div', String(c.result).substring(1), 'k-time'));
      if (photos && c.carphoto) {
        var img = td.appendChild(cell('img'));
        img.alt = 'Car ' + c.carnumber;
        img.src = photoUrl('400x400', c.carphoto);
        img.dataset.large = photoUrl('work', c.carphoto);
      }
      lane = c.lane;
    });
    if (row) byes(nlanes);

    table.replaceChildren(cols, body);
    var now = table.querySelector('tr[data-heat="' + current.roundid + '_' + current.heat + '"]');
    if (now) now.classList.add('k-current');
    if (deck.next) {
      var next = table.querySelector('tr[data-heat="' + deck.next.roundid + '_' + deck.next.heat + '"]');
      if (next && next !== now) next.classList.add('k-next');
    }
    var target = now || table.querySelector('tr.k-next');
    if (target) {
      var top = Math.max(0, target.offsetTop - root.clientHeight * 0.15);
      root.scrollTo({top: top, behavior: first ? 'auto' : 'smooth'});
    }
  }

  function poll() {
    return Kiosk.action({query: 'poll', values: 'ondeck,rounds,current-heat,current-reschedule'}).then(draw);
  }

  document.addEventListener('DOMContentLoaded', function () {
    root = document.querySelector('[data-ondeck]');
    table = root.querySelector('[data-chart]');
    modal = document.querySelector('[data-modal]');
    nlanes = Number(root.dataset.lanes) || 0;
    colors = root.dataset.colors ? root.dataset.colors.split(',') : [];
    photos = root.dataset.photos === '1';
    table.addEventListener('click', function (e) {
      var img = e.target.closest('img[data-large]');
      if (!img) return;
      modal.querySelector('[data-modal-img]').src = img.dataset.large;
      modal.hidden = false;
    });
    modal.querySelector('[data-modal-close]').addEventListener('click', function () { modal.hidden = true; });
    Kiosk.poll(poll, 1000, 'after');
    Kiosk.start();
  });
}());
