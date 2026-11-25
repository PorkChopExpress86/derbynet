---- MODULE HeatIntermission ----
EXTENDS Naturals, Sequences

(*
 * Formal model of DerbyNet's current-heat state machine, result services,
 * and playlist operator-gate intermission (owner decisions D27, D28, D32, D135, D136).
 *
 * Scope and bounds:
 *   - One group with a 2-step playlist:
 *       Step 1: Round 1 ("R1") with 2 heats (1, 2), feeder round.
 *       Step 2: Round 2 ("R2") with 1 heat (1), waiting final (waits_for_operator = TRUE).
 *   - Each service call runs under lock_race_state(event.id) as one atomic step.
 *   - R1 heat results are abstracted to {"none", "v1", "v2"} so any result change
 *     that alters the finalist cut ("v1" <-> "v2" or "none") is observable.
 *
 * Covered source files (sync commit: 7c3cd879a90246a83044ae86ece6206935892679):
 *   - server/racing/engine/heat_state.py
 *   - server/racing/services/heats.py
 *   - server/racing/services/results.py
 *   - server/racing/services/playlist.py
 *)

VARIABLES
    curRound,       \* "none", "R1", "R2" (RaceState.current_round_id)
    curHeat,        \* 0 (none), 1, 2 (RaceState.current_heat)
    racing,         \* BOOLEAN (RaceState.racing)
    waiting,        \* BOOLEAN (RaceState.waiting_entry = Final, ended_round = R1)
    r1,             \* [1..2 -> {"none", "v1", "v2"}] (HeatLane results for R1)
    r2,             \* {"none", "done"} (HeatLane result for Final R2 heat 1)
    finalBuilt,     \* BOOLEAN (Final has RosterEntry + HeatLane rows)
    finalRoster,    \* <<r1[1], r1[2]>> snapshot from which Final's roster was built
    lastHeatRound,  \* "none", "R1", "R2" (RaceState.last_heat["round_id"])
    lastHeatNum,    \* 0, 1, 2 (RaceState.last_heat["heat"])
    lastHeatSaved,  \* "none", "v1", "v2", "done" (recoverable saved result from rerun)
    discardRound,   \* "none", "R1", "R2" (saved target of discard_heat for restore_results)
    discardNum,     \* 0, 1, 2
    discardVal,     \* "none", "v1", "v2", "done"
    nonCoordBreak   \* BOOLEAN: TRUE if a non-CONTROL_RACE caller altered R1 after R2 raced

vars == <<
    curRound, curHeat, racing, waiting,
    r1, r2, finalBuilt, finalRoster,
    lastHeatRound, lastHeatNum, lastHeatSaved,
    discardRound, discardNum, discardVal,
    nonCoordBreak
>>

HeatHasResult(rnd, h) ==
    IF rnd = "R1" THEN r1[h] /= "none"
    ELSE IF rnd = "R2" THEN r2 /= "none"
    ELSE FALSE

HeatResult(rnd, h) ==
    IF rnd = "R1" THEN r1[h]
    ELSE IF rnd = "R2" THEN r2
    ELSE "none"

HeatExists(rnd, h) ==
    (rnd = "R1" /\ h \in {1, 2}) \/ (rnd = "R2" /\ h = 1 /\ finalBuilt)

R1Complete(r1Val) == r1Val[1] /= "none" /\ r1Val[2] /= "none"

FinalHasResult(r2Val) == r2Val /= "none"

(* close_intermission_on_result (D136), which rebuild_waiting_final calls
   after every result change: the intermission stays open only while the
   final has no result. *)
StillWaiting(waitingBefore, r2After) == waitingBefore /\ ~FinalHasResult(r2After)

(* Rebuild waiting final (D27): heats.py:342-358, playlist.py:319-360 *)
RebuiltRoster(endedBefore, changedRnd, waitingAfter, r1After, r2After, rosterAfter) ==
    IF endedBefore /\ changedRnd = "R1" /\ waitingAfter /\ ~FinalHasResult(r2After)
    THEN r1After
    ELSE rosterAfter

(* Initial state: start_playlist activated R1 heat 1 with racing on *)
Init ==
    /\ curRound = "R1"
    /\ curHeat = 1
    /\ racing = TRUE
    /\ waiting = FALSE
    /\ r1 = <<"none", "none">>
    /\ r2 = "none"
    /\ finalBuilt = FALSE
    /\ finalRoster = <<"none", "none">>
    /\ lastHeatRound = "none"
    /\ lastHeatNum = 0
    /\ lastHeatSaved = "none"
    /\ discardRound = "none"
    /\ discardNum = 0
    /\ discardVal = "none"
    /\ nonCoordBreak = FALSE

(* record_timer_result on R1: results.py:117-177, heat_state.py:644-684, playlist.py:170-216.
   The hook's _after_round rebuilds a built final that has no result from the
   standings (D135), so the roster follows r1 whether or not the intermission
   was open before the result. A final that has a result does not wait
   (_is_raced, and _waits for D136). *)
TimerFinishR1(val) ==
    /\ racing
    /\ curRound = "R1"
    /\ val \in {"v1", "v2"}
    /\ LET r1New == [r1 EXCEPT ![curHeat] = val]
           endedBefore == waiting
       IN
       /\ r1' = r1New
       /\ r2' = r2
       /\ lastHeatRound' = "R1"
       /\ lastHeatNum' = curHeat
       /\ lastHeatSaved' = "none"
       /\ IF curHeat = 1 THEN
            IF r1New[2] = "none" THEN
                /\ curRound' = "R1"
                /\ curHeat' = 2
                /\ racing' = TRUE
                /\ waiting' = StillWaiting(waiting, r2)
                /\ finalBuilt' = finalBuilt
                /\ finalRoster' = RebuiltRoster(endedBefore, "R1", waiting, r1New, r2, finalRoster)
            ELSE
                \* target_refused_after_round: Move(R1, 2), OFF, PlaylistHook(R1)
                /\ IF FinalHasResult(r2) THEN
                      /\ curRound' = "R1"
                      /\ curHeat' = 2
                      /\ racing' = FALSE
                      /\ waiting' = StillWaiting(waiting, r2)
                      /\ finalBuilt' = finalBuilt
                      /\ finalRoster' = finalRoster
                   ELSE
                      /\ finalBuilt' = TRUE
                      /\ curRound' = "R2"
                      /\ curHeat' = 1
                      /\ racing' = FALSE
                      /\ waiting' = TRUE
                      /\ finalRoster' = r1New  \* round_ended builds or rebuilds the unraced final (D135)
          ELSE \* curHeat = 2
            IF R1Complete(r1New) THEN
                \* no_target_after_round: OFF, PlaylistHook(R1)
                /\ IF FinalHasResult(r2) THEN
                      /\ curRound' = "R1"
                      /\ curHeat' = 2
                      /\ racing' = FALSE
                      /\ waiting' = StillWaiting(waiting, r2)
                      /\ finalBuilt' = finalBuilt
                      /\ finalRoster' = finalRoster
                   ELSE
                      /\ finalBuilt' = TRUE
                      /\ curRound' = "R2"
                      /\ curHeat' = 1
                      /\ racing' = FALSE
                      /\ waiting' = TRUE
                      /\ finalRoster' = r1New  \* round_ended builds or rebuilds the unraced final (D135)
            ELSE
                \* no_target: OFF
                /\ curRound' = "R1"
                /\ curHeat' = 2
                /\ racing' = FALSE
                /\ waiting' = StillWaiting(waiting, r2)
                /\ finalBuilt' = finalBuilt
                /\ finalRoster' = RebuiltRoster(endedBefore, "R1", waiting, r1New, r2, finalRoster)
       /\ UNCHANGED <<discardRound, discardNum, discardVal, nonCoordBreak>>

(* record_timer_result on R2: results.py:117-177, heat_state.py:678-684 *)
TimerFinishR2 ==
    /\ racing
    /\ curRound = "R2"
    /\ r2' = "done"
    /\ racing' = FALSE
    /\ lastHeatRound' = "R2"
    /\ lastHeatNum' = 1
    /\ lastHeatSaved' = "none"
    /\ waiting' = StillWaiting(waiting, "done")  \* D136
    /\ UNCHANGED <<curRound, curHeat, r1, finalBuilt, finalRoster,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* record_manual_result on R1: results.py:180-226, heat_state.py:686-717 *)
ManualWriteR1(val, isCoord) ==
    /\ curRound = "R1"
    /\ val \in {"v1", "v2"}
    /\ (isCoord \/ ~FinalHasResult(r2))  \* locked_failure in results.py:198
    /\ LET r1New == [r1 EXCEPT ![curHeat] = val]
           endedBefore == waiting
       IN
       /\ r1' = r1New
       /\ r2' = r2
       /\ lastHeatRound' = "R1"
       /\ lastHeatNum' = curHeat
       /\ lastHeatSaved' = "none"
       /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ FinalHasResult(r2)))
       /\ IF ~racing THEN
            \* heat_state.py:717: ManualResult while paused does not advance
            /\ curRound' = curRound
            /\ curHeat' = curHeat
            /\ racing' = FALSE
            /\ waiting' = StillWaiting(waiting, r2)
            /\ finalBuilt' = finalBuilt
            /\ finalRoster' = RebuiltRoster(endedBefore, "R1", waiting, r1New, r2, finalRoster)
          ELSE IF curHeat = 1 THEN
            IF r1New[2] = "none" THEN
                /\ curRound' = "R1"
                /\ curHeat' = 2
                /\ racing' = TRUE
                /\ waiting' = StillWaiting(waiting, r2)
                /\ finalBuilt' = finalBuilt
                /\ finalRoster' = RebuiltRoster(endedBefore, "R1", waiting, r1New, r2, finalRoster)
            ELSE
                /\ IF FinalHasResult(r2) THEN
                      /\ curRound' = "R1"
                      /\ curHeat' = 2
                      /\ racing' = FALSE
                      /\ waiting' = StillWaiting(waiting, r2)
                      /\ finalBuilt' = finalBuilt
                      /\ finalRoster' = finalRoster
                   ELSE
                      /\ finalBuilt' = TRUE
                      /\ curRound' = "R2"
                      /\ curHeat' = 1
                      /\ racing' = FALSE
                      /\ waiting' = TRUE
                      /\ finalRoster' = r1New  \* round_ended builds or rebuilds the unraced final (D135)
          ELSE \* racing /\ curHeat = 2
            IF R1Complete(r1New) THEN
                /\ IF FinalHasResult(r2) THEN
                      /\ curRound' = "R1"
                      /\ curHeat' = 2
                      /\ racing' = FALSE
                      /\ waiting' = StillWaiting(waiting, r2)
                      /\ finalBuilt' = finalBuilt
                      /\ finalRoster' = finalRoster
                   ELSE
                      /\ finalBuilt' = TRUE
                      /\ curRound' = "R2"
                      /\ curHeat' = 1
                      /\ racing' = FALSE
                      /\ waiting' = TRUE
                      /\ finalRoster' = r1New  \* round_ended builds or rebuilds the unraced final (D135)
            ELSE
                /\ curRound' = "R1"
                /\ curHeat' = 2
                /\ racing' = FALSE
                /\ waiting' = StillWaiting(waiting, r2)
                /\ finalBuilt' = finalBuilt
                /\ finalRoster' = RebuiltRoster(endedBefore, "R1", waiting, r1New, r2, finalRoster)
       /\ UNCHANGED <<discardRound, discardNum, discardVal>>

(* record_manual_result on R2: results.py:180-226, heat_state.py:716-717 *)
ManualWriteR2 ==
    /\ curRound = "R2"
    /\ r2' = "done"
    /\ racing' = FALSE
    /\ lastHeatRound' = "R2"
    /\ lastHeatNum' = 1
    /\ lastHeatSaved' = "none"
    /\ waiting' = StillWaiting(waiting, "done")  \* D136
    /\ UNCHANGED <<curRound, curHeat, r1, finalBuilt, finalRoster,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* select_heat: heats.py:387-410, heat_state.py:600-605 *)
SelectHeat(rnd, h) ==
    /\ HeatExists(rnd, h)
    /\ (curRound /= rnd \/ curHeat /= h \/ racing \/ waiting)
    /\ curRound' = rnd
    /\ curHeat' = h
    /\ racing' = FALSE
    /\ waiting' = FALSE  \* CloseIntermission()
    /\ UNCHANGED <<r1, r2, finalBuilt, finalRoster,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* set_racing(True): heats.py:413-415, heat_state.py:574-596 *)
SetRacingOn ==
    /\ ~racing
    /\ curRound /= "none"
    /\ ~HeatHasResult(curRound, curHeat)
    /\ racing' = TRUE
    /\ waiting' = FALSE  \* By.MANAGER emits CloseIntermission()
    /\ UNCHANGED <<curRound, curHeat, r1, r2, finalBuilt, finalRoster,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* set_racing(False): heats.py:413-415, heat_state.py:597-598 *)
SetRacingOff ==
    /\ racing
    /\ racing' = FALSE
    /\ UNCHANGED <<curRound, curHeat, waiting, r1, r2, finalBuilt, finalRoster,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* rerun: heats.py:445-476, heat_state.py:718-733 *)
Rerun(which, isCoord) ==
    /\ which \in {"current", "last"}
    /\ LET tgtRnd == IF which = "current" THEN curRound ELSE lastHeatRound
           tgtNum == IF which = "current" THEN curHeat ELSE lastHeatNum
       IN
       /\ HeatExists(tgtRnd, tgtNum)
       /\ (isCoord \/ tgtRnd /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in heats.py:466
       /\ curRound' = tgtRnd
       /\ curHeat' = tgtNum
       /\ racing' = TRUE
       /\ waiting' = FALSE  \* By.RERUN emits CloseIntermission() (D136)
       /\ lastHeatRound' = tgtRnd
       /\ lastHeatNum' = tgtNum
       /\ lastHeatSaved' = HeatResult(tgtRnd, tgtNum)
       /\ IF tgtRnd = "R1" THEN
            LET r1New == [r1 EXCEPT ![tgtNum] = "none"]
            IN
            /\ r1' = r1New
            /\ r2' = r2
            /\ finalRoster' = RebuiltRoster(waiting, "R1", FALSE, r1New, r2, finalRoster)
          ELSE
            /\ r1' = r1
            /\ r2' = "none"
            /\ finalRoster' = finalRoster
       /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ tgtRnd = "R1" /\ FinalHasResult(r2)))
       /\ UNCHANGED <<finalBuilt, discardRound, discardNum, discardVal>>

(* reinstate: heats.py:479-500, heat_state.py:734-741 *)
Reinstate(isCoord) ==
    /\ lastHeatRound /= "none"
    /\ lastHeatSaved /= "none"
    /\ (isCoord \/ lastHeatRound /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in heats.py:491
    /\ racing' = FALSE
    /\ lastHeatRound' = "none"
    /\ lastHeatNum' = 0
    /\ lastHeatSaved' = "none"
    /\ IF lastHeatRound = "R1" THEN
         LET r1New == [r1 EXCEPT ![lastHeatNum] = lastHeatSaved]
         IN
         /\ r1' = r1New
         /\ r2' = r2
         /\ waiting' = StillWaiting(waiting, r2)
         /\ finalRoster' = RebuiltRoster(waiting, "R1", StillWaiting(waiting, r2), r1New, r2, finalRoster)
       ELSE
         /\ r1' = r1
         /\ r2' = lastHeatSaved
         /\ waiting' = StillWaiting(waiting, lastHeatSaved)  \* D136
         /\ finalRoster' = finalRoster
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ lastHeatRound = "R1" /\ FinalHasResult(r2)))
    /\ UNCHANGED <<curRound, curHeat, finalBuilt, discardRound, discardNum, discardVal>>

(* discard_heat: results.py:309-333 *)
DiscardHeat(rnd, h, isCoord) ==
    /\ HeatExists(rnd, h)
    /\ HeatHasResult(rnd, h)
    /\ (isCoord \/ rnd /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in results.py:324
    /\ discardRound' = rnd
    /\ discardNum' = h
    /\ discardVal' = HeatResult(rnd, h)
    /\ IF rnd = "R1" THEN
         LET r1New == [r1 EXCEPT ![h] = "none"]
         IN
         /\ r1' = r1New
         /\ r2' = r2
         /\ waiting' = StillWaiting(waiting, r2)
         /\ finalRoster' = RebuiltRoster(waiting, "R1", StillWaiting(waiting, r2), r1New, r2, finalRoster)
       ELSE
         /\ r1' = r1
         /\ r2' = "none"
         /\ waiting' = waiting
         /\ finalRoster' = finalRoster
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ rnd = "R1" /\ FinalHasResult(r2)))
    /\ UNCHANGED <<curRound, curHeat, racing, finalBuilt,
                   lastHeatRound, lastHeatNum, lastHeatSaved>>

(* restore_results: results.py:336-378. Racing goes off when the restored
   heat is the current heat (#499), as reinstate turns it off. *)
RestoreResults(isCoord) ==
    /\ discardRound /= "none"
    /\ ~HeatHasResult(discardRound, discardNum)
    /\ (isCoord \/ discardRound /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in results.py:355
    /\ IF discardRound = "R1" THEN
         LET r1New == [r1 EXCEPT ![discardNum] = discardVal]
         IN
         /\ r1' = r1New
         /\ r2' = r2
         /\ waiting' = StillWaiting(waiting, r2)
         /\ finalRoster' = RebuiltRoster(waiting, "R1", StillWaiting(waiting, r2), r1New, r2, finalRoster)
       ELSE
         /\ r1' = r1
         /\ r2' = discardVal
         /\ waiting' = StillWaiting(waiting, discardVal)  \* D136
         /\ finalRoster' = finalRoster
    /\ racing' = IF discardRound = curRound /\ discardNum = curHeat THEN FALSE ELSE racing
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ discardRound = "R1" /\ FinalHasResult(r2)))
    /\ UNCHANGED <<curRound, curHeat, finalBuilt,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal>>

(* start_waiting_step: playlist.py:219-248 *)
StartWaitingStep ==
    /\ waiting
    /\ finalBuilt
    /\ waiting' = FALSE
    /\ IF ~FinalHasResult(r2) THEN
         /\ curRound' = "R2"
         /\ curHeat' = 1
         /\ racing' = TRUE
       ELSE
         /\ curRound' = curRound
         /\ curHeat' = curHeat
         /\ racing' = FALSE
    /\ UNCHANGED <<r1, r2, finalBuilt, finalRoster,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* skip_waiting_step: playlist.py:266-296 *)
SkipWaitingStep ==
    /\ waiting
    /\ waiting' = FALSE
    /\ racing' = FALSE
    /\ UNCHANGED <<curRound, curHeat, r1, r2, finalBuilt, finalRoster,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

Next ==
    \/ \E val \in {"v1", "v2"} : TimerFinishR1(val)
    \/ TimerFinishR2
    \/ \E val \in {"v1", "v2"}, isCoord \in BOOLEAN : ManualWriteR1(val, isCoord)
    \/ ManualWriteR2
    \/ \E h \in {1, 2} : SelectHeat("R1", h)
    \/ SelectHeat("R2", 1)
    \/ SetRacingOn
    \/ SetRacingOff
    \/ \E which \in {"current", "last"}, isCoord \in BOOLEAN : Rerun(which, isCoord)
    \/ \E isCoord \in BOOLEAN : Reinstate(isCoord)
    \/ \E h \in {1, 2}, isCoord \in BOOLEAN : DiscardHeat("R1", h, isCoord)
    \/ \E isCoord \in BOOLEAN : DiscardHeat("R2", 1, isCoord)
    \/ \E isCoord \in BOOLEAN : RestoreResults(isCoord)
    \/ StartWaitingStep
    \/ SkipWaitingStep

Spec == Init /\ [][Next]_vars

(* ---------- INVARIANTS ---------- *)

\* 1. Racing mode is only active on a heat that exists and has no results yet (heat_state.py:89-97).
RacingHeatAllowed ==
    racing => (curRound /= "none" /\ HeatExists(curRound, curHeat) /\ ~HeatHasResult(curRound, curHeat))

\* 2. While the intermission is open and the final has not raced, the waiting
\*    final's roster matches Round 1's current standings (D27, D135).
WaitingFinalMatchesStandings ==
    (waiting /\ ~FinalHasResult(r2)) => (finalBuilt /\ finalRoster = r1)

\* 3. Once any heat of the final has a result, the intermission for that final
\*    is closed (D136).
NoIntermissionAfterFinalRaces ==
    FinalHasResult(r2) => ~waiting

\* 4. Once the final has a result, a non-CONTROL_RACE caller never alters Round 1 (D32).
LockedFeederProtected ==
    ~nonCoordBreak

====
