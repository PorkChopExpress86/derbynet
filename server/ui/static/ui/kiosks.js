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
// mouse press (#316's swap_mid_click probe; PHP's kiosk-dashboard.php loses
// one the same way, #344's probe). Only the newest board held is kept.
//
// The press ends on the control's click, once its own handlers and default
// action have run; on a pointercancel; when the pointer moves off the
// control while still down; or at the bound, from the pointerdown, for a
// pointer that never comes up. A touch sends its click after its pointerup
// and pointerout, so a pointerout after the pointerup is no cancel.
//
// The held board is applied when the press ends, or, when the click sent a
// request, once that request ends: a swap before then detached the control,
// and htmx then looks for its reply's out-of-band board in the detached
// control's tree and drops it (htmx 2.0.4 `swap()`, `contextElement`). A
// reply that brought the board brought a newer one, and the held one is
// dropped.
function holdBoardUnderPress(board, bound) {
  let pressed = null;
  let up = false;
  let timer = null;
  let sent = null;
  let held = null;
  let swaps = 0;

  function apply() {
    if (held === null) return;
    const html = held;
    held = null;
    htmx.swap(board, html, { swapStyle: 'innerHTML' },
              { eventInfo: { elt: board, target: board } });
  }

  function release() {
    if (!pressed) return;
    pressed = null;
    clearTimeout(timer);
    const request = sent;
    sent = null;
    if (held === null) return;
    if (!request) {
      apply();
      return;
    }
    const before = swaps;
    request.addEventListener('htmx:afterRequest', () => {
      if (pressed) return;
      if (swaps === before) apply();
      else held = null;
    }, { once: true });
  }

  document.addEventListener('pointerdown', (e) => {
    const control = e.target.closest && e.target.closest('button, a[href]');
    if (!control || !board.contains(control)) return;
    release();
    pressed = control;
    up = false;
    timer = setTimeout(release, bound());
  }, true);
  document.addEventListener('pointerup', () => { up = true; }, true);
  document.addEventListener('pointercancel', release, true);
  document.addEventListener('pointerout', (e) => {
    if (pressed && !up && pressed.contains(e.target) &&
        !pressed.contains(e.relatedTarget)) release();
  }, true);
  document.addEventListener('click', (e) => {
    if (pressed && pressed.contains(e.target)) setTimeout(release, 0);
  });
  document.addEventListener('htmx:beforeSend', (e) => {
    if (pressed && pressed.contains(e.detail.elt)) sent = e.detail.elt;
  });

  // `swaps` counts the boards htmx swaps in unheld, so a deferred apply
  // knows whether the click's reply brought one.
  board.addEventListener('htmx:beforeSwap', (e) => {
    if (e.detail.target !== board || !e.detail.shouldSwap) return;
    if (!pressed) {
      swaps += 1;
      return;
    }
    held = e.detail.serverResponse;
    e.detail.shouldSwap = false;
  });
  board.addEventListener('htmx:oobBeforeSwap', (e) => {
    if (e.detail.target !== board || !e.detail.shouldSwap) return;
    if (!pressed) {
      swaps += 1;
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
