"use client";

import React, { useState, useId } from "react";
import { HelpCircle } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Inline "?" affordance that reveals a short explanation on hover/focus/tap.
 * Use this for definitions that were previously buried in a `title`
 * attribute (invisible on touch, easy to miss on desktop) — e.g. the EV
 * formula, a model's calibration caveat, or what a risk score scale means.
 */
export default function InfoTooltip({
  label = "More info",
  children,
  className,
  side = "top",
}: {
  label?: string;
  children: React.ReactNode;
  className?: string;
  side?: "top" | "bottom";
}) {
  const [open, setOpen] = useState(false);
  const tooltipId = useId();

  return (
    <span className={cn("relative inline-flex items-center", className)}>
      <button
        type="button"
        aria-describedby={tooltipId}
        aria-label={label}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center justify-center text-zinc-400 hover:text-zinc-600 dark:text-zinc-500 dark:hover:text-zinc-300 transition-colors"
      >
        <HelpCircle className="w-3.5 h-3.5" />
      </button>
      {open && (
        <span
          id={tooltipId}
          role="tooltip"
          className={cn(
            "absolute z-50 left-1/2 -translate-x-1/2 w-56 sm:w-64 px-3 py-2 rounded-lg",
            "bg-zinc-900 dark:bg-zinc-800 text-zinc-100 text-[11px] leading-relaxed font-normal",
            "shadow-lg border border-zinc-800 dark:border-white/10",
            side === "top" ? "bottom-full mb-2" : "top-full mt-2"
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}
