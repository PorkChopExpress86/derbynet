// Shared Alpine state for the #39 prototypes: one sheet at a time, focus return, and the
// flag that pauses htmx polling while a sheet is open or an input has focus.
document.addEventListener('alpine:init', () => {
  Alpine.store('ui', {
    sheet: null,
    opener: null,
    racer: { car: '', first: '', last: '', partition: '', passed: false, photo: true },
    open(name, ev) {
      this.opener = (ev && ev.currentTarget) || document.activeElement;
      this.sheet = name;
    },
    close() {
      const opener = this.opener;
      this.sheet = null;
      this.opener = null;
      if (opener) setTimeout(() => opener.focus(), 0);
    },
    modal() {
      return this.sheet !== null;
    },
    editing() {
      const el = document.activeElement;
      return this.sheet !== null || !!(el && el.matches('input, select, textarea'));
    },
  });
});
