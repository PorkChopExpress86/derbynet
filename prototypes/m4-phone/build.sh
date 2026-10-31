#!/usr/bin/env bash
# Build the #39 phone prototypes into dist/, then optionally screenshot them.
#   ./build.sh            render templates, build Tailwind CSS, copy static files
#   ./build.sh --shoot    also take the screenshots and write results.json (needs Node and Playwright)
set -euo pipefail
cd "$(dirname "$0")"

TAILWIND_VERSION=4.1.11
DJANGO_SPEC='Django>=5.2,<5.3'
AXE_VERSION=4.10.3

mkdir -p .cache dist
TW=.cache/tailwindcss-$TAILWIND_VERSION
if [[ ! -x $TW ]]; then
  curl -fsSL -o "$TW" "https://github.com/tailwindlabs/tailwindcss/releases/download/v$TAILWIND_VERSION/tailwindcss-linux-x64"
  chmod +x "$TW"
fi

uv run --no-project --quiet --with "$DJANGO_SPEC" python -I render.py dist
cp -r static/app.js static/vendor dist/
"$TW" --input src/app.css --output dist/app.css --minify

if [[ ${1:-} == --shoot ]]; then
  if [[ ! -f .cache/node_modules/axe-core/axe.min.js ]]; then
    npm install --silent --no-save --prefix .cache "axe-core@$AXE_VERSION"
  fi
  node shoot.js
  python3 -I report.py
fi
