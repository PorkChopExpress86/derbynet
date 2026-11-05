// Award Presentations (#52), rebuilt from award-presentations-kiosk.js.
// `award.current` 500 ms after each reply. A new award shows its name; on
// reveal the recipient and photos rise in, and confetti plays for 20 s
// unless the kiosk's `confetti` parameter is false. Loading the page
// changes nothing on the server (S28).
(function () {
  'use strict';

  var root, video;
  var shownKey = null;
  var revealed = true;
  var confetti = true;
  var stopConfetti = 0;

  function el(name) {
    return root.querySelector('[data-' + name + ']');
  }

  function text(name, value) {
    var found = el(name);
    found.textContent = value || '';
    found.hidden = !value;
  }

  function resetVideo() {
    clearTimeout(stopConfetti);
    video.pause();
    video.currentTime = 0;
    video.hidden = true;
  }

  function hideReveal() {
    el('reveal').hidden = true;
    el('photos').hidden = true;
    el('reveal').classList.remove('k-shown');
    el('photos').classList.remove('k-shown');
  }

  function show(data) {
    if (!data.hasOwnProperty('current')) {
      el('award-name').textContent = 'Award Presentation';
      hideReveal();
      return;
    }
    var award = data.current;
    if (shownKey !== award.key) {
      hideReveal();
      el('award-name').textContent = award.awardname;
      el('award-name').animate([{opacity: 0, transform: 'translateY(3vh)'}, {opacity: 1, transform: 'none'}], {duration: 600, easing: 'ease-out'});
      text('carnumber', String(award.carnumber || ''));
      text('recipient', award.recipient);
      text('carname', award.carname);
      text('subgroup', award.subgroup);
      var photos = el('photos');
      photos.textContent = '';
      [award.headphoto, award.carphoto].forEach(function (url) {
        if (url) {
          var img = document.createElement('img');
          img.src = url;
          img.alt = '';
          photos.appendChild(img);
        }
      });
      revealed = false;
    }
    if (!award.reveal) {
      hideReveal();
      resetVideo();
    } else if (!revealed) {
      el('reveal').hidden = false;
      el('reveal').classList.add('k-shown');
      if (el('photos').children.length) {
        el('photos').hidden = false;
        el('photos').classList.add('k-shown');
      }
      if (confetti && shownKey !== null) {
        setTimeout(function () {
          video.hidden = false;
          video.play().catch(function () {});
        }, 500);
        stopConfetti = setTimeout(function () {
          video.animate([{opacity: 1}, {opacity: 0}], {duration: 500}).onfinish = resetVideo;
        }, 20500);
      }
    }
    revealed = award.reveal;
    shownKey = award.key;
  }

  function poll() {
    return Kiosk.action({query: 'award.current'}).then(function (data) {
      if (data['kiosk-setting'] && data['kiosk-setting'].reload) location.reload();
      show(data);
    });
  }

  function setConfetti(parameters) {
    confetti = parameters.confetti !== false;
  }

  document.addEventListener('DOMContentLoaded', function () {
    root = document.querySelector('[data-award]');
    video = root.querySelector('[data-confetti]');
    setConfetti(Kiosk.parameters);
    Kiosk.onParameters = setConfetti;
    // 500 ms after each reply (award-presentations-kiosk.js:47-49).
    Kiosk.poll(poll, 500, 'after');
    Kiosk.start();
  });
}());
