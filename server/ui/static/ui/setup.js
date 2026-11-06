// The set-up page (#48). Loaded before Alpine, so the store registers on
// `alpine:init`. The page has one sheet, whose body a control loads: a
// purge's confirmation, the snapshot import's, or the fake roster form.
'use strict';

window.setupPage = {
  open(title, ev) {
    const body = document.getElementById('setup-sheet-body');
    if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
    Alpine.store('setup').title = title;
    Alpine.store('ui').open('setup', ev);
  },
};

document.addEventListener('alpine:init', () => {
  Alpine.store('setup', { title: '' });
});

document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'setup-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
