// fullscreen.php (#52): load the URL in a full-window frame and ask the
// browser to go full screen, as screenfull.request() did.
document.addEventListener('DOMContentLoaded', function () {
  'use strict';
  var form = document.querySelector('[data-setup]');
  var frame = document.querySelector('[data-interior]');
  form.addEventListener('submit', function (e) {
    e.preventDefault();
    frame.src = form.elements.inner_url.value;
    form.hidden = true;
    frame.hidden = false;
    var root = document.documentElement;
    var request = root.requestFullscreen || root.webkitRequestFullscreen;
    if (request) {
      var asked = request.call(root);
      if (asked && asked.catch) asked.catch(function () {});
    }
  });
});
