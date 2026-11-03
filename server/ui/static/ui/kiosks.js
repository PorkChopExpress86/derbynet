// The kiosk dashboard and the scene editor (#46). Loaded before Alpine, so
// the stores and components register on `alpine:init`.
'use strict';

// Each page keeps one sheet, which a control opens and whose hx-get loads
// its body; a body names itself when it arrives.
function kioskSheetOf(name, store) {
  return {
    open(title, ev) {
      const body = document.getElementById(`${name}-sheet-body`);
      if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
      Alpine.store(store).title = title;
      Alpine.store('ui').open(name, ev);
    },
    show(title) {
      Alpine.store(store).title = title;
      if (Alpine.store('ui').sheet !== name) Alpine.store('ui').open(name);
    },
  };
}

window.kioskSheet = kioskSheetOf('kiosks', 'kioskSheet');
window.sceneSheet = kioskSheetOf('scenes', 'sceneSheet');

document.addEventListener('alpine:init', () => {
  Alpine.store('kioskSheet', { title: '' });
  Alpine.store('sceneSheet', { title: '' });

  // The board's "Last contact: Ns ago", counted here each second from the
  // contact time the board carries, so the polled board stays the same
  // between contacts and its poll can answer 204. The server's clock at
  // page load sets the offset from this device's clock.
  Alpine.data('kioskDashboard', (serverNow, quiet) => ({
    offset: serverNow - Date.now() / 1000,
    init() {
      this.tick();
      setInterval(() => this.tick(), 1000);
    },
    tick() {
      const now = Date.now() / 1000 + this.offset;
      for (const el of document.querySelectorAll('[data-last-contact]')) {
        const age = Math.max(0, Math.floor(now - Number(el.dataset.lastContact)));
        el.hidden = age <= quiet;
        el.textContent = `Last contact: ${age}s ago`;
      }
    },
    // "New Kiosk Window", as kiosk-dashboard.js opens it.
    newWindow(url) {
      const address = `localhost ${100000 + Math.floor(Math.random() * 100000)}`;
      window.open(`${url}?address=${encodeURIComponent(address)}`, '_blank');
    },
  }));
});

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  const id = e.detail.target && e.detail.target.id;
  if (id === 'kiosks-sheet-body' || id === 'scenes-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
