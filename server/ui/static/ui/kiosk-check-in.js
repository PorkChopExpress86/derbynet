// Please Check In (#52), rebuilt from please-check-in.kiosk. The list
// refreshes every 30 s, where PHP reloaded the page
// (please-check-in.kiosk:16), and with the kiosk's `classids` parameter. It
// takes the fewest of 1 to 4 columns that fit, then a font up to twice the
// base, after please-check-in.kiosk:19-45, which sizes the columns from a
// 4-column layout and grows the font by up to 2x.
(function () {
  'use strict';

  var root;

  // The fewest columns, up to 4, that hold the list at the base font, then
  // the largest font up to twice the base that still fits.
  function fit() {
    var list = root.querySelector('[data-wanted]');
    if (!list) return;
    var base = Math.max(12, window.innerHeight * 0.026);
    // The list's height is the screen's; what does not fit spills into
    // columns past its right edge.
    var fits = function () {
      return list.scrollWidth <= list.clientWidth + 1 && list.scrollHeight <= list.clientHeight + 1;
    };
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
    // Every 30 s (please-check-in.kiosk:16).
    Kiosk.poll(refresh, 30000, 'every');
  });
}());
