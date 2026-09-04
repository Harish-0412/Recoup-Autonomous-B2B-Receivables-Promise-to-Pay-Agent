"use client";

import React from "react";
import Link from "next/link";
import { motion, useReducedMotion } from "framer-motion";
import { CheckCircle2, XCircle, MinusCircle, ArrowRight, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export interface TopCaseItem {
  invoice_id: string;
  outstanding: number;
  p_recovery: number;
  expected_value: number;
  tier: "WAIT" | "REMIND" | "ESCALATE";
  rationale: string;
  policy_allowed: boolean | null;
  reason: string;
}

export interface PriorityQueueProps {
  cases: TopCaseItem[];
}

function formatINR(amount: number): string {
  if (amount >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (amount >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${amount.toLocaleString("en-IN")}`;
}

function formatEV(amount: number): string {
  if (amount >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${(amount / 1000).toFixed(0)}K`;
}

export function PriorityQueue({ cases }: PriorityQueueProps) {
  const shouldReduceMotion = useReducedMotion();

  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: { staggerChildren: 0.05 },
    },
  } as const;

  const itemVariants = {
    hidden: { opacity: 0, x: -8 },
    show: { opacity: 1, x: 0, transition: { duration: 0.35, ease: "easeOut" as const } },
  } as const;

  const getTierBadge = (tier: string) => {
    switch (tier) {
      case "WAIT":
        return {
          badge:
            "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20",
          dot: "bg-amber-500",
        };
      case "REMIND":
        return {
          badge:
            "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-200/60 dark:border-blue-500/20",
          dot: "bg-blue-500",
        };
      case "ESCALATE":
        return {
          badge:
            "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border border-red-200/60 dark:border-red-500/20",
          dot: "bg-red-500",
        };
      default:
        return {
          badge:
            "bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700",
          dot: "bg-zinc-400",
        };
    }
  };

  const getPRecoveryColor = (p: number) => {
    if (p >= 0.75) return "text-emerald-600 dark:text-emerald-400";
    if (p >= 0.45) return "text-amber-600 dark:text-amber-400";
    return "text-red-600 dark:text-red-400";
  };

  const getPBarColor = (p: number) => {
    if (p >= 0.75) return "bg-emerald-500";
    if (p >= 0.45) return "bg-amber-500";
    return "bg-red-500";
  };

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className={cn(
        "rounded-2xl flex flex-col overflow-hidden h-full",
        "bg-white dark:bg-neutral-900",
        "border border-zinc-200 dark:border-white/10",
        "shadow-sm"
      )}
    >
      <div className="flex items-start justify-between p-6 pb-4 border-b border-zinc-100 dark:border-white/5">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Sparkles className="w-4 h-4 text-indigo-500" />
            <h3 className="text-lg font-bold tracking-tight text-zinc-900 dark:text-white">
              Priority Queue
            </h3>
          </div>
          <p className="text-sm text-zinc-500 dark:text-zinc-400">
            Top cases ranked by expected value (EV-ranked)
          </p>
        </div>
        <Link
          href="/queue"
          className="hidden sm:inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border border-indigo-200/50 dark:border-indigo-500/20 hover:bg-indigo-100 dark:hover:bg-indigo-500/15 transition-colors"
        >
          Full Queue <ArrowRight className="w-3 h-3" />
        </Link>
      </div>

      <div className="w-full overflow-x-auto flex-1">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="bg-zinc-50/80 dark:bg-white/[0.02] text-zinc-500 dark:text-zinc-400 border-b border-zinc-100 dark:border-white/5">
              <th className="px-6 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                Invoice ID
              </th>
              <th className="px-6 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                Outstanding
              </th>
              <th className="px-6 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                P(Recovery)
              </th>
              <th className="px-6 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                Tier
              </th>
              <th className="px-6 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                Policy
              </th>
            </tr>
          </thead>
          <motion.tbody
            variants={shouldReduceMotion ? undefined : containerVariants}
            initial="hidden"
            animate="show"
          >
            {cases.slice(0, 5).map((item, idx) => {
              const tier = getTierBadge(item.tier);
              return (
                <motion.tr
                  key={item.invoice_id}
                  variants={shouldReduceMotion ? undefined : itemVariants}
                  className="border-b border-zinc-50 dark:border-white/[0.03] hover:bg-zinc-50/60 dark:hover:bg-white/[0.02] transition-colors"
                >
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      <span className="inline-flex items-center justify-center w-6 h-6 rounded-md text-[10px] font-bold bg-zinc-100 dark:bg-white/5 text-zinc-500 dark:text-zinc-400 tabular-nums">
                        {idx + 1}
                      </span>
                      <Link
                        href={`/invoices/${item.invoice_id}`}
                        className="font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 hover:underline underline-offset-2 transition-colors"
                      >
                        {item.invoice_id}
                      </Link>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-col">
                      <span className="font-bold text-zinc-900 dark:text-white tabular-nums">
                        {formatINR(item.outstanding)}
                      </span>
                      <span className="text-[10px] text-zinc-500 dark:text-zinc-400 font-mono">
                        EV {formatEV(item.expected_value)}
                      </span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-col gap-1.5 min-w-[100px]">
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={cn(
                            "text-sm font-bold tabular-nums",
                            getPRecoveryColor(item.p_recovery)
                          )}
                        >
                          {(item.p_recovery * 100).toFixed(0)}%
                        </span>
                      </div>
                      <div className="h-1.5 w-full bg-zinc-100 dark:bg-white/5 rounded-full overflow-hidden">
                        <motion.div
                          initial={{ width: 0 }}
                          animate={{ width: `${Math.min(item.p_recovery * 100, 100)}%` }}
                          transition={{
                            duration: 0.8,
                            delay: idx * 0.08,
                            ease: "easeOut",
                          }}
                          className={cn(
                            "h-full rounded-full",
                            getPBarColor(item.p_recovery)
                          )}
                        />
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <span
                      className={cn(
                        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-bold",
                        tier.badge
                      )}
                    >
                      <span className={cn("w-1.5 h-1.5 rounded-full", tier.dot)} />
                      {item.tier}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-2">
                      {item.policy_allowed === true ? (
                        <>
                          <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                          <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400 hidden sm:inline">
                            Passed
                          </span>
                        </>
                      ) : item.policy_allowed === false ? (
                        <>
                          <XCircle className="w-5 h-5 text-red-500" />
                          <span className="text-xs font-medium text-red-600 dark:text-red-400 hidden sm:inline">
                            Blocked
                          </span>
                        </>
                      ) : (
                        <>
                          <MinusCircle className="w-5 h-5 text-zinc-400" />
                          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400 hidden sm:inline">
                            N/A
                          </span>
                        </>
                      )}
                    </div>
                  </td>
                </motion.tr>
              );
            })}
          </motion.tbody>
        </table>
      </div>

      <div className="p-4 pt-3 border-t border-zinc-100 dark:border-white/5 bg-zinc-50/50 dark:bg-white/[0.015] sm:hidden">
        <Link
          href="/queue"
          className="flex items-center justify-center gap-2 text-sm font-semibold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 transition-colors py-1"
        >
          View full queue <ArrowRight className="w-4 h-4" />
        </Link>
      </div>
    </motion.div>
  );
}
