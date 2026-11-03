// The award pages (#45): the awards editor, judging and the presentation
// console. Loaded before Alpine, so the stores and components register on
// `alpine:init`.
'use strict';

// Each page keeps one sheet, which a control opens and whose hx-get loads
// its body; a body names itself when it arrives.
function awardSheet(name) {
  return {
    open(title, ev) {
      const body = document.getElementById(`${name}-sheet-body`);
      if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
      Alpine.store(name).title = title;
      Alpine.store('ui').open(name, ev);
    },
    show(title) {
      Alpine.store(name).title = title;
      if (Alpine.store('ui').sheet !== name) Alpine.store('ui').open(name);
    },
  };
}

window.awardsPage = awardSheet('awards');
window.judging = awardSheet('judging');

document.addEventListener('alpine:init', () => {
  Alpine.store('awards', { title: '' });
  Alpine.store('judging', { title: '' });
  // The key of the award on the kiosk, which the console's list marks.
  Alpine.store('presenter', { key: '' });

  // The console's type and group chips, as the two selects of
  // awards-presentation.php filter its list. The Next button posts the same
  // chips, and the server picks the next award from the list they show.
  Alpine.data('presenterFilters', () => ({
    type: '',
    scope: 'all',
    shows(type, group, subgroup) {
      if (this.type !== '' && Number(this.type) !== type) return false;
      if (this.scope === 'pack') return group === 0 && subgroup === 0;
      if (this.scope.startsWith('g')) return group === Number(this.scope.slice(1));
      if (this.scope.startsWith('s')) return subgroup === Number(this.scope.slice(1));
      return true;
    },
    rows: [],
    init() {
      const data = document.getElementById('presenter-rows');
      if (data) this.rows = JSON.parse(data.textContent);
    },
    get none() {
      return !this.rows.some((row) => this.shows(...row));
    },
  }));
});

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  const id = e.detail.target && e.detail.target.id;
  if (id === 'awards-sheet-body' || id === 'judging-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
