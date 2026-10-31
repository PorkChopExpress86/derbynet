<script>
  // SPIKE #71 candidate B: the standings reveal.  It polls the legacy
  // `query=poll.kiosk` every 5 s (kiosk-poller.js's contract); the kiosk's
  // parameters say which catalog entry to show and how many rows to reveal
  // from the bottom ({key, reveal: N | "all"}).
  import { fly } from 'svelte/transition';
  import { flip } from 'svelte/animate';

  const S = window.__spike;
  const q = new URLSearchParams(location.search);
  const address = q.get('address') || 'spike-b';
  const push = q.get('push') === '1';

  let title = $state('Standings');
  let rows = $state([]);
  let revealed = $state(0);
  let paused = $state(false);
  let key = null, target = 0, auto = false, stepping = false;

  // The bottom `revealed` rows, still in place order.
  const visible = $derived(rows.slice(rows.length - revealed));

  async function pollKiosk() {
    S.requests++;
    try {
      const r = await (await fetch('/action.php?query=poll.kiosk&address=' + encodeURIComponent(address))).json();
      await apply(JSON.parse(r['kiosk-setting'].parameters || '{}'));
    } catch (e) { /* next poll */ }
  }

  async function apply(params) {
    const k = params.key || '';
    if (k !== key) {
      const d = await (await fetch('/spike/api/standings?key=' + encodeURIComponent(k))).json();
      key = k;
      title = d.entry ? d.entry.name : 'Standings';
      revealed = 0;
      rows = d.rows;
    }
    auto = params.reveal === 'all';
    target = auto ? rows.length : Math.min(Number(params.reveal) || 0, rows.length);
    if (target < revealed) revealed = target;
    tick();
  }

  function tick() {
    if (paused || stepping || revealed >= target) return;
    stepping = true;
    revealed++;
    S.mark('reveal', revealed);
    setTimeout(() => { stepping = false; tick(); }, auto ? 1500 + 500 : 500);
  }

  S.onReplay(p => { paused = p; if (!p) tick(); });
  setInterval(pollKiosk, 5000);
  pollKiosk();
  if (push) new EventSource('/spike/events').addEventListener('change', pollKiosk);
</script>

<main class="kiosk">
  <h1>{title}<small>Standings</small></h1>
  <div class="standings">
    <div class="shead"><span>Place</span><span>Car</span><span>Name</span><span class="heats">Heats</span><span class="avg">Average</span></div>
    {#each visible as row (row.id)}
      <div class="srow p{row.place}" animate:flip={{ duration: 500 }}
           in:fly={{ y: -40, duration: 500 }}>
        <span>{row.place}</span><span>{row.carnumber}</span><span>{row.name}</span>
        <span class="heats">{row.heats}</span><span class="avg">{row.avg}</span>
      </div>
    {/each}
  </div>
  {#if paused}<div class="badge-paused">paused for replay</div>{/if}
</main>
<div class="badge-candidate">B: Svelte</div>
