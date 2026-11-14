#!/usr/bin/env bash
# The first pass of #61: each LibreOffice guide in docs/ to GitHub Markdown.
#
#   docs/tools/fodt-to-md.sh [OUT_DIR]
#
# Each docs/*.fodt goes to an .odt with soffice, then to Markdown with
# `pandoc --extract-media`, as OUT_DIR/<name>.md with its images in
# OUT_DIR/img/<name>/.  <name> is the guide's file name in the table of #61,
# or a lowercase name for a guide #61 drops.  OUT_DIR defaults to
# docs/first-pass, which .gitignore leaves out: the guides in docs/ are this
# pass rewritten by hand, so the pass is a starting point to compare against,
# not a file to commit.
#
# Needs soffice (LibreOffice Writer) and pandoc on the PATH.  The host has
# neither, so #61 built a throwaway Debian image with both and ran the script
# in it as the checkout's owner, so the files it writes are yours:
#
#   printf '%s\n' 'FROM debian:trixie-slim' \
#     'RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends libreoffice-writer-nogui pandoc >/dev/null && rm -rf /var/lib/apt/lists/*' \
#     | docker build -t derbynet-fodt -
#   docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
#     -v "$PWD:/repo" -w /repo derbynet-fodt docs/tools/fodt-to-md.sh
#   docker image rm derbynet-fodt
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
out="${1:-$repo/docs/first-pass}"

for tool in soffice pandoc; do
  command -v "$tool" >/dev/null || { echo "fodt-to-md: $tool is not on the PATH" >&2; exit 1; }
done

# The guide to its first-pass name.  The kept names are #61's table.
declare -A NAME=(
  ["Installation- Docker"]=install-docker
  ["Installation- Debian"]=installation-debian
  ["Installation- Mac"]=installation-mac
  ["Installation- Windows"]=installation-windows
  ["Advanced Database Set-Up for DerbyNet"]=database
  ["Sharing a Database With GPRM"]=sharing-with-gprm
  ["Default Roles and Passwords"]=roles-and-passwords
  ["Running a Race with DerbyNet"]=running-a-race
  ["Timer Operation"]=timer-operation
  ["Photo Capture Stand"]=photo-stand
  ["Photo Upload Options"]=photo-upload
  ["Secure Contexts and DerbyNet"]=secure-contexts
  ["Live Streaming DerbyNet"]=live-streaming
  ["Online Balloting in DerbyNet"]=online-balloting
  ["Developers- Timer Messages"]=dev-timer-protocol
  ["Developers- Replay Messages"]=dev-replay-protocol
  ["Quick Reference"]=quick-reference
)

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$out/img"

shopt -s nullglob
count=0
for fodt in "$repo"/docs/*.fodt; do
  guide="$(basename "$fodt" .fodt)"
  name="${NAME[$guide]:-}"
  if [[ -z "$name" ]]; then
    echo "fodt-to-md: $guide.fodt has no name in this script's table" >&2
    exit 1
  fi
  # soffice writes <guide>.odt into the work folder.  A private profile keeps
  # it from reading or writing the user's LibreOffice settings.
  soffice -env:UserInstallation="file://$work/profile" --headless \
    --convert-to odt --outdir "$work" "$fodt" >/dev/null 2>&1
  rm -rf "$out/img/$name"
  # Run from OUT_DIR, so the image links are relative to the .md file.
  (cd "$out" && pandoc "$work/$guide.odt" -f odt -t gfm --wrap=none \
    --extract-media="img/$name" -o "$name.md")
  images=0
  [[ -d "$out/img/$name" ]] && images=$(find "$out/img/$name" -type f | wc -l)
  printf '%-40s -> %s.md (%d images)\n' "$guide.fodt" "$name" "$images"
  count=$((count + 1))
done

echo "$count guides converted into $out"
