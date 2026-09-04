"use client";

import React from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  FunnelChart,
  Funnel,
  LabelList,
  Cell,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { cn } from "@/lib/utils";

interface RecoveryFunnelProps {
  invoicesProcessed: number;
  flagged: number;
  actedOn: number;
  recovered: number;
}

const COLORS = ["#6366f1", "#3b82f6", "#06b6d4", "#10b981"];

export function RecoveryFunnel({
  invoicesProcessed,
  flagged,
  actedOn,
  recovered,
}: RecoveryFunnelProps) {
  const shouldReduceMotion = useReducedMotion();

  const data = [
    {
      name: "Processed",
      value: invoicesProcessed,
      fill: COLORS[0],
      description: "Open invoices scored this run",
    },
    {
      name: "Flagged",
      value: flagged,
      fill: COLORS[1],
      description: "Above intervention threshold",
    },
    {
      name: "Acted On",
      value: actedOn,
      fill: COLORS[2],
      description: "Policy-gated & executed",
    },
    {
      name: "Recovered",
      value: recovered,
      fill: COLORS[3],
      description: "Verified payments received",
    },
  ];

  const funnelPercentages = data.map((d) => {
    const idx = data.findIndex((x) => x.name === d.name);
    const prevVal = idx > 0 ? data[idx - 1].value : 0;
    return {
      ...d,
      pctOfTop: invoicesProcessed > 0 ? ((d.value / invoicesProcessed) * 100).toFixed(1) : "0.0",
      pctOfPrev:
        d.name === "Processed"
          ? "100"
          : prevVal > 0
          ? ((d.value / prevVal) * 100).toFixed(0)
          : "0",
    };
  });

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className={cn(
        "rounded-2xl p-6 flex flex-col gap-5 h-full",
        "bg-white dark:bg-neutral-900",
        "border border-zinc-200 dark:border-white/10",
        "shadow-sm"
      )}
    >
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-lg font-bold tracking-tight text-zinc-900 dark:text-white">
            Recovery Funnel
          </h3>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            End-to-end pipeline from scoring to cash
          </p>
        </div>
        <div className="hidden sm:flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400">
          <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
          Live
        </div>
      </div>

      <div className="flex-1 min-h-[220px]">
        <ResponsiveContainer width="100%" height="100%">
          <FunnelChart margin={{ top: 10, right: 20, bottom: 10, left: 20 }}>
            <Tooltip
              content={({ active, payload }) => {
                if (active && payload && payload.length) {
                  const item = payload[0].payload;
                  return (
                    <div className="bg-white dark:bg-neutral-800 border border-zinc-200 dark:border-white/10 rounded-xl shadow-xl p-3 text-xs space-y-1.5 min-w-[180px]">
                      <p className="font-bold text-sm text-zinc-900 dark:text-white flex items-center gap-2">
                        <span
                          className="w-2.5 h-2.5 rounded-full"
                          style={{ backgroundColor: item.fill }}
                        />
                        {item.name}
                      </p>
                      <p className="font-mono font-semibold text-zinc-800 dark:text-zinc-100">
                        {item.value.toLocaleString("en-IN")} invoices
                      </p>
                      <p className="text-zinc-500 dark:text-zinc-400">
                        {item.description}
                      </p>
                      <div className="pt-1.5 border-t border-zinc-100 dark:border-white/5 space-y-0.5">
                        <p className="text-zinc-600 dark:text-zinc-300">
                          % of processed:{" "}
                          <span className="font-semibold">{item.pctOfTop}%</span>
                        </p>
                        <p className="text-zinc-600 dark:text-zinc-300">
                          Stage yield:{" "}
                          <span className="font-semibold">{item.pctOfPrev}%</span>
                        </p>
                      </div>
                    </div>
                  );
                }
                return null;
              }}
            />
            <Funnel
              dataKey="value"
              data={funnelPercentages}
              isAnimationActive={!shouldReduceMotion}
              animationDuration={800}
              animationEasing="ease-out"
            >
              {funnelPercentages.map((entry, index) => (
                <Cell key={`cell-${index}`} fill={entry.fill} />
              ))}
              <LabelList
                position="right"
                fill="currentColor"
                stroke="none"
                dataKey="name"
                className="text-zinc-700 dark:text-zinc-300 text-[11px] font-medium"
              />
              <LabelList
                position="left"
                fill="currentColor"
                stroke="none"
                dataKey="value"
                className="text-zinc-900 dark:text-white text-xs font-bold font-mono"
                formatter={((value: unknown) => Number(value ?? 0).toLocaleString("en-IN")) as never}
              />
            </Funnel>
          </FunnelChart>
        </ResponsiveContainer>
      </div>

      <div className="grid grid-cols-4 gap-3 pt-4 border-t border-zinc-100 dark:border-white/5">
        {funnelPercentages.map((d) => (
          <div key={d.name} className="text-center">
            <div
              className="text-[10px] font-semibold uppercase tracking-wider mb-1"
              style={{ color: d.fill }}
            >
              {d.name}
            </div>
            <div className="text-sm font-bold text-zinc-900 dark:text-white font-mono tabular-nums">
              {d.pctOfPrev}
              <span className="text-[10px] font-normal text-zinc-500 dark:text-zinc-400 ml-0.5">
                %
              </span>
            </div>
            <div className="text-[10px] text-zinc-500 dark:text-zinc-400">
              yield
            </div>
          </div>
        ))}
      </div>
    </motion.div>
  );
}
