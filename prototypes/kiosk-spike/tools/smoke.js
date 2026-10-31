// SPIKE #71: loads each page at 1920x1080, drives it, and screenshots it.
//   node tools/smoke.js [base] [outdir]
'use strict';
const { chromium } = require('playwright');
const d = require('./driver.js');

const BASE = process.argv[2] || 'http://127.0.0.1:8771';
const OUT = process.argv[3] || require('path').join(__dirname, '..', 'var', 'shots');

(async () => {
  require('fs').mkdirSync(OUT, { recursive: true });
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 } });
  const errors = [];
  const open = async (path) => {
    const page = await ctx.newPage();
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') errors.push(path + ': ' + m.text()); });
    page.on('pageerror', e => errors.push(path + ': ' + e.message));
    await page.goto(BASE + path);
    return page;
  };
  const s = await d.coordinator(BASE);
  await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 0 }, s);
  const pages = {
    a_nr: await open('/spike/a/now-racing/'),
    b_nr: await open('/spike/b/now-racing.html'),
    a_st: await open('/spike/a/standings/?address=smoke-a'),
    b_st: await open('/spike/b/standings.html?address=smoke-b'),
    a_ld: await open('/spike/a/leaders/'),
    b_ld: await open('/spike/b/leaders.html'),
  };
  await d.sleep(1500);
  await pages.a_nr.screenshot({ path: OUT + '/a-now-racing-lineup.png' });
  await pages.b_nr.screenshot({ path: OUT + '/b-now-racing-lineup.png' });
  await d.raceHeat(BASE, 5, { s, stageMs: 2500, runMs: 500 });
  await d.sleep(3500);
  await pages.a_nr.screenshot({ path: OUT + '/a-now-racing-results.png' });
  await pages.b_nr.screenshot({ path: OUT + '/b-now-racing-results.png' });
  await d.assignAll(BASE, 'spike/standings', { key: 'st-r2', reveal: 'all' }, s);
  await d.sleep(5500 + 13 * 2000);
  await pages.a_st.screenshot({ path: OUT + '/a-standings.png' });
  await pages.b_st.screenshot({ path: OUT + '/b-standings.png' });
  await pages.a_ld.screenshot({ path: OUT + '/a-leaders.png' });
  await pages.b_ld.screenshot({ path: OUT + '/b-leaders.png' });
  for (const [k, p] of Object.entries(pages)) {
    console.log(k, JSON.stringify(await p.evaluate(() => ({ log: window.__spike.log.map(x => x.ev + ':' + (x.detail ?? '')), requests: window.__spike.requests }))));
  }
  console.log('errors:', errors);
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
