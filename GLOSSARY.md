# DerbyNet

DerbyNet runs a pinewood derby race day: registering racers, checking cars in, scheduling heats, timing them, and presenting results and awards.

## Race structure

**Pack**:
Everyone taking part in one event. The label is configurable (`supergroup-label`).
_Avoid_: Supergroup (the setting's name, not a word for people)

**Partition**:
The division a racer registers into, such as a den, or Scouts and Outlaws. Every racer belongs to exactly one partition. The label is configurable (`partition-label`, default "Den").
_Avoid_: Den (one possible label, not the concept), division

**Group**:
A set of racers who race against each other in the same rounds. Racers in different groups never share a heat.
_Avoid_: Class (the legacy name)

**Subgroup**:
A part of a group whose racers are ranked separately for standings and awards, while still racing with the rest of the group.
_Avoid_: Rank (the legacy name)

**Group formation rule**:
The event setting that decides how partitions become groups and subgroups. It is one of by partition, one group, or custom.

**By partition**:
The group formation rule where each partition is its own group with one subgroup, so partitions never race each other. The default.

**One group**:
The group formation rule where all racers form one group and each partition is a subgroup of it.

**Custom**:
The group formation rule where the race coordinator arranges groups and subgroups by hand.

## Race day

**Race coordinator**:
The person running the race from the dashboard, who decides which rounds race and who races in them.
_Avoid_: Operator, race manager

**Final**:
A later round of a group whose racers are the top finishers of the round before it.
_Avoid_: Finals round, follow-on round (a follow-on round need not be a final)

**Playlist**:
The ordered list of rounds the race coordinator queues for the day. Each entry is a playlist step.
_Avoid_: Queue, schedule (a schedule is one round's heats)

**Race crew**:
The volunteers who check cars in and help run the track.

**Check-in**:
The race crew weighs a car and inspects its dimensions. A racer whose car passes check-in is eligible to race.
_Avoid_: Inspection, registration (registration only enters the racer)

**Speed award**:
An award decided by race times: first, second, and third place, fastest in a group or subgroup, and any other award placed by time.
_Avoid_: Speed trophy (one award type, not the concept)

**Judged award**:
An award that people choose, such as best paint job or most scout-like.
_Avoid_: Design award (only one kind of judged award)

**Award scope**:
The racers an award can go to: one partition, or all partitions.

**All-partition award**:
An award open to all partitions, such as fastest in the pack. A partition such as Parents or Siblings can be set to stay out of all-partition awards.

**Not eligible for any award**:
A racer who races but can win neither speed awards nor judged awards.
_Avoid_: Excluded (the legacy field is `exclude`)

**Not eligible for speed awards**:
A racer who races and can win judged awards, but no speed awards.

**Staged heat**:
The next heat to run, whose cars the race crew is lining up, or has lined up, while the current heat runs. It is the next heat of the current round, or under the master schedule the next heat in master order, which can be in another round.
_Avoid_: Next-up (the legacy name)

**On-deck report**:
The display that shows the race crew the photos, names, and car numbers of the cars in the coming heats.

**Rerun**:
Racing a heat again after the timer fails, so that the new times replace the old ones.

**Late racer**:
A racer who passes check-in after some heats of their round have already run.
_Avoid_: Late arrival, latecomer

**Departed racer**:
A racer taken off a round's roster, or whose check-in was reverted, whose car leaves its unraced heats. A racer who only goes home is not departed.

**Closed lane**:
A lane the race coordinator takes out of use during a round because it is damaged. It stays closed in the following rounds until the race coordinator reopens it, which is possible only before the next round is scheduled.
