// Standings (#52), rebuilt from standings-kiosk.js. The rows reveal one at
// a time every 1.5 s, last place first. Once all show, a list taller than
// the screen scrolls one row every 2 s in a loop. With no `key` parameter
// the first catalog entry starts after 5 s; a new `key` fetches that list
// and starts again.
(function () {
  'use strict';

  var root;
  var revealTimer = 0;
  var scrollTimer = 0;
  var shownKey = null;

  function rows() {
    return root.querySelector('[data-rows]');
  }

  function stop() {
    clearTimeout(revealTimer);
    clearInterval(scrollTimer);
  }

  function overflowing(list) {
    return list.scrollHeight > list.clientHeight + 2;
  }

  function scrollOne() {
    var list = rows();
    if (!list || !list.firstElementChild) return;
    var items = list.children;
    Kiosk.flip(items, function () { list.appendChild(list.firstElementChild); });
  }

  function revealOne() {
    var list = rows();
    if (!list) return;
    var hidden = list.querySelectorAll('[data-racer][hidden]');
    if (hidden.length) {
      var row = hidden[hidden.length - 1];
      Kiosk.flip(list.children, function () { row.hidden = false; });
      revealTimer = setTimeout(revealOne, 1500);
    } else if (overflowing(list)) {
      scrollTimer = setInterval(function () { if (!Kiosk.paused) scrollOne(); }, 2000);
    }
  }

  function start() {
    stop();
    shownKey = (root.querySelector('[data-shown-key]') || {dataset: {}}).dataset.shownKey || '';
    revealOne();
  }

  function choose(key) {
    if (key === shownKey) return;
    stop();
    Kiosk.fragment(root.dataset.url + '?' + new URLSearchParams({key: key})).then(function (html) {
      if (html === null) return;
      root.innerHTML = html;
      start();
    }).catch(function () {});
  }

  document.addEventListener('DOMContentLoaded', function () {
    root = document.querySelector('[data-standings]');
    Kiosk.onParameters = function (parameters) {
      if (typeof parameters.key === 'string' && parameters.key) choose(parameters.key);
    };
    if (root.dataset.key) start();
    else setTimeout(function () { if (shownKey === null) start(); }, 5000);
  });
}());
