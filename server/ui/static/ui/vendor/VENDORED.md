# Vendored front-end libraries

The manager UI serves these files itself, because a race venue often has no internet. Nothing loads from a CDN. Each file is the release's own minified build, byte for byte, and `ui/tests/test_vendored.py` checks the checksums below.

| File | Library and version | License | Source URL | sha256 |
|---|---|---|---|---|
| `htmx-2.0.4.min.js` | htmx 2.0.4 | 0BSD | https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js | `e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447` |
| `alpinejs-3.14.8.min.js` | Alpine.js 3.14.8 | MIT | https://cdn.jsdelivr.net/npm/alpinejs@3.14.8/dist/cdn.min.js | `b600e363d99d95444db54acbfb2deffec9ae792aa99a09229bcda078e5b55643` |
| `sortable-1.15.6.min.js` | SortableJS 1.15.6 | MIT | https://cdn.jsdelivr.net/npm/sortablejs@1.15.6/Sortable.min.js | `6d0a831fc19b4bae851797ad3393157e861afb7862459c11226359b27e2c4337` |

The browser tests also vendor `ui/tests/browser/vendor/axe-4.10.3.min.js`, axe-core 4.10.3 (MPL-2.0), from https://cdn.jsdelivr.net/npm/axe-core@4.10.3/axe.min.js, sha256 `880970c081707360e64f34cea25ff91892f5bc95675b0776925b9709dd8a68bb`. It is never served.

To update a library, download the new release's file from its source URL, rename it with the version, update this table and the template that loads it, and run the browser tests.
