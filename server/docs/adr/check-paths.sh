#!/usr/bin/env bash
# Checks that every backticked repository path cited in the ADR files exists.
set -euo pipefail

adr_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$adr_dir/../../.." && pwd)"

# Cited paths that are intended but absent from the repository.
ALLOWED_MISSING=(
  # ADR-13 names the future home of the golden fixtures.
  "server/tests/golden/"
  # The installer creates local/ per install. It is not tracked in git.
  "local/config-roles.inc"
  "local/kiosks/"
  "local/kiosks/feedback.kiosk"
)

# Backticked text that looks like a path but is not one from the repository root.
NOT_PATHS=(
  # URL routes that the server answers.
  "photo.php/..."
  "slide.php/..."
  "image.php/..."
  "photo.php/info"
  "derbynet/"
  "about.php"
  "index.php"
  "kiosk.php"
  "post-timer-log.php"
  "render-document.php"
  "timer.php"
  # Docker platform names.
  "linux/amd64"
  "linux/arm64"
  # File names that build output or a running install creates.
  "config-roles.inc"
  "derby-timer.jar"
  "profiles.js"
  "settings.lock"
)

# A backticked bare file name with one of these extensions is checked from the repository root.
file_name_pattern='^[A-Za-z0-9_-][A-Za-z0-9_.-]*\.(php|inc|js|java|xml|sh|css|html|md|fodt|jar|lock|kiosk|txt|json|py)$'

contains() {
  local needle="$1"
  shift
  local item
  for item in "$@"; do
    [[ "$item" == "$needle" ]] && return 0
  done
  return 1
}

failures=0
checked=0

while IFS= read -r span; do
  [[ "$span" == */* || "$span" =~ $file_name_pattern ]] || continue
  [[ "$span" == /* ]] && continue
  contains "$span" "${NOT_PATHS[@]}" && continue

  path="$span"
  line=""
  if [[ "$span" =~ ^(.*):([0-9]+)$ ]]; then
    path="${BASH_REMATCH[1]}"
    line="${BASH_REMATCH[2]}"
  fi
  checked=$((checked + 1))

  if contains "$path" "${ALLOWED_MISSING[@]}"; then
    if [[ -e "$repo_root/$path" ]]; then
      echo "STALE ALLOWLIST $path exists now. Remove it from ALLOWED_MISSING."
      failures=$((failures + 1))
    else
      echo "allowed  $span"
    fi
    continue
  fi

  if [[ ! -e "$repo_root/$path" ]]; then
    echo "MISSING  $span"
    failures=$((failures + 1))
  elif [[ -n "$line" ]] && (( $(wc -l < "$repo_root/$path") < line )); then
    echo "BAD LINE $span is past the end of the file"
    failures=$((failures + 1))
  else
    echo "ok       $span"
  fi
done < <(grep -oh '`[^`]*`' "$adr_dir"/*.md | tr -d '`' | sort -u)

echo "$checked paths checked, $failures failures"
(( failures == 0 ))
