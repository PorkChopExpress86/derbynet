// SPIKE #71: a check that every standings reveal step animates.  Counts, per
// step, the animation frames in which an animation (or view transition)
// runs.  A step with none appeared without motion.
//   node tools/step-anims.js [base] [candidate...]
'use strict';
const { chromium } = require('playwright');
const d = require('./driver.js');
const sc = require('./scenarios.js');

const BASE = process.argv[2] || 'http://127.0.0.1:8771';
const CANDS = process.argv.slice(3).length ? process.argv.slice(3) : sc.CANDIDATES;

(async () => {
  const s = await d.coordinator(BASE);
  const browser = await chromium.launch();
  for (const cand of CANDS) {
    await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 0 }, s);
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
    await page.addInitScript(() => {
      window.__f = {};
      (function f() {
        if (document.getAnimations().length && window.__spike) {
          const n = window.__spike.log.filter(x => x.ev === 'reveal').length;
          window.__f[n] = (window.__f[n] || 0) + 1;
        }
        requestAnimationFrame(f);
      })();
    });
    await page.goto(BASE + sc.PAGES.standings[cand] + 'steps-' + cand + '-' + Date.now());
    await d.sleep(500);
    await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 'all' }, s);
    await d.sleep(5000 + 13 * 2000 + 1500);
    const f = await page.evaluate(() => window.__f);
    const steps = Array.from({ length: 13 }, (_, i) => f[i + 1] || 0);
    console.log(cand, 'animated frames per step:', steps.join(' '),
                '| steps with no motion:', steps.filter(x => x === 0).length);
    await page.close();
  }
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
