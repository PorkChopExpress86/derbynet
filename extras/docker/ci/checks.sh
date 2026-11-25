#! /bin/bash
#
# Checks the Raspberry Pi images (#59) against a running server image.
# .github/workflows/pi-images.yml runs each check on GitHub's arm64 runner;
# on an amd64 machine they run under QEMU (binfmt for arm64 registered).
#
# Usage: extras/docker/ci/checks.sh [check ...]   (no check: every check)
#
# Images, each a tag the local Docker has:
#   SERVER_IMAGE      the server image (server/docker/Dockerfile)
#   TIMER_IMAGE       the timer image (extras/docker/timer.Dockerfile)
#   PHOTOSTAND_IMAGE  the photo stand image (extras/docker/photostand.Dockerfile)
# PREFIX (default pi-check) names every container, network, volume and
# Compose project a check makes, and each check removes them when it ends.
# LOGS (default a new folder under /tmp) keeps each container's log, and the
# timer check's derby-timer.log and timer-*.log.
#
# Checks:
#   timer             the timer image's derby-timer.jar, with its simulated
#                     timer, races every heat of a round on the server, with
#                     testing/race-with-derby-timer.sh's bounds and thread
#                     dumps.  It runs java in the image past
#                     timer-entrypoint.sh, which timer-args and
#                     timer-compose cover
#   timer-serial      the jar with no simulated timer opens the serial port
#                     passed in with --device through jssc's native library
#   timer-compose     compose.timer.yaml, run from a folder with its .env as
#                     docs/pi-setup.md runs it, races the round too, with the
#                     restart policy that starts it at boot
#   timer-args        TIMER_PASSWORD reaches the jar as one argument, and
#                     TIMER_ARGS splits at spaces with no file-name matching
#   photostand        a barcode on a stand-in scanner makes the photo stand
#                     take a photo with fswebcam's test source, upload it,
#                     and check the racer in; QUITQUITQUIT leaves it running
#   photostand-conf   the entrypoint writes each setting into
#                     /etc/derbynet.conf so the shell reads back what was
#                     passed, and leaves an empty one out; a single-quoted
#                     .env value comes through compose.photostand.yaml as typed
#   photostand-compose
#                     compose.photostand.yaml, run the same way, takes,
#                     uploads and checks in on a scan.  It passes in the
#                     machine's /dev/snd and USB bus, so .github/workflows/
#                     pi-images.yml runs it on GitHub's runner and not on a
#                     developer's machine, where the stand would speak
#
# The stand-in scanner is a FIFO under a folder mounted at /dev/input, which
# the photo stand's read-barcodes reads as it reads a real scanner's device;
# the key events are packed inside the container, so they have the
# platform's own layout.

set -euo pipefail
trap 'exit 143' TERM INT

ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
PREFIX="${PREFIX:-pi-check}"
LOGS="${LOGS:-$(mktemp -d)}"
mkdir -p "$LOGS"
NET="$PREFIX-net"
SERVER="$PREFIX-server"
WAIT_SECONDS="${WAIT_SECONDS:-900}"
SCANNER=usb-Stand-in_Scanner-event-kbd
BASE=""
COOKIES=""
STAND=""
ROUND=""
SCHEDULED=""
CONTAINER=""
PROJECTS=()

say() { printf '== %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

cleanup() {
    local name project
    for project in "${PROJECTS[@]}"; do
        docker compose -p "$project" logs --no-color > "$LOGS/$project.log" 2>&1 || true
        docker compose -p "$project" down -v >/dev/null 2>&1 || true
    done
    PROJECTS=()
    for name in "$PREFIX-timer" "$PREFIX-photostand" "$SERVER"; do
        if docker container inspect "$name" >/dev/null 2>&1; then
            docker logs "$name" > "$LOGS/$name.log" 2>&1 || true
            # A --rm container already on its way out refuses the rm.
            docker rm -f "$name" >/dev/null 2>&1 || true
        fi
    done
    docker network rm "$NET" >/dev/null 2>&1 || true
    docker volume rm "$PREFIX-server-data" >/dev/null 2>&1 || true
    [ -z "$COOKIES" ] || rm -f "$COOKIES"
    COOKIES=""
}
trap cleanup EXIT

# The coordinator's requests, through the published port.
coordinator() {
    curl -sf -b "$COOKIES" -c "$COOKIES" "$@"
}

# A server on its own network, with the roster testing/import-roster.sh
# imports, and the coordinator logged in.  Its port is published on
# SERVER_ADDRESS (default 127.0.0.1).
start_server() {
    : "${SERVER_IMAGE:?set SERVER_IMAGE}"
    docker network create "$NET" >/dev/null
    docker run -d --name "$SERVER" --network "$NET" -p "${SERVER_ADDRESS:-127.0.0.1}::80" \
        -v "$PREFIX-server-data:/var/lib/derbynet" "$SERVER_IMAGE" >/dev/null
    BASE="http://$(docker port "$SERVER" 80/tcp | sed -n 1p)"
    local _
    for _ in $(seq 120); do
        curl -fsS -o /dev/null "$BASE/index.php" 2>/dev/null && break
        sleep 1
    done
    grep -q success <<<"$(curl -sf -d 'action=setup.nodata&ez-new=pi-check' "$BASE/action.php")" \
        || fail "the server made no event"
    (cd "$ROOT" && bash testing/reset-database.sh "$BASE" && bash testing/import-roster.sh "$BASE") \
        > "$LOGS/roster.log" 2>&1 || { tail -n 20 "$LOGS/roster.log"; fail "the roster did not import"; }
    COOKIES="$(mktemp)"
    grep -q success <<<"$(coordinator -d 'action=role.login&name=RaceCoordinator&password=doyourbest' "$BASE/action.php")" \
        || fail "the coordinator cannot log in"
    say "server $SERVER_IMAGE answers at $BASE"
}

# Container $1's output, then any log file derby-timer.jar wrote in its
# working folder: the jar logs a timer loop's exceptions only there.
container_output() {
    docker logs "$1" 2>&1
    docker exec "$1" sh -c 'cat /var/lib/derbynet-timer/timer-*.log 2>/dev/null' 2>/dev/null || true
}

# Waits up to WAIT_SECONDS for a line matching $2 in container $1's output.
wait_for_log() {
    local name="$1" pattern="$2" _
    for _ in $(seq "$WAIT_SECONDS"); do
        grep -qE "$pattern" <<<"$(container_output "$name")" && return 0
        [ "$(docker container inspect -f '{{.State.Running}}' "$name" 2>/dev/null)" = true ] \
            || { docker logs "$name" 2>&1 | tail -n 40; fail "$name stopped before: $pattern"; }
        sleep 1
    done
    docker logs "$name" 2>&1 | tail -n 40
    fail "no line matching '$pattern' from $name in $WAIT_SECONDS s"
}

# A copy of compose file $1 in a folder with the .env lines on stdin, as
# docs/pi-setup.md has the owner make one.  Prints the folder.
compose_folder() {
    local folder="$LOGS/$PREFIX-compose"
    rm -rf "$folder"
    mkdir -p "$folder"
    cp "$ROOT/extras/docker/$1" "$folder/"
    cat > "$folder/.env"
    printf '%s\n' "$folder"
}

# Runs `docker compose -f $2 up -d` in folder $1 as project $3, as
# docs/pi-setup.md runs it, and checks the restart policy that starts the
# container again at boot.  CONTAINER names the container; cleanup takes
# the project down.  Not in a $(...): PROJECTS must reach cleanup.
compose_up() {
    PROJECTS+=("$3")
    (cd "$1" && docker compose -p "$3" -f "$2" up -d)
    CONTAINER="$(cd "$1" && docker compose -p "$3" -f "$2" ps -q)"
    [ "$(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$CONTAINER")" = unless-stopped ] \
        || fail "$2's container has no unless-stopped restart policy"
}

# The address of this machine on its network, as a Pi reaches the server.
lan_address() {
    hostname -I | awk '{print $1}'
}

round_done() {
    coordinator "$BASE/action.php?query=poll.coordinator" \
        | jq -e --argjson r "$ROUND" \
             '.rounds[] | select(.roundid == $r) | .heats_scheduled > 0 and .heats_run == .heats_scheduled' \
             >/dev/null
}

# A round scheduled and racing, as testing/race-with-derby-timer.sh sets it
# up: ROUND names it, SCHEDULED counts its heats.
schedule_round() {
    coordinator -d 'action=settings.write&n-lanes=4&unused-lane-mask=0' "$BASE/action.php" | jq -e '.outcome.summary == "success"' >/dev/null
    coordinator -d 'action=racer.bulk&who=all&what=checkin' "$BASE/action.php" | jq -e '.outcome.summary == "success"' >/dev/null
    ROUND="$(coordinator "$BASE/action.php?query=poll.coordinator" | jq '.rounds[0].roundid')"
    coordinator -d "action=schedule.generate&roundid=$ROUND" "$BASE/action.php" | jq -e '.outcome.summary == "success"' >/dev/null
    coordinator -d "action=heat.select&roundid=$ROUND&now_racing=1" "$BASE/action.php" | jq -e '.outcome.summary == "success"' >/dev/null
    SCHEDULED="$(coordinator "$BASE/action.php?query=poll.coordinator" | jq --argjson r "$ROUND" '.rounds[] | select(.roundid == $r) | .heats_scheduled')"
    say "round $ROUND has $SCHEDULED heats"
}

# Waits for every heat of ROUND to have a result, from timer container $1.
wait_for_round() {
    local name="$1" _
    for _ in $(seq "$WAIT_SECONDS"); do
        round_done && break
        sleep 1
    done
    docker logs "$name" > "$LOGS/timer-$name.log" 2>&1
    round_done || { tail -n 40 "$LOGS/timer-$name.log"; fail "$name did not race every heat of round $ROUND"; }
    say "derby-timer.jar in $name raced all $SCHEDULED heats of round $ROUND"
    coordinator "$BASE/action.php?query=poll.coordinator" \
        | jq -c --argjson r "$ROUND" '.rounds[] | select(.roundid == $r) | {roundid, heats_scheduled, heats_run}'
    grep -E 'Successful login|Starting derby-timer' "$LOGS/timer-$name.log" || true
}

# testing/race-with-derby-timer.sh's race, bounds and thread dumps, with the
# image's jar run in the timer image by timer-java (#518).  The jar's logs
# go to LOGS as well.
check_timer() {
    : "${TIMER_IMAGE:?set TIMER_IMAGE}"
    start_server
    local status=0
    # A race that fails before it starts the jar writes no log of its own.
    rm -f "$ROOT"/testing/derby-timer.log "$ROOT"/testing/timer-*.log
    PREFIX="$PREFIX" TIMER_IMAGE="$TIMER_IMAGE" JAVA="$ROOT/extras/docker/ci/timer-java" \
        bash "$ROOT/testing/race-with-derby-timer.sh" "$BASE" /usr/bin/derby-timer.jar || status=$?
    cp "$ROOT"/testing/derby-timer.log "$ROOT"/testing/timer-*.log "$LOGS/" 2>/dev/null || true
    [ "$status" = 0 ] || fail "testing/race-with-derby-timer.sh failed with the jar in $TIMER_IMAGE"
}

# With no -simulate-timer the jar opens the timer's port through jssc's
# native library for the platform, which a missing library fails with
# UnsatisfiedLinkError in the jar's log file.  /dev/null stands in for the timer as the device
# passed in at /dev/ttyUSB0, as compose.timer.yaml passes it: jssc's native
# open refuses it as no serial port, and the jar logs the refusal.
check_timer_serial() {
    : "${TIMER_IMAGE:?set TIMER_IMAGE}"
    start_server
    docker run -d --name "$PREFIX-timer" --network "$NET" \
        --device /dev/null:/dev/ttyUSB0 \
        -e DERBYNET_SERVER="http://$SERVER" -e TIMER_ARGS="-n /dev/ttyUSB0" \
        "$TIMER_IMAGE" >/dev/null
    wait_for_log "$PREFIX-timer" 'Successful login'
    wait_for_log "$PREFIX-timer" 'Port busy|Port not found|Permission denied|Incorrect serial port|UnsatisfiedLinkError'
    container_output "$PREFIX-timer" > "$LOGS/timer-serial.log"
    if grep -m 3 -E 'UnsatisfiedLinkError' "$LOGS/timer-serial.log"; then
        fail "jssc did not load its native library"
    fi
    say "derby-timer.jar in $TIMER_IMAGE tried /dev/ttyUSB0 through jssc's native library, which refused /dev/null"
    grep -E 'ttyUSB0|Port busy|Port not found|Permission denied|Incorrect serial port' "$LOGS/timer-serial.log" | sed -n 1,6p
}

# compose.timer.yaml with /dev/null for the serial port, against the server
# by this machine's address; the simulated timer races the round.
check_timer_compose() {
    : "${TIMER_IMAGE:?set TIMER_IMAGE}"
    SERVER_ADDRESS="$(lan_address)" start_server
    schedule_round
    local folder
    folder="$(compose_folder compose.timer.yaml <<ENV
DERBYNET_SERVER=$BASE
DERBYNET_TIMER_DEVICE=/dev/null
TIMER_ARGS=-simulate-timer -lanes 4 -pace 1
DERBYNET_TIMER_IMAGE=$TIMER_IMAGE
ENV
)"
    compose_up "$folder" compose.timer.yaml "$PREFIX-timer-compose"
    wait_for_round "$CONTAINER"
}

# A folder to mount at /dev/input, holding the stand-in scanner.  Prints it.
stand_in_input() {
    local input="$LOGS/stand-in-input"
    rm -rf "$input"
    mkdir -p "$input/by-id"
    mkfifo "$input/by-id/$SCANNER"
    printf '%s\n' "$input"
}

# Writes the key events of barcode $1 and Enter into the stand-in scanner of
# container STAND, packed by the container's own python3.
scan() {
    docker exec -i "$STAND" python3 -I - "$1" "/dev/input/by-id/$SCANNER" <<'PY'
import struct, sys
CODES = {c: k for k, c in {
    2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0",
    16: "q", 17: "w", 20: "t", 22: "u", 23: "i", 25: "p", 32: "d"}.items()}
event = struct.Struct("@llHHi")
out = b""
def key(code, value):
    global out
    out += event.pack(0, 0, 1, code, value) + event.pack(0, 0, 0, 0, 0)
for ch in sys.argv[1]:
    if ch.isupper():
        key(42, 1)
    key(CODES[ch.lower()], 1)
    key(CODES[ch.lower()], 0)
    if ch.isupper():
        key(42, 0)
key(28, 1)
key(28, 0)
with open(sys.argv[2], "wb") as f:
    f.write(out)
PY
}

# Car 101's row in racer.list: whether it passed, and its photos.
car_101() {
    coordinator "$BASE/action.php?query=racer.list" \
        | jq -c '.racers[] | select((.carnumber | tostring) == "101") | {racerid, passed, headshot, car_photo}'
}

# Scans PWD101 on STAND and waits for the upload's outcome; the log must
# show success.
scan_and_upload() {
    say "scanning PWD101"
    scan PWD101
    wait_for_log "$STAND" 'Announce: (success|upload-failed|upload-ok-but-checkin-failed)'
    docker logs "$STAND" > "$LOGS/$1.log" 2>&1
    grep -q 'Announce: success' "$LOGS/$1.log" \
        || { tail -n 40 "$LOGS/$1.log"; fail "the photo stand did not upload and check in"; }
    grep -E '^(Scanned|Checking in|Capturing|Uploading|Announce: (capture-ok|success))' "$LOGS/$1.log"
}

check_photostand() {
    : "${PHOTOSTAND_IMAGE:?set PHOTOSTAND_IMAGE}"
    start_server
    local input racer photo
    input="$(stand_in_input)"
    racer="$(car_101)"
    say "before the scan: $racer"
    jq -e '.passed == false and .headshot == ""' <<<"$racer" >/dev/null \
        || fail "car 101 starts checked in, or with a racer photo"
    # PHOTO_REPO=head and PHOTO_CHECKIN=1 differ from photostand.sh's
    # defaults (car, 0), so the photo landing as a racer photo and the racer
    # checked in show the settings reached the script.
    STAND="$PREFIX-photostand"
    docker run -d --name "$STAND" --network "$NET" \
        -v "$input:/dev/input" \
        -e DERBYNET_SERVER="http://$SERVER" \
        -e PHOTO_CAPTURE=fswebcam \
        -e FSWEBCAM_ARGS="--device TEST --resolution 640x480 --no-banner" \
        -e PHOTO_REPO=head -e PHOTO_CHECKIN=1 \
        "$PHOTOSTAND_IMAGE" >/dev/null
    wait_for_log "$STAND" 'Successfully logged in'
    scan_and_upload photostand
    racer="$(car_101)"
    say "after the scan: $racer"
    jq -e '.passed == true and .headshot != "" and .car_photo == ""' <<<"$racer" >/dev/null \
        || fail "car 101 has no racer photo, has a car photo, or is not checked in"
    photo="$(jq -r .headshot <<<"$racer")"
    curl -fsS -o "$LOGS/photostand-photo.jpg" "$BASE/${photo#/}"
    file "$LOGS/photostand-photo.jpg"
    say "the photo stand in $PHOTOSTAND_IMAGE took, uploaded and checked in car 101"
    # QUITQUITQUIT runs `sudo shutdown -h now`, which a container cannot do
    # (docs/pi-setup.md): the stand announces it and reads the next scan.
    scan QUITQUITQUIT
    wait_for_log "$STAND" 'Announce: terminating'
    scan PWD202
    wait_for_log "$STAND" 'Scanned PWD202'
    say "after QUITQUITQUIT the photo stand still reads scans"
    grep -E 'QUITQUITQUIT|terminating|shutdown|sudo|PWD202' <<<"$(docker logs "$STAND" 2>&1)" | sed -n 1,8p
}

check_photostand_conf() {
    : "${PHOTOSTAND_IMAGE:?set PHOTOSTAND_IMAGE}"
    local password='a"b$c`d e\f'"'"'g&h' conf
    conf="$(docker run --rm --entrypoint bash \
        -e DERBYNET_SERVER=http://example.invalid -e PHOTO_PASSWORD="$password" \
        -e PHOTO_REPO= "$PHOTOSTAND_IMAGE" -c \
        '/usr/local/bin/derbynet-photostand --write-conf-only && cat /etc/derbynet.conf && PHOTO_REPO=car && . /etc/derbynet.conf && printf "read back: %s|%s\n" "$PHOTO_PASSWORD" "$PHOTO_REPO"')"
    printf '%s\n' "$conf"
    grep -qxF "read back: $password|car" <<<"$conf" \
        || fail "the shell did not read back the password, or an empty PHOTO_REPO replaced the default"
    say "/etc/derbynet.conf reads back every setting as passed"
    # The same through compose.photostand.yaml and a .env written as
    # docs/pi-setup.md says, each value in single quotes: Compose itself
    # reads $ and # in an unquoted value (#59 review).  A .env value cannot
    # hold a single quote.
    local envpass='pa$sword#x&y "b`c\d e' folder
    stand_in_input > /dev/null
    folder="$(compose_folder compose.photostand.yaml <<ENV
DERBYNET_SERVER='http://example.invalid'
PHOTO_PASSWORD='$envpass'
DERBYNET_INPUT_DIR='$LOGS/stand-in-input'
DERBYNET_PHOTOSTAND_IMAGE='$PHOTOSTAND_IMAGE'
ENV
)"
    sed -n 2p "$folder/.env"
    PROJECTS+=("$PREFIX-conf")
    conf="$(cd "$folder" && docker compose -p "$PREFIX-conf" -f compose.photostand.yaml run --rm -T \
        --entrypoint bash photostand -c \
        '/usr/local/bin/derbynet-photostand --write-conf-only && . /etc/derbynet.conf && printf "read back: %s\n" "$PHOTO_PASSWORD"')"
    printf '%s\n' "$conf" | grep 'read back'
    grep -qxF "read back: $envpass" <<<"$conf" \
        || fail "the password did not come through compose.photostand.yaml's .env as typed"
    say "a single-quoted .env value reaches /etc/derbynet.conf as typed"
}

# TIMER_PASSWORD reaches the jar as one -p argument, spaces and all, and a
# `?` in TIMER_ARGS as typed, not matched against /usr/bin's files.  The
# jar logs each argument it got before it logs in; the server refuses the
# password, and the jar stops.
check_timer_args() {
    : "${TIMER_IMAGE:?set TIMER_IMAGE}"
    start_server
    docker run -d --name "$PREFIX-timer" --network "$NET" \
        -e DERBYNET_SERVER="http://$SERVER" -e TIMER_PASSWORD='pa ss*' \
        -e TIMER_ARGS='-obs-start /usr/bin/derby-time?.jar' \
        "$TIMER_IMAGE" >/dev/null
    local _ args
    for _ in $(seq "$WAIT_SECONDS"); do
        [ "$(docker container inspect -f '{{.State.Running}}' "$PREFIX-timer")" = true ] || break
        sleep 1
    done
    docker logs "$PREFIX-timer" 2>&1 | tail -n 3
    args="$( (docker cp "$PREFIX-timer:/var/lib/derbynet-timer" - | tar -xOf - --wildcards '*.log') 2>/dev/null \
        | sed -n '/Command Line Arguments/,/=====$/p' | awk -F '\t' '{print $NF}')" || true
    [ -n "$args" ] || fail "the jar wrote no argument list: it refused its command line"
    printf '%s\n' "$args"
    grep -qxF 'pa ss*' <<<"$args" || fail "TIMER_PASSWORD did not reach the jar as one argument"
    grep -qxF '/usr/bin/derby-time?.jar' <<<"$args" || fail "TIMER_ARGS's ? was matched against file names"
    say "the jar got TIMER_PASSWORD and TIMER_ARGS as given"
}

check_photostand_compose() {
    : "${PHOTOSTAND_IMAGE:?set PHOTOSTAND_IMAGE}"
    SERVER_ADDRESS="$(lan_address)" start_server
    local input folder racer
    input="$(stand_in_input)"
    # PHOTO_REPO is left out, so photostand.sh's default, car, holds.
    folder="$(compose_folder compose.photostand.yaml <<ENV
DERBYNET_SERVER=$BASE
PHOTO_CAPTURE=fswebcam
FSWEBCAM_ARGS=--device TEST --resolution 640x480 --no-banner
PHOTO_CHECKIN=1
DERBYNET_INPUT_DIR=$input
DERBYNET_PHOTOSTAND_IMAGE=$PHOTOSTAND_IMAGE
ENV
)"
    compose_up "$folder" compose.photostand.yaml "$PREFIX-photostand-compose"
    STAND="$CONTAINER"
    wait_for_log "$STAND" 'Successfully logged in'
    scan_and_upload photostand-compose
    racer="$(car_101)"
    say "after the scan: $racer"
    jq -e '.passed == true and .car_photo != "" and .headshot == ""' <<<"$racer" >/dev/null \
        || fail "car 101 has no car photo, has a racer photo, or is not checked in"
    say "compose.photostand.yaml's stand took, uploaded and checked in car 101"
}

check() {
    case "$1" in
        timer) check_timer ;;
        timer-serial) check_timer_serial ;;
        timer-compose) check_timer_compose ;;
        timer-args) check_timer_args ;;
        photostand) check_photostand ;;
        photostand-conf) check_photostand_conf ;;
        photostand-compose) check_photostand_compose ;;
        *) fail "no check named $1" ;;
    esac
}

[ $# -gt 0 ] || set -- timer timer-serial timer-compose timer-args photostand photostand-conf photostand-compose
say "logs in $LOGS"
for name in "$@"; do
    say "check $name"
    check "$name"
    cleanup
done
say "every check passed: $*"
