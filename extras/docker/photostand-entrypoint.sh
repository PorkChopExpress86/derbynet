#! /bin/bash
#
# The photo stand image's entrypoint (#59).  It writes /etc/derbynet.conf from
# the environment, where a Pi install's owner edits that file by hand, then
# runs the unchanged extras/scripts/photostand.sh with the scanner's barcodes
# on its input (read-barcodes.py).
#
# photo-preamble.sh sets its defaults before it reads /etc/derbynet.conf, so
# a variable passed to the container would be overwritten by a default; written
# into the file, it overrides the default as a Pi's own file does.  Each value
# is quoted with printf %q, so the script reads back exactly the value passed.
# A variable that is unset or empty is left out, so photostand.sh keeps its
# default, as compose.photostand.yaml's empty settings expect, so a blank
# PHOTO_PASSWORD or FSWEBCAM_ARGS cannot be set.  These settings are all the
# image reads: nothing is mounted at /etc/derbynet.d or /boot.
#
# ADJUST_CLOCK is left out: a container cannot set the Pi's clock.

set -euo pipefail

CONF=/etc/derbynet.conf
SETTINGS=(DERBYNET_SERVER PHOTO_CAPTURE FSWEBCAM_ARGS PHOTO_REPO PHOTO_DIR
          AUTOCROP PHOTO_USER PHOTO_PASSWORD PHOTO_CHECKIN BARCODE_SCANNER_DEVS)

write_conf() {
    echo "# Written by the photo stand image's entrypoint from its environment."
    local name
    for name in "${SETTINGS[@]}"; do
        if [[ -n "${!name:-}" ]]; then
            printf '%s=%q\n' "$name" "${!name}"
        fi
    done
}

if [[ -z "${DERBYNET_SERVER:-}" ]]; then
    echo "derbynet-photostand: set DERBYNET_SERVER to the server's address, such as http://192.168.1.20" >&2
    exit 64
fi

write_conf > "$CONF"
# chdkptp is not in the image.  An arm64 build mounted at /opt/chdkptp
# becomes the chdkptp command; chdkptp.sh finds its files beside its own path.
if [[ -x /opt/chdkptp/chdkptp.sh ]]; then
    ln -sf /opt/chdkptp/chdkptp.sh /usr/local/bin/chdkptp
fi
[[ "${1:-}" == "--write-conf-only" ]] && exit 0

exec /usr/share/derbynet/scripts/photostand.sh < <(/usr/local/bin/read-barcodes)
