"use client";

import React, { useEffect } from "react";
import { motion, useSpring, useTransform, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";
import type { LucideIcon } from "lucide-react";

interface KpiCardProps {
  title: string;
  value: number | string;
  icon?: LucideIcon;
  accentColor?: "default" | "success" | "danger" | "warning" | "info";
  suffix?: string;
  prefix?: string;
  formatter?: (v: number) => string;
  trend?: {
    value: number;
    positive?: boolean;
    label?: string;
  };
}

const accentStyles = {
  default: {
    border: "border-zinc-200 dark:border-white/10",
    iconBg: "bg-zinc-100 dark:bg-white/5",
    icon: "text-zinc-600 dark:text-zinc-400",
    gradient: "from-zinc-500/5 to-zinc-500/0",
    ring: "",
  },
  success: {
    border: "border-emerald-200/60 dark:border-emerald-500/15",
    iconBg: "bg-emerald-50 dark:bg-emerald-500/10",
    icon: "text-emerald-600 dark:text-emerald-400",
    gradient: "from-emerald-500/10 to-emerald-500/0",
    ring: "ring-1 ring-emerald-500/10",
  },
  danger: {
    border: "border-red-200/60 dark:border-red-500/15",
    iconBg: "bg-red-50 dark:bg-red-500/10",
    icon: "text-red-600 dark:text-red-400",
    gradient: "from-red-500/10 to-red-500/0",
    ring: "ring-1 ring-red-500/10",
  },
  warning: {
    border: "border-amber-200/60 dark:border-amber-500/15",
    iconBg: "bg-amber-50 dark:bg-amber-500/10",
    icon: "text-amber-600 dark:text-amber-400",
    gradient: "from-amber-500/10 to-amber-500/0",
    ring: "ring-1 ring-amber-500/10",
  },
  info: {
    border: "border-blue-200/60 dark:border-blue-500/15",
    iconBg: "bg-blue-50 dark:bg-blue-500/10",
    icon: "text-blue-600 dark:text-blue-400",
    gradient: "from-blue-500/10 to-blue-500/0",
    ring: "ring-1 ring-blue-500/10",
  },
};

function AnimatedNumber({
  value,
  formatter,
  prefix = "",
  suffix = "",
}: {
  value: number;
  formatter?: (v: number) => string;
  prefix?: string;
  suffix?: string;
}) {
  const prefersReduced = useReducedMotion();
  const spring = useSpring(0, {
    stiffness: 55,
    damping: 22,
    mass: 1,
  });
  const display = useTransform(spring, (v) => {
    if (formatter) return prefix + formatter(v) + suffix;
    if (Number.isInteger(value))
      return prefix + Math.round(v).toLocaleString("en-IN") + suffix;
    return prefix + v.toFixed(1) + suffix;
  });

  useEffect(() => {
    if (prefersReduced) {
      spring.jump(value);
    } else {
      spring.set(value);
    }
  }, [value, spring, prefersReduced]);

  return <motion.span>{display}</motion.span>;
}

export default function KpiCard({
  title,
  value,
  icon: Icon,
  accentColor = "default",
  suffix,
  prefix,
  formatter,
  trend,
}: KpiCardProps) {
  const isNumber = typeof value === "number";
  const accent = accentStyles[accentColor];

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-2xl p-5",
        "bg-white dark:bg-neutral-900",
        "border",
        accent.border,
        accent.ring,
        "shadow-sm hover:shadow-md hover:-translate-y-0.5",
        "transition-all duration-300 ease-out"
      )}
    >
      <div
        className={cn(
          "absolute inset-0 bg-gradient-to-br pointer-events-none opacity-60",
          accent.gradient
        )}
      />
      <div className="relative flex flex-col gap-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-2">
              {title}
            </p>
            <p className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 dark:text-white font-feature-settings-ss01 tabular-nums">
              {isNumber ? (
                <AnimatedNumber
                  value={value}
                  formatter={formatter}
                  prefix={prefix}
                  suffix={suffix}
                />
              ) : (
                <motion.span
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.4 }}
                >
                  {value}
                </motion.span>
              )}
            </p>
          </div>
          {Icon && (
            <div
              className={cn(
                "flex-shrink-0 p-2.5 rounded-xl",
                accent.iconBg,
                accent.icon,
                "transition-transform duration-300 group-hover:scale-110"
              )}
            >
              <Icon size={22} strokeWidth={2} />
            </div>
          )}
        </div>

        {trend && (
          <div className="flex items-center gap-1.5 pt-1">
            <span
              className={cn(
                "inline-flex items-center gap-0.5 px-2 py-0.5 rounded-md text-xs font-semibold",
                trend.positive
                  ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                  : "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400"
              )}
            >
              {trend.positive ? "▲" : "▼"} {trend.value}%
            </span>
            {trend.label && (
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                {trend.label}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
