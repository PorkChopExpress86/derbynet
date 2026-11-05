// The vote page (#54): `vote.js` rebuilt as one Alpine store. Loaded before
// Alpine, so the store registers on `alpine:init`.
//
// The page's config (`vote-config`) holds every judged award with the racers
// who can win it, and every racer's car number and photos. `ballot.get`
// says which awards are on the ballot, how many votes each takes, and this
// voter's choices; each change posts the award's whole list to `vote.cast`,
// as `toggle_vote()` does in `vote.js`. `/ballot/status` is polled every
// 5 s, and a change swaps the state in place where `vote.js` reloads.
'use strict';

(() => {
  // What a refusal of vote.cast tells the voter.
  const REFUSALS = {
    closed: () => 'Voting is closed now.',
    'too-many': (store) => `${store.awardName()} takes no more than ${store.maxFor(store.awardId)}.`,
    'no-award': () => 'That award is no longer on the ballot.',
    password: () => 'Enter the ballot password again.',
    ineligible: (store) => `That car cannot win ${store.awardName()}.`,
  };

  function plural(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  document.addEventListener('alpine:init', () => {
    const config = JSON.parse(document.getElementById('vote-config').textContent);

    Alpine.store('vote', {
      // loading, closed, password, none or open.
      state: config.open ? 'loading' : 'closed',
      balloting: config.open ? 'open' : 'closed',
      // {awardid: {max_votes, votes}} from ballot.get.
      ballot: {},
      awardId: null,
      racerId: null,
      order: config.order,
      password: '',
      wrongPassword: false,
      busy: false,
      // Bumped by each ballot.get, so a reply to an older one is dropped.
      asked: 0,

      init() {
        if (config.open) this.load();
        setInterval(() => this.poll(), config.pollSeconds * 1000);
      },

      // ----- the ballot -----

      async load() {
        const asked = ++this.asked;
        const showing = this.state === 'password';
        this.busy = true;
        let data;
        try {
          const query = new URLSearchParams({ query: 'ballot.get', password: this.password });
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
          if (data.outcome.code === 'password') {
            // vote.js shows "incorrect" only for a password the voter typed.
            this.wrongPassword = showing;
            this.state = 'password';
          } else {
            derbynet.toast(data.outcome.description || 'The ballot could not be loaded.');
          }
          return;
        }
        this.wrongPassword = false;
        this.ballot = data.ballot || {};
        const onBallot = Object.keys(this.ballot).some((id) => id in config.awards);
        this.state = onBallot ? 'open' : 'none';
        if (this.awardId !== null && !this.onBallot(this.awardId)) this.closeAward();
      },

      submitPassword() {
        this.load();
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
      votesFor(awardId) {
        const entry = this.ballot[awardId];
        return entry ? entry.votes.filter((id) => String(id) in config.racers) : [];
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
        } else if (outcome.code === 'password') {
          this.closeAward();
          this.state = 'password';
        } else if (outcome.code === 'no-award' || outcome.code === 'too-many') {
          this.load();
        }
      },
    });
  });
})();
