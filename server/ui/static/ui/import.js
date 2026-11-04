// The import flow (#49). Loaded before Alpine, on the import page only, with
// SheetJS (`ui/vendor/xlsx-0.13.5.full.min.js`). It reads the file as
// `website/js/import-csv.js` does: the encoding select decodes the bytes with
// SheetJS's code pages, the worksheet select picks a sheet, and the header
// row switch says whether the first row names the columns. Each column gets
// a field from a select, filled in from its header when the name matches.
// The mapped rows go to the server as JSON in the hidden `rows` field, which
// the Preview and Import buttons' htmx posts include.
//
// A results file is read with `raw`, so a text file's cells stay as written:
// SheetJS otherwise reads "2017-03-04 15:25:07" as a date and writes it back
// as "3/4/17", which PHP then stored as the heat's completion time.
'use strict';

// `buildEncodingSelector()` in import-csv.js; an empty entry is a divider.
window.IMPORT_CODEPAGES = [
  ['1252', 'Western (Windows-1252, ISO-8859-1)'],
  ['10000', 'Mac OS Roman (Macintosh)'],
  ['65001', 'UTF-8'],
  ['', ''],
  ['437', 'DOS Original'],
  ['850', 'DOS Latin-1'],
  ['895', 'DOS Czech'],
  ['620', 'DOS Polish'],
  ['737', 'DOS Greek'],
  ['852', 'DOS Latin-2'],
  ['857', 'DOS Latin-5'],
  ['861', 'DOS Icelandic'],
  ['865', 'DOS Danish/Norwegian'],
  ['866', 'DOS Belarusian/Russian/Ukranian'],
  ['874', 'Windows Thai'],
  ['932', 'Windows Shift-JIS (Windows-932)'],
  ['936', 'Windows Simplified Chinese (Windows-936)'],
  ['949', 'Windows Korean (Windows-949)'],
  ['950', 'Windows Traditional Chinese (Windows-950)'],
  ['1250', 'Windows Central Europe (Windows-1250)'],
  ['1251', 'Windows Cyrillic (Windows-1251)'],
  ['1253', 'Windows Greek (Windows-1253)'],
  ['1254', 'Windows Turkish (Windows-1254)'],
  ['1255', 'Windows Hebrew (Windows-1255)'],
  ['1256', 'Windows Arabic (Windows-1256)'],
  ['1257', 'Windows Baltic (Windows-1257)'],
  ['1258', 'Windows Vietnamese (Windows-1258)'],
  ['10006', 'Macintosh Greek'],
  ['10007', 'Macintosh Cyrillic'],
  ['10008', 'Macintosh Simplified Chinese'],
  ['10029', 'Macintosh Central Europe'],
  ['10079', 'Macintosh Icelandic'],
  ['10081', 'Macintosh Turkish'],
];

// 32 hex digits naming one import. A new file or mapping gets a new one; the
// server refuses a token it has imported already, so a double tap or a
// replay imports once. `crypto.randomUUID` needs HTTPS, which a race-day
// LAN lacks, so this uses `getRandomValues`.
function importToken() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

// A header's text with case, spaces and punctuation left out.
function importKey(text) {
  return String(text).toLowerCase().replace(/[^a-z0-9]/g, '');
}

document.addEventListener('alpine:init', () => {
  Alpine.data('importer', (config) => ({
    config,
    fileName: '',
    contents: null,
    encoding: '65001',
    sheets: [],
    sheet: 0,
    header: true,
    table: [],
    mapping: [],
    // A field chosen by hand, which a header switch then keeps.
    touched: false,
    token: importToken(),
    error: '',

    get width() {
      return this.table.reduce((n, row) => Math.max(n, row.length), 0);
    },
    get columns() {
      return Array.from({ length: this.width }, (_, i) => i);
    },
    get body() {
      return this.header ? this.table.slice(1) : this.table;
    },
    get firstRow() {
      return this.header ? 2 : 1;
    },
    get labelOf() {
      return Object.fromEntries(this.config.fields);
    },
    get mapped() {
      return this.mapping.filter((f) => f);
    },
    // A required field the mapping lacks. First and last name are met by
    // the combined name field, as import-roster.js lets it stand for both.
    get unmet() {
      const mapped = new Set(this.mapped);
      if (mapped.has('first-last')) {
        mapped.add('firstname');
        mapped.add('lastname');
      }
      return this.config.required.filter((f) => !mapped.has(f));
    },
    get duplicated() {
      const seen = new Set();
      return this.mapped.filter((f) => (seen.has(f) ? true : (seen.add(f), false)));
    },
    get ready() {
      return this.table.length > 0 && this.unmet.length === 0 && this.duplicated.length === 0;
    },
    // `uploadTableRowsFrom()`'s parameters: each mapped column's text, by
    // row number as the table shows it, the header row being row 1.
    get rows() {
      return this.body.map((cells, i) => {
        const fields = {};
        this.mapping.forEach((field, col) => {
          if (field) fields[field] = col < cells.length ? cells[col] : '';
        });
        return { row: this.firstRow + i, fields };
      });
    },
    get rowsJson() {
      return this.ready ? JSON.stringify(this.rows) : '';
    },
    // A new token whenever the rows the page would send change.
    renew() {
      this.token = importToken();
    },

    pick(event) {
      const file = event.target.files[0];
      if (!file) return;
      this.fileName = file.name;
      this.error = '';
      const reader = new FileReader();
      reader.onload = (e) => {
        this.contents = e.target.result;
        this.load(0);
      };
      reader.onerror = () => {
        this.error = `Unable to read ${file.name}.`;
      };
      reader.readAsBinaryString(file);
    },

    // `loadWorkbook()`: decode with the chosen code page, else let SheetJS
    // read the bytes with it.
    load(index) {
      if (this.contents === null) return;
      let book = null;
      try {
        const text = cptable.utils.decode(this.encoding, this.contents);
        book = XLSX.read(text, { type: 'string', raw: this.config.raw });
      } catch (e) {
        try {
          book = XLSX.read(this.contents, {
            type: 'binary', codepage: Number(this.encoding), raw: this.config.raw,
          });
        } catch (e2) {
          this.error = 'This file is not a spreadsheet DerbyNet can read.';
          this.table = [];
          return;
        }
      }
      this.book = book;
      this.sheets = book.SheetNames.slice();
      this.sheet = index < this.sheets.length ? index : 0;
      this.readSheet();
    },

    readSheet() {
      const sheet = this.book.Sheets[this.sheets[this.sheet]];
      const unlikely = 'default-value-hopefully-unlikely-to-occur-in-a-real-spreadsheet';
      const arrays = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: unlikely });
      this.table = arrays.map((row) => row.map((v) => (v === unlikely ? '' : String(v))));
      this.touched = false;
      this.autoMap();
      this.renew();
    },

    // Fill in each column's field from its header, or for results the
    // fixed columns of a DerbyNet export.
    autoMap() {
      const byName = {};
      this.config.fields.forEach(([f, label]) => {
        byName[importKey(f)] = f;
        byName[importKey(label)] = f;
      });
      Object.entries(this.config.aliases).forEach(([name, f]) => {
        byName[importKey(name)] = f;
      });
      const head = this.header && this.table.length ? this.table[0] : [];
      const taken = new Set();
      const mapping = this.columns.map((col) => {
        const found = byName[importKey(head[col] || '')];
        if (found && !taken.has(found)) {
          taken.add(found);
          return found;
        }
        return '';
      });
      if (this.config.fixed.length && !mapping.some((f) => f)) {
        this.mapping = this.columns.map((col) => this.config.fixed[col] || '');
      } else {
        this.mapping = mapping;
      }
    },

    setEncoding() {
      this.load(this.sheet);
    },
    setSheet() {
      this.readSheet();
    },
    setHeader() {
      this.header = !this.header;
      if (!this.touched) this.autoMap();
      this.renew();
    },
    choose(col, field) {
      this.touched = true;
      const mapping = this.mapping.slice();
      mapping[col] = field;
      this.mapping = mapping;
      this.renew();
    },
  }));
});
