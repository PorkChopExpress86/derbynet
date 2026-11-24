// Shared front-end code for the manager UI (#39, #40). Loaded before Alpine
// and htmx, so the Alpine components below register on `alpine:init`.
'use strict';

window.derbynet = {
  // The htmx poll filter: `hx-trigger="every 2s [derbynet.pollable(this)]"`,
  // which the {% poll_trigger %} tag writes. A poll waits while a sheet is
  // open or while a field of the polled section has focus, so the swap never
  // resets what the user is doing (#39 htmx pattern 3).
  pollable(section) {
    if (window.Alpine && Alpine.store('ui').sheet !== null) return false;
    const focused = document.activeElement;
    return !(focused && section.contains(focused) &&
             focused.matches('input, select, textarea, [contenteditable]'));
  },

  // A client-side toast, for a failure no server toast covers: a network
  // error or a 5xx.
  toast(message) {
    const template = document.getElementById('toast-template');
    const region = document.getElementById('toasts');
    if (!template || !region) return;
    const toast = template.content.firstElementChild.cloneNode(true);
    toast.querySelector('[data-message]').textContent = message;
    region.append(toast);
  },

  loadScript(src) {
    return new Promise((resolve, reject) => {
      const existing = document.querySelector(`script[src="${src}"]`);
      if (existing) {
        if (existing.dataset.loaded) resolve();
        else existing.addEventListener('load', resolve);
        return;
      }
      const script = document.createElement('script');
      script.src = src;
      script.addEventListener('load', () => { script.dataset.loaded = '1'; resolve(); });
      script.addEventListener('error', reject);
      document.head.append(script);
    });
  },
};

// D132 (#383): Enter or Space held down as a sheet opens or closes does nothing
// where the focus lands. Chromium clicks a button or submits a form on each
// of Enter's auto-repeat keydowns, and clicks a button on Space's keyup once
// a repeat has landed on it (D131). A sheet moves the focus into itself as
// it opens (sheet.html) and back to its opener as it closes, so a held
// Enter's repeats submitted the field or pressed the Close button that took
// the focus, or reopened the sheet from its opener
// (test_a_held_enter_on_a_sheet_opener_opens_it_once,
// test_an_enter_held_as_a_sheet_saves_opens_nothing,
// test_a_space_held_as_the_mouse_saves_opens_nothing). PHP's kiosk dashboard
// takes such a repeat: its naming dialog focuses the name field 210 ms after
// it opens (modal.js:59), and a repeat there submits the name and closes it
// (kiosk-dashboard.js:255, 262; #383's php-scratch probe). Enter or Space down when a
// sheet opens or closes has its repeats cancelled until its keyup, and a fresh
// press acts as before. An auto-repeat comes only while its key is down,
// so carrying both keys at each open and close holds no key that is up:
// its next keydown is a fresh press.
const carriedKeys = new Set();
document.addEventListener('keydown', (e) => {
  if (!e.repeat) carriedKeys.delete(e.key);
  else if (carriedKeys.has(e.key)) e.preventDefault();
}, true);
function carryHeldKeys() {
  carriedKeys.add('Enter');
  carriedKeys.add(' ');
}
// #400: a form in a sheet that submits carries Enter too, so a held Enter
// submits it once, from a field or a submit button. Chromium submits again
// on each auto-repeat, so `hx-sync` replace sent a POST per repeat, aborting
// the one before, and drop, or no hx-sync, sent one when a repeat came after
// a reply that kept the focus in the form, such as a refusal
// (test_a_held_enter_in_a_sheet_form_submits_once,
// test_a_held_enter_after_a_refusal_sends_nothing_more,
// test_a_held_enter_adds_a_racer_and_saves_nothing_more). PHP's naming
// dialog posts once for the same press: it closes before its POST
// (kiosk-dashboard.js:261-263), and the repeats land on the page's body
// (#400's php-scratch run). A form off any sheet keeps its repeats
// (test_a_held_enter_in_a_form_off_a_sheet_still_repeats).
document.addEventListener('submit', (e) => {
  if (e.target.closest('[data-sheet]')) carriedKeys.add('Enter');
}, true);

document.addEventListener('alpine:init', () => {
  // One sheet at a time (#39: one level only). Focus returns to the control
  // that opened it. When a save's reply swapped the opener out, the page
  // hears `ui:opener-gone` with the old opener, and may focus its
  // swapped-in copy (D132, #383, kiosks.js).
  Alpine.store('ui', {
    sheet: null,
    opener: null,
    open(name, ev) {
      this.opener = (ev && ev.currentTarget) || document.activeElement;
      this.sheet = name;
      carryHeldKeys();
    },
    close() {
      const opener = this.opener;
      this.sheet = null;
      this.opener = null;
      // No opener: no sheet was open, as on an Escape (base.html), so a key
      // held then keeps its repeats
      // (test_a_close_with_no_sheet_open_leaves_a_held_key_alone).
      if (!opener) return;
      carryHeldKeys();
      setTimeout(() => {
        if (opener.isConnected) opener.focus();
        else document.dispatchEvent(new CustomEvent('ui:opener-gone', { detail: { opener } }));
      }, 0);
    },
    modal() {
      return this.sheet !== null;
    },
  });

  // A switch. With a URL it flips at once and posts `name=1|0`; when the
  // request fails it flips back (#39 pattern 5), and the server's 422 toast
  // explains why. Without a URL it only dispatches `switch-change`.
  Alpine.data('uiSwitch', (on, url, name) => ({
    on,
    init() {
      this.$el.addEventListener('htmx:afterRequest', (e) => {
        if (e.detail.elt === this.$el && !e.detail.successful) this.on = !this.on;
      });
    },
    toggle() {
      this.on = !this.on;
      this.$dispatch('switch-change', { name, on: this.on });
      if (url) {
        htmx.ajax('POST', url, {
          source: this.$el, target: '#toasts', swap: 'beforeend',
          values: { [name]: this.on ? '1' : '0' },
        });
      }
    },
  }));

  // A stepper: a number field with large minus and plus buttons.
  Alpine.data('uiStepper', (value, min, max) => ({
    value,
    step(delta) {
      const next = Number(this.value || 0) + delta;
      this.value = Math.min(max, Math.max(min, next));
      this.$nextTick(() => this.$refs.input.dispatchEvent(new Event('change', { bubbles: true })));
    },
  }));

  // A toast. Errors stay until closed. Others leave after 8 s, and wait while
  // the pointer or the focus is on them.
  Alpine.data('uiToast', (level) => ({
    timer: null,
    init() {
      if (level !== 'error') this.arm();
    },
    arm() {
      if (level === 'error') return;
      this.timer = setTimeout(() => this.dismiss(), 8000);
    },
    hold() {
      clearTimeout(this.timer);
    },
    dismiss() {
      this.$root.remove();
    },
  }));

  // A sortable list. Rows move by their drag handle (SortableJS, loaded only
  // when a page has such a list) or by Move up and Move down in the row's
  // menu, so drag is never the only way (#39). Each move posts the new order
  // as `name=<id>,<id>,...` when the list has a URL, and dispatches `reorder`.
  // A list nested in another's rows names its own handles with
  // `data-handle-selector`, so a drag moves only rows of its own list.
  Alpine.data('uiSortable', (url, name) => ({
    status: '',
    init() {
      const start = () => {
        Sortable.create(this.$refs.list, {
          handle: this.$el.dataset.handleSelector || '[data-handle]',
          animation: 150,
          onEnd: (e) => { if (e.oldIndex !== e.newIndex) this.changed(e.item); },
        });
      };
      if (window.Sortable) start();
      else derbynet.loadScript(this.$el.dataset.sortableSrc).then(start);
    },
    move(item, delta) {
      const sibling = delta < 0 ? item.previousElementSibling : item.nextElementSibling;
      if (!sibling) {
        this.status = `${item.dataset.label} is already ${delta < 0 ? 'first' : 'last'}.`;
        return;
      }
      if (delta < 0) sibling.before(item); else sibling.after(item);
      this.changed(item);
      const menu = item.querySelector('[data-row-menu]');
      if (menu) menu.focus();
    },
    changed(item) {
      const rows = [...this.$refs.list.children];
      const order = rows.map((row) => row.dataset.id);
      this.status = `${item.dataset.label} moved to position ${rows.indexOf(item) + 1} of ${rows.length}.`;
      this.$dispatch('reorder', { order });
      if (url) {
        htmx.ajax('POST', url, {
          source: this.$el, target: '#toasts', swap: 'beforeend',
          values: { [name]: order.join(',') },
        });
      }
    },
  }));
});

// Sheets teleport out of the page into #sheets, so the page behind can go
// inert. htmx never saw the teleported copies, so process them as they come.
document.addEventListener('DOMContentLoaded', () => {
  const sheets = document.getElementById('sheets');
  if (!sheets) return;
  new MutationObserver((records) => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType === Node.ELEMENT_NODE) htmx.process(node);
      }
    }
  }).observe(sheets, { childList: true });
});

// A poll's swap can remove a control while its request is out, such as a
// check-in card's Passed switch. htmx then fires the reply's events on the
// detached control, so nothing listening on the page hears them: the list's
// `checkin-refresh` never comes and the card shows the old state (#45 retro).
// Refire the reply's HX-Trigger events and its failure on the body.
document.addEventListener('htmx:beforeRequest', (start) => {
  const source = start.detail.elt;
  const xhr = start.detail.xhr;
  const detached = (e) => e.detail.xhr === xhr && !source.isConnected;
  const onLoad = (e) => {
    if (!detached(e)) return;
    const header = xhr.getResponseHeader('HX-Trigger');
    if (!header) return;
    if (header.trim().startsWith('{')) {
      for (const [name, value] of Object.entries(JSON.parse(header))) {
        htmx.trigger(document.body, name, typeof value === 'object' ? value : { value });
      }
    } else {
      for (const name of header.split(',')) htmx.trigger(document.body, name.trim());
    }
  };
  const onFailure = (e) => {
    if (detached(e)) document.dispatchEvent(new CustomEvent(e.type, { detail: e.detail }));
  };
  const types = ['htmx:sendError', 'htmx:responseError'];
  const done = (e) => {
    if (e.detail.xhr !== xhr) return;
    source.removeEventListener('htmx:beforeOnLoad', onLoad);
    for (const type of types) source.removeEventListener(type, onFailure);
    source.removeEventListener('htmx:afterRequest', done);
  };
  source.addEventListener('htmx:beforeOnLoad', onLoad);
  for (const type of types) source.addEventListener(type, onFailure);
  source.addEventListener('htmx:afterRequest', done);
});

// A network failure or a 5xx has no server toast to swap in.
document.addEventListener('htmx:sendError', () => {
  derbynet.toast('The server did not answer. Check the connection and try again.');
});
document.addEventListener('htmx:responseError', (e) => {
  const status = e.detail.xhr.status;
  if (status !== 403 && status !== 422) {
    derbynet.toast(`The server failed (HTTP ${status}). Try again.`);
  }
});
