// Race check-in (#42). Loaded before Alpine, so the store and the components
// register on `alpine:init`. The photo and crop sheets are in photo-sheets.js.
'use strict';

window.checkin = {
  // Open the page's one sheet from a control, whose hx-get loads its body.
  open(title, ev) {
    const body = document.getElementById('checkin-sheet-body');
    if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
    Alpine.store('checkin').title = title;
    Alpine.store('ui').open('checkin', ev);
  },

  // A sheet body names itself when it arrives, and opens the sheet when a
  // reply brought it, such as the D36 question after a pass.
  show(title) {
    Alpine.store('checkin').title = title;
    if (Alpine.store('ui').sheet !== 'checkin') Alpine.store('ui').open('checkin');
  },

  sheetOpen() {
    return Boolean(window.Alpine) && Alpine.store('ui').sheet === 'checkin';
  },

  // The class's next car number, from the server, for a field the
  // volunteer has not typed in.
  suggest(number) {
    const field = document.getElementById('f-carno');
    const touched = document.getElementById('f-touched');
    if (field && touched && touched.value !== '1') field.value = number;
  },

  asset(name) {
    const root = document.querySelector('[data-checkin]');
    return root ? root.dataset[name] : '';
  },

  // The QR code for another phone, drawn by the vendored qrcode-generator.
  qr(el, url) {
    derbynet.loadScript(this.asset('qrcode')).then(() => {
      const code = qrcode(0, 'M');
      code.addData(url);
      code.make();
      el.innerHTML = code.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
      const svg = el.querySelector('svg');
      if (svg) {
        svg.setAttribute('aria-hidden', 'true');
        svg.classList.add('size-full');
      }
    });
  },
};

document.addEventListener('alpine:init', () => {
  Alpine.store('checkin', { title: '' });
});

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'checkin-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
