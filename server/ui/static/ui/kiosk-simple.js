// The kiosk pages with no content poll (#52): identify, qrcode, scale-MPH.
// Each reads its parameters from Kiosk.onParameters, as PHP's pages read
// KioskPoller.param_callback.
(function () {
  'use strict';

  function windowSize() {
    var el = document.querySelector('[data-window-size]');
    if (el) el.textContent = window.innerWidth + ' x ' + window.innerHeight;
  }

  // qrcode.kiosk: the title and the code follow the kiosk's parameters.
  var shownContent = null;
  function showQr(parameters) {
    var title = document.querySelector('[data-qr-title]');
    var box = document.querySelector('[data-qr]');
    if (!box) return;
    if (typeof parameters.title === 'string') title.textContent = parameters.title;
    var content = typeof parameters.content === 'string' ? parameters.content : '';
    if (content === shownContent) return;
    shownContent = content;
    box.hidden = content === '';
    if (!content) return;
    var code = qrcode(0, 'M');
    code.addData(content);
    code.make();
    box.innerHTML = code.createSvgTag({cellSize: 8, margin: 1, scalable: true});
    box.querySelector('svg').setAttribute('aria-label', 'QR code');
    box.animate([{transform: 'scale(.85)', opacity: 0}, {transform: 'none', opacity: 1}], {duration: 500, easing: 'ease-out'});
  }

  // scale-MPH.kiosk: the car drives across while the time steps down, with
  // the scale speed from `track-length`.
  function scale() {
    var root = document.querySelector('[data-scale]');
    if (!root) return;
    var length = Number(root.dataset.trackLength);
    var car = root.querySelector('[data-car]');
    var time = root.querySelector('[data-time]');
    var speed = root.querySelector('[data-speed]');
    var times = [4.0, 3.5, 3.2, 3.1, 3.0];
    var i = 0;
    function step() {
      var t = times[i++ % times.length];
      time.textContent = t.toFixed(1);
      speed.textContent = (length / t * 3600 * 25 / 5280).toFixed(1);
    }
    // `fill: 'forwards'` holds the car at 100vw, off the screen, through the
    // wait, and each drive starts again from 45vw, as PHP's animate_car()
    // does (scale-MPH.kiosk:11-18, #252; test_kiosk_scale_mph.py).
    function drive() {
      var a = car.animate([
        {left: '45vw', maxWidth: '10vw'},
        {left: '5vw', maxWidth: '90vw', offset: 0.71},
        {left: '100vw', maxWidth: '90vw'},
      ], {duration: 2800, easing: 'ease-in-out', fill: 'forwards'});
      a.onfinish = function () { setTimeout(drive, 10000); };
    }
    step();
    setInterval(step, 2000);
    drive();
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (document.querySelector('[data-window-size]')) {
      windowSize();
      setInterval(windowSize, 500);
    }
    if (document.querySelector('[data-qr]')) {
      Kiosk.onParameters = showQr;
      showQr(Kiosk.parameters);
    }
    scale();
  });
}());
