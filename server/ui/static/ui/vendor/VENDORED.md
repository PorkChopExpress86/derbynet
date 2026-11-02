# Vendored front-end libraries

The manager UI serves these files itself, because a race venue often has no internet. Nothing loads from a CDN. Each file is the release's own minified build, byte for byte, and `ui/tests/test_vendored.py` checks the checksums below.

| File | Library and version | License | Source URL | sha256 |
|---|---|---|---|---|
| `htmx-2.0.4.min.js` | htmx 2.0.4 | 0BSD | https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `alpinejs-3.14.8.min.js` | Alpine.js 3.14.8 | MIT | https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js | `b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643` |
| `sortable-1.15.6.min.js` | SortableJS 1.15.6 | MIT | https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js | `6d0a831fc19b4bae851797ad3393157e861afb7862459c11226359b27e2c4337` |
| `cropper-1.6.2.min.js` | Cropper.js 1.6.2, the check-in crop sheet (#42) | MIT | https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.js | `6290a3fa581b17ccf4692cf3ec617a74ae65de88d9140c72d492ae9b47707efb` |
| `cropper-1.6.2.min.css` | Cropper.js 1.6.2 stylesheet | MIT | https://cdn.jsdelivr.net/npm/cropperjs@1.6.2/dist/cropper.min.css | `23679f552ce73499f817de22b5dcdd9e1e5a1e50cd9d77e34bbd31bf65a85186` |
| `adapter-9.0.6.js` | WebRTC adapter.js 9.0.6, loaded only for check-in's live camera preview (#42); #53 vendors the same file. It replaces the unpinned `adapter-latest.js` that the Ant target `webrtc-adapter` downloads. | BSD-3-Clause | https://cdn.jsdelivr.net/npm/webrtc-adapter@9.0.6/out/adapter.js | `4c229747115c1545d5c35ab4dd1dce13de30817fc362697f44dc6abe3efa8d17` |
| `qrcode-generator-1.4.4.js` | qrcode-generator 1.4.4, the check-in QR code for other phones (#42) | MIT | https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js | `18ae399f81182bc9de916e9c77b195df20cc58d6f2d55a62b085a299f1bf1780` |

The browser tests also vendor `ui/tests/browser/vendor/axe-4.10.3.min.js`, axe-core 4.10.3 (MPL-2.0), from https://cdn.jsdelivr.net/npm/axe-core@4.10.3/axe.min.js, sha256 `880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb`. It is never served.

To update a library, download the new release's file from its source URL, rename it with the version, update this table and the template that loads it, and run the browser tests.
