#!/usr/bin/env bash
# Checks that every backticked repository path and relative Markdown link in
# the ADR files exists.
#
#   check-paths.sh                 the ADRs in this folder
#   check-paths.sh --docs [DIR]    the guides in DIR (default docs/), #61
#
# With --docs it also checks each #fragment of a link to a guide against the
# ids the docs build gives that guide's headings, and fails on a remote image
# (a guide must work with no internet), a long dash (#61's house style), and
# an instruction from a PHP install that no longer exists: editing
# config-roles.inc, a php.ini file or an ODBC data source, or installing a
# Debian package, the Mac package or the Windows build (#61, D3, D4).  In a
# guide, a backticked span with a space, `://`, `=`, `<`, `>` or `*` is a
# command, a URL or a placeholder, not a path.
set -euo pipefail

adr_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$adr_dir/../../.." && pwd)"

docs=0
if [[ "${1:-}" == "--docs" ]]; then
  docs=1
  shift
  check_dir="$(cd "${1:-$repo_root/docs}" && pwd)"
else
  check_dir="$adr_dir"
fi
# CHECK_PATHS_ROOT points the repository root at a test's fixture.
repo_root="${CHECK_PATHS_ROOT:-$repo_root}"

shopt -s nullglob
files=("$check_dir"/*.md)
shopt -u nullglob
if (( ${#files[@]} == 0 )); then
  echo "No Markdown files in $check_dir"
  exit 1
fi

# Cited paths that are intended but absent from the repository.
ALLOWED_MISSING=(
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
  "health/"            # URL route served by the core app
  "ghcr.io/porkchopexpress86/derbynet_server"  # the published image (D89)
  "kiosks/now-racing.kiosk"  # a kiosk page string, not a file
  "kiosks/NAME.kiosk"        # a kiosk page string, not a file
  "server/var/docs"    # manage.py build_docs writes it; git ignores var/
  "America/New_York"   # a time zone name
  "application/x-www-form-urlencoded"  # a media type
  "multipart/form-data"                # a media type
  "text/plain"                         # a media type
)

# Instructions from a PHP install that no longer exists (#61), as extended
# regular expressions, matched without regard to case.
FORBIDDEN=(
  'config-roles\.inc'           # roles change on the roles page (D79)
  'php\.ini'                    # there is no PHP
  '\bODBC\b'                    # removed (D4)
  '\.deb\b'                     # no Debian package (D3)
  'derbynet-(server|extras)\b'  # the Debian packages' names
  '\.(dmg|pkg|msi)\b'           # the Mac and Windows installers (D3)
  'UniServer|XAMPP'             # the Windows build's web servers
  'jeffpiazza/derbynet_server'  # the PHP image; #58 publishes another (D89)
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
  if (( docs )) && [[ "$span" =~ [[:space:]=\<\>*] || "$span" == *://* ]]; then
    continue
  fi
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
done < <(grep -oh '`[^`]*`' "${files[@]}" | tr -d '`' | sort -u)

# A guide's anchors are the ids the docs build gives its headings, which the
# build reports itself (`server/ui/docs/build.py --anchors`), so the check
# never keeps a copy of its rule.  CHECK_PATHS_PYTHON names a Python with the
# markdown package; by default the server's uv environment runs it.
guide_ids=""
if (( docs )); then
  check_dir_real="$(cd "$check_dir" && pwd -P)"
  build_py="$adr_dir/../../ui/docs/build.py"
  if [[ -n "${CHECK_PATHS_PYTHON:-}" ]]; then
    guide_ids="$("$CHECK_PATHS_PYTHON" -I "$build_py" --anchors "${files[@]}")"
  else
    guide_ids="$(uv run --project "$adr_dir/../.." --frozen --no-dev --quiet \
                   python -I "$build_py" --anchors "${files[@]}")"
  fi
fi

# anchors FILE: the ids a #fragment of a link to FILE may name.  A guide's
# come from the build.  Another Markdown file opens on GitHub, which makes
# its own: lowercase, punctuation dropped, spaces as hyphens.
anchors() {
  local real
  real="$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")"
  if [[ "$(dirname "$real")" == "$check_dir_real" ]]; then
    awk -F '\t' -v f="$real" '$1 == f { print $2 }' <<<"$guide_ids"
  else
    grep -E '^#{1,6} ' "$1" | sed -E 's/^#+ +//' | tr '[:upper:]' '[:lower:]' \
      | sed -E 's/[^a-z0-9 _-]//g; s/ /-/g'
  fi
}

# Relative Markdown link and image targets, and <img src> in raw HTML,
# resolved against the folder checked.
while IFS= read -r target; do
  [[ "$target" == http* || "$target" == mailto:* || "$target" == \#* ]] && continue
  file="${target%%#*}"
  fragment=""
  [[ "$target" == *#* ]] && fragment="${target#*#}"
  checked=$((checked + 1))
  if [[ ! -e "$check_dir/$file" ]]; then
    echo "MISSING  link $target"
    failures=$((failures + 1))
  elif (( docs )) && [[ -n "$fragment" && "$file" == *.md ]] \
       && ! anchors "$check_dir/$file" | grep -qxF -- "$fragment"; then
    echo "MISSING  anchor $target"
    failures=$((failures + 1))
  else
    echo "ok       link $target"
  fi
done < <({ grep -oh '](\([^)]*\))' "${files[@]}" | sed 's/^](//; s/)$//' || true
           grep -ohE '<img [^>]*src="[^"]*"' "${files[@]}" | sed -E 's/.*src="([^"]*)"/\1/' || true
         } | sort -u)

if (( docs )); then
  # Each guide's links to its own headings.
  for f in "${files[@]}"; do
    while IFS= read -r fragment; do
      checked=$((checked + 1))
      if anchors "$f" | grep -qxF -- "$fragment"; then
        echo "ok       anchor $(basename "$f")#$fragment"
      else
        echo "MISSING  anchor $(basename "$f")#$fragment"
        failures=$((failures + 1))
      fi
    done < <(grep -oh '](#[^)]*)' "$f" | sed 's/^](#//; s/)$//' | sort -u || true)
  done

  # A remote image does not load at a race with no internet.
  while IFS= read -r hit; do
    echo "REMOTE IMAGE $hit"
    failures=$((failures + 1))
  done < <(grep -HnoE '!\[[^]]*\]\(https?:[^)]*\)|<img [^>]*src="https?:[^"]*"' "${files[@]}" || true)

  while IFS= read -r hit; do
    echo "LONG DASH $hit"
    failures=$((failures + 1))
  done < <(grep -Hn $'—\|–' "${files[@]}" || true)

  for pattern in "${FORBIDDEN[@]}"; do
    while IFS= read -r hit; do
      echo "PHP INSTALL $hit"
      failures=$((failures + 1))
    done < <(grep -HniE -- "$pattern" "${files[@]}" || true)
  done
fi

echo "$checked paths checked, $failures failures"
(( failures == 0 ))
