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
# at a usable speed when the job starts.  A mirror that fails the probe
# leaves the runner's list as it is.
#
# The probe does not bound a stall that starts later.  On 2026-11-13 apt's
# download of that same index from this mirror hung until the job's timeout
# cancelled it 39 min later (run 43366304478, job 131705797349), and on
# 2026-11-14 for 49 min after a probe that passed (run 43640908879, job
# 132588740039).  A download that sends a byte now and then never trips
# apt's own timeout, which counts only silence.  So the script also:
#
# - writes $APT_CONF, so apt gives up on a silent server after
#   APT_TIMEOUT_SECONDS and retries APT_RETRIES times before the next
#   mirror (apt's defaults, 30 s and 3, cost two minutes a file);
# - puts a shim at $APT_GET_SHIM, ahead of /usr/bin/apt-get on the PATH,
#   sudo's and the runner's alike.  It runs apt-get under `timeout
#   $APT_GET_SECONDS`.  When apt-get fails or runs out of time while the
#   mirror is listed, the shim takes the mirror out of the list and runs the
#   same apt-get once more.  playwright's --with-deps runs apt-get itself,
#   and goes through the shim too.
#
# Run it with sudo before any apt-get, playwright's --with-deps included;
# server/tests/test_ubuntu_mirror.py fails a workflow step that runs apt
# without it.  The tests set APT_MIRRORS_LIST, UBUNTU_MIRROR,
# UBUNTU_CODENAME, the probe's two bounds, APT_CONF, APT_GET_SHIM, APT_GET
# (the real apt-get) and the three apt bounds.
set -eu
list=${APT_MIRRORS_LIST:-/etc/apt/apt-mirrors.txt}
mirror=${UBUNTU_MIRROR:-https://mirrors.edge.kernel.org/ubuntu/}

conf=${APT_CONF:-/etc/apt/apt.conf.d/80derbynet-timeouts}
cat > "$conf" <<EOF
// Written by .github/ubuntu-mirror.sh: a silent mirror costs seconds, not minutes.
Acquire::http::Timeout "${APT_TIMEOUT_SECONDS:-10}";
Acquire::https::Timeout "${APT_TIMEOUT_SECONDS:-10}";
Acquire::Retries "${APT_RETRIES:-1}";
EOF

shim=${APT_GET_SHIM:-/usr/local/sbin/apt-get}
{
  echo '#!/bin/sh'
  echo '# Written by .github/ubuntu-mirror.sh, which says why.'
  printf "real='%s' list='%s' mirror='%s' bound='%s'\n" \
    "${APT_GET:-/usr/bin/apt-get}" "$list" "$mirror" "${APT_GET_SECONDS:-300}"
  cat <<'EOF'
echo "apt-get $*: at most $bound s, by .github/ubuntu-mirror.sh" >&2
rc=0
timeout -k 10 "$bound" "$real" "$@" || rc=$?
[ "$rc" -eq 0 ] && exit 0
grep -qsF "$mirror" "$list" || exit "$rc"
echo "ubuntu-mirror.sh: apt-get $* failed (exit $rc, 124 is out of time);" \
  "$mirror leaves $list and apt-get runs again" >&2
grep -vF "$mirror" "$list" > "$list.new" || true
mv "$list.new" "$list"
: > "$list.dropped"
exec timeout -k 10 "$bound" "$real" "$@"
EOF
} > "$shim.new"
chmod 755 "$shim.new"
mv "$shim.new" "$shim"
found=$(command -v apt-get || true)
[ "$found" = "$shim" ] ||
  echo "::warning::ubuntu-mirror.sh: apt-get on this PATH is ${found:-missing}, not $shim; apt-get runs unbounded" >&2

# A runner with no mirror list gets the bounds above and no mirror.
[ -f "$list" ] || exit 0
# Listed already, by any scheme or path: a rerun of the job adds nothing.
host=${mirror#*://}
host=${host%%/*}
grep -qF "://$host/" "$list" && exit 0
# The shim took the mirror out earlier in this job: it stays out.
if [ -e "$list.dropped" ]; then
  echo "ubuntu-mirror.sh: $mirror failed earlier in this job; the list stays as it is"
  cat "$list"
  exit 0
fi
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
