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

document.addEventListener('alpine:init', () => {
  // One sheet at a time (#39: one level only). Focus returns to the control
  // that opened it.
  Alpine.store('ui', {
    sheet: null,
    opener: null,
    open(name, ev) {
      this.opener = (ev && ev.currentTarget) || document.activeElement;
      this.sheet = name;
    },
    close() {
      const opener = this.opener;
      this.sheet = null;
      this.opener = null;
      if (opener && opener.isConnected) setTimeout(() => opener.focus(), 0);
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
  Alpine.data('uiSortable', (url, name) => ({
    status: '',
    init() {
      const start = () => {
        Sortable.create(this.$refs.list, {
          handle: '[data-handle]',
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
