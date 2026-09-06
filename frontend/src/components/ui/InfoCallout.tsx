"use client";

import React from "react";
import Link from "next/link";
import { Info, AlertTriangle, ArrowRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type InfoCalloutLink = {
  label: string;
  href: string;
};

export type InfoCalloutTone = "info" | "warning";

const TONE_STYLES: Record<
  InfoCalloutTone,
  { wrap: string; icon: string; link: string }
> = {
  info: {
    wrap: "bg-indigo-50/60 dark:bg-indigo-500/10 border-indigo-200/50 dark:border-indigo-500/20",
    icon: "text-indigo-500",
    link: "text-indigo-700 dark:text-indigo-300 hover:text-indigo-900 dark:hover:text-indigo-100",
  },
  warning: {
    wrap: "bg-amber-50/70 dark:bg-amber-500/10 border-amber-200/60 dark:border-amber-500/25",
    icon: "text-amber-500",
    link: "text-amber-700 dark:text-amber-300 hover:text-amber-900 dark:hover:text-amber-100",
  },
};

/**
 * Shared "what is actually happening here" banner used across pages to
 * explain backend mechanics, honesty caveats, or point to a related page
 * (e.g. where a Decision Trace entry this action wrote can be reviewed).
 */
export default function InfoCallout({
  tone = "info",
  icon,
  title,
  children,
  links,
  className,
}: {
  tone?: InfoCalloutTone;
  icon?: LucideIcon;
  title?: string;
  children: React.ReactNode;
  links?: InfoCalloutLink[];
  className?: string;
}) {
  const styles = TONE_STYLES[tone];
  const Icon = icon ?? (tone === "warning" ? AlertTriangle : Info);

  return (
    <div
      className={cn(
        "rounded-xl px-4 py-3 border flex items-start gap-3",
        styles.wrap,
        className
      )}
    >
      <Icon className={cn("w-[18px] h-[18px] mt-0.5 flex-shrink-0", styles.icon)} />
      <div className="text-xs sm:text-sm leading-relaxed text-zinc-700 dark:text-zinc-200 min-w-0">
        {title && <span className="font-bold">{title} </span>}
        {children}
        {links && links.length > 0 && (
          <span className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
            {links.map((l) => (
              <Link
                key={l.href + l.label}
                href={l.href}
                className={cn(
                  "font-semibold underline underline-offset-2 inline-flex items-center gap-0.5",
                  styles.link
                )}
              >
                {l.label} <ArrowRight className="w-3 h-3" />
              </Link>
            ))}
          </span>
        )}
      </div>
    </div>
  );
}
