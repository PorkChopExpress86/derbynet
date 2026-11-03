// The photo capture and crop sheets (#42), shared by check-in and photo
// management (#47). Loaded before Alpine, so the components register on
// `alpine:init`. A page that uses them names its vendored files on an element
// with `data-photo-assets` (data-cropper-js, data-cropper-css, data-adapter).
'use strict';

// The longest side of an uploaded photo, and its JPEG quality. A phone's
// 4 MB to 12 MB original leaves as a few hundred kilobytes.
const PHOTO_MAX_SIDE = 1600;
const PHOTO_QUALITY = 0.85;
// The autocrop ratio, 3 wide by 4 tall (`autocrop()` in media/photos.py).
const CROP_RATIO = 3 / 4;

window.photoSheets = {
  asset(name) {
    const root = document.querySelector('[data-photo-assets]');
    return root ? root.dataset[name] : '';
  },

  loadStyle(href) {
    if (document.querySelector(`link[href="${href}"]`)) return;
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = href;
    document.head.append(link);
  },

  // A sheet body names itself when it arrives, and opens the page's sheet
  // when a reply brought it. `sheet` is the sheet's name and its store's.
  show(sheet, title) {
    Alpine.store(sheet).title = title;
    if (Alpine.store('ui').sheet !== sheet) Alpine.store('ui').open(sheet);
  },

  // Decode a photo upright (the browser applies its EXIF orientation),
  // scale it to PHOTO_MAX_SIDE, and encode it as a JPEG with no metadata.
  async downscale(source, name) {
    let bitmap;
    if (source instanceof Blob) {
      bitmap = await createImageBitmap(source, { imageOrientation: 'from-image' });
    } else {
      bitmap = source;
    }
    const width = bitmap.videoWidth || bitmap.width;
    const height = bitmap.videoHeight || bitmap.height;
    const scale = Math.min(1, PHOTO_MAX_SIDE / Math.max(width, height));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(width * scale);
    canvas.height = Math.round(height * scale);
    canvas.getContext('2d').drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    if (bitmap.close) bitmap.close();
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', PHOTO_QUALITY));
    const stem = (name || 'photo').replace(/\.[^.]*$/, '').replace(/[^-a-zA-Z0-9 ]+/g, '_') || 'photo';
    return new File([blob], `${stem}.jpg`, { type: 'image/jpeg' });
  },
};

document.addEventListener('alpine:init', () => {
  // The photo sheet. A picked or captured photo is downscaled and put in the
  // form's hidden file input, which the htmx form posts.
  Alpine.data('photoCapture', () => ({
    secure: window.isSecureContext && Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
    stream: null,
    live: false,
    devices: [],
    device: '',
    ready: false,
    busy: false,
    preview: '',
    summary: '',

    init() {
      this.$watch('$store.ui.sheet', (sheet) => { if (sheet === null) this.stop(); });
    },
    destroy() {
      this.stop();
    },

    async use(file, original) {
      const transfer = new DataTransfer();
      transfer.items.add(file);
      this.$refs.upload.files = transfer.files;
      if (this.preview) URL.revokeObjectURL(this.preview);
      this.preview = URL.createObjectURL(file);
      const kb = (n) => `${Math.round(n / 1024)} KB`;
      this.summary = original
        ? `${file.name}: ${kb(original)} made ${kb(file.size)} for the upload.`
        : `${file.name}: ${kb(file.size)}.`;
      this.ready = true;
    },

    async picked(ev) {
      const file = ev.target.files && ev.target.files[0];
      if (!file) return;
      this.ready = false;
      try {
        await this.use(await photoSheets.downscale(file, file.name), file.size);
      } catch (e) {
        derbynet.toast('That photo could not be read. Try another one.');
      }
    },

    async start() {
      this.stop();
      try {
        await derbynet.loadScript(photoSheets.asset('adapter'));
        const video = this.device ? { deviceId: { exact: this.device } } : { facingMode: 'environment' };
        this.stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
        const found = await navigator.mediaDevices.enumerateDevices();
        this.devices = found.filter((d) => d.kind === 'videoinput');
        const track = this.stream.getVideoTracks()[0];
        const settings = track && track.getSettings ? track.getSettings() : {};
        if (settings.deviceId) this.device = settings.deviceId;
        const el = this.$refs.video;
        el.srcObject = this.stream;
        el.onplaying = () => { this.live = true; };
        await el.play();
      } catch (e) {
        this.stop();
        derbynet.toast('The camera did not start. Use Take photo instead.');
      }
    },

    stop() {
      this.live = false;
      if (this.stream) this.stream.getTracks().forEach((t) => t.stop());
      this.stream = null;
    },

    async capture() {
      if (!this.live) return;
      await this.use(await photoSheets.downscale(this.$refs.video, 'camera'), 0);
    },
  }));

  // The crop sheet: Cropper.js at 3:4. The box goes into the form's hidden
  // fields as it moves, in pixels of the work render the sheet shows.
  Alpine.data('photoCrop', () => ({
    ready: false,
    cropper: null,

    init() {
      photoSheets.loadStyle(photoSheets.asset('cropperCss'));
      derbynet.loadScript(photoSheets.asset('cropperJs')).then(() => {
        const image = this.$refs.image;
        const begin = () => {
          // The sheet may have moved on while Cropper.js loaded.
          if (!image.isConnected) return;
          this.cropper = new Cropper(image, {
            aspectRatio: CROP_RATIO,
            viewMode: 1,
            autoCropArea: 0.9,
            background: false,
            crop: (e) => this.fill(e.detail),
            ready: () => { this.ready = true; },
          });
        };
        if (image.complete && image.naturalWidth) begin();
        else image.addEventListener('load', begin, { once: true });
      });
    },
    destroy() {
      if (this.cropper) this.cropper.destroy();
    },

    fill(box) {
      const image = this.$refs.image;
      if (!image || !image.isConnected || !this.$refs.left) return;
      this.$refs.left.value = box.x;
      this.$refs.top.value = box.y;
      this.$refs.right.value = box.x + box.width;
      this.$refs.bottom.value = box.y + box.height;
      this.$refs.width.value = image.naturalWidth;
      this.$refs.height.value = image.naturalHeight;
    },
  }));
});
