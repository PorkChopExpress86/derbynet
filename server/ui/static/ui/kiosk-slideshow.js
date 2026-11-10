// Slideshow (#52), rebuilt from slideshow.js. `slide.next` every 10 s, each
// reply naming the next query: the title slide, the slides, the subdir's
// slides, then the racers with photos. The `title`, `subdir` and `classids`
// parameters follow the kiosk.
//
// Each new slide fades in while the one before fades out, and the old one is
// removed when its fade ends, so only one slide shows between changes (#253).
// PHP cuts from one slide to the next with no fade (slideshow.js:51-52,
// slideshow.css:56-59); the 1.2 s crossfade is D10's animation.
// test_kiosk_slideshow.py checks both ends of it.
(function () {
  'use strict';

  var box, titleSlide;
  var next = {mode: 'slide', file: ''};

  function show(photo) {
    next = photo.next || {mode: 'slide', file: ''};
    var slide = document.createElement('div');
    slide.className = 'k-slide';
    var img = document.createElement('img');
    img.className = 'k-photo';
    img.alt = '';
    img.src = photo.photo;
    slide.appendChild(img);
    if (photo.hasOwnProperty('name')) {
      var caption = document.createElement('p');
      caption.className = 'k-caption';
      caption.textContent = (photo.hasOwnProperty('carnumber') ? photo.carnumber + ': ' : '') + photo.name;
      if (photo.carname) {
        caption.appendChild(document.createElement('br'));
        var i = document.createElement('i');
        i.textContent = photo.carname;
        caption.appendChild(i);
      }
      slide.appendChild(caption);
    }
    if (photo.inset) {
      var inset = document.createElement('img');
      inset.className = 'k-inset';
      inset.alt = '';
      inset.src = photo.inset;
      slide.appendChild(inset);
    }
    var title = Kiosk.parameters.title;
    if (photo.title && title) {
      var main = document.createElement('p');
      main.className = 'k-maintitle';
      main.textContent = title;
      slide.appendChild(main);
    }
    [].forEach.call(box.children, leave);
    box.appendChild(slide);
  }

  // Fade a slide out, then remove it. A slide already fading out keeps its
  // course. A slide still fading in starts its fade out from the opacity it
  // has reached (k-fade-out sets only its end), and the fade in it leaves is
  // cancelled, which fires no animationend. No element inside a slide is
  // animated. test_kiosk_slideshow.py checks each.
  function leave(old) {
    if (old.classList.contains('k-leaving')) return;
    old.style.opacity = getComputedStyle(old).opacity;
    old.classList.add('k-leaving');
    old.addEventListener('animationend', function () { old.remove(); });
  }

  function titleOnly() {
    show({photo: titleSlide, title: true, next: {mode: 'slide', file: ''}});
  }

  function poll() {
    var query = Object.assign({}, next, {query: 'slide.next'});
    var parameters = Kiosk.parameters;
    if (parameters.subdir) query.subdir = parameters.subdir;
    if (parameters.classids && parameters.classids.length) query.classids = parameters.classids.join(',');
    return Kiosk.action(query).then(function (data) {
      if (data.hasOwnProperty('photo')) show(data.photo);
      else titleOnly();
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    box = document.querySelector('[data-slides]');
    titleSlide = box.dataset.titleSlide;
    titleOnly();
    // Every 10 s (slideshow.js:122).
    Kiosk.poll(poll, 10000, 'every');
  });
}());
