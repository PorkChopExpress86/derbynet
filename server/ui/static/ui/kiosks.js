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

// D102 (#344): while a pointer is down on a control of the kiosk board, the
// board's swap waits, whether a poll's reply or a change's reply out of band
// brings it, so the press still taps the control it went down on. A swap
// under the press replaced the control, and the browser sent no click for a
// mouse press (#316's swap_mid_click probe). PHP's dashboard loses such a
// press when a kiosk's data changes under it: it rebuilds every kiosk's
// controls with `.empty()` (kiosk-dashboard.js:99) when its hash of the
// kiosks changes (kiosk-dashboard.js:76-92). Only the newest board held is
// kept, and a board swapped in unheld drops it.
//
// The press ends on the control's click, once its own handlers and default
// action have run; when the pointer leaves the control while still down,
// which Chromium also does after a pointercancel; or at the bound, from the
// latest pointerdown, for a pointer that never comes up. A second pointer
// down keeps the hold for the newer press. A touch sends its click after its
// pointerup and pointerout, so a pointerout after the pointerup is no cancel.
//
// The held board is applied once the press has ended and no request a board
// control sent is out, which htmx marks with its request class on the control
// until just before `htmx:afterRequest`: a swap before then detached that
// control, and htmx then looks for its reply's out-of-band board in the
// detached control's tree and drops it (htmx 2.0.4 `swap()`,
// `contextElement`); a request sent while the board swapped went out with no
// CSRF header (#344 review). A control is detached only by a swap the board
// let through, which dropped the held board, so a detached control's request
// has nothing to wait for. A request that ends with no reply (status 0:
// replaced by a newer one, or a network error) drops the held board, and so
// does an open sheet (`derbynet.pollable`), where a swap would detach the
// sheet's opener: the board's signature (#kiosks-sig) is still the old one,
// so the next poll brings the board again.
function holdBoardUnderPress(board, bound) {
  let pressed = null;
  let down = false;
  let timer = null;
  let held = null;

  function apply() {
    if (held === null || pressed || board.querySelector('.htmx-request')) return;
    const html = held;
    held = null;
    if (!derbynet.pollable(board)) return;
    htmx.swap(board, html, { swapStyle: 'innerHTML' },
              { eventInfo: { elt: board, target: board } });
  }

  function release() {
    pressed = null;
    clearTimeout(timer);
    apply();
  }

  document.addEventListener('pointerdown', (e) => {
    const control = e.target.closest('button, a[href]');
    // contains(null) is false: a press on no control holds nothing.
    if (!board.contains(control)) return;
    pressed = control;
    down = true;
    clearTimeout(timer);
    timer = setTimeout(release, bound());
  }, true);
  document.addEventListener('pointerup', () => { down = false; }, true);
  // Board controls hold text only (board.html), so a pointerout while the
  // pointer is down leaves the pressed control.
  document.addEventListener('pointerout', () => {
    if (down) release();
  }, true);
  document.addEventListener('click', (e) => {
    if (pressed && pressed.contains(e.target)) setTimeout(release, 0);
  });
  document.addEventListener('htmx:afterRequest', (e) => {
    if (e.detail.xhr.status === 0) held = null;
    apply();
  });

  board.addEventListener('htmx:beforeSwap', (e) => {
    if (e.detail.target !== board || !e.detail.shouldSwap) return;
    if (!pressed) {
      held = null;
      return;
    }
    held = e.detail.serverResponse;
    e.detail.shouldSwap = false;
  });
  // htmx fires this on the out-of-band target itself, with shouldSwap true
  // (htmx 2.0.4 `oobSwap`), and a change's reply has one, the board
  // (views.changed). A poll's or a sheet's reply bubbles its beforeSwap up
  // from the control or the board instead, hence the target check above.
  board.addEventListener('htmx:oobBeforeSwap', (e) => {
    if (!pressed) {
      held = null;
      return;
    }
    // htmx hands over the out-of-band element itself; its children are
    // what an innerHTML swap puts in the board.
    const box = document.createElement('div');
    for (const node of e.detail.fragment.childNodes) box.append(node.cloneNode(true));
    held = box.innerHTML;
    e.detail.shouldSwap = false;
  });
}

document.addEventListener('alpine:init', () => {
  Alpine.store('kioskSheet', { title: '' });
  Alpine.store('sceneSheet', { title: '' });

  // The board's "Last contact: Ns ago" for a quiet kiosk, counted here each
  // second from the contact time the board carries only once the kiosk is
  // quiet, so a kiosk checking in on time never changes the board and its
  // poll answers 204. The server's clock at page load sets the offset from
  // this device's clock.
  Alpine.data('kioskDashboard', (serverNow, quiet, pollSeconds) => ({
    offset: serverNow - Date.now() / 1000,
    // How long a press holds the board's swap at most: one poll interval
    // from its pointerdown (D102, #344).
    pressBound: pollSeconds * 1000,
    init() {
      this.tick();
      setInterval(() => this.tick(), 1000);
      holdBoardUnderPress(document.getElementById('kiosk-board'), () => this.pressBound);
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
