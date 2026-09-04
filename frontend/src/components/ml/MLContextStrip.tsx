"use client";

import React from "react";
import Link from "next/link";
import { Activity, Clock, ShieldAlert, ExternalLink } from "lucide-react";
import type { DriftFlag, NextTimeSuggestion } from "@/lib/api";
import { cn } from "@/lib/utils";

function ChipSkeleton() {
  return <div className="h-9 w-44 rounded-xl bg-zinc-200 dark:bg-white/10 animate-pulse" />;
}

/**
 * Read-only ML context for one customer, shown BEFORE a cycle runs: the
 * latest drift verdict, the bandit's send window, and the open-promise risk.
 * Nothing here mutates state — it is the backdrop the next run will decide
 * against. `undefined` = still loading, `null` = backend answered "none".
 */
export function MLContextStrip({
  drift,
  timing,
  brokenPromiseScore,
  promiseStatus,
}: {
  drift: DriftFlag | null | undefined;
  timing: NextTimeSuggestion | null | undefined;
  brokenPromiseScore?: number | null;
  promiseStatus?: string | null;
}) {
  return (
    <div>
      <p className="text-[11px] font-mono uppercase tracking-widest text-zinc-500 mb-2">
        ML context · read-only backdrop for the next run
      </p>
      <div className="flex flex-wrap items-stretch gap-2.5">
        {drift === undefined ? (
          <ChipSkeleton />
        ) : drift ? (
          <Link
            href="/models#drift"
            className={cn(
              "inline-flex items-center gap-2 px-3.5 py-2 rounded-xl border text-xs font-semibold transition-colors",
              drift.flagged
                ? "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/25 hover:bg-red-500/15"
                : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25 hover:bg-emerald-500/15"
            )}
            title={`Drift model ${drift.model_version} · window ${drift.window_days}d · scored ${drift.created_at}`}
          >
            <Activity className="h-4 w-4" />
            <span>
              {drift.flagged
                ? `Drift flagged (${drift.anomaly_score.toFixed(2)})`
                : `No drift (${drift.anomaly_score.toFixed(2)})`}
            </span>
            <ExternalLink className="h-3 w-3 opacity-60" />
          </Link>
        ) : (
          <span className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl border border-zinc-200 dark:border-white/10 text-xs text-zinc-400">
            <Activity className="h-4 w-4" /> Customer never drift-scored
          </span>
        )}

        {timing === undefined ? (
          <ChipSkeleton />
        ) : timing ? (
          <Link
            href="/models#timing"
            className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl border border-sky-500/25 bg-sky-500/10 text-sky-700 dark:text-sky-300 text-xs font-semibold hover:bg-sky-500/15 transition-colors"
            title={`Bandit segment ${timing.segment} · expected response ${(timing.expected_response_rate * 100).toFixed(1)}% over ${timing.observations} observations${timing.backed_off_to_global ? " · global prior (thin segment)" : ""}${timing.fallback_used ? ` · heuristic fallback: ${timing.fallback_reason}` : ""}`}
          >
            <Clock className="h-4 w-4" />
            <span>
              Send {timing.arm.replace("_", " ")} ·{" "}
              {(timing.expected_response_rate * 100).toFixed(0)}% reply odds
              {timing.fallback_used && " (heuristic)"}
            </span>
            <ExternalLink className="h-3 w-3 opacity-60" />
          </Link>
        ) : (
          <span className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl border border-zinc-200 dark:border-white/10 text-xs text-zinc-400">
            <Clock className="h-4 w-4" /> No send window (unknown customer)
          </span>
        )}

        {brokenPromiseScore != null ? (
          <Link
            href="/models#broken-promise"
            className={cn(
              "inline-flex items-center gap-2 px-3.5 py-2 rounded-xl border text-xs font-semibold transition-colors",
              brokenPromiseScore > 0.6
                ? "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/25 hover:bg-red-500/15"
                : brokenPromiseScore < 0.35
                  ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25 hover:bg-emerald-500/15"
                  : "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25 hover:bg-amber-500/15"
            )}
            title={`Broken-promise risk model · promise ${promiseStatus ?? "unknown"}`}
          >
            <ShieldAlert className="h-4 w-4" />
            <span>Promise risk {(brokenPromiseScore * 100).toFixed(0)}%</span>
            <ExternalLink className="h-3 w-3 opacity-60" />
          </Link>
        ) : (
          <span className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl border border-zinc-200 dark:border-white/10 text-xs text-zinc-400">
            <ShieldAlert className="h-4 w-4" /> No open promise — risk scorer on standby
          </span>
        )}
      </div>
    </div>
  );
}
