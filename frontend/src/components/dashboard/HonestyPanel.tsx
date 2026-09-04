"use client";

import React, { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Info, ChevronDown, ChevronUp, AlertTriangle, Shield, Eye, Target } from "lucide-react";
import { cn } from "@/lib/utils";

export interface HonestyPanelProps {
  falseInterventions: number | null;
  unnecessaryInterventions?: number | null;
  correctlyLeftAlone: number | null;
  missedRecoveries: number | null;
  caveats?: string[];
}

export function HonestyPanel({
  falseInterventions,
  unnecessaryInterventions,
  correctlyLeftAlone,
  missedRecoveries,
  caveats = [],
}: HonestyPanelProps) {
  const shouldReduceMotion = useReducedMotion();
  const [showTooltip, setShowTooltip] = useState(false);

  const defaultCaveat =
    "Recovery rate measures targeting accuracy, not causation. The agent selected cases that were independently likely to pay; a holdout experiment would be needed to measure true uplift.";

  const allCaveats = [defaultCaveat, ...caveats];
  const combinedFalse = (falseInterventions || 0) + (unnecessaryInterventions || 0);

  const statCards = [
    {
      label: "Unnecessary Interventions",
      value: combinedFalse,
      description: "Cases contacted that would have self-cured",
      icon: AlertTriangle,
      tone: "warning",
    },
    {
      label: "Correctly Left Alone",
      value: correctlyLeftAlone,
      description: "Cases ignored that successfully paid",
      icon: Shield,
      tone: "success",
    },
    {
      label: "Missed Recoveries",
      value: missedRecoveries,
      description: "Cases left alone that didn't pay",
      icon: Target,
      tone: "danger",
    },
  ];

  const toneStyles: Record<string, string> = {
    warning: "text-amber-600 dark:text-amber-400",
    success: "text-emerald-600 dark:text-emerald-400",
    danger: "text-red-600 dark:text-red-400",
  };

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.55, ease: "easeOut", delay: 0.1 }}
      className={cn(
        "rounded-2xl overflow-hidden",
        "bg-gradient-to-br from-amber-50/60 via-white to-orange-50/40 dark:from-amber-950/10 dark:via-neutral-900 dark:to-orange-950/10",
        "border border-amber-200/50 dark:border-amber-900/40",
        "shadow-sm"
      )}
    >
      <div className="p-6">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div className="flex items-start gap-3">
            <div className="p-2.5 rounded-xl bg-amber-100 dark:bg-amber-500/15 flex-shrink-0">
              <Info className="w-5 h-5 text-amber-700 dark:text-amber-400" />
            </div>
            <div>
              <h3 className="text-lg font-bold tracking-tight text-zinc-900 dark:text-white">
                System Honesty Report
              </h3>
              <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5 max-w-xl">
                Transparency by design. We show every miss so you can trust
                every hit.
              </p>
            </div>
          </div>
          <button
            onClick={() => setShowTooltip(!showTooltip)}
            className="flex-shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-white/60 dark:bg-white/5 border border-amber-200/50 dark:border-amber-500/20 text-amber-700 dark:text-amber-400 hover:bg-white dark:hover:bg-white/10 transition-colors"
          >
            <Eye className="w-3.5 h-3.5" />
            Why show this?
            {showTooltip ? (
              <ChevronUp className="w-3 h-3" />
            ) : (
              <ChevronDown className="w-3 h-3" />
            )}
          </button>
        </div>

        {showTooltip && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25 }}
            className="mb-6 p-4 rounded-xl bg-white/70 dark:bg-white/[0.03] border border-amber-200/40 dark:border-amber-500/15 text-sm text-amber-800 dark:text-amber-200 leading-relaxed"
          >
            <p className="font-semibold mb-1.5">Transparency builds trust.</p>
            <p className="text-amber-700/90 dark:text-amber-300/90">
              An autonomous financial agent that hides its mistakes cannot be
              relied upon. Most AI demos omit panels like this — we lead with
              ours. A regulator or auditor who reads this will trust every
              other number on the page more, not less.
            </p>
          </motion.div>
        )}

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-6">
          {statCards.map((card, i) => {
            const Icon = card.icon;
            return (
              <motion.div
                key={card.label}
                initial={
                  shouldReduceMotion ? undefined : { opacity: 0, y: 8 }
                }
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, delay: 0.15 + i * 0.08 }}
                className="p-5 rounded-xl bg-white dark:bg-neutral-900/60 border border-zinc-200/70 dark:border-white/[0.06] shadow-sm hover:shadow-md transition-shadow"
              >
                <div className="flex items-start justify-between mb-3">
                  <Icon
                    className={cn("w-4.5 h-4.5 w-[18px] h-[18px]", toneStyles[card.tone])}
                  />
                </div>
                <div className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 dark:text-white font-mono tabular-nums mb-1.5">
                  {card.value !== null ? card.value : "—"}
                </div>
                <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-200 mb-0.5">
                  {card.label}
                </div>
                <div className="text-xs text-zinc-500 dark:text-zinc-400 leading-snug">
                  {card.description}
                </div>
              </motion.div>
            );
          })}
        </div>

        <div className="space-y-2.5">
          <p className="text-xs font-semibold uppercase tracking-wider text-amber-700/80 dark:text-amber-400/80 flex items-center gap-1.5">
            <Info className="w-3.5 h-3.5" />
            Methodological Caveats
          </p>
          <div className="space-y-2">
            {allCaveats.map((caveat, index) => (
              <motion.div
                key={index}
                initial={
                  shouldReduceMotion ? undefined : { opacity: 0, x: -6 }
                }
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.35, delay: 0.4 + index * 0.08 }}
                className="p-4 rounded-xl bg-white/50 dark:bg-white/[0.025] border border-zinc-200/50 dark:border-white/[0.04] backdrop-blur-sm"
              >
                <p className="text-sm text-zinc-600 dark:text-zinc-300 leading-relaxed flex gap-2.5">
                  <span className="text-amber-500 dark:text-amber-400 font-bold flex-shrink-0 mt-0.5 select-none">
                    *
                  </span>
                  <span className="font-sans">{caveat}</span>
                </p>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
