#!/bin/bash
# SPIKE #71: one command for the owner's Raspberry Pi 4 runs.  For each
# candidate (a = htmx + Alpine.js with View Transitions, a0 = the same without
# View Transitions, b = Svelte) and each page (now racing, standings reveal,
# intermission leaders) it opens the prototype full screen in Chromium the way
# extras/scripts/kiosk.sh does, drives the same scenario as tools/scenarios.js
# through the legacy API with curl, and keeps
#   - a Chromium performance trace (no DevTools needed: --trace-startup), and
#   - a screen recording, in a second pass so encoding does not skew the trace
#     (wf-recorder on Wayland, ffmpeg x11grab on X11; skipped if neither).
# Then it prints the dropped-frame table (summarize-trace.py).
#
# On the laptop that serves the prototypes:
#   prototypes/kiosk-spike/serve.sh 8771
#   prototypes/kiosk-spike/tools/fixture.sh http://127.0.0.1:8771
# On the Pi (Raspberry Pi OS desktop, Chromium installed, 1920x1080 display):
#   bash record-kiosk-spike.sh http://LAPTOP-IP:8771 [outdir]
# Environment: CANDS="a a0 b", PAGES="now-racing standings leaders",
# PASSES="trace video".
set -u
SERVER="${1:?usage: record-kiosk-spike.sh http://server:8771 [outdir]}"
SERVER="${SERVER%/}"
OUT="${2:-$HOME/kiosk-spike-$(date +%Y%m%d-%H%M%S)}"
CANDS="${CANDS:-a a0 b}"
PAGES="${PAGES:-now-racing standings leaders}"
PASSES="${PASSES:-trace video}"
HERE="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$OUT"
JAR="$OUT/cookies.txt"

if [ -z "${BROWSER:-}" ]; then
    test -x /usr/bin/chromium && BROWSER=chromium
    test -x /usr/bin/chromium-browser && BROWSER=chromium-browser
fi
[ -n "$BROWSER" ] || { echo "Chromium is not installed"; exit 1; }

if [ -n "${DISPLAY:-}" ]; then
    xset s off 2>/dev/null; xset -dpms 2>/dev/null; xset s noblank 2>/dev/null
fi
test -x /usr/bin/unclutter && { unclutter & }

post() { curl -s -b "$JAR" -c "$JAR" -d "$1" "$SERVER/action.php" >/dev/null; }
get() { curl -s -b "$JAR" -c "$JAR" "$SERVER/action.php?$1"; }

post "action=role.login&name=RaceCoordinator&password=doyourbest"
ROUNDID=$(get "query=poll&values=rounds" | python3 -c '
import json, sys
print(next(r["roundid"] for r in json.load(sys.stdin)["rounds"]
           if str(r["class"]).startswith("Lions") and r["round"] == 1))')
TIMERJAR="$OUT/timer-cookies.txt"
tpost() { curl -s -b "$TIMERJAR" -c "$TIMERJAR" -d "$1" "$SERVER/action.php" >/dev/null; }
curl -s -c "$TIMERJAR" -d "action=role.login&name=Timer&password=" "$SERVER/action.php" >/dev/null

assign_all() {  # page params-json: every kiosk, one write
    curl -s -b "$JAR" -c "$JAR" --data-urlencode "all=$1#$2" -d action=kiosk.assign \
         "$SERVER/action.php" >/dev/null
}

NEXT_HEAT=1
race_heat() {  # heat: stage it afresh, then the timer reports it
    post "action=result.delete&roundid=$ROUNDID&heat=$1"
    tpost "action=timer-message&message=HELLO"
    tpost "action=timer-message&message=IDENTIFIED&nlanes=4"
    post "action=heat.select&roundid=$ROUNDID&heat=$1&now_racing=1"
    sleep 1.5
    tpost "action=timer-message&message=STARTED"
    sleep 1.5
    tpost "action=timer-message&message=FINISHED&lane1=2.$RANDOM&lane2=3.$RANDOM&lane3=2.$RANDOM&lane4=3.$RANDOM"
}

before() {  # page
    case "$1" in
        standings) assign_all spike/standings '{"key":"st-r2","reveal":0}' ;;
        now-racing)
            [ $NEXT_HEAT -gt 15 ] && NEXT_HEAT=1
            post "action=result.delete&roundid=$ROUNDID&heat=$NEXT_HEAT"
            post "action=heat.select&roundid=$ROUNDID&heat=$NEXT_HEAT&now_racing=0" ;;
    esac
}

scenario() {  # page; about 30 s, as tools/scenarios.js
    case "$1" in
        now-racing)
            sleep 2
            for _ in 1 2; do race_heat $NEXT_HEAT; NEXT_HEAT=$((NEXT_HEAT + 1)); sleep 8; done ;;
        standings)
            sleep 0.5
            assign_all spike/standings '{"key":"st-r2","reveal":"all"}'
            sleep 32 ;;
        leaders) sleep 20 ;;
    esac
}

url_of() {  # cand page
    local a="/spike/a" vt=""
    [ "$1" = a0 ] && vt="vt=0&"
    case "$1:$2" in
        a*:now-racing) echo "$SERVER$a/now-racing/?${vt}" ;;
        a*:standings) echo "$SERVER$a/standings/?${vt}address=pi-$1" ;;
        a*:leaders) echo "$SERVER$a/leaders/?${vt}" ;;
        b:now-racing) echo "$SERVER/spike/b/now-racing.html" ;;
        b:standings) echo "$SERVER/spike/b/standings.html?address=pi-b" ;;
        b:leaders) echo "$SERVER/spike/b/leaders.html" ;;
    esac
}

REC=""
start_recorder() {  # file; sets REC to the recorder's pid
    REC=""
    if [ -n "${WAYLAND_DISPLAY:-}" ] && command -v wf-recorder >/dev/null; then
        wf-recorder -f "$1.mp4" </dev/null >/dev/null 2>&1 &
        REC=$!
    elif [ -n "${DISPLAY:-}" ] && command -v ffmpeg >/dev/null; then
        ffmpeg -loglevel error -f x11grab -framerate 30 -video_size 1920x1080 -i "$DISPLAY" \
               -c:v libx264 -preset ultrafast -crf 28 "$1.mp4" </dev/null >/dev/null 2>&1 &
        REC=$!
    else
        echo "   (no wf-recorder or ffmpeg: no recording)"
    fi
}

TRACE_CATS="cc,viz,benchmark,devtools.timeline,disabled-by-default-devtools.timeline,disabled-by-default-devtools.timeline.frame"
for cand in $CANDS; do
    for page in $PAGES; do
        for pass in $PASSES; do
            name="$cand-$page"
            profile=$(mktemp -d)
            args=(--noerrdialogs --no-default-browser-check --disable-session-crashed-bubble
                  --disable-infobars --no-first-run --user-data-dir="$profile" --kiosk
                  ${CHROMIUM_EXTRA_ARGS:-})
            if [ "$pass" = trace ]; then
                rm -f "$OUT/$name.trace.json"
                args+=(--trace-startup="$TRACE_CATS" --trace-startup-format=json
                       --trace-startup-file="$OUT/$name.trace.json"
                       --trace-startup-duration=40)
            fi
            before "$page"
            echo "== $name ($pass)"
            "$BROWSER" "${args[@]}" "$(url_of "$cand" "$page")" >/dev/null 2>&1 &
            BPID=$!
            sleep 6   # load
            REC=""
            [ "$pass" = video ] && start_recorder "$OUT/$name"
            scenario "$page"
            if [ -n "$REC" ]; then kill -INT "$REC" 2>/dev/null; wait "$REC" 2>/dev/null; fi
            if [ "$pass" = trace ]; then
                for _ in $(seq 30); do [ -s "$OUT/$name.trace.json" ] && break; sleep 1; done
                sleep 2
            fi
            kill "$BPID" 2>/dev/null; wait "$BPID" 2>/dev/null
            sleep 2
            rm -rf "$profile" 2>/dev/null
        done
    done
done

python3 "$HERE/summarize-trace.py" "$OUT"/*.trace.json | tee "$OUT/frames.txt"
echo "Traces, recordings and frames.txt are in $OUT"
