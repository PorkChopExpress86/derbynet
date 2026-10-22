#!/usr/bin/env bash
# Checks that every backticked repository path and relative Markdown link in the ADR files exists.
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

# Backticked text that is not a repository file. Each entry carries its reason.
NOT_PATHS=(
  "derbynet/"          # URL base the server answers under
  "linux/amd64"        # Docker platform name
  "linux/arm64"        # Docker platform name
  "timer.php"          # Ant generates it from timer/ templates (build.xml)
  "derby-timer.jar"    # Ant build output
  "profiles.js"        # Ant build output
  "config-roles.inc"   # the installer writes it into local/
  "settings.lock"      # created by an install that locks its settings
  "poll.kiosk"         # an action.php query name, not a file
)

# A bare name with a file extension is looked up in the repository root, then in website/.
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
  # A URL route such as photo.php/info names the file before the first slash.
  [[ "$path" =~ ^([A-Za-z0-9_-]+\.php)/ ]] && path="${BASH_REMATCH[1]}"
  # A bare name that is not in the root is a website/ file.
  [[ "$path" != */* && ! -e "$repo_root/$path" && -e "$repo_root/website/$path" ]] && path="website/$path"
  contains "$path" "${NOT_PATHS[@]}" && continue
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

# Relative Markdown link targets, resolved against the ADR folder.
while IFS= read -r target; do
  [[ "$target" == http* || "$target" == \#* ]] && continue
  target="${target%%#*}"
  checked=$((checked + 1))
  if [[ -e "$adr_dir/$target" ]]; then
    echo "ok       link $target"
  else
    echo "MISSING  link $target"
    failures=$((failures + 1))
  fi
done < <(grep -oh '](\([^)]*\))' "$adr_dir"/*.md | sed 's/^](//; s/)$//' | sort -u)

echo "$checked paths checked, $failures failures"
(( failures == 0 ))
