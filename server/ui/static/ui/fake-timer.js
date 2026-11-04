// The fake timer (#50): a port of website/js/fake-timer.js for the phone
// page. The PHP file cannot be served as it is: it posts to the relative
// `action.php`, which from /timer/fake/ is /timer/fake/action.php, and it
// drives the PHP page's jQuery table. This port keeps its messages, fields
// and timings: HELLO, then IDENTIFIED only once HELLO succeeds, with the lane
// count, `FakeTimer` and `Fake Timer`; a HEARTBEAT with confirmed=1 every
// second; STARTED with confirmed=1; FINISHED with each lane's text; heat-ready
// acted on after 1 s; auto mode starting 5 s after it and stopping each lane
// at 2.8 to 3.8 s. The browser tests in test_timer.py drive it.
// Loaded before Alpine, so the component registers on `alpine:init`.
'use strict';

document.addEventListener('alpine:init', () => {
  // Each lane: running 0 masked off, 1 running, 2 stopped.
  Alpine.data('fakeTimer', (actionUrl, homeUrl, laneCount) => ({
    lanes: laneCount,
    times: [],
    summary: 'Not racing.',
    ready: false,
    running: false,
    auto: false,
    // Seconds between heat ready and the start in auto mode.
    autoPace: 5,
    pendingHeat: null,
    heatReadyTimer: 0,
    preparedTimer: 0,
    ticker: 0,
    startedAt: 0,

    init() {
      this.reset();
      this.send({ message: 'HELLO' }).then((ok) => ok && this.send({
        message: 'IDENTIFIED',
        lane_count: String(this.lanes),
        timer: 'FakeTimer',
        human: 'Fake Timer',
      }));
      setInterval(() => this.send({ message: 'HEARTBEAT', confirmed: '1' }), 1000);
    },

    reset() {
      this.times = Array.from({ length: this.lanes }, () => ({ running: 1, text: '0.000' }));
      this.ready = false;
      this.pendingHeat = null;
    },

    // True when the reply came, as jQuery's `success` callback runs: only
    // then does PHP's send_hello() send IDENTIFIED.
    async send(fields) {
      const body = new URLSearchParams({ action: 'timer-message', ...fields });
      let reply;
      try {
        reply = await fetch(actionUrl, { method: 'POST', body, credentials: 'same-origin' });
      } catch (e) {
        return false;
      }
      if (!reply.ok) return false;
      const text = await reply.text();
      if ((reply.headers.get('Content-Type') || '').includes('json')) {
        let data = {};
        try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
        if (data.cease) window.location.href = homeUrl;
        return false;
      }
      this.process(new DOMParser().parseFromString(text, 'text/xml'));
      return true;
    },

    // <heat-ready lane-mask= class= round= roundid= heat=/> and <abort/>.
    process(doc) {
      if (doc.getElementsByTagName('abort').length > 0) {
        this.summary = 'Not racing.';
        this.reset();
        clearTimeout(this.heatReadyTimer);
        this.heatReadyTimer = 0;
        clearTimeout(this.preparedTimer);
        this.preparedTimer = 0;
      }
      const heat = doc.getElementsByTagName('heat-ready')[0];
      if (!heat) return;
      const attr = (name) => heat.getAttribute(name) || '';
      this.summary = `Ready for heat ${attr('heat')} of ${attr('class')}, round ${attr('round')}`;
      const pending = this.pendingHeat;
      if (pending && pending.roundid === attr('roundid') && pending.heat === attr('heat') &&
          pending.mask === attr('lane-mask')) {
        return;
      }
      this.pendingHeat = { roundid: attr('roundid'), heat: attr('heat'), mask: attr('lane-mask') };
      if (this.heatReadyTimer === 0) {
        this.heatReadyTimer = setTimeout(() => {
          if (this.pendingHeat) this.prepare(Number(this.pendingHeat.mask));
          this.heatReadyTimer = 0;
        }, 1000);
      }
    },

    prepare(mask) {
      this.times.forEach((lane, i) => {
        const used = (mask & (1 << i)) !== 0;
        lane.running = used ? 1 : 0;
        lane.text = used ? '0.000' : '---';
      });
      this.ready = true;
      if (this.auto) {
        this.preparedTimer = setTimeout(() => { this.preparedTimer = 0; this.start(); },
                                        this.autoPace * 1000);
      }
    },

    start() {
      if (this.running) return;
      this.startedAt = Date.now();
      this.running = true;
      this.ticker = setInterval(() => {
        const t = ((Date.now() - this.startedAt) / 1000).toFixed(3);
        for (const lane of this.times) {
          if (lane.running === 1) lane.text = t;
        }
      }, 10);
      this.send({ message: 'STARTED', confirmed: '1' });
      this.ready = false;
      if (this.auto) {
        // Each lane stops at a random time.
        this.times.forEach((lane, i) => {
          const when = (2.8 + Math.random()) * 1000;
          setTimeout(() => this.stop(i, (when / 1000).toFixed(3)), when);
        });
      }
    },

    stopNow(i) {
      this.stop(i, this.times[i].text);
    },

    stop(i, text) {
      const lane = this.times[i];
      if (lane && lane.running === 1 && this.running) {
        lane.text = text;
        lane.running = 2;
      }
      if (!this.times.some((l) => l.running === 1)) this.end();
    },

    end() {
      if (!this.running) return;
      clearInterval(this.ticker);
      this.running = false;
      const fields = { message: 'FINISHED' };
      this.times.forEach((lane, i) => { fields[`lane${i + 1}`] = lane.text; });
      this.summary = 'Not racing.';
      this.send(fields);
    },

    toggleAuto() {
      this.auto = !this.auto;
      if (this.auto && this.ready) this.start();
    },
  }));
});
