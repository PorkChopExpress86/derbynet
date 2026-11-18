#! /bin/sh
#
# The timer image's entrypoint (#59).  It does what extras/scripts/derby-timer.sh
# does on a Pi install, with its settings from the environment in place of
# /etc/derbynet.conf and /boot/derbynet.conf:
#
#   DERBYNET_SERVER  the server's address, with no slash at the end (required)
#   TIMER_ARGS       more derby-timer.jar options, split at spaces as
#                    derby-timer.sh splits them, such as "-n /dev/ttyUSB0"
#
# The jar always runs headless (-x), since a container has no screen.  It waits
# for the server first, as derby-timer.sh does, but 5 s between tries, so a Pi
# that boots before the server logs one line per try.

set -eu

if [ -z "${DERBYNET_SERVER:-}" ]; then
    echo "derbynet-timer: set DERBYNET_SERVER to the server's address, such as http://192.168.1.20" >&2
    exit 64
fi

until curl --insecure --connect-timeout 6 --location --silent --output /dev/null \
        "$DERBYNET_SERVER/index.php"; do
    echo "Waiting for $DERBYNET_SERVER"
    sleep 5
done
echo "Starting derby-timer.jar against $DERBYNET_SERVER"

# shellcheck disable=SC2086  # TIMER_ARGS is split at spaces, as derby-timer.sh splits it.
exec java -jar /usr/bin/derby-timer.jar -x ${TIMER_ARGS:-} "$DERBYNET_SERVER"
