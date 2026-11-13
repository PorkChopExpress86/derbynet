#!/bin/sh
# Puts a second official Ubuntu mirror first in the GitHub runner's apt
# mirror list, ahead of the runner's own three (azure.archive, archive and
# security.ubuntu.com).  On 2026-11-11 all three timed out and every job
# that ran apt-get failed in setup (PR #284's run 43054997286, three
# attempts); mirrors.edge.kernel.org answered.  apt checks each package
# against Ubuntu's signing keys whichever mirror serves it, and falls back
# to the runner's mirrors when this one fails.
#
# The mirror goes first only when it serves the noble-updates package index
# at a usable speed when the job starts.  On 2026-11-13 apt gave up on its
# InRelease files and took them from azure.archive, then its download of
# that same index from this mirror hung until the job's timeout cancelled
# it 39 min later (run 43366304478, job 131705797349).  A mirror that fails
# the probe leaves the runner's list as it is.
#
# Run it with sudo before any apt-get, playwright's --with-deps included.
# server/tests/test_ubuntu_mirror.py sets APT_MIRRORS_LIST, UBUNTU_MIRROR,
# UBUNTU_CODENAME and the probe's two bounds.
set -eu
list=${APT_MIRRORS_LIST:-/etc/apt/apt-mirrors.txt}
mirror=${UBUNTU_MIRROR:-https://mirrors.edge.kernel.org/ubuntu/}
[ -f "$list" ] || exit 0
# Listed already, by any scheme or path: a rerun of the job adds nothing.
host=${mirror#*://}
host=${host%%/*}
grep -qF "://$host/" "$list" && exit 0
codename=${UBUNTU_CODENAME:-$(. /etc/os-release && echo "$VERSION_CODENAME")}
probe="${mirror}dists/$codename-updates/main/binary-amd64/Packages.gz"
# Anything but a 200 with a body, slower than 50 kB/s for
# MIRROR_STALL_SECONDS, or longer than MIRROR_PROBE_SECONDS in all, and the
# mirror is left out.  On a timeout curl still prints its -w line, with
# the status and the bytes it got, so its exit status counts too.  Its
# error message goes to the job's log.
rc=0
got=$(curl -sS -o /dev/null -w '%{http_code} %{size_download}' --speed-limit 50000 \
    --speed-time "${MIRROR_STALL_SECONDS:-10}" \
    --max-time "${MIRROR_PROBE_SECONDS:-60}" "$probe") || rc=$?
case "$rc $got" in
  "0 200 "[1-9]*) ;;
  *)
    echo "ubuntu-mirror.sh: $probe failed the probe (curl exit $rc: $got); the runner's mirrors stay as they are"
    cat "$list"
    exit 0 ;;
esac
{
  printf '%s\tpriority:1\n' "$mirror"
  # The runner's lines end in priority:N; each moves down one place.
  awk -F'\tpriority:' 'NF == 2 { printf "%s\tpriority:%d\n", $1, $2 + 1; next } { print }' "$list"
} > "$list.new"
mv "$list.new" "$list"
cat "$list"
