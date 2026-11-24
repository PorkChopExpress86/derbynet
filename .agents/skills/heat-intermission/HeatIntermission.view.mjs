export const title = "DerbyNet heat lifecycle & playlist intermission";

export const state = (v) => {
  const heat = v.curRound === "none" ? "No heat" : `${v.curRound} H${v.curHeat}`;
  const mode = v.racing ? "racing" : "paused";
  const gate = v.waiting ? ", intermission" : "";
  const r2 = v.r2 === "done" ? ", final raced" : v.finalBuilt ? ", final built" : "";
  return `${heat} (${mode}${gate}${r2})`;
};

export const step = (action, from, to) => {
  switch (action) {
    case "TimerFinishR1": return "timer finishes R1 heat";
    case "TimerFinishR2": return "timer finishes final";
    case "ManualWriteR1": return "manual result on R1";
    case "ManualWriteR2": return "manual result on final";
    case "SelectHeat": return `select ${to.curRound} H${to.curHeat}`;
    case "SetRacingOn": return "turn racing on";
    case "SetRacingOff": return "turn racing off";
    case "Rerun": return "rerun heat";
    case "Reinstate": return "reinstate last heat";
    case "DiscardHeat": return "discard heat results";
    case "RestoreResults": return "undo discard (restore results)";
    case "StartWaitingStep": return "start waiting final";
    case "SkipWaitingStep": return "skip waiting final";
    default: return action;
  }
};
