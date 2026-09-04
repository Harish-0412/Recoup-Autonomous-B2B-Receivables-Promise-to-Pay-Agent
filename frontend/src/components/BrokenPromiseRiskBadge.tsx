"use client";

import React, { useState } from "react";
import { ShieldAlert, ShieldCheck, AlertTriangle, Info } from "lucide-react";
import { cn } from "@/lib/utils";

export interface BrokenPromiseRiskBadgeProps {
  score?: number | null;
  className?: string;
  showBar?: boolean;
}

export function BrokenPromiseRiskBadge({
  score,
  className,
  showBar = true,
}: BrokenPromiseRiskBadgeProps) {
  const [showTooltip, setShowTooltip] = useState(false);

  if (score === undefined || score === null) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono border bg-zinc-100 dark:bg-zinc-800 text-zinc-500 border-zinc-200 dark:border-zinc-700">
        Risk: N/A
      </span>
    );
  }

  const safeScore = Math.max(0, Math.min(1, score));
  const percentage = Math.round(safeScore * 100);

  let tier: "LOW" | "MEDIUM" | "HIGH";
  let tierLabel: string;
  let badgeColor: string;
  let barColor: string;
  let Icon: typeof ShieldCheck;
  let advice: string;

  if (safeScore < 0.33) {
    tier = "LOW";
    tierLabel = "Low Risk";
    badgeColor = "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20";
    barColor = "bg-emerald-500";
    Icon = ShieldCheck;
    advice = "High confidence commitment. Hold automated escalation; wait for customer payment.";
  } else if (safeScore < 0.66) {
    tier = "MEDIUM";
    tierLabel = "Medium Risk";
    badgeColor = "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20";
    barColor = "bg-amber-500";
    Icon = AlertTriangle;
    advice = "Moderate risk of payment slip. Schedule a standard polite reminder on promised date.";
  } else {
    tier = "HIGH";
    tierLabel = "High Risk";
    badgeColor = "bg-red-500/10 text-red-600 dark:text-red-400 border-red-500/20";
    barColor = "bg-red-500";
    Icon = ShieldAlert;
    advice = "High probability of broken promise. Prepare automated ladder escalation if unpaid within 24h.";
  }

  return (
    <div
      className={cn("relative inline-flex flex-col gap-1 select-none", className)}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <div
        className={cn(
          "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-semibold border cursor-help transition-all hover:scale-105",
          badgeColor
        )}
      >
        <Icon className="h-3 w-3 shrink-0" />
        <span>
          Broken Risk: {percentage}% ({tierLabel})
        </span>
        <Info className="h-2.5 w-2.5 opacity-60 ml-0.5" />
      </div>

      {showBar && (
        <div className="w-full bg-zinc-200 dark:bg-zinc-800 rounded-full h-1.5 overflow-hidden">
          <div
            className={cn("h-full rounded-full transition-all duration-500", barColor)}
            style={{ width: `${percentage}%` }}
          />
        </div>
      )}

      {/* Tooltip with detailed explanation */}
      {showTooltip && (
        <div className="absolute left-0 bottom-full mb-2 z-50 w-64 p-3 rounded-xl bg-zinc-900 dark:bg-zinc-950 text-white text-xs shadow-xl border border-zinc-800 pointer-events-none animate-in fade-in zoom-in-95 duration-150">
          <div className="flex items-center justify-between border-b border-zinc-800 pb-1.5 mb-1.5 font-mono">
            <span className="font-bold flex items-center gap-1">
              <Icon className="h-3.5 w-3.5 text-orange-400" />
              Broken-Promise Risk
            </span>
            <span className="text-[10px] text-zinc-400">LightGBM ONNX</span>
          </div>
          <p className="text-zinc-300 mb-1.5">
            Model estimates a <strong className="text-white">{percentage}%</strong> likelihood this promise will be broken (&gt;3 days late).
          </p>
          <p className="text-[11px] text-zinc-400 leading-tight">
            <strong>Action:</strong> {advice}
          </p>
        </div>
      )}
    </div>
  );
}
export default BrokenPromiseRiskBadge;
