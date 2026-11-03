// The rounds playlist page (#44). Loaded before Alpine, so the store
// registers on `alpine:init`.
'use strict';

window.playlistPage = {
  // Open the page's one sheet from a control, whose hx-get loads its body.
  open(title, ev) {
    const body = document.getElementById('playlist-sheet-body');
    if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
    Alpine.store('playlist').title = title;
    Alpine.store('ui').open('playlist', ev);
  },

  // A sheet body names itself when it arrives.
  show(title) {
    Alpine.store('playlist').title = title;
    if (Alpine.store('ui').sheet !== 'playlist') Alpine.store('ui').open('playlist');
  },
};

document.addEventListener('alpine:init', () => {
  Alpine.store('playlist', { title: '' });
});

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'playlist-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
