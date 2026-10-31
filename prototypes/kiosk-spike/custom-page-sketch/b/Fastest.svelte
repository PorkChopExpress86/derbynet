<script>
  // SKETCH (not wired): an owner-built "fastest five" page in candidate B.
  // The owner adds this file under kiosk-ui/src/pages/, and the build turns
  // every file there into a page.  A shared module gives it the kiosk poll
  // (and later the push notice) and a JSON endpoint for its data.  Changing
  // it means rebuilding the bundle: `npm run build` on a machine with Node,
  // or a new image (#58), because the server has no Node at runtime.
  import { flip } from 'svelte/animate';
  import { fly } from 'svelte/transition';
  import { onKioskChange, fetchJson } from '../lib/kiosk.js';

  let runs = $state([]);
  async function load() { runs = (await fetchJson('/kiosk/api/fastest')).runs.slice(0, 5); }
  onKioskChange(load);   // poll.kiosk every 5 s, or a push notice
  load();
</script>

<main class="kiosk">
  <h1>Fastest five today</h1>
  <ol class="standings">
    {#each runs as run, i (run.resultid)}
      <li class="srow" animate:flip={{ duration: 400 }} in:fly={{ y: 40, delay: i * 150 }}>
        <span>{i + 1}</span><span>#{run.carnumber}</span><span>{run.name}</span>
        <span class="avg">{run.time}</span>
      </li>
    {/each}
  </ol>
</main>
