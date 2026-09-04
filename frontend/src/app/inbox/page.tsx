"use client";

import React, { useState, useMemo, useEffect } from "react";
import Link from "next/link";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import {
  fetchReplyReviewQueue,
  markReplyReviewed,
  fetchTaskStatus,
  type ReplyReviewItem,
  type ReplyReviewQueue,
  type TaskStatusResponse,
} from "@/lib/api";
import {
  Inbox,
  Mail,
  UserRound,
  Clock3,
  AlertTriangle,
  CheckCircle2,
  Check,
  ChevronRight,
  X,
  CalendarDays,
  IndianRupee,
  Tag,
  PartyPopper,
  RefreshCw,
  Sparkles,
  Eye,
  ArrowRight,
  BrainCircuit,
  ShieldCheck,
  ListOrdered,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useOutsideClick } from "@/hooks/use-outside-click";

const INTENT_LABEL_STYLES: Record<string, { label: string; className: string }> = {
  PROMISE_TO_PAY: {
    label: "Promise to Pay",
    className:
      "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-200/60 dark:border-emerald-500/20",
  },
  DISPUTE: {
    label: "Dispute",
    className:
      "bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-200/60 dark:border-rose-500/20",
  },
  QUESTION: {
    label: "Question",
    className:
      "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-200/60 dark:border-blue-500/20",
  },
  OPT_OUT: {
    label: "Opt-Out",
    className:
      "bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-400 border-violet-200/60 dark:border-violet-500/20",
  },
  ACKNOWLEDGE: {
    label: "Acknowledgment",
    className:
      "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-200/60 dark:border-amber-500/20",
  },
  OTHER: {
    label: "Other",
    className:
      "bg-zinc-100 dark:bg-white/5 text-zinc-700 dark:text-zinc-300 border-zinc-200 dark:border-white/10",
  },
};

function getIntentStyle(intent?: string) {
  if (!intent) return INTENT_LABEL_STYLES.OTHER;
  return INTENT_LABEL_STYLES[intent] ?? INTENT_LABEL_STYLES.OTHER;
}

function confidenceColor(confidence: number): {
  bar: string;
  text: string;
  label: string;
} {
  const pct = Math.max(0, Math.min(1, confidence));
  if (pct < 0.6) {
    return {
      bar: "bg-gradient-to-r from-rose-500 to-rose-400",
      text: "text-rose-600 dark:text-rose-400",
      label: "Low confidence — routed to you",
    };
  }
  if (pct < 0.8) {
    return {
      bar: "bg-gradient-to-r from-amber-500 to-amber-400",
      text: "text-amber-600 dark:text-amber-400",
      label: "Moderate confidence — flagged for review",
    };
  }
  return {
    bar: "bg-gradient-to-r from-emerald-500 to-emerald-400",
    text: "text-emerald-600 dark:text-emerald-400",
    label: "High confidence — verified path",
  };
}

function formatRelativeTime(iso: string): string {
  try {
    const date = new Date(iso);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMin = Math.floor(diffMs / 60_000);
    const diffHr = Math.floor(diffMs / 3_600_000);
    const diffDay = Math.floor(diffMs / 86_400_000);
    if (diffMin < 1) return "just now";
    if (diffMin < 60) return `${diffMin}m ago`;
    if (diffHr < 24) return `${diffHr}h ago`;
    if (diffDay < 7) return `${diffDay}d ago`;
    return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
  } catch {
    return iso;
  }
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleString("en-IN", {
      weekday: "short",
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function bodyPreview(body: string, max = 200): { text: string; truncated: boolean } {
  if (!body) return { text: "(no body)", truncated: false };
  const clean = body.replace(/\s+/g, " ").trim();
  if (clean.length <= max) return { text: clean, truncated: false };
  return { text: clean.slice(0, max) + "…", truncated: true };
}

function extractMockEntities(body: string): {
  amount?: number;
  date?: string;
  invoiceId?: string;
} {
  const out: { amount?: number; date?: string; invoiceId?: string } = {};
  const amt = body.match(/(?:₹|INR|Rs\.?|rupees?)\s*([\d,]+(?:\.\d{1,2})?)/i);
  if (amt) out.amount = parseFloat(amt[1].replace(/,/g, ""));
  const inv = body.match(/\b(?:INV[-\s]?)(\d{3,6})\b/i);
  if (inv) out.invoiceId = `INV-${inv[1]}`;
  return out;
}

function SkeletonRow() {
  return (
    <div className="rounded-2xl p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm animate-pulse">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0 space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="h-3 w-32 rounded bg-zinc-200 dark:bg-white/10" />
            <div className="h-5 w-20 rounded-full bg-zinc-200 dark:bg-white/10" />
          </div>
          <div className="h-5 w-72 rounded bg-zinc-200 dark:bg-white/10" />
          <div className="h-4 w-full rounded bg-zinc-100 dark:bg-white/5" />
          <div className="h-4 w-3/4 rounded bg-zinc-100 dark:bg-white/5" />
        </div>
        <div className="w-28 space-y-2">
          <div className="h-2 w-full rounded bg-zinc-200 dark:bg-white/10" />
          <div className="h-8 w-full rounded-xl bg-zinc-200 dark:bg-white/10" />
        </div>
      </div>
    </div>
  );
}

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, Math.round(value * 100)));
  const { bar, text } = confidenceColor(value);
  return (
    <div className="space-y-1.5 w-full">
      <div className="flex items-center justify-between text-[10px] font-bold uppercase tracking-wider">
        <span className="text-zinc-500 dark:text-zinc-400">Classifier confidence</span>
        <span className={cn(text, "tabular-nums font-mono")}>{pct}%</span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-zinc-100 dark:bg-white/5 overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: "easeOut" }}
          className={cn("h-full rounded-full", bar)}
        />
      </div>
    </div>
  );
}

function QueueRow({
  item,
  index,
  isSelected,
  onClick,
  isReviewing,
}: {
  item: ReplyReviewItem;
  index: number;
  isSelected: boolean;
  onClick: () => void;
  isReviewing: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const preview = bodyPreview(item.body, 200);
  const intentStyle = getIntentStyle(item.intent);
  const shouldReduce = useReducedMotion();

  return (
    <motion.div
      layout
      initial={shouldReduce ? { opacity: 1 } : { opacity: 0, y: 14 }}
      animate={{
        opacity: isReviewing ? 0 : 1,
        y: isReviewing ? -8 : 0,
        scale: isReviewing ? 0.98 : 1,
        height: isReviewing ? 0 : "auto",
        marginBottom: isReviewing ? 0 : undefined,
      }}
      exit={shouldReduce ? {} : { opacity: 0, x: 80, height: 0, marginBottom: 0 }}
      transition={{ duration: 0.35, ease: [0.32, 0.72, 0, 1] }}
      onClick={onClick}
      className={cn(
        "group rounded-2xl border bg-white dark:bg-neutral-900 shadow-sm overflow-hidden cursor-pointer",
        "transition-all duration-200",
        isSelected
          ? "border-indigo-300 dark:border-indigo-500/40 ring-2 ring-indigo-500/20"
          : "border-zinc-200 dark:border-white/10 hover:border-zinc-300 dark:hover:border-white/20 hover:shadow-md"
      )}
    >
      <div className="p-4 sm:p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            {/* Top row: from + intent badge + time */}
            <div className="flex flex-wrap items-center gap-2 mb-2">
              <div className="inline-flex items-center gap-1.5 min-w-0">
                <div className="flex-shrink-0 w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500/10 to-violet-500/10 border border-indigo-500/20 flex items-center justify-center text-indigo-500">
                  <UserRound className="w-3.5 h-3.5" strokeWidth={2} />
                </div>
                <span className="text-sm font-bold text-zinc-900 dark:text-white truncate max-w-[240px]">
                  {item.from_email}
                </span>
              </div>
              <div className="h-4 w-px bg-zinc-200 dark:bg-white/10 hidden sm:block" />
              {item.intent && (
                <span
                  className={cn(
                    "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border",
                    intentStyle.className
                  )}
                >
                  <Tag className="w-3 h-3" />
                  {intentStyle.label}
                </span>
              )}
              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400 ml-auto">
                <Clock3 className="w-3 h-3" />
                {formatRelativeTime(item.received_at)}
              </span>
            </div>

            {/* Subject */}
            <h3 className="text-[15px] font-bold text-zinc-900 dark:text-white mb-2 leading-snug truncate">
              {item.subject || "(no subject)"}
            </h3>

            {/* Body preview - expandable */}
            <div className="mb-3">
              <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-300">
                {expanded ? item.body : preview.text}
              </p>
              {preview.truncated && (
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpanded((v) => !v);
                  }}
                  className="mt-1.5 inline-flex items-center gap-0.5 text-[11px] font-bold text-indigo-600 dark:text-indigo-400 hover:text-indigo-700 dark:hover:text-indigo-300 transition-colors"
                >
                  {expanded ? "Show less" : "Read more"}
                  <ChevronRight
                    className={cn(
                      "w-3 h-3 transition-transform",
                      expanded ? "rotate-90" : ""
                    )}
                  />
                </button>
              )}
            </div>

            {/* Disposition reason */}
            <div className="flex items-start gap-2 rounded-xl px-3 py-2.5 bg-amber-50/60 dark:bg-amber-500/10 border border-amber-200/50 dark:border-amber-500/20">
              <AlertTriangle className="w-4 h-4 text-amber-500 mt-0.5 flex-shrink-0" />
              <p className="text-xs leading-relaxed text-amber-900/85 dark:text-amber-200/90 flex-1">
                <span className="font-bold">Why it is here: </span>
                {item.reason}
              </p>
            </div>
          </div>

          {/* Right column: confidence + open arrow */}
          <div className="flex-shrink-0 w-32 sm:w-36 space-y-3">
            <ConfidenceBar value={item.confidence} />
            <div className="flex items-center justify-between pt-1">
              <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                #{index + 1}
              </span>
              <div className="inline-flex items-center justify-center w-8 h-8 rounded-lg bg-zinc-100 dark:bg-white/5 text-zinc-500 group-hover:bg-indigo-500 group-hover:text-white group-hover:shadow-sm group-hover:shadow-indigo-500/20 transition-all">
                <ArrowRight className="w-4 h-4" />
              </div>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
  );
}

function DetailDrawer({
  item,
  onClose,
  onMarkReviewed,
  isMarking,
}: {
  item: ReplyReviewItem;
  onClose: () => void;
  onMarkReviewed: () => void;
  isMarking: boolean;
}) {
  const ref = React.useRef<HTMLDivElement>(null);
  useOutsideClick(ref, onClose);
  const intentStyle = getIntentStyle(item.intent);
  const entities = extractMockEntities(item.body);
  const hasEntity = entities.amount || entities.date || entities.invoiceId;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        transition={{ duration: 0.2 }}
        className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <motion.aside
        ref={ref}
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ type: "spring", damping: 30, stiffness: 300, mass: 0.8 }}
        className="fixed inset-y-0 right-0 z-50 w-full max-w-2xl bg-white dark:bg-neutral-950 border-l border-zinc-200 dark:border-white/10 shadow-2xl flex flex-col"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 px-6 py-5 border-b border-zinc-200 dark:border-white/10">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1.5">
              <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold tracking-wider uppercase bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border border-indigo-200/60 dark:border-indigo-500/20">
                <Eye className="w-3 h-3" /> Human Review Queue
              </div>
              {item.intent && (
                <span
                  className={cn(
                    "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider border",
                    intentStyle.className
                  )}
                >
                  {intentStyle.label}
                </span>
              )}
            </div>
            <h2 className="text-lg font-bold text-zinc-900 dark:text-white leading-snug truncate">
              {item.subject || "(no subject)"}
            </h2>
          </div>
          <button
            onClick={onClose}
            className="flex-shrink-0 inline-flex items-center justify-center w-9 h-9 rounded-xl bg-zinc-100 dark:bg-white/5 hover:bg-zinc-200 dark:hover:bg-white/10 text-zinc-600 dark:text-zinc-300 transition-colors"
            aria-label="Close detail"
          >
            <X className="w-4.5 h-4.5 w-[18px] h-[18px]" />
          </button>
        </div>

        {/* Meta */}
        <div className="px-6 py-4 border-b border-zinc-200/60 dark:border-white/5 space-y-3 bg-zinc-50/50 dark:bg-white/[0.02]">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="flex items-start gap-2.5">
              <div className="p-2 rounded-lg bg-indigo-50 dark:bg-indigo-500/10 text-indigo-500 flex-shrink-0">
                <Mail className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  From
                </p>
                <p className="text-sm font-semibold text-zinc-900 dark:text-white truncate">
                  {item.from_email}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-2.5">
              <div className="p-2 rounded-lg bg-zinc-100 dark:bg-white/5 text-zinc-500 flex-shrink-0">
                <Clock3 className="w-4 h-4" />
              </div>
              <div className="min-w-0">
                <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  Received
                </p>
                <p className="text-sm font-semibold text-zinc-900 dark:text-white">
                  {formatDateTime(item.received_at)}
                </p>
              </div>
            </div>
          </div>

          <div className="space-y-2 pt-2">
            <ConfidenceBar value={item.confidence} />
            {item.classifier_version && (
              <p className="text-[10px] font-mono text-zinc-500 dark:text-zinc-400 inline-flex items-center gap-1.5">
                <BrainCircuit className="w-3 h-3" />
                classifier: {item.classifier_version}
              </p>
            )}
          </div>
        </div>

        {/* Extracted entities */}
        {hasEntity && (
          <div className="px-6 py-4 border-b border-zinc-200/60 dark:border-white/5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-violet-500" />
              <p className="text-[11px] font-bold uppercase tracking-wider text-zinc-600 dark:text-zinc-400">
                Extracted Entities
              </p>
              <span className="text-[10px] font-medium text-zinc-400">heuristic · verify before acting</span>
            </div>
            <div className="flex flex-wrap gap-2">
              {entities.amount != null && (
                <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20 text-xs font-bold">
                  <IndianRupee className="w-3.5 h-3.5" />
                  Amount: ₹{entities.amount.toLocaleString("en-IN")}
                </span>
              )}
              {entities.date && (
                <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-200/60 dark:border-blue-500/20 text-xs font-bold">
                  <CalendarDays className="w-3.5 h-3.5" />
                  Date: {entities.date}
                </span>
              )}
              {entities.invoiceId && (
                <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-400 border border-violet-200/60 dark:border-violet-500/20 text-xs font-bold">
                  <Tag className="w-3.5 h-3.5" />
                  Invoice: {entities.invoiceId}
                </span>
              )}
            </div>
          </div>
        )}

        {/* Disposition */}
        <div className="px-6 py-4 border-b border-zinc-200/60 dark:border-white/5">
          <div className="flex items-start gap-2.5 rounded-2xl px-4 py-3.5 bg-gradient-to-br from-amber-50 to-amber-50/40 dark:from-amber-500/10 dark:to-amber-500/5 border border-amber-200/60 dark:border-amber-500/20">
            <AlertTriangle className="w-4.5 h-4.5 w-[18px] h-[18px] text-amber-500 mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <p className="text-[10px] font-bold uppercase tracking-wider text-amber-700 dark:text-amber-400 mb-1">
                Disposition Reason
              </p>
              <p className="text-sm leading-relaxed text-amber-900/85 dark:text-amber-200/90">
                {item.reason}
              </p>
            </div>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
          <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-3">
            Full Reply Body
          </p>
          <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-white/[0.02] p-5">
            <pre className="text-sm leading-relaxed text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap font-sans">
              {item.body || "(empty body)"}
            </pre>
          </div>
        </div>

        {/* Footer actions */}
        <div className="flex-shrink-0 border-t border-zinc-200 dark:border-white/10 bg-white dark:bg-neutral-950 px-6 py-4">
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
            <div className="flex-1 text-xs text-zinc-500 dark:text-zinc-400">
              <p className="inline-flex items-center gap-1.5">
                <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
                Clicking <span className="font-bold text-zinc-700 dark:text-zinc-200">Mark Reviewed</span> records your sign-off and removes this row from the queue.
              </p>
            </div>
            <div className="flex items-center gap-2.5 justify-end">
              <button
                onClick={onClose}
                className="px-4 py-2.5 rounded-xl text-sm font-semibold bg-zinc-100 dark:bg-white/5 hover:bg-zinc-200 dark:hover:bg-white/10 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 transition-colors"
              >
                Back to list
              </button>
              <button
                onClick={onMarkReviewed}
                disabled={isMarking}
                className={cn(
                  "inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold transition-all",
                  "bg-gradient-to-br from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white",
                  "shadow-md hover:shadow-lg shadow-emerald-500/15 active:scale-[0.97]",
                  "disabled:opacity-70 disabled:active:scale-100 disabled:cursor-wait"
                )}
              >
                {isMarking ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" />
                    Recording…
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="w-4 h-4" />
                    Mark Reviewed
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </motion.aside>
    </AnimatePresence>
  );
}

function InboxZero({ countToday }: { countToday: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ duration: 0.5, ease: "easeOut" }}
      className="relative rounded-3xl border border-emerald-200/60 dark:border-emerald-500/20 bg-gradient-to-br from-emerald-50 via-white to-teal-50/60 dark:from-emerald-500/[0.07] dark:via-neutral-900 dark:to-teal-500/[0.05] p-8 sm:p-12 text-center overflow-hidden shadow-sm"
    >
      <div className="absolute -top-16 -right-16 w-64 h-64 rounded-full bg-emerald-400/20 blur-3xl pointer-events-none" />
      <div className="absolute -bottom-20 -left-16 w-72 h-72 rounded-full bg-teal-400/20 blur-3xl pointer-events-none" />

      <div className="relative">
        <motion.div
          initial={{ scale: 0.8, rotate: -12 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ type: "spring", damping: 16, stiffness: 220, delay: 0.1 }}
          className="mx-auto w-20 h-20 sm:w-24 sm:h-24 rounded-3xl bg-gradient-to-br from-emerald-500 via-teal-500 to-emerald-600 flex items-center justify-center shadow-xl shadow-emerald-500/25 mb-6"
        >
          <motion.div
            initial={{ y: 6, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            transition={{ type: "spring", delay: 0.25 }}
          >
            <PartyPopper className="w-10 h-10 sm:w-12 sm:h-12 text-white" strokeWidth={2} />
          </motion.div>
        </motion.div>

        <motion.h2
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.2 }}
          className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 dark:text-white mb-2"
        >
          Inbox zero.
        </motion.h2>
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.28 }}
          className="text-base sm:text-lg text-zinc-600 dark:text-zinc-300 max-w-xl mx-auto leading-relaxed mb-8"
        >
          <span className="font-bold text-emerald-700 dark:text-emerald-400">
            Every reply this cycle was confidently classified
          </span>{" "}
          and handled automatically. The ML system kept humans out of the loop for{" "}
          <span className="font-semibold">{countToday > 0 ? `${countToday}+ messages` : "everything"}</span> it was sure about.
        </motion.p>

        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.36 }}
          className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4 max-w-2xl mx-auto mb-8"
        >
          {[
            { icon: BrainCircuit, title: "Classifier", value: "100%", label: "confidence on auto-handled" },
            { icon: ShieldCheck, title: "False positives", value: "—", label: "held, not auto-committed" },
            { icon: Sparkles, title: "Human time", value: "Saved", label: "you stay focused on exceptions" },
          ].map((m, i) => (
            <div
              key={i}
              className="rounded-2xl bg-white/80 dark:bg-neutral-900/60 backdrop-blur-sm border border-emerald-200/50 dark:border-emerald-500/15 p-4 text-left shadow-sm"
            >
              <m.icon className="w-4 h-4 text-emerald-500 mb-2" />
              <p className="text-[10px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1">
                {m.title}
              </p>
              <p className="text-xl font-bold text-zinc-900 dark:text-white mb-0.5">{m.value}</p>
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400 leading-snug">{m.label}</p>
            </div>
          ))}
        </motion.div>

        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4, delay: 0.44 }}
          className="flex flex-wrap items-center justify-center gap-3"
        >
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white shadow-md hover:shadow-lg shadow-emerald-500/15 active:scale-[0.97] transition-all"
          >
            <ListOrdered className="w-4 h-4" />
            Back to Command Center
          </Link>
          <Link
            href="/queue"
            className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <Inbox className="w-4 h-4 text-indigo-500" />
            Browse Full Queue
          </Link>
        </motion.div>
      </div>
    </motion.div>
  );
}

export default function InboxPage() {
  const shouldReduce = useReducedMotion();
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const {
    data: queue,
    isLoading,
    isFetching,
    refetch,
  } = useQuery<ReplyReviewQueue>({
    queryKey: ["reply-review-queue"],
    queryFn: fetchReplyReviewQueue,
    staleTime: 15_000,
  });

  const { data: taskStatus } = useQuery<TaskStatusResponse | null>({
    queryKey: ["task-status"],
    queryFn: fetchTaskStatus,
    staleTime: 30_000,
  });

  const markMutation = useMutation({
    mutationFn: (replyId: string) => markReplyReviewed(replyId),
    onMutate: async (replyId) => {
      await queryClient.cancelQueries({ queryKey: ["reply-review-queue"] });
      const prev = queryClient.getQueryData<ReplyReviewQueue>(["reply-review-queue"]);
      if (prev) {
        queryClient.setQueryData<ReplyReviewQueue>(["reply-review-queue"], {
          count: Math.max(0, prev.count - 1),
          items: prev.items.filter((it) => it.reply_id !== replyId),
        });
      }
      if (selectedId === replyId) setSelectedId(null);
      return { prev };
    },
    onError: (_err, _replyId, ctx) => {
      if (ctx?.prev) {
        queryClient.setQueryData<ReplyReviewQueue>(["reply-review-queue"], ctx.prev);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["task-status"] });
    },
  });

  const count = queue?.count ?? queue?.items.length ?? 0;
  const selectedItem = useMemo(
    () => (queue?.items ?? []).find((i) => i.reply_id === selectedId) ?? null,
    [queue, selectedId]
  );

  const handleMarkReviewed = () => {
    if (selectedItem) markMutation.mutate(selectedItem.reply_id);
  };

  const queueItems = queue?.items ?? [];
  const isEmpty = !isLoading && queueItems.length === 0;

  const queueStatusBadge = isLoading ? (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-zinc-100 dark:bg-white/5 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-white/10">
      <span className="w-2 h-2 rounded-full bg-zinc-400 animate-pulse" />
      Loading…
    </span>
  ) : count === 0 ? (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20">
      <Check className="w-3 h-3" />
      Inbox zero
    </span>
  ) : (
    <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20 tabular-nums">
      <AlertTriangle className="w-3 h-3" />
      {count} awaiting review · oldest first
    </span>
  );

  return (
    <main className="p-4 sm:p-6 lg:p-8 max-w-[1200px] mx-auto space-y-6 lg:space-y-8 min-h-full">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="flex flex-col lg:flex-row lg:items-start justify-between gap-5 pb-6 border-b border-zinc-200/60 dark:border-white/10"
      >
        <div className="space-y-2 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-mono text-amber-600 dark:text-amber-400 font-bold tracking-[0.12em] uppercase">
              <Inbox className="w-3.5 h-3.5" />
              <span>Reply Inbox</span>
            </div>
            {queueStatusBadge}
            {taskStatus?.replies_awaiting_review != null && !isLoading && count > 0 && (
              <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-[10px] font-bold bg-zinc-100 dark:bg-white/5 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-white/10 font-mono tabular-nums">
                system says: {taskStatus.replies_awaiting_review} waiting
              </span>
            )}
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-zinc-900 dark:text-white leading-tight">
              Where a human actually works.
            </h1>
            <p className="text-sm sm:text-base text-zinc-500 dark:text-zinc-400 mt-1.5 max-w-2xl leading-relaxed">
              The replies the classifier would not bet on — low confidence, ambiguity, disputes,
              unrouteable senders. Everything else was handled silently. Click a row to read the
              full reply and sign off.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <ListOrdered className="w-4 h-4 text-amber-500" />
            <span>Command Center</span>
          </Link>

          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-amber-500 to-orange-500 hover:from-amber-600 hover:to-orange-600 text-white shadow-md hover:shadow-lg shadow-amber-500/15 transition-all active:scale-[0.97] disabled:opacity-70 disabled:active:scale-100"
          >
            <RefreshCw className={cn("w-4 h-4", isFetching && "animate-spin")} />
            <span>{isFetching ? "Refreshing…" : "Refresh Inbox"}</span>
          </button>
        </div>
      </motion.div>

      {/* Why this queue exists banner */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: "easeOut", delay: 0.05 }}
        className="rounded-xl px-4 py-3 bg-indigo-50/60 dark:bg-indigo-500/10 border border-indigo-200/50 dark:border-indigo-500/20 flex items-start gap-3"
      >
        <ShieldCheck className="w-[18px] h-[18px] w-4.5 h-4.5 text-indigo-500 mt-0.5 flex-shrink-0" />
        <div className="text-xs sm:text-sm leading-relaxed text-indigo-900/80 dark:text-indigo-200/90">
          <span className="font-bold">The two mistakes are not symmetric.</span> Inventing a
          customer commitment we never received stops collections on a live debt. So anything the
          classifier is not sure about lands here — rather than getting guessed.{" "}
          <Link
            href="/policy"
            className="font-semibold underline underline-offset-2 hover:text-indigo-800 dark:hover:text-indigo-100 inline-flex items-center gap-0.5"
          >
            See confidence thresholds in Policy <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
      </motion.div>

      {/* Content */}
      {isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <SkeletonRow key={i} />
          ))}
        </div>
      ) : isEmpty ? (
        <InboxZero countToday={taskStatus?.replies_awaiting_review ?? 0} />
      ) : (
        <AnimatePresence mode="popLayout">
          <motion.div
            layout
            variants={{
              show: {
                transition: { staggerChildren: shouldReduce ? 0 : 0.06 },
              },
            }}
            initial="hidden"
            animate="show"
            className="space-y-3"
          >
            {queueItems.map((it, idx) => (
              <QueueRow
                key={it.reply_id}
                item={it}
                index={idx}
                isSelected={selectedId === it.reply_id}
                isReviewing={markMutation.variables === it.reply_id && markMutation.isPending}
                onClick={() => setSelectedId(it.reply_id)}
              />
            ))}
          </motion.div>
        </AnimatePresence>
      )}

      {/* Detail Drawer */}
      <AnimatePresence>
        {selectedItem && (
          <DetailDrawer
            item={selectedItem}
            onClose={() => setSelectedId(null)}
            onMarkReviewed={handleMarkReviewed}
            isMarking={markMutation.isPending}
          />
        )}
      </AnimatePresence>
    </main>
  );
}
