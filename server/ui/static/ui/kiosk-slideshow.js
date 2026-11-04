// Slideshow (#52), rebuilt from slideshow.js. `slide.next` every 10 s, each
// reply naming the next query: the title slide, the slides, the subdir's
// slides, then the racers with photos. Each new slide fades in over the one
// before. The `title`, `subdir` and `classids` parameters follow the kiosk.
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
    box.appendChild(slide);
    while (box.children.length > 2) box.removeChild(box.firstChild);
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
    Kiosk.poll(poll, 10000, 'every');
  });
}());
