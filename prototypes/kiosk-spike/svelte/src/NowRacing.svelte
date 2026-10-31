<script>
  // SPIKE #71 candidate B: the now-racing page.  It polls the legacy
  // `query=poll` itself, 500 ms after each response (now-racing-poller.js's
  // contract).  With ?push=1 a server-sent "change" event polls at once and
  // the chain slows to 5 s.
  import { fly, scale } from 'svelte/transition';
  import { backOut } from 'svelte/easing';

  const S = window.__spike;
  const push = new URLSearchParams(location.search).get('push') === '1';
  const DELAY = push ? 5000 : 500;
  const LINGER_MS = 4000;
  const VALUES = 'current-heat,heat-results,precision,racers,timer-trouble,current-reschedule';

  let board = $state(null);
  let paused = $state(false);
  let holdUntil = 0, timer = 0, inflight = false, again = false;

  function queue() { clearTimeout(timer); timer = setTimeout(poll, DELAY); }

  async function poll() {
    clearTimeout(timer);
    if (paused) return queue();
    if (inflight) { again = true; return; }
    inflight = true;
    S.requests++;
    try {
      const q = new URLSearchParams({ query: 'poll', values: VALUES,
                                      roundid: board?.roundid ?? '', heat: board?.heat ?? '' });
      apply(await (await fetch('/action.php?' + q)).json());
    } catch (e) { /* try again next time */ }
    inflight = false;
    if (again) { again = false; return poll(); }
    queue();
  }

  // As now-racing.js decides it: results for the heat on screen come first
  // (timer-message has usually moved the current heat on by then), and only
  // once they are shown does the next lineup replace it.
  function apply(data) {
    const cur = data['current-heat'];
    const key = cur.roundid + '/' + cur.heat;
    const results = data['heat-results'] || [];
    if (board && board.heat && results.length > board.nresults) {
      const byLane = new Map(results.map(r => [r.lane, r]));
      board.lanes = board.lanes.map(l => {
        const r = byLane.get(l.lane);
        return r ? { ...l, time: String(r.time), place: r.place } : l;
      });
      board.nresults = results.length;
      S.mark('results', board.key);
      holdUntil = Date.now() + LINGER_MS;
    } else if ((!board || board.key !== key) && Date.now() >= holdUntil) {
      S.mark('lineup', key);
      board = { key, roundid: cur.roundid, heat: cur.heat, cls: cur.class, round: cur.round,
                nheats: cur['number-of-heats'], nresults: 0,
                lanes: (data.racers || []).map(r => ({ lane: r.lane, carnumber: r.carnumber,
                                                      name: r.name, time: null, place: null })) };
    }
  }

  S.onReplay(p => {
    paused = p;
    if (!p) holdUntil = Date.now() + LINGER_MS;  // as now-racing.js lingers
  });
  if (push) new EventSource('/spike/events').addEventListener('change', () => poll());
  poll();
</script>

<main class="kiosk">
  <div class="stack">
    {#if board}
      {#key board.key}
        <section class="board" in:fly={{ x: 2000, duration: 550, opacity: 0 }}
                 out:fly={{ x: -2000, duration: 450, opacity: 0 }}>
          <h1>{board.cls || 'Waiting for racing'}{#if board.heat}<small>Round {board.round} &middot; Heat {board.heat} of {board.nheats}</small>{/if}</h1>
          <ol class="lanes">
            {#each board.lanes as l (l.lane)}
              <li class="lane {l.place ? 'p' + l.place : ''}" class:glow={l.place === 1}>
                <span class="lane-no">Lane {l.lane}</span>
                <span class="car">{l.carnumber}</span>
                <span class="name">{l.name}</span>
                {#if l.place}
                  <span class="time" in:fly={{ y: 30, duration: 600, delay: (l.place - 1) * 400 }}>{l.time}</span>
                  <span class="place" in:scale={{ start: 0.3, duration: 600, delay: (l.place - 1) * 400, easing: backOut }}>{l.place}</span>
                {:else}
                  <span class="time"></span><span class="place"></span>
                {/if}
              </li>
            {/each}
          </ol>
        </section>
      {/key}
    {/if}
  </div>
  {#if paused}<div class="badge-paused">paused for replay</div>{/if}
</main>
<div class="badge-candidate">B: Svelte</div>

<style>
  .stack { display: grid; }
  .stack > :global(*) { grid-area: 1 / 1; }
  .glow { animation: glow 1200ms ease-in-out 1800ms 2 alternate both; }
  @keyframes glow { to { box-shadow: 0 0 0 6px var(--gold), 0 0 60px var(--gold); } }
</style>
