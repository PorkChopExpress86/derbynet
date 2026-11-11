#!/bin/sh
# Puts a second official Ubuntu mirror first in the GitHub runner's apt
# mirror list, ahead of the runner's own three (azure.archive, archive and
# security.ubuntu.com).  On 2026-11-11 all three timed out and every job
# that ran apt-get failed in setup (PR #284's run 43054997286, three
# attempts); mirrors.edge.kernel.org answered.  apt checks each package
# against Ubuntu's signing keys whichever mirror serves it, and falls back
# to the runner's mirrors when this one fails.
#
# Run it with sudo before any apt-get, playwright's --with-deps included.
set -eu
list=/etc/apt/apt-mirrors.txt
[ -f "$list" ] || exit 0
grep -q 'mirrors.edge.kernel.org' "$list" && exit 0
{
  printf 'https://mirrors.edge.kernel.org/ubuntu/\tpriority:1\n'
  # The runner's lines end in priority:N; each moves down one place.
  awk -F'\tpriority:' 'NF == 2 { printf "%s\tpriority:%d\n", $1, $2 + 1; next } { print }' "$list"
} > "$list.new"
mv "$list.new" "$list"
cat "$list"
