// The racing groups editor (#43). Loaded before Alpine, so the store and the
// components register on `alpine:init`.
'use strict';

window.groups = {
  // Open the page's one sheet from a control, whose hx-get loads its body.
  open(title, ev) {
    const body = document.getElementById('groups-sheet-body');
    if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
    Alpine.store('groups').title = title;
    Alpine.store('ui').open('groups', ev);
  },

  // A sheet body names itself when it arrives, and opens the sheet when the
  // page loaded it, as check-in's "Edit partitions" link does.
  show(title) {
    Alpine.store('groups').title = title;
    if (Alpine.store('ui').sheet !== 'groups') Alpine.store('ui').open('groups');
  },
};

document.addEventListener('alpine:init', () => {
  Alpine.store('groups', { title: '' });

  // The formation rule. A change opens the confirm sheet; closing the sheet
  // without changing the rule puts the rule in force back (D31).
  Alpine.data('groupsRule', (rule) => ({
    rule,
    init() {
      this.$watch('$store.ui.sheet', (sheet) => { if (sheet === null) this.reset(); });
    },
    reset() {
      this.$el.querySelectorAll('input[name="rule"]').forEach((radio) => {
        radio.checked = radio.value === this.rule;
      });
    },
  }));
});

// /groups/partitions/ opens the editor with the partitions sheet open.
document.addEventListener('alpine:initialized', () => {
  const editor = document.querySelector('[data-open-sheet]');
  if (!editor) return;
  htmx.ajax('GET', editor.dataset.openSheet, { target: '#groups-sheet-body', swap: 'innerHTML' });
});

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'groups-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
