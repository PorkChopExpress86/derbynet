// The vote page (#54): `vote.js` rebuilt as one Alpine store. Loaded before
// Alpine, so the store registers on `alpine:init`.
//
// The page's config (`vote-config`) holds every judged award with the racers
// who can win it, and every racer's car number and photos. `ballot.get`
// says which awards are on the ballot, how many votes each takes, and this
// voter's choices; each change posts the award's whole list to `vote.cast`,
// as `toggle_vote()` does (`website/js/vote.js:106` to `:127`).
// `/ballot/status` is polled every 5 s, and a change swaps the state in
// place (D77) where `vote.js` reloads (`website/js/vote.js:203` to `:220`).
// A voter gets in with a racer's voting code, which the vote link put in
// the session (#206, D86); without one the page asks for a code, where
// `vote.js` asks for the ballot password.

'use strict';

(() => {
  // What a refusal of vote.cast tells the voter, in a toast (D77); vote.js
  // posts with no success handler and ignores the reply
  // (`website/js/vote.js:119` to `:124`).
  const REFUSALS = {
    closed: () => 'Voting is closed now.',
    'too-many': (store) => `${store.awardName()} takes no more than ${store.maxFor(store.awardId)}.`,
    'no-award': () => 'That award is no longer on the ballot.',
    'no-token': () => 'This voting code is no longer valid. Scan a racer\'s voting code again.',
    // The page's list of the award's cars is older than the server's.
    ineligible: (store) => `A car you chose can no longer win ${store.awardName()}. Reload the page.`,
  };

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  document.addEventListener('alpine:init', () => {
    const config = JSON.parse(document.getElementById('vote-config').textContent);

    Alpine.store('vote', {
      // loading, closed, code, none or open.
      state: !config.hasToken ? 'code' : config.open ? 'loading' : 'closed',
      // The session holds a voting code; once ballot.get or vote.cast
      // refuses it, the page stays on the code state.
      hasToken: config.hasToken,
      balloting: config.open ? 'open' : 'closed',
      // {awardid: {max_votes, votes}} from ballot.get.
      ballot: {},
      awardId: null,
      racerId: null,
      order: config.order,
      busy: false,
      // Bumped by each ballot.get, so a reply to an older one is dropped.
      asked: 0,

      init() {
        if (config.open && this.hasToken) this.load();
        setInterval(() => this.poll(), config.pollSeconds * 1000);
      },

      // ----- the ballot -----

      async load() {
        const asked = ++this.asked;
        this.busy = true;
        let data;
        try {
          const query = new URLSearchParams({ query: 'ballot.get' });
          const reply = await fetch(`${config.actionUrl}?${query}`, { credentials: 'same-origin' });
          data = await reply.json();
        } catch (e) {
          derbynet.toast('The server did not answer. Check the connection and try again.');
          return;
        } finally {
          if (asked === this.asked) this.busy = false;
        }
        if (asked !== this.asked || this.balloting !== 'open') return;
        if (data.outcome && data.outcome.summary === 'failure') {
          if (data.outcome.code === 'no-token') {
            this.lostToken();
          } else {
            derbynet.toast(data.outcome.description || 'The ballot could not be loaded.');
          }
          return;
        }
        this.ballot = data.ballot || {};
        const onBallot = Object.keys(this.ballot).some((id) => id in config.awards);
        this.state = onBallot ? 'open' : 'none';
        if (this.awardId !== null && !this.onBallot(this.awardId)) this.closeAward();
      },

      // The server refused the session's voting code.
      lostToken() {
        this.hasToken = false;
        this.closeAward();
        this.ballot = {};
        this.state = 'code';
      },

      async poll() {
        let data;
        try {
          const reply = await fetch(config.statusUrl, { credentials: 'same-origin' });
          data = await reply.json();
        } catch (e) {
          return;
        }
        if (data.balloting === this.balloting) return;
        this.balloting = data.balloting;
        if (!this.hasToken) return;
        if (data.balloting === 'open') {
          this.state = 'loading';
          this.load();
        } else {
          this.asked++;
          this.state = 'closed';
          this.ballot = {};
          this.closeAward();
        }
      },

      // ----- what the cards show -----

      onBallot(awardId) {
        return String(awardId) in this.ballot && String(awardId) in config.awards;
      },
      maxFor(awardId) {
        const entry = this.ballot[awardId];
        return entry ? Number(entry.max_votes) : 0;
      },
      // The voter's choices the award can still take. A car chosen earlier
      // that has since left the award's group or partition list drops out,
      // so the next change replaces it: vote.cast refuses the whole list
      // while it names a car the award cannot take (S14), where PHP's
      // stores any id (`action.vote.cast.inc:33`, `:55` to `:59`).
      votesFor(awardId) {
        const entry = this.ballot[awardId];
        const racers = this.racersFor(awardId);
        return entry ? entry.votes.filter((id) => racers.includes(id)) : [];
      },
      racersFor(awardId) {
        const award = config.awards[awardId];
        return award ? award.racers : [];
      },
      racer(id) {
        return config.racers[id] || { car: '', thumb: '', photo: '', group: '', subgroup: '' };
      },
      limitText(awardId) {
        return `Vote for up to ${this.maxFor(awardId)}`;
      },
      chosenText(awardId) {
        return `${this.votesFor(awardId).length} chosen`;
      },
      awardName() {
        const award = config.awards[this.awardId];
        return award ? award.name : '';
      },
      canWin(racerId) {
        return this.awardId !== null && this.racersFor(this.awardId).includes(racerId);
      },
      position(racerId) {
        return (config.orders[this.order] || []).indexOf(racerId);
      },
      chose(racerId) {
        return this.awardId !== null && this.votesFor(this.awardId).includes(racerId);
      },
      // The award is full and this racer is not one of the choices.
      blocked() {
        if (this.awardId === null || this.racerId === null) return false;
        return !this.chose(this.racerId) &&
          this.votesFor(this.awardId).length >= this.maxFor(this.awardId);
      },
      fullText() {
        const n = this.maxFor(this.awardId);
        return `You already chose ${plural(n, 'car', 'cars')} for ${this.awardName()}. Remove one to vote for this car.`;
      },

      // ----- moving around -----

      openAward(awardId) {
        this.awardId = awardId;
        window.scrollTo(0, 0);
      },
      closeAward() {
        if (Alpine.store('ui').sheet === 'vote-racer') Alpine.store('ui').close();
        this.awardId = null;
        this.racerId = null;
      },
      sorted() {
        // vote.php's dead sort links pointed at ballot.php (#64 C33); the
        // order stays in this page's own URL.
        const url = new URL(window.location.href);
        url.searchParams.set('order', this.order);
        window.history.replaceState(null, '', url);
      },
      openRacer(racerId, ev) {
        this.racerId = racerId;
        Alpine.store('ui').open('vote-racer', ev);
      },
      sheetRacer() {
        return this.racerId === null ? null : this.racer(this.racerId);
      },
      sheetTitle() {
        const racer = this.sheetRacer();
        return racer ? `Car ${racer.car}` : '';
      },
      sheetDetails() {
        const racer = this.sheetRacer();
        if (!racer) return '';
        return [racer.group, racer.subgroup].filter(Boolean).join(' · ');
      },

      // ----- voting -----

      async toggle() {
        if (this.busy || this.awardId === null || this.racerId === null) return;
        if (this.blocked()) return;
        const awardId = this.awardId;
        const racerId = this.racerId;
        const before = this.votesFor(awardId);
        const votes = before.includes(racerId)
          ? before.filter((id) => id !== racerId)
          : [...before, racerId];
        this.busy = true;
        let data;
        try {
          const body = new URLSearchParams({
            action: 'vote.cast', awardid: awardId, votes: JSON.stringify(votes),
          });
          const reply = await fetch(config.actionUrl, {
            method: 'POST', body, credentials: 'same-origin',
          });
          data = await reply.json();
        } catch (e) {
          derbynet.toast('The server did not answer. Your vote was not saved.');
          return;
        } finally {
          this.busy = false;
        }
        const outcome = data.outcome || {};
        if (outcome.summary === 'success') {
          if (this.ballot[awardId]) this.ballot[awardId].votes = votes;
          return;
        }
        const say = REFUSALS[outcome.code];
        derbynet.toast(say ? say(this) : (outcome.description || 'Your vote was not saved.'));
        if (outcome.code === 'closed') {
          this.poll();
        } else if (outcome.code === 'no-token') {
          this.lostToken();
        } else if (outcome.code === 'no-award' || outcome.code === 'too-many') {
          this.load();
        }
      },
    });
  });
})();
