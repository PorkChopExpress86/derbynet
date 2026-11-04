// Please Check In (#52), rebuilt from please-check-in.kiosk. The list
// refreshes every 30 s, where PHP reloaded the page, and with the kiosk's
// `classids` parameter. It takes 1 to 4 columns and the largest font that
// fits the screen, as PHP's layout code chose them.
(function () {
  'use strict';

  var root;

  // The fewest columns, up to 4, that hold the list at the base font, then
  // the largest font up to twice the base that still fits.
  function fit() {
    var list = root.querySelector('[data-wanted]');
    if (!list) return;
    var room = window.innerHeight - list.getBoundingClientRect().top - 16;
    var base = Math.max(12, window.innerHeight * 0.026);
    var fits = function () { return list.scrollHeight <= room + 1; };
    list.style.height = 'auto';
    list.style.setProperty('--wanted-font', base + 'px');
    var columns = 1;
    for (; columns < 4; ++columns) {
      list.style.columnCount = columns;
      if (fits()) break;
    }
    list.style.columnCount = columns;
    for (var px = base * 2; px > base; px -= 1) {
      list.style.setProperty('--wanted-font', px + 'px');
      if (fits()) break;
    }
    if (!fits()) list.style.setProperty('--wanted-font', base + 'px');
    list.style.height = room + 'px';
  }

  function refresh() {
    var query = new URLSearchParams({parameters: JSON.stringify(Kiosk.parameters)});
    return Kiosk.fragment(root.dataset.url + '?' + query).then(function (html) {
      if (html === null) return;
      root.innerHTML = html;
      var titled = root.querySelector('[data-title]');
      var banner = document.querySelector('[data-banner-title]');
      if (titled && banner) banner.textContent = titled.dataset.title;
      fit();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    root = document.querySelector('[data-check-in]');
    fit();
    window.addEventListener('resize', fit);
    Kiosk.poll(refresh, 30000, 'every');
  });
}());
