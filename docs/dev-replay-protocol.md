# The replay protocol

The replay station records each heat with a camera, and plays the finish back in slow motion on its screen. The server tells it when a heat starts, when the race starts, and when to play the clip. This guide is the contract between the server and a replay client.

DerbyNet's own replay client is the replay page of the server, `/replay/`. The older XML replay messages, for a separate replay program, are gone (D9). The protocol here is the JSON one, `replay.message`, which the replay page uses. The server code is `server/racing/services/replay.py` and `server/legacy_api/handlers/replay.py`, and the replay page's script is `server/ui/static/ui/replay.js`.

## Log in

Every request below needs a login whose role controls the race, such as RaceCoordinator (D16). The replay page logs in through the login page. Another client logs in with `action=role.login`, as in [the timer protocol](dev-timer-protocol.md#log-in), and sends the session cookie with every request.

## Poll for commands

The client polls the server with a POST to `action.php`, at the server's root or under `/derbynet/`:

```
action=replay.message&status=0&finished-replay=0
```

- `status` is the client's state: `0` ready, `1` recording, `2` playing, or a negative number for an error. The race dashboard shows it.
- `finished-replay` is accepted and not used.

The answer is JSON. `replay` lists the commands queued since the last poll, oldest first. After the race coordinator presses "Trigger Replay":

```json
{
    "action": {"action": "replay.message", "status": 0, "finished-replay": 0},
    "outcome": {"summary": "success", "code": "success", "description": ""},
    "replay": ["TEST 4000 2 50"]
}
```

The replay page polls four times a second. A client that has not polled for more than 10 seconds counts as gone: the server queues no commands for it, so a client that comes back never gets a burst of stale ones. Its first poll after that starts with `HELLO`. Each command goes to one poll only.

## Commands

Each command is a line of text: a word, then its values, separated by spaces.

| Command | When the server sends it | What the client does |
|---|---|---|
| `HELLO` | First, in the first poll after the client was gone. | Nothing. |
| `START NAME` | A heat becomes the current heat while racing is on: racing turns on, the heat advances, or a heat is run again. | Names the next clip `NAME`, such as `Lions_Round1_Heat04`. The timer test names its heats `Timer Test_Round1_HeatNN`. |
| `CANCEL` | Racing turns off, or the current heat changes. | Drops any replay it has waiting. |
| `RACE_STARTS SKIPBACK SHOWINGS RATE` | The timer reports that the gate opened (its STARTED message). | Waits `SKIPBACK` milliseconds, then replays. A `REPLAY` that comes first replays at once instead, and a `REPLAY` that comes after is ignored. |
| `REPLAY SKIPBACK SHOWINGS RATE` | The timer reports the heat's results (FINISHED), or someone asks for a replay. | Replays, unless the wait after `RACE_STARTS` already replayed this heat. |
| `TEST SKIPBACK SHOWINGS RATE` | The race coordinator presses "Trigger Replay" to test the station. | Replays. |

The values:

- `SKIPBACK`: milliseconds of video before the finish to play, 4000 by default (D18).
- `SHOWINGS`: how many times to play the clip, 2 by default.
- `RATE`: the playback speed as a percentage, 50 by default for half speed.

The race coordinator sets them on the settings page, in the Replay section.

## The clip

The replay page keeps recording for a short time after the finish, 2 seconds by default, so the clip shows the cars cross the line. The clip runs from `SKIPBACK` before the finish to that time after it, and is never longer than the longest clip, 10 seconds by default (D18). The settings page sets both, in the Replay section. The page reads them when it loads. They do not come with the commands.

## Upload the clip

When the settings say to upload videos, the client uploads each clip with a multipart POST to `action.php`:

| Field | Value |
|---|---|
| `action` | `video.upload` |
| `video` | The clip, as a file named `NAME.mkv` or `NAME.webm`, where `NAME` comes from `START`. |
| `MAX_FILE_SIZE` | Optional. The largest file the client means to send, in bytes. |

The server keeps the clip in the event's videos folder. It refuses a clip larger than `DERBYNET_VIDEO_UPLOAD_MAX_MB`, 100 MB by default, and any other file type. A name already in use gets a number added, such as `-1`. The upload needs the same login as the polls (D16).

## Other requests

| Request | What it does |
|---|---|
| `action=replay.test` | Queues `TEST`, as "Trigger Replay" does. |
| `action=replay.trigger` | Queues `REPLAY` now. |

Both need a role that controls the race, and answer JSON.

## The remote camera

A phone can be the replay camera (`/replay/camera/`). It sends its video to the replay page over WebRTC. The two pages find each other through a message relay on the server:

- `action=message.send` with `message[recipient]=ID` and the message's other fields, as a form array, sends a message.
- `action=message.retrieve&recipient=ID` answers JSON, with `messages` listing the messages waiting for `ID`. Each message goes once.

The camera's id is `camera-replay`. Each viewer has an id of its own, starting with `viewer-`. The messages are `solicitation` (a viewer asks for video, with the size and frame rate it wants), `offer` and `answer` (with the WebRTC session description), and `ice-candidate`. A message larger than 64 KiB is refused with `too-large` (D59). Both requests need a role that controls the race (D16).

The settings page sets the STUN servers both pages use, in the Replay section (D76). With none, the camera works on a local network with no internet.
