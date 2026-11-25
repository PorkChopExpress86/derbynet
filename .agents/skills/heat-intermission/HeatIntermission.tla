---- MODULE HeatIntermission ----
EXTENDS Naturals, Sequences

(*
 * Formal model of DerbyNet's current-heat state machine, result services,
 * and playlist operator-gate intermission (owner decisions D27, D28, D32, D135, D136).
 *
 * Scope and bounds:
 *   - One group with a 2-step playlist:
 *       Step 1: Round 1 ("R1") with 2 heats (1, 2), feeder round.
 *       Step 2: Round 2 ("R2") with 1 heat (1), the final, which waits for the
 *       operator until set_step_waits switches that off.
 *   - The cut picks 2 racers or more, or one (D28), and changes at any time.
 *     A cut that picks none is left out: _activate writes an empty roster
 *     again, where RoundOneEnded has a step that does not wait keep its roster.
 *   - Each service call runs under lock_race_state(event.id) as one atomic step.
 *   - R1 heat results are abstracted to {"none", "v1", "v2"} so any result change
 *     that alters the finalist cut ("v1" <-> "v2" or "none") is observable.
 *
 * Covered source files (sync commit: 603cf1fa678ac7da614e1eb6f3c4c87ac5d68338):
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
    finalShort,     \* BOOLEAN (Final has a roster of one racer and no chart, D28)
    stepWaits,      \* BOOLEAN (the final step's waits_for_operator)
    cutShort,       \* BOOLEAN (the cut picks one racer now, too few for a final, D28)
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
    r1, r2, finalBuilt, finalShort, finalRoster, stepWaits, cutShort,
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

(* The part of the race state the playlist changes: the current heat,
   racing, the intermission, and the final's chart and roster. *)
Now == [cr |-> curRound, ch |-> curHeat, rc |-> racing, w |-> waiting,
        fb |-> finalBuilt, fs |-> finalShort, fr |-> finalRoster]

Becomes(s) ==
    /\ curRound' = s.cr
    /\ curHeat' = s.ch
    /\ racing' = s.rc
    /\ waiting' = s.w
    /\ finalBuilt' = s.fb
    /\ finalShort' = s.fs
    /\ finalRoster' = s.fr

(* rebuild_waiting_final in heats.py, which every result change calls.
   close_intermission_on_result (D136): the intermission stays open only
   while the final has no result. Then rebuild_waiting_round (D27), when
   Round 1 changed with the intermission open: the final is made again from
   the standings and the cut as they are now. A cut of one racer leaves it
   a roster and no chart (D28). unschedule cancels the final when
   it is the current round, and _activate selects its first heat again when
   it has one; a current heat in Round 1 stays. Where the cut picks the same
   racers the code keeps the chart and the model makes it again, which
   differs only with racing on, and NoRacingDuringIntermission has racing
   off here. *)
Rebuilt(s, endedBefore, changedR1, r1After, r2After) ==
    LET open == s.w /\ ~FinalHasResult(r2After)
        t == [s EXCEPT !.w = open]
        u == [t EXCEPT !.fr = r1After, !.fb = ~cutShort, !.fs = cutShort]
    IN
    IF ~(endedBefore /\ changedR1 /\ open) THEN t
    ELSE IF s.cr = "R1" THEN u
    ELSE IF cutShort THEN [u EXCEPT !.cr = "none", !.ch = 0, !.rc = FALSE]
    ELSE [u EXCEPT !.cr = "R2", !.ch = 1, !.rc = FALSE]

(* round_ended and _after_round in playlist.py, for Round 1. The Advance
   row has turned racing off and the current heat is Round 1's last.
   A final that has a result is passed (_is_raced). Otherwise a step that
   waits has its final made again from the standings (D135), and one that
   does not wait keeps the roster it has (_activate). A final with a chart
   gets its first heat selected (_select). A step that waits opens the
   intermission (_waits). One that does not leaves the intermission as it
   was, and with continue_racing (cont) on Round 1's step the playlist turns
   racing on (_racing_on), where _RACING_ON_EXTRAS[By.PLAYLIST] closes the
   intermission (#514). The racing-on rows of TABLE refuse a current heat
   that has a result, as Round 1's last heat does when the final has no
   chart. *)
RoundOneEnded(s, r1Now, cont) ==
    IF FinalHasResult(r2) THEN s
    ELSE
    LET remade == stepWaits \/ ~(s.fb \/ s.fs)
        short == IF remade THEN cutShort ELSE s.fs
        t == [s EXCEPT !.fb = ~short, !.fs = short,
                       !.fr = IF remade THEN r1Now ELSE s.fr]
        u == IF short THEN t ELSE [t EXCEPT !.cr = "R2", !.ch = 1]
    IN
    IF stepWaits THEN [u EXCEPT !.w = TRUE]
    ELSE IF cont /\ ~short THEN [u EXCEPT !.rc = TRUE, !.w = FALSE]
    ELSE u

(* The Advance("next") rows of TABLE in heat_state.py, from the heat s
   names. Only Round 1's end runs a playlist step: the final is the last
   step, so round_ended for it finds no next step. *)
NextHeat(s, r1Now, cont) ==
    IF s.cr = "R1" /\ s.ch = 1 THEN
        IF s.rc /\ r1Now[2] /= "none" THEN
            \* target_refused_after_round, or target_refused
            IF R1Complete(r1Now)
            THEN RoundOneEnded([s EXCEPT !.ch = 2, !.rc = FALSE], r1Now, cont)
            ELSE [s EXCEPT !.ch = 2, !.rc = FALSE]
        ELSE [s EXCEPT !.ch = 2]
    ELSE IF s.cr = "R1" THEN
        \* no_target_after_round, or no_target
        IF R1Complete(r1Now)
        THEN RoundOneEnded([s EXCEPT !.rc = FALSE], r1Now, cont)
        ELSE [s EXCEPT !.rc = FALSE]
    ELSE [s EXCEPT !.rc = FALSE]

(* Initial state: start_playlist activated R1 heat 1 with racing on *)
Init ==
    /\ curRound = "R1"
    /\ curHeat = 1
    /\ racing = TRUE
    /\ waiting = FALSE
    /\ r1 = <<"none", "none">>
    /\ r2 = "none"
    /\ finalBuilt = FALSE
    /\ finalShort = FALSE
    /\ finalRoster = <<"none", "none">>
    /\ stepWaits = TRUE
    /\ cutShort = FALSE
    /\ lastHeatRound = "none"
    /\ lastHeatNum = 0
    /\ lastHeatSaved = "none"
    /\ discardRound = "none"
    /\ discardNum = 0
    /\ discardVal = "none"
    /\ nonCoordBreak = FALSE

(* record_timer_result on R1: results.py, the TimerResult rows of TABLE in
   heat_state.py, then rebuild_waiting_final. *)
TimerFinishR1(val, cont) ==
    /\ racing
    /\ curRound = "R1"
    /\ val \in {"v1", "v2"}
    /\ LET r1New == [r1 EXCEPT ![curHeat] = val]
       IN
       /\ r1' = r1New
       /\ Becomes(Rebuilt(NextHeat(Now, r1New, cont), waiting, TRUE, r1New, r2))
    /\ r2' = r2
    /\ lastHeatRound' = "R1"
    /\ lastHeatNum' = curHeat
    /\ lastHeatSaved' = "none"
    /\ UNCHANGED <<stepWaits, cutShort, discardRound, discardNum, discardVal, nonCoordBreak>>

(* record_timer_result on R2: results.py, the "timer result" row of TABLE *)
TimerFinishR2 ==
    /\ racing
    /\ curRound = "R2"
    /\ r2' = "done"
    /\ racing' = FALSE
    /\ lastHeatRound' = "R2"
    /\ lastHeatNum' = 1
    /\ lastHeatSaved' = "none"
    /\ waiting' = FALSE  \* close_intermission_on_result (D136)
    /\ UNCHANGED <<curRound, curHeat, r1, finalBuilt, finalShort, finalRoster,
                   stepWaits, cutShort, discardRound, discardNum, discardVal, nonCoordBreak>>

(* record_manual_result on R1: results.py, the ManualResult rows of TABLE,
   then rebuild_waiting_final. A manual result with racing off does not
   advance. *)
ManualWriteR1(val, isCoord, cont) ==
    /\ curRound = "R1"
    /\ val \in {"v1", "v2"}
    /\ (isCoord \/ ~FinalHasResult(r2))  \* locked_failure in record_manual_result
    /\ LET r1New == [r1 EXCEPT ![curHeat] = val]
           moved == IF racing THEN NextHeat(Now, r1New, cont) ELSE Now
       IN
       /\ r1' = r1New
       /\ Becomes(Rebuilt(moved, waiting, TRUE, r1New, r2))
    /\ r2' = r2
    /\ lastHeatRound' = "R1"
    /\ lastHeatNum' = curHeat
    /\ lastHeatSaved' = "none"
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ FinalHasResult(r2)))
    /\ UNCHANGED <<stepWaits, cutShort, discardRound, discardNum, discardVal>>

(* record_manual_result on R2: results.py, the "manual result" rows of TABLE *)
ManualWriteR2 ==
    /\ curRound = "R2"
    /\ r2' = "done"
    /\ racing' = FALSE
    /\ lastHeatRound' = "R2"
    /\ lastHeatNum' = 1
    /\ lastHeatSaved' = "none"
    /\ waiting' = FALSE  \* close_intermission_on_result (D136)
    /\ UNCHANGED <<curRound, curHeat, r1, finalBuilt, finalShort, finalRoster,
                   stepWaits, cutShort, discardRound, discardNum, discardVal, nonCoordBreak>>

(* select_heat: heats.py, the "select heat" row of TABLE *)
SelectHeat(rnd, h) ==
    /\ HeatExists(rnd, h)
    /\ (curRound /= rnd \/ curHeat /= h \/ racing \/ waiting)
    /\ curRound' = rnd
    /\ curHeat' = h
    /\ racing' = FALSE
    /\ waiting' = FALSE  \* CloseIntermission()
    /\ UNCHANGED <<r1, r2, finalBuilt, finalShort, finalRoster, stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* set_racing(True): heats.py, the RacingOn rows of TABLE *)
SetRacingOn ==
    /\ ~racing
    /\ curRound /= "none"
    /\ ~HeatHasResult(curRound, curHeat)
    /\ racing' = TRUE
    /\ waiting' = FALSE  \* By.MANAGER emits CloseIntermission()
    /\ UNCHANGED <<curRound, curHeat, r1, r2, finalBuilt, finalShort, finalRoster,
                   stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* set_racing(False): heats.py, the RacingOff rows of TABLE *)
SetRacingOff ==
    /\ racing
    /\ racing' = FALSE
    /\ UNCHANGED <<curRound, curHeat, waiting, r1, r2, finalBuilt, finalShort, finalRoster,
                   stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* rerun: heats.py, the Rerun rows of TABLE and _RACING_ON_EXTRAS[By.RERUN] *)
Rerun(which, isCoord) ==
    /\ which \in {"current", "last"}
    /\ LET tgtRnd == IF which = "current" THEN curRound ELSE lastHeatRound
           tgtNum == IF which = "current" THEN curHeat ELSE lastHeatNum
       IN
       /\ HeatExists(tgtRnd, tgtNum)
       /\ (isCoord \/ tgtRnd /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in rerun
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
          ELSE
            /\ r1' = r1
            /\ r2' = "none"
       /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ tgtRnd = "R1" /\ FinalHasResult(r2)))
       \* the intermission closes before rebuild_waiting_final, so the final stays
       /\ UNCHANGED <<finalBuilt, finalShort, finalRoster, stepWaits, cutShort,
                      discardRound, discardNum, discardVal>>

(* reinstate: heats.py, the Reinstate rows of TABLE, then
   rebuild_waiting_final *)
Reinstate(isCoord) ==
    /\ lastHeatRound /= "none"
    /\ lastHeatSaved /= "none"
    /\ (isCoord \/ lastHeatRound /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in reinstate
    /\ lastHeatRound' = "none"
    /\ lastHeatNum' = 0
    /\ lastHeatSaved' = "none"
    /\ LET off == [Now EXCEPT !.rc = FALSE]
       IN
       IF lastHeatRound = "R1" THEN
         LET r1New == [r1 EXCEPT ![lastHeatNum] = lastHeatSaved]
         IN
         /\ r1' = r1New
         /\ r2' = r2
         /\ Becomes(Rebuilt(off, waiting, TRUE, r1New, r2))
       ELSE
         \* a final made again with no chart has no lane to write back to
         LET r2New == IF finalBuilt THEN lastHeatSaved ELSE r2
         IN
         /\ r1' = r1
         /\ r2' = r2New
         /\ Becomes(Rebuilt(off, waiting, FALSE, r1, r2New))  \* D136
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ lastHeatRound = "R1" /\ FinalHasResult(r2)))
    /\ UNCHANGED <<stepWaits, cutShort, discardRound, discardNum, discardVal>>

(* discard_heat: results.py, then rebuild_waiting_final *)
DiscardHeat(rnd, h, isCoord) ==
    /\ HeatExists(rnd, h)
    /\ HeatHasResult(rnd, h)
    /\ (isCoord \/ rnd /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in discard_heat
    /\ discardRound' = rnd
    /\ discardNum' = h
    /\ discardVal' = HeatResult(rnd, h)
    /\ IF rnd = "R1" THEN
         LET r1New == [r1 EXCEPT ![h] = "none"]
         IN
         /\ r1' = r1New
         /\ r2' = r2
         /\ Becomes(Rebuilt(Now, waiting, TRUE, r1New, r2))
       ELSE
         /\ r1' = r1
         /\ r2' = "none"
         /\ Becomes(Rebuilt(Now, waiting, FALSE, r1, "none"))
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ rnd = "R1" /\ FinalHasResult(r2)))
    /\ UNCHANGED <<stepWaits, cutShort, lastHeatRound, lastHeatNum, lastHeatSaved>>

(* restore_results: results.py, then rebuild_waiting_final. Racing goes off
   when the restored heat is the current heat (#499), as reinstate turns it
   off. *)
RestoreResults(isCoord) ==
    /\ discardRound /= "none"
    /\ HeatExists(discardRound, discardNum)  \* with no lane to write, nothing changes
    /\ ~HeatHasResult(discardRound, discardNum)
    /\ (isCoord \/ discardRound /= "R1" \/ ~FinalHasResult(r2))  \* locked_failure in restore_results
    /\ LET off == IF discardRound = curRound /\ discardNum = curHeat
                  THEN [Now EXCEPT !.rc = FALSE] ELSE Now
       IN
       IF discardRound = "R1" THEN
         LET r1New == [r1 EXCEPT ![discardNum] = discardVal]
         IN
         /\ r1' = r1New
         /\ r2' = r2
         /\ Becomes(Rebuilt(off, waiting, TRUE, r1New, r2))
       ELSE
         /\ r1' = r1
         /\ r2' = discardVal
         /\ Becomes(Rebuilt(off, waiting, FALSE, r1, discardVal))  \* D136
    /\ nonCoordBreak' = (nonCoordBreak \/ (~isCoord /\ discardRound = "R1" /\ FinalHasResult(r2)))
    /\ UNCHANGED <<stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal>>

(* start_waiting_step: playlist.py *)
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
    /\ UNCHANGED <<r1, r2, finalBuilt, finalShort, finalRoster, stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* skip_waiting_step: playlist.py *)
SkipWaitingStep ==
    /\ waiting
    /\ waiting' = FALSE
    /\ racing' = FALSE
    /\ UNCHANGED <<curRound, curHeat, r1, r2, finalBuilt, finalShort, finalRoster,
                   stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* Next with no result written. select_heat with heat "next" (heats.py),
   which the dashboard's Next and heat.select call: the "select heat" row of
   TABLE closes the intermission and turns racing off before its
   Advance("next"). advance (heats.py), which only tests call, decides
   Advance("next") alone, so it ends Round 1 with the intermission as it
   was. *)
NextNoResult(bySelect, cont) ==
    /\ LET from == IF bySelect THEN [Now EXCEPT !.w = FALSE, !.rc = FALSE] ELSE Now
       IN Becomes(NextHeat(from, r1, cont))
    /\ UNCHANGED <<r1, r2, stepWaits, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* set_step_waits: playlist.py, the "Wait for operator" switch on the final's
   step. It changes the step alone. *)
SetStepWaits ==
    /\ stepWaits' = ~stepWaits
    /\ UNCHANGED <<curRound, curHeat, racing, waiting,
                   r1, r2, finalBuilt, finalShort, finalRoster, cutShort,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

(* The cut starts or stops picking one racer (D28). Nothing is
   rebuilt until a Round 1 result changes or Round 1 ends again. *)
ChangeCut ==
    /\ cutShort' = ~cutShort
    /\ UNCHANGED <<curRound, curHeat, racing, waiting,
                   r1, r2, finalBuilt, finalShort, finalRoster, stepWaits,
                   lastHeatRound, lastHeatNum, lastHeatSaved,
                   discardRound, discardNum, discardVal, nonCoordBreak>>

Next ==
    \/ \E val \in {"v1", "v2"}, cont \in BOOLEAN : TimerFinishR1(val, cont)
    \/ TimerFinishR2
    \/ \E val \in {"v1", "v2"}, isCoord \in BOOLEAN, cont \in BOOLEAN :
          ManualWriteR1(val, isCoord, cont)
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
    \/ \E bySelect \in BOOLEAN, cont \in BOOLEAN : NextNoResult(bySelect, cont)
    \/ SetStepWaits
    \/ ChangeCut

Spec == Init /\ [][Next]_vars

(* ---------- INVARIANTS ---------- *)

\* 1. Racing mode is only active on a heat that exists and has no results yet (Chart.allowed in heat_state.py).
RacingHeatAllowed ==
    racing => (curRound /= "none" /\ HeatExists(curRound, curHeat) /\ ~HeatHasResult(curRound, curHeat))

\* 2. While the intermission is open and the final has not raced, the waiting
\*    final's roster matches Round 1's current standings (D27, D135). It has a
\*    chart, or a roster of one racer and none (D28).
WaitingFinalMatchesStandings ==
    (waiting /\ ~FinalHasResult(r2)) => ((finalBuilt \/ finalShort) /\ finalRoster = r1)

\* 3. Once any heat of the final has a result, the intermission for that final
\*    is closed (D136).
NoIntermissionAfterFinalRaces ==
    FinalHasResult(r2) => ~waiting

\* 4. Once the final has a result, a non-CONTROL_RACE caller never alters Round 1 (D32).
LockedFeederProtected ==
    ~nonCoordBreak

\* 5. Racing never runs with the intermission open (#514). The timer test,
\*    which the model leaves out, still turns racing on with it open (#515).
NoRacingDuringIntermission ==
    racing => ~waiting

====
