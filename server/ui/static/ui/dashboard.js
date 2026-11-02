// The race dashboard (#41). Loaded before Alpine, so the store registers on
// `alpine:init`.
'use strict';

window.dashboard = {
  // Open the dashboard's one sheet. The control's hx-get or hx-post loads
  // its body, so the sheet shows "Loading" until it arrives.
  open(title, ev) {
    const body = document.getElementById('dash-sheet-body');
    if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
    Alpine.store('dash').title = title;
    Alpine.store('ui').open('dash', ev);
  },

  // Open the browser timer, or bring it to the front when it already runs,
  // as `open_timer_window()` in website/js/timer-alive.js does. The timer
  // page answers on BroadcastChannel `timer-alive` (#50).
  lastHeard: 0,
  openTimer() {
    if (Date.now() - this.lastHeard < 1500) {
      const focus = new BroadcastChannel('timer-focus');
      focus.postMessage({ focus: true });
      focus.close();
      return;
    }
    window.open('timer.php', 'timer3', 'menubar=off,toolbar=off,height=800,width=400,location=off');
  },
};

try {
  new BroadcastChannel('timer-alive').onmessage = () => { dashboard.lastHeard = Date.now(); };
} catch (e) {
  // No BroadcastChannel: the Timer button always opens a window.
}

document.addEventListener('alpine:init', () => {
  Alpine.store('dash', { title: '' });
});

// The ages of the timer and replay status count up between polls. The
// server sends the age when it renders, and a 204 poll leaves it counting.
function formatAge(seconds) {
  if (seconds < 120) return `${seconds} s`;
  if (seconds < 7200) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h`;
}
setInterval(() => {
  for (const el of document.querySelectorAll('[data-age]')) {
    const next = Number(el.dataset.age) + 1;
    el.dataset.age = String(next);
    el.textContent = formatAge(next);
  }
}, 1000);

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'dash-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
