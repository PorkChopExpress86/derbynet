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
//
// D125 (#369): a Space key held on a focused board button holds the swap the
// same way. Chromium clicks a button on Space's keyup only when the key
// went down on that button, by its keydown or an auto-repeat (the PR's
// stand-in probes): a swap under the key moved the focus to the page's body
// and the keyup there sent no click
// (`test_a_space_press_that_spans_a_board_swap_still_taps`). PHP's dashboard
// loses the key on Reload the same way when a rename rebuilds its controls
// with `.empty()` (kiosk-dashboard.js:99, the PR's probe). The key's press
// starts on its first keydown, not an auto-repeat, and ends on the button's
// click; when the focus leaves the button, since a keyup elsewhere clicks
// nothing; or at the same bound from that keydown, for a keyup that never
// comes.
function holdBoardUnderPress(board, bound) {
  let pressed = null;
  let down = false;
  let keyed = false;
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
    keyed = false;
    clearTimeout(timer);
    apply();
  }

  function hold(control) {
    pressed = control;
    clearTimeout(timer);
    timer = setTimeout(release, bound());
  }

  document.addEventListener('pointerdown', (e) => {
    const control = e.target.closest('button, a[href]');
    // contains(null) is false: a press on no control holds nothing.
    if (!board.contains(control)) return;
    hold(control);
    keyed = false;
    down = true;
  }, true);
  document.addEventListener('keydown', (e) => {
    if (e.key !== ' ' || e.repeat) return;
    const control = e.target.closest('button');
    if (!board.contains(control)) return;
    hold(control);
    keyed = true;
  }, true);
  // A key's press is on the focused button, so any focusout leaves it.
  document.addEventListener('focusout', () => {
    if (keyed) release();
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

  // htmx fires both on the swap's target (htmx 2.0.4 `handleAjaxResponse`,
  // `oobSwap`), and no board control's reply targets anything inside the
  // board, so the board hears only its own swaps: a poll's reply, and a
  // change's reply out of band (views.changed). A poll's 204 and an error
  // come with shouldSwap false; an out-of-band swap always comes with it
  // true.
  board.addEventListener('htmx:beforeSwap', (e) => {
    if (!e.detail.shouldSwap) return;
    if (!pressed) {
      held = null;
      return;
    }
    held = e.detail.serverResponse;
    e.detail.shouldSwap = false;
  });
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

// D126 (#369): a held Enter on a board button is one tap. Chromium clicks a
// button on each Enter keydown, auto-repeats included, so a repeat is
// cancelled before it clicks; D127 puts the focus back on the button the
// first tap's reply swapped in, where the repeats would land otherwise.
function ignoreEnterRepeats(board) {
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && e.repeat && board.contains(e.target.closest('button'))) {
      e.preventDefault();
    }
  }, true);
}

// The board's controls that D127 gives the focus back to: its buttons and
// each card's Preview link.
const BOARD_FOCUS_CONTROLS = 'button, a[href]';

// D127 (#369): after each swap of the board, a poll's, a change's reply out
// of band or a held board applied (D102, D125), the focus goes back to the
// swapped-in copy of the board control that had it, a button or a card's
// Preview link. When a rename rebuilds PHP's controls, PHP leaves the focus
// that was on Reload on the page's body (the PR's probe). "The same control"
// is the same name, value, data-action and kiosk card (D127): Reload and
// Identify share a name and value, and Page, Name, Configure and Preview
// have neither. The focus is given back only when the control that had it
// is gone and nothing else has taken the focus since, so a crew member who
// moved it elsewhere keeps it there.
function keepFocusAcrossSwaps(board) {
  let focused = null;
  const controlKey = (el) => {
    const card = el.closest('[data-kiosk]');
    return JSON.stringify([el.tagName, el.getAttribute('name'), el.getAttribute('value'),
      el.dataset.action, card && card.dataset.kiosk]);
  };
  document.addEventListener('focusin', (e) => {
    focused = board.contains(e.target) && e.target.matches(BOARD_FOCUS_CONTROLS)
      ? e.target : null;
  });
  // A control that lost the focus and is still in the page once that task
  // is over lost it to the crew, such as a tap on the page's background,
  // not to a swap.
  document.addEventListener('focusout', (e) => {
    const el = e.target;
    setTimeout(() => {
      if (focused === el && el.isConnected) focused = null;
    });
  });
  new MutationObserver(() => {
    if (!focused || focused.isConnected) return;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    const key = controlKey(focused);
    focused = null;
    const match = [...board.querySelectorAll(BOARD_FOCUS_CONTROLS)]
      .find((el) => controlKey(el) === key);
    if (match) match.focus({ preventScroll: true });
  }).observe(board, { childList: true });
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
    // from its pointerdown (D102, #344) or its Space keydown (D125, #369).
    pressBound: pollSeconds * 1000,
    init() {
      this.tick();
      setInterval(() => this.tick(), 1000);
      const board = document.getElementById('kiosk-board');
      holdBoardUnderPress(board, () => this.pressBound);
      ignoreEnterRepeats(board);
      keepFocusAcrossSwaps(board);
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
