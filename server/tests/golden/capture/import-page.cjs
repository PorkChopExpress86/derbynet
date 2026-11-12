// Drives PHP's roster import page, website/import-roster.php, in a browser, as a
// setup volunteer would (#242, for #49's golden).
//
//   node import-page.cjs <base-url> <file> <codepage> <field,field,...> [sheet]
//
// It logs in as the race coordinator, opens the page, picks the encoding by its
// codepage (the page's own #select-encoding values, such as 10000 for Mac OS
// Roman), loads <file>, picks worksheet [sheet] (0 by default), drags each field
// label onto its column with the mouse (an empty field leaves that column
// unlabeled), presses Import, and waits for "Import complete!".  Then it asks
// for query=racer.list&order=name, so a recording proxy in front of <base-url>
// records the racers the page made.  It fails if any row failed.

const puppeteer = require('puppeteer');

const [base, file, codepage, fieldList, sheet = '0'] = process.argv.slice(2);
const fields = fieldList.split(',');
const TIMEOUT = 120000;

// A real drag, so jQuery UI's draggable and droppable run as they do for a user.
// Both boxes are measured just before the drag; the page shows the file's
// statistics a short while after loading it, which moves the table.
async function drag(page, from, to) {
  for (const selector of [from, to]) {
    await page.$eval(selector, (e) => e.scrollIntoView({ block: 'center', inline: 'center' }));
  }
  const a = await (await page.$(from)).boundingBox();
  const b = await (await page.$(to)).boundingBox();
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 5, a.y + a.height / 2 + 5, { steps: 5 });
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 20 });
  await page.mouse.up();
}

(async () => {
  const browser = await puppeteer.launch();
  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(TIMEOUT);
    await page.setViewport({ width: 1600, height: 1200 });
    page.on('dialog', async (d) => {
      console.error(`dialog: ${d.message()}`);
      await d.dismiss();
    });

    await page.goto(`${base}/login.php`);
    await Promise.all([
      page.waitForNavigation(),
      page.evaluate(() => window.handle_login('RaceCoordinator', 'doyourbest')),
    ]);

    await page.goto(`${base}/import-roster.php`);
    await page.waitForSelector('#select-encoding');
    await page.select('#select-encoding', codepage);
    const input = await page.$('#csv_file');
    await input.uploadFile(file);
    await page.waitForSelector('#csv_content [data-row="2"]');
    if (sheet !== '0') {
      await page.select('#select-sheet', sheet);
      await page.waitForFunction(
        (s) => document.querySelector('#select-sheet').value === s, {}, sheet);
    }
    if (!(await page.$eval('#header-row-present', (e) => e.checked))) {
      throw new Error('the page did not take the first row as a header');
    }

    for (const [column, field] of fields.entries()) {
      if (!field) continue;
      const label = `.fields .field[data-field="${field}"]`;
      const target = `#csv_content th.label_target[data-column="${column}"]`;
      const landed = `${target} .field[data-field="${field}"]`;
      await page.waitForSelector(label, { visible: true });
      // A drag that misses, as one did on a loaded machine, is tried again.
      for (let tries = 1; ; ++tries) {
        await drag(page, label, target);
        try {
          await page.waitForSelector(landed, { timeout: 3000 });
          break;
        } catch (e) {
          if (tries === 5) throw e;
          console.error(`drag of ${field} to column ${column} missed, try ${tries}`);
        }
      }
    }

    await page.waitForSelector('#import_button:not([disabled])');
    await page.click('#import_button');
    await page.waitForSelector('.upload_complete_message');
    const failed = await page.$$eval('.failed_outcome', (e) => e.length);
    if (failed) throw new Error(`${failed} rows failed to import`);
    const posted = await page.$$eval('.ok_outcome', (e) => e.length);
    console.error(`imported ${posted} rows from ${file}`);

    await page.evaluate(() => fetch('action.php?query=racer.list&order=name').then((r) => r.text()));
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
