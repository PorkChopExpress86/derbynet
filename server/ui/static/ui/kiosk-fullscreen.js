// fullscreen.php (#52): load the URL in a full-window frame and ask the
// browser to go full screen with screenfull, as fullscreen.php:42-51 does.
document.addEventListener('DOMContentLoaded', function () {
  'use strict';
  var form = document.querySelector('[data-setup]');
  var frame = document.querySelector('[data-interior]');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    frame.src = form.elements.inner_url.value;
    form.hidden = true;
    frame.hidden = false;
    if (screenfull.enabled) screenfull.request();
  });
});
