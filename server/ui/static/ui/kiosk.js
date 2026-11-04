// What every kiosk page shares (#52): the kiosk poll, the content polls,
// the one entry point that runs them at once, the replay handshake and the
// lost-contact banner.
//
// - The kiosk poll is `KioskPoller` of website/js/kiosk-poller.js: every 5 s
//   it asks `poll.kiosk` for this kiosk's setting, reloads when the page
//   string differs from the one loaded or a reload was asked for, and
//   otherwise hands the parameters to the page (`Kiosk.onParameters`).
//   Preview and standalone pages have no address and no kiosk poll.
// - A page registers its content poll with `Kiosk.poll(fetch, ms, mode)`,
//   keeping the interval its PHP page used: `after` queues the next fetch
//   `ms` after each reply, as now-racing-poller.js does; `every` runs on a
//   fixed timer.
// - `kioskNudge()` runs the kiosk poll and every content poll at once. #56
//   calls it when the push stream says something changed (D21, ADR-15); a
//   browser with no EventSource keeps the polls above.
// - Inside replay.php, `replay-started` pauses the page's animations and its
//   content polls, and `replay-ended` resumes them, as now-racing.js does.
(function () {
  'use strict';

  var config = JSON.parse(document.getElementById('kiosk-config').textContent);
  var polls = [];
  var unloading = false;
  var hideFailure = 0;

  var Kiosk = {
    config: config,
    parameters: config.parameters || {},
    paused: false,
    onParameters: null,
    onReplay: null,
  };
  window.Kiosk = Kiosk;

  window.addEventListener('beforeunload', function () { unloading = true; });

  function failure(status, text) {
    if (unloading) return;
    var banner = document.getElementById('ajax-failure');
    if (!banner) return;
    banner.querySelector('[data-ajax-status]').textContent =
      status + ' (' + (status === 0 ? 'likely timeout' : text) + ')';
    banner.hidden = false;
    clearTimeout(hideFailure);
    hideFailure = setTimeout(function () { banner.hidden = true; }, 10000);
  }

  function contact() {
    var banner = document.getElementById('ajax-failure');
    if (banner) banner.hidden = true;
  }

  // GET action.php with `query`, resolving to the parsed reply. A failure
  // shows the lost-contact banner and rejects.
  Kiosk.action = function (query) {
    var url = config.action + '?' + new URLSearchParams(query).toString();
    return fetch(url, {credentials: 'same-origin', headers: {Accept: 'application/json'}})
      .then(function (response) {
        if (!response.ok) {
          failure(response.status, response.statusText);
          throw new Error('HTTP ' + response.status);
        }
        contact();
        return response.json();
      }, function (error) {
        failure(0, '');
        throw error;
      });
  };

  // GET a fragment of this server, as text; 204 resolves to null.
  Kiosk.fragment = function (url) {
    return fetch(url, {credentials: 'same-origin'}).then(function (response) {
      if (!response.ok) {
        failure(response.status, response.statusText);
        throw new Error('HTTP ' + response.status);
      }
      contact();
      return response.status === 204 ? null : response.text();
    }, function (error) {
      failure(0, '');
      throw error;
    });
  };

  function reload() {
    location.reload();
  }

  function setName(name) {
    document.querySelectorAll('[data-kiosk-name]').forEach(function (el) {
      el.textContent = name || '';
    });
  }

  Kiosk.pollKiosk = function () {
    if (!config.address) return Promise.resolve();
    return Kiosk.action({query: 'poll.kiosk', address: config.address}).then(function (data) {
      if (data.cease) {
        location.href = config.home;
        return;
      }
      var setting = data['kiosk-setting'];
      setName(setting.name);
      if (setting.page !== config.loaded || setting.reload) {
        reload();
        return;
      }
      var parameters = {};
      try { parameters = JSON.parse(setting.parameters) || {}; } catch (e) { parameters = {}; }
      Kiosk.parameters = parameters;
      if (Kiosk.onParameters) Kiosk.onParameters(parameters);
    }).catch(function () {});
  };

  // Register a content poll. `fetch` returns a promise (or nothing).
  Kiosk.poll = function (fetchFn, ms, mode) {
    var poll = {fetch: fetchFn, ms: ms, mode: mode || 'after', timer: 0, busy: false};
    polls.push(poll);
    if (poll.mode === 'every') {
      poll.timer = setInterval(function () { run(poll); }, ms);
    }
    return poll;
  };

  function queue(poll) {
    if (poll.mode !== 'after') return;
    clearTimeout(poll.timer);
    poll.timer = setTimeout(function () { run(poll); }, poll.ms);
  }

  function run(poll) {
    if (poll.busy) return;
    if (Kiosk.paused) {
      queue(poll);
      return;
    }
    poll.busy = true;
    var done = function () { poll.busy = false; queue(poll); };
    Promise.resolve().then(poll.fetch).then(done, done);
  }

  // The one entry point: fetch every poll endpoint now.
  window.kioskNudge = function () {
    Kiosk.pollKiosk();
    polls.forEach(run);
  };

  Kiosk.start = function () {
    polls.forEach(run);
  };

  window.addEventListener('message', function (e) {
    if (e.data === 'replay-started') {
      Kiosk.paused = true;
      document.getAnimations().forEach(function (a) { a.pause(); });
    } else if (e.data === 'replay-ended') {
      Kiosk.paused = false;
      document.getAnimations().forEach(function (a) { a.play(); });
    } else {
      return;
    }
    if (Kiosk.onReplay) Kiosk.onReplay(Kiosk.paused);
  });

  // Runs `change`, then glides each element from where it was to where it
  // is, and drops a newly shown one in: Svelte's animate:flip, as the #71
  // spike's flip() helper did with the Web Animations API.
  Kiosk.flip = function (elements, change) {
    var els = Array.prototype.slice.call(elements);
    var before = els.map(function (el) { return el.offsetParent ? el.getBoundingClientRect().top : null; });
    change();
    els.forEach(function (el, i) {
      if (!el.offsetParent) return;
      if (before[i] === null) {
        el.animate([{transform: 'translateY(-40px) scaleY(.4)', opacity: 0}, {transform: 'none', opacity: 1}],
                   {duration: 500, easing: 'ease-out'});
        return;
      }
      var dy = before[i] - el.getBoundingClientRect().top;
      if (dy) el.animate([{transform: 'translateY(' + dy + 'px)'}, {transform: 'none'}], {duration: 500, easing: 'ease-out'});
    });
  };

  document.addEventListener('DOMContentLoaded', function () {
    if (config.address) setInterval(Kiosk.pollKiosk, 5000);
  });
}());
