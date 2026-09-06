"use client";

import React, { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Gauge,
  FilePlus2,
  ShieldCheck,
  ShieldAlert,
  Send,
  Check,
  X,
  Pause,
  Loader2,
} from "lucide-react";
import type { RunCycleResponse } from "@/lib/api";
import { describePolicyCode } from "@/lib/policyLabels";
import InfoTooltip from "@/components/ui/InfoTooltip";
import { cn } from "@/lib/utils";

export type CycleStage = "score" | "propose" | "gate" | "execute";
export type StageStatus = "idle" | "active" | "done" | "blocked" | "halted";

interface StageDef {
  id: CycleStage;
  label: string;
  sub: string;
}

const STAGES: StageDef[] = [
  { id: "score", label: "Score", sub: "EV · p(recovery)" },
  { id: "propose", label: "Propose", sub: "tier · ladder rung" },
  { id: "gate", label: "Gate", sub: "policy verdict" },
  { id: "execute", label: "Execute", sub: "deliver / halt" },
];

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function stageStatusFor(result: RunCycleResponse | null, stage: CycleStage): StageStatus {
  if (!result) return "idle";
  if (stage === "score") return "done";
  if (stage === "propose") return result.tier === "WAIT" ? "blocked" : "done";
  if (stage === "gate") {
    if (!result.decision) return "blocked"; // WAIT → no proposal → gate never fires; that's working as designed
    return result.decision.allowed ? "done" : "blocked";
  }
  // execute
  if (result.tier === "WAIT") return "halted";
  if (!result.decision || !result.decision.allowed) return "halted";
  if (!result.transitioned) return "halted";
  if (result.execution) {
    if (result.execution.delivered) return "done";
    if (result.execution.error) return "halted";
    return "done";
  }
  return "done";
}

function StageIcon({ stage, status }: { stage: CycleStage; status: StageStatus }) {
  if (status === "idle") {
    if (stage === "score") return <Gauge className="h-5 w-5" />;
    if (stage === "propose") return <FilePlus2 className="h-5 w-5" />;
    if (stage === "gate") return <ShieldCheck className="h-5 w-5" />;
    return <Send className="h-5 w-5" />;
  }
  if (status === "active") return <Loader2 className="h-5 w-5 animate-spin" />;
  if (status === "done") return <Check className="h-5 w-5" />;
  if (status === "blocked") return <Pause className="h-5 w-5" />;
  return <X className="h-5 w-5" />;
}

export function DecisionCycleVisualizer({
  result,
  running,
  variant = "full",
}: {
  result: RunCycleResponse | null;
  running: boolean;
  variant?: "full" | "static";
}) {
  const [revealed, setRevealed] = useState(0);

  useEffect(() => {
    if (variant === "static") {
      setRevealed(4);
      return;
    }
    if (running) {
      setRevealed(0);
      return;
    }
    if (!result) {
      setRevealed(0);
      return;
    }
    // One round-trip today — animate the reveal sequentially (~400ms stagger),
    // not a faked live stream. Real SSE streaming is the §12 upgrade.
    setRevealed(0);
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (let i = 1; i <= 4; i++) {
      timers.push(setTimeout(() => setRevealed(i), i * 400));
    }
    return () => timers.forEach(clearTimeout);
  }, [result, running, variant]);

  const statuses: StageStatus[] = STAGES.map((s, idx) => {
    if (running) return idx === 0 ? "active" : "idle";
    if (!result) return "idle";
    if (idx >= revealed) return idx === revealed ? "active" : "idle";
    return stageStatusFor(result, s.id);
  });

  const renderDetail = (stage: CycleStage) => {
    if (!result || revealed < STAGES.findIndex((s) => s.id === stage) + 1) {
      return <span className="text-zinc-400">Awaiting cycle…</span>;
    }
    if (stage === "score") {
      return (
        <span>
          p(recovery) <strong>{(result.p_recovery * 100).toFixed(1)}%</strong> · EV{" "}
          <strong>{formatINR(result.expected_value)}</strong>
          <InfoTooltip side="bottom">
            EV = P(recovery) × outstanding × urgency weight − intervention cost. The tier this
            invoice gets proposed next is ranked on this number, the same way the queue is.
          </InfoTooltip>
          <span className="block text-[10px] opacity-70 mt-0.5">
            {result.scorer_fallback ? `rules-based (${result.scorer_version || "fallback"})` : result.scorer_version}
          </span>
        </span>
      );
    }
    if (stage === "propose") {
      return (
        <span>
          Tier <strong>{result.tier}</strong>
          {result.action_type ? (
            <>
              {" "}· <strong>{result.action_type}</strong>
            </>
          ) : (
            " · no action proposed"
          )}
          {result.ladder_step && result.ladder_step !== "none" && (
            <span className="block text-[10px] opacity-70 mt-0.5">rung: {result.ladder_step}</span>
          )}
        </span>
      );
    }
    if (stage === "gate") {
      if (!result.decision) {
        return <span className="text-amber-600 dark:text-amber-400">Gate not reached — WAIT tier proposes nothing. System working correctly.</span>;
      }
      return (
        <span className={result.decision.allowed ? "" : "text-amber-600 dark:text-amber-400"}>
          {result.decision.allowed ? "Allowed" : "Blocked"} — {result.decision.reason}
          {result.decision.violations.length > 0 && (
            <span className="block text-[10px] mt-0.5">
              {result.decision.violations.map((v) => describePolicyCode(v.code)).join(", ")}
            </span>
          )}
        </span>
      );
    }
    // execute
    if (result.tier === "WAIT" || !result.decision || !result.decision.allowed) {
      return <span className="text-amber-600 dark:text-amber-400">Halted — gated, nothing sent. This is the system working correctly.</span>;
    }
    if (!result.transitioned) {
      return <span className="text-amber-600 dark:text-amber-400">Halted — ladder refused the move ({result.reason || "no transition"}).</span>;
    }
    if (result.execution) {
      const ex = result.execution;
      return (
        <span>
          {ex.delivered ? "Delivered" : "Failed"}
          {ex.dry_run && <strong> · DRY_RUN</strong>}
          {ex.payment_link_url && (
            <a href={ex.payment_link_url} target="_blank" rel="noreferrer" className="block text-orange-600 dark:text-orange-400 underline text-[11px] mt-0.5">
              {ex.payment_link_id || "payment link"}
            </a>
          )}
          {ex.error && <span className="block text-[10px] mt-0.5">{ex.error}</span>}
        </span>
      );
    }
    return <span>Moved {result.state_before} → {result.state_after}</span>;
  };

  return (
    <div className="w-full">
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        <span className="text-[11px] font-mono uppercase tracking-widest text-zinc-500">
          score → propose → <strong className="text-zinc-800 dark:text-zinc-100">gate</strong> → transition → execute
        </span>
        {result?._offline_fallback && (
          <span
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide bg-amber-100 dark:bg-amber-500/15 text-amber-700 dark:text-amber-400 border border-amber-300/60 dark:border-amber-500/25"
            title="POST /invoices/{id}/run-cycle was unreachable. This is fabricated client-side sample data, not a real decision — nothing was scored, gated, or sent."
          >
            Simulated — backend unreachable
          </span>
        )}
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 relative">
        {STAGES.map((s, idx) => {
          const status = statuses[idx];
          const isLast = idx === STAGES.length - 1;
          return (
            <div key={s.id} className="relative">
              <motion.div
                initial={{ opacity: 0.6, scale: 0.98 }}
                animate={
                  status === "active"
                    ? { opacity: 1, scale: [1, 1.04, 1] }
                    : status === "idle"
                      ? { opacity: 0.6, scale: 0.98 }
                      : { opacity: 1, scale: 1 }
                }
                transition={status === "active" ? { duration: 0.6, repeat: Infinity } : { duration: 0.3 }}
                className={cn(
                  "rounded-2xl border p-4 min-h-[150px] flex flex-col gap-2 bg-white dark:bg-zinc-950",
                  status === "done" && "border-emerald-500/40",
                  status === "blocked" && "border-amber-500/50 bg-amber-50/60 dark:bg-amber-500/5",
                  status === "halted" && "border-amber-500/50 bg-amber-50/60 dark:bg-amber-500/5",
                  status === "active" && "border-orange-500/60",
                  status === "idle" && "border-zinc-200 dark:border-white/10"
                )}
              >
                <div className="flex items-center justify-between">
                  <span
                    className={cn(
                      "p-1.5 rounded-lg",
                      status === "done" && "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
                      (status === "blocked" || status === "halted") && "bg-amber-500/15 text-amber-600 dark:text-amber-400",
                      status === "active" && "bg-orange-500/15 text-orange-600 dark:text-orange-400",
                      status === "idle" && "bg-zinc-100 dark:bg-zinc-900 text-zinc-400"
                    )}
                  >
                    <StageIcon stage={s.id} status={status} />
                  </span>
                  <span className="text-[10px] font-mono text-zinc-400">0{idx + 1}</span>
                </div>
                <div>
                  <p className="text-sm font-bold">{s.label}</p>
                  <p className="text-[10px] font-mono text-zinc-500">{s.sub}</p>
                </div>
                <p className="text-[11px] leading-snug text-zinc-600 dark:text-zinc-300">{renderDetail(s.id)}</p>
              </motion.div>
              {!isLast && (
                <div className="hidden lg:block absolute top-1/2 -right-3.5 w-7 h-px z-10">
                  <svg viewBox="0 0 28 4" className="w-full h-2 overflow-visible">
                    <motion.line
                      x1="0"
                      y1="2"
                      x2="28"
                      y2="2"
                      stroke={revealed > idx ? "#10b981" : "#52525b"}
                      strokeWidth="2"
                      strokeLinecap="round"
                      initial={{ pathLength: 0 }}
                      animate={{ pathLength: revealed > idx ? 1 : 0.2 }}
                      transition={{ duration: 0.4 }}
                    />
                  </svg>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <p className="text-[10px] font-mono text-zinc-400 mt-2">
        Single round-trip today; stages reveal sequentially. Live SSE streaming is the roadmap upgrade.
      </p>
    </div>
  );
}
