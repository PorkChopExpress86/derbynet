# The timer protocol

Most pinewood derby timers talk to a computer over a serial or USB connection. A timer client reads the timer, and reports each heat to the DerbyNet server over HTTP. DerbyNet has two timer clients: the browser timer, a page of the server (`/timer.php`), and `derby-timer.jar`, the Java program. A timer that speaks HTTP itself could talk to the server directly with this protocol, and need neither.

This guide is the contract between a timer client and the server. The Django server keeps it as the PHP server had it (ADR-02, ADR-11). Every message here is part of the contract (D17). A few details still differ from the PHP server in ways a client does not see in the replies: a lane count above 32767, timer text longer than the server keeps, and when a silent timer's timeout is recorded. #307 settles them. The server code is `server/legacy_api/handlers/timer.py` and `server/racing/services/timer.py`, and the PHP original is `website/ajax/action.timer-message.inc`.

## Requests

A client sends each request as an HTTP POST to `action.php`, at the server's root or under `/derbynet/`. The body is form data, `application/x-www-form-urlencoded` or `multipart/form-data`. An `action` parameter names what the request does.

The server keeps the login in a session cookie, `sessionid`. Send the cookie the login sets with every later request. Send no `Origin` header: the server refuses a POST whose `Origin` names another site, as a browser on another site would send.

## Log in

`derby-timer.jar` first asks the server which roles may send timer messages, then logs in as one of them. Both requests are open to anyone (D23).

1. `GET action.php?query=role.list` answers JSON. Each role in `roles` has its `name`, and a `timer-message` value that is not 0 when the role may send timer messages. A new install answers:

   ```json
   {
       "roles": [
           {"name": "", "timer-message": 0},
           {"name": "Timer", "timer-message": 4096},
           {"name": "Photo", "timer-message": 0},
           {"name": "RaceCrew", "timer-message": 0},
           {"name": "RaceCoordinator", "timer-message": 4096}
       ]
   }
   ```

2. `POST action.php` with `action=role.login&name=Timer&password=` logs in. A new install's Timer role has an empty password. The answer is JSON, and sets the session cookie:

   ```json
   {
       "action": {"action": "role.login", "name": "Timer", "password": "..."},
       "outcome": {"summary": "success", "code": "success", "description": ""},
       "timecheck": "2026-11-13T23:07:50-0500"
   }
   ```

   The answer also carries `role`, the role's name, when the login gave a password. A login that fails answers `"summary": "failure"` with the code `role` (no such role) or `login` (wrong password).

The browser timer logs in through the login page, as RaceCoordinator on race day (D53).

Every timer message needs a login whose role may send timer messages. Without one, the server answers with a `notauthorized` failure.

## Messages to the server

A timer message is a POST with `action=timer-message` and a `message` parameter:

```
action=timer-message&message=HEARTBEAT
```

Two parameters may come with any message:

- `remote-start=YES` says the timer can open the start gate. See [Remote start](#remote-start).
- `overdue=MS` says the client's previous message is that many milliseconds late. The server records it in the timer's log. The browser timer sends it.

### HELLO

The client sends HELLO after it logs in, while it searches for the timer. The server puts the timer in its searching state, and starts a new timer log.

- `interface` (optional) names the client, such as `jar` or `web`. `build` (optional) is the client's build.

The answer always carries `<query/>`, which asks for a [FLAGS](#flags) message.

When another client has sent a message in the last second with a different `User-Agent`, the server answers `<competing>` with that client's `User-Agent`, and keeps the other client.

### IDENTIFIED

The client sends IDENTIFIED once it has found the timer. Every parameter is optional:

- `lane_count`: the number of lanes the timer reports. It sets the race's lane count only while no heat is scheduled. Once a schedule exists, the race keeps its count, and the timer status page and the race dashboard show the timer's count beside it (D74).
- `timer`: the timer's type. `derby-timer.jar` sends the name of its Java class.
- `human`: a name for the timer that people read, such as its maker and model.
- `ident`: the timer's own identity, such as a serial number.
- `options`: the timer's special features, as a comma-separated list of `key:value` pairs.
- `vid` and `pid`: the USB vendor and product ids.
- `confirmed`: `0` when the timer has not yet sent anything, so its identity is a guess. The server then holds the timer as unconfirmed. Anything else confirms it.

### STARTED

The client sends STARTED when it sees the start gate open. The server marks the heat as running, and tells the replay station that a race has started (see [the replay protocol](dev-replay-protocol.md)).

### FINISHED

The client sends FINISHED with the times and places the timer reports:

```
action=timer-message&message=FINISHED&roundid=7&heat=4&lane1=3.21&place1=2&lane2=3.33&place2=3&lane3=3.14&place3=1
```

- `laneN`: lane N's time in seconds.
- `placeN` (optional): lane N's place.
- `roundid` and `heat`: the heat the results are for. The server refuses them when they are not the current heat. `0` for either skips the check.

What the server does:

- When racing is off, it answers the failure `notracing` and records no result.
- When no round is current, it answers the failure `nocurrentround`.
- When `roundid` and `heat` name another heat, it answers the failure `wrong-heat`, and records nothing.
- When every time is zero, it stops racing for a person to look, and answers success. The replay station replays the heat.
- Otherwise it records the heat, and moves to the next heat. A lane that has a car but no time, a zero time, or a time that is not a number gets 9.999 seconds, and the heat counts as run (D42).
- When the event is read-only, it answers the failure `event-read-only`, and records nothing (D81).

The `nocurrentround` and `wrong-heat` answers carry `<success/>` after the failure.

With the lanes numbered from the other end of the track (the "reverse-lanes" setting), the server mirrors the lanes of the results.

### HEARTBEAT

The client sends HEARTBEAT every few seconds. It keeps the connection alive, and asks the server for the next heat.

- `unhealthy` (optional, no value): the timer cannot race right now.
- `confirmed` (optional): `0` when the timer has not yet sent anything.

The server answers `<competing>` when the client is not the one it keeps (see [HELLO](#hello)).

### MALFUNCTION

The client sends MALFUNCTION when it loses the timer, such as when a cable comes out.

- `detectable`: `1` when the client will know when the problem is fixed. The server then marks the timer as unhealthy until it hears otherwise. `0` when it will not, and the server shows the malfunction for 60 seconds.
- `error`: a message for people.

### FLAGS

The client sends FLAGS in answer to `<query/>`. It describes the client's settings, the serial ports it sees, and the timer types it knows. The race coordinator reads and changes them on the timer page.

- `flag-NAME=TYPE:VALUE`, one per setting, such as `flag-x=bool:false`.
- `desc-NAME=TEXT`, the setting's description, such as `desc-x=Run headless: without GUI`.
- `ports`, the serial ports, separated by commas, such as `ports=COM1,COM2`.
- `device-CLASS=TEXT`, one per timer type, such as `device-FastTrackDevice=FastTrack K-series`.

```
action=timer-message&message=FLAGS&flag-x=bool:false&desc-x=Run+headless:+without+GUI&ports=COM1%2CCOM2&device-FastTrackDevice=FastTrack+K-series
```

### Any other message

The server answers the failure `notunderstood`.

## Answers from the server

The server answers every timer message with XML. This answer to a HEARTBEAT, while racing is on, sends the first heat of a round:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<action-response action="timer-message" message="HEARTBEAT">
  <success/>
  <heat-ready lane-mask="15" class="Wolves" round="1" roundid="1" heat="1" lanes="4"/>
<remote-log send='false'/>
</action-response>
```

It is recorded from the PHP server in `server/tests/golden/http/timer/race-staging-and-results.http`, and `server/legacy_api/tests/test_timer_parity.py` checks that the Django server answers the same, byte for byte.

An answer holds the elements below. They come in this order: the message's own answer first, then `<abort />`, `<heat-ready>`, the assignments, `<remote-start/>`, `<remote-log>`, and the last `<abort/>`. A client reads every element of every answer, since one answer can carry several.

| Element | When |
|---|---|
| `<success/>` | The server handled the message. |
| `<failure code="CODE">TEXT</failure>` | The server did not handle it, or not all of it. The codes are `notracing`, `nocurrentround`, `wrong-heat`, `event-read-only`, `notunderstood`, and `notauthorized`. |
| `<competing>AGENT</competing>` | Another client holds the timer connection. See [HELLO](#hello). |
| `<query/>` | Send a FLAGS message. It follows every HELLO, and every assignment below. |
| `<abort />` | The heat the server sent in `<heat-ready>` is no longer the current heat. Forget it. |
| `<heat-ready .../>` | A heat is ready to race. See below. |
| `<assign-flag flag="NAME" value="VALUE"/>` | Change a setting. Some settings, such as `-x`, act only when the client starts. |
| `<assign-port port="PORT"/>` | Use this serial port. With no `port`, drop the port chosen before. |
| `<assign-device device="CLASS"/>` | Use this timer type. With no `device`, drop the type chosen before. |
| `<remote-start/>` | Open the start gate now. See [Remote start](#remote-start). |
| `<remote-log send="true"/>` | Turn remote logging on (`true`) or off (`false`). It comes with every answer. |
| `<abort/>` | Racing has stopped while the client was staging or running a heat. Stop it. |

### heat-ready

`<heat-ready>` comes while racing is on, the timer is connected and not already running a heat, and the current heat has no results yet. Its attributes:

- `lane-mask`: the lanes that have cars, as a decimal bit mask. Bit 0 is lane 1. `14` means cars in lanes 2, 3 and 4, and lane 1 empty. With reverse lanes, the mask is mirrored.
- `class`: the name of the class or group racing.
- `round`: the round's number within its class, 1 for the first round.
- `roundid`: the round's id, unique across every round of the event. Send it back with FINISHED.
- `heat`: the heat's number within its round, 1 for the first. Send it back with FINISHED.
- `lanes`: the number of lanes on the track.

On the timer test page, `roundid` is `-100`, and the mask holds every lane the test uses.

The server never sends `cease` (D17). Clients may still handle it, as both DerbyNet clients do.

## Remote logging

The client can send its log to the server, to help find a timer problem. `<remote-log send="true"/>` turns it on. The client then POSTs its log lines to `post-timer-log.php`, at the server's root or under `/derbynet/`, as a `text/plain` body. The server adds them to the timer's log. The request needs the same login as a timer message. The race coordinator turns remote logging on from the timer page.

## Remote start

Some timers can open the start gate with a solenoid. A client for such a timer adds `remote-start=YES` to every message. The server remembers it for 60 seconds after the last such message.

When the race coordinator presses "Start Race" on the race dashboard or the timer page, the server sends `<remote-start/>` in its next answer, once.
