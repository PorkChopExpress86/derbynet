// The timer status and timer test pages (#50). Loaded before Alpine, so the
// component registers on `alpine:init`.
'use strict';

document.addEventListener('alpine:init', () => {
  // The seconds since the timer's last contact count up here, from the epoch
  // second every status poll's reply carries in X-Timer-Contact, so a
  // heartbeat never changes the polled card and its poll answers 204. The
  // server's clock at page load sets the offset from this device's clock.
  //
  // With a log URL, the timer log tails as poll_for_timer_log() in
  // website/js/timer-test.js:332-366 does: read from the last size seen,
  // from 0 at once; again after 50 ms when data came, and after
  // twice the wait, up to 1 s, when none did (browser test
  // test_the_log_tail_backs_off). Unlike PHP it stops while the page is
  // hidden, as #50's risks ask, since every manager watching it polls.
  Alpine.data('timerStatus', (serverNow, contact, logUrl) => ({
    offset: serverNow - Date.now() / 1000,
    contact,
    now: Date.now() / 1000,
    path: '',
    seek: 0,
    wait: 50,
    tailing: false,
    init() {
      setInterval(() => { this.now = Date.now() / 1000; }, 1000);
      if (logUrl && this.$refs.log) {
        document.addEventListener('visibilitychange', () => this.tail());
        this.tail();
      }
    },
    heard(event) {
      const xhr = event.detail.xhr;
      if (!xhr || event.detail.elt.id !== 'timer-status') return;
      const header = xhr.getResponseHeader('X-Timer-Contact');
      if (header !== null) this.contact = header === '' ? null : Number(header);
    },
    ageText() {
      if (this.contact === null) return 'No contact yet';
      const age = Math.max(0, Math.floor(this.now + this.offset - this.contact));
      return `Last contact ${age} s ago`;
    },
    tail() {
      if (this.tailing || document.hidden) return;
      this.tailing = true;
      this.readLog();
    },
    async readLog() {
      if (document.hidden) {
        this.tailing = false;
        return;
      }
      try {
        const reply = await fetch(`${logUrl}?seek=${this.seek}`, { credentials: 'same-origin' });
        if (!reply.ok) throw new Error(String(reply.status));
        const data = await reply.json();
        if (data.path) this.path = data.path;
        if (data.data) {
          this.append(data.data);
          this.wait = 50;
        } else {
          this.wait = Math.min(2 * this.wait, 1000);
        }
        this.seek = data.size || 0;
      } catch (e) {
        this.wait = 1000;
      }
      setTimeout(() => this.readLog(), this.wait);
    },
    // The whole log stays on the page, as in PHP.
    append(text) {
      const log = this.$refs.log;
      const atEnd = log.scrollTop + log.clientHeight >= log.scrollHeight - 4;
      log.append(document.createTextNode(text));
      if (atEnd) log.scrollTop = log.scrollHeight;
    },
  }));
});
