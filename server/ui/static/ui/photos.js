// Photo management (#47). Loaded before Alpine, after photo-sheets.js, so the
// store and the uploader register on `alpine:init`.
'use strict';

window.photos = {
  // Open the page's one sheet from a control, whose hx-get loads its body.
  open(title, ev) {
    const body = document.getElementById('photos-sheet-body');
    if (body) body.innerHTML = '<p class="py-6 text-center text-ink-muted">Loading…</p>';
    Alpine.store('photos').title = title;
    Alpine.store('ui').open('photos', ev);
  },

  // The CSRF header every htmx request sends, from the body's hx-headers.
  csrf() {
    try {
      return JSON.parse(document.body.getAttribute('hx-headers') || '{}')['X-CSRFToken'] || '';
    } catch (e) {
      return '';
    }
  },

  // One file to the upload URL, reporting progress as 0 to 100. Resolves
  // with the stored name; rejects with the server's message.
  send(url, file, progress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('X-CSRFToken', photos.csrf());
      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) progress(Math.round((e.loaded / e.total) * 100));
      });
      xhr.addEventListener('load', () => {
        let reply = {};
        try {
          reply = JSON.parse(xhr.responseText);
        } catch (e) {
          reply = {};
        }
        if (xhr.status === 200 && reply.name) resolve(reply.name);
        else reject(new Error(reply.message || `The server failed (HTTP ${xhr.status}).`));
      });
      xhr.addEventListener('error', () => reject(new Error('The server did not answer.')));
      const form = new FormData();
      form.append('photo', file);
      xhr.send(form);
    });
  },
};

document.addEventListener('alpine:init', () => {
  Alpine.store('photos', { title: '' });

  // Bulk upload. Each picked file is downscaled as on check-in, then sent one
  // at a time, with a progress bar each. A failed file keeps its Retry; the
  // list refreshes once the queue is done.
  Alpine.data('photoUpload', (url) => ({
    items: [],
    running: false,
    next: 1,

    add(ev) {
      for (const file of ev.target.files || []) {
        this.items.push({
          id: this.next++, file, name: file.name, state: 'waiting', status: 'Waiting', progress: 0,
        });
      }
      ev.target.value = '';
      this.run();
    },

    retry(item) {
      item.state = 'waiting';
      item.status = 'Waiting';
      item.progress = 0;
      this.run();
    },

    summary() {
      const done = this.items.filter((i) => i.state === 'done').length;
      const failed = this.items.filter((i) => i.state === 'failed').length;
      let text = `${done} of ${this.items.length} uploaded`;
      if (failed) text += `, ${failed} failed`;
      return `${text}.`;
    },

    async run() {
      if (this.running) return;
      this.running = true;
      let uploaded = false;
      let item;
      while ((item = this.items.find((i) => i.state === 'waiting'))) {
        item.state = 'uploading';
        item.status = 'Shrinking';
        let file = item.file;
        try {
          file = await photoSheets.downscale(item.file, item.file.name);
        } catch (e) {
          // Not an image the browser reads: send it as it is, and the
          // server says why it refuses it.
        }
        item.status = 'Uploading';
        try {
          const name = await photos.send(url, file, (p) => { item.progress = p; });
          item.state = 'done';
          item.progress = 100;
          item.status = name === file.name ? 'Uploaded' : `Uploaded as ${name}`;
          uploaded = true;
        } catch (e) {
          item.state = 'failed';
          item.status = e.message;
        }
      }
      this.running = false;
      if (uploaded) htmx.trigger(document.body, 'photos-refresh');
    },
  }));
});

// `?racerid=` scrolls to the racer, whose card the page marks.
document.addEventListener('DOMContentLoaded', () => {
  const marked = document.querySelector('#photos-list [data-highlight]');
  if (marked) marked.scrollIntoView({ block: 'center' });
});

// A sheet's body arrives after the sheet opens, so move the focus to its
// first field once it does.
document.addEventListener('htmx:afterSwap', (e) => {
  if (e.detail.target && e.detail.target.id === 'photos-sheet-body') {
    const first = e.detail.target.querySelector('[data-autofocus]');
    if (first) first.focus();
  }
});
