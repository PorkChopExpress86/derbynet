<script>
  // SPIKE #71 candidate B: the intermission screen (D12), each class's top
  // three in turn, a new panel every 6 s.
  import { fly } from 'svelte/transition';
  import { backOut } from 'svelte/easing';

  const S = window.__spike;
  let panels = $state([]);
  let i = $state(0);
  let paused = $state(false);
  let pending = false;

  async function next() {
    if (paused) { pending = true; return; }
    S.requests++;
    try {
      panels = (await (await fetch('/spike/api/leaders')).json()).panels;
      i = panels.length ? (i + 1) % panels.length : 0;
      S.mark('panel');
    } catch (e) { /* next time */ }
    setTimeout(next, 6000);
  }

  S.onReplay(p => { paused = p; if (!p && pending) { pending = false; next(); } });
  i = -1;
  next();
</script>

<main class="kiosk">
  <div class="stack">
    {#if panels[i]}
      {#key i}
        <section class="leaders" in:fly={{ x: 2000, duration: 550, opacity: 0 }}
                 out:fly={{ x: -2000, duration: 450, opacity: 0 }}>
          <h1>{panels[i].name}<small>Leaders so far</small></h1>
          <div class="podium">
            {#each panels[i].rows as row (row.id)}
              <div class="step p{row.place}"
                   in:fly|global={{ y: 200, duration: 700, delay: 400 + (4 - row.place) * 300, easing: backOut }}>
                <div class="pl">{row.place}</div>
                <div class="nm">{row.name}</div>
                <div class="av">#{row.carnumber} &middot; {row.avg}</div>
              </div>
            {/each}
          </div>
        </section>
      {/key}
    {/if}
  </div>
  {#if paused}<div class="badge-paused">paused for replay</div>{/if}
</main>
<div class="badge-candidate">B: Svelte</div>

<style>
  .stack { display: grid; height: 100%; }
  .stack > :global(*) { grid-area: 1 / 1; }
</style>
