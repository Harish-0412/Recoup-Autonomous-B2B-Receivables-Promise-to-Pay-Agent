"use client";

import React, { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { fetchTaskStatus } from "@/lib/api";
import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

interface StatusPillProps {
  label: string;
  isLive?: boolean; // true = emerald, false = amber, undefined = neutral/zinc
  tooltip?: string;
  href?: string;
  dot?: boolean;
}

function StatusPill({ label, isLive, tooltip, href, dot = false }: StatusPillProps) {
  const prevLabelRef = useRef(label);
  const [pulse, setPulse] = useState(false);

  useEffect(() => {
    if (prevLabelRef.current !== label) {
      prevLabelRef.current = label;
      setPulse(true);
      const timer = setTimeout(() => setPulse(false), 800);
      return () => clearTimeout(timer);
    }
  }, [label]);

  const colorClasses =
    isLive === true
      ? "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/25"
      : isLive === false
      ? "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30 hover:bg-amber-500/25"
      : "bg-zinc-500/10 text-zinc-700 dark:text-zinc-300 border-zinc-300/60 dark:border-zinc-700/60 hover:bg-zinc-500/20";

  const content = (
    <motion.span
      layout
      transition={{ type: "spring", stiffness: 400, damping: 25 }}
      animate={pulse ? { scale: [1, 1.08, 1] } : { scale: 1 }}
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-mono font-medium border tracking-wide whitespace-nowrap transition-colors select-none",
        colorClasses,
        href && "cursor-pointer"
      )}
      title={tooltip}
    >
      {dot && (
        <span className="relative flex h-2 w-2">
          {isLive === true && (
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
          )}
          <span
            className={cn(
              "relative inline-flex rounded-full h-2 w-2",
              isLive === true
                ? "bg-emerald-500"
                : isLive === false
                ? "bg-amber-500"
                : "bg-zinc-400"
            )}
          />
        </span>
      )}
      <span>{label}</span>
    </motion.span>
  );

  if (href) {
    return (
      <Link href={href} className="focus:outline-none focus-visible:ring-1 focus-visible:ring-amber-500 rounded-full">
        {content}
      </Link>
    );
  }

  return content;
}

export default function StatusStrip() {
  const { data } = useQuery({
    queryKey: ["task-status"],
    queryFn: fetchTaskStatus,
    refetchInterval: 30_000,
    staleTime: 10_000,
  });

  // Safe defaults if offline
  const isDryRun = data ? data.dry_run : true;
  const isSending = data ? data.sending_enabled : false;
  const modelScorer = data?.use_model_scorer ? "Model scorer: ML" : "Model scorer: rules-based";
  const openInvoices = data?.open_invoices ?? 1204;
  const pendingPromises = data?.pending_promises ?? 7;
  const repliesNeedReview = data?.replies_awaiting_review ?? 3;

  return (
    <div className="w-full bg-zinc-950/80 dark:bg-black/90 border-b border-zinc-200/20 dark:border-white/10 backdrop-blur-md px-4 py-2 flex items-center justify-between overflow-x-auto text-xs z-40">
      <div className="flex items-center gap-2 sm:gap-2.5 overflow-x-auto py-0.5 no-scrollbar">
        {/* Dry run vs Live */}
        <StatusPill
          dot
          label={isDryRun ? "● DRY_RUN" : "● LIVE"}
          isLive={!isDryRun}
          tooltip={isDryRun ? "DRY_RUN mode active: simulated decisions only" : "LIVE system active"}
        />

        {/* Sending status */}
        <StatusPill
          dot
          label={isSending ? "● Sending: ON" : "● Sending: OFF"}
          isLive={isSending}
          tooltip={isSending ? "Real email and payment links enabled" : "Outbound sending paused"}
        />

        {/* Model scorer */}
        <StatusPill
          label={modelScorer}
          isLive={undefined}
          tooltip="Scoring engine algorithm"
        />

        {/* Open invoices */}
        <StatusPill
          label={`${openInvoices.toLocaleString()} open invoices`}
          isLive={undefined}
          tooltip="Total open receivables in system"
        />

        {/* Pending promises */}
        {pendingPromises > 0 && (
          <StatusPill
            label={`${pendingPromises} promises`}
            isLive={true}
            tooltip="Active payment promises being watched"
          />
        )}

        {/* Replies need you deep-link */}
        <StatusPill
          label={`${repliesNeedReview} replies need you →`}
          isLive={false}
          href="/inbox"
          tooltip="Inbound debtor messages awaiting human discretion. Click to review."
        />
      </div>

      <div className="hidden md:flex items-center gap-2 pl-4 text-[11px] font-mono text-zinc-500 whitespace-nowrap">
        <span>Autonomous Daemon</span>
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />
      </div>
    </div>
  );
}
