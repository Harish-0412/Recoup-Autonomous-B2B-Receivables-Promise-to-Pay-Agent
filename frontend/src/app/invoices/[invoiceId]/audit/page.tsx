"use client";

import React, { use, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  ShieldCheck,
  ShieldAlert,
  ChevronDown,
  ChevronRight,
  RefreshCw,
  Database,
  Braces,
  History,
} from "lucide-react";
import { fetchAuditTrail, type DecisionTraceOut } from "@/lib/api";
import { cn } from "@/lib/utils";
import InfoCallout from "@/components/ui/InfoCallout";

function truncHash(h: string): string {
  if (!h) return "…";
  if (h.length <= 10) return h;
  return `${h.slice(0, 4)}…${h.slice(-4)}`;
}

// Plain-English glossary for the event/outcome enum values seen on trace
// entries. Falls back to the raw string (humanized) for anything unlisted.
const EVENT_LABELS: Record<string, string> = {
  "invoice:flagged": "Invoice flagged for review by the batch sweep",
  "scoring:evaluated": "Recovery scorer produced a probability and EV",
  "policy:gated": "Policy engine evaluated the proposed action",
  "execution:dispatched": "Action sent to the customer (or simulated in dry-run)",
  "promise:made": "Customer commitment to pay recorded",
  "promise:broken": "Promise date passed without payment",
  "escalation:transitioned": "Case moved to the next ladder state",
};

const OUTCOME_LABELS: Record<string, string> = {
  wait: "No action — below the acting threshold",
  remind: "A reminder was proposed",
  escalate: "Escalation was proposed",
  blocked: "Policy refused the action",
  clamped: "Policy lowered the requested value, action proceeded",
  approved: "Policy raised no objection",
};

function describeEvent(event: string): string {
  return EVENT_LABELS[event] ?? event.replace(/[:_]/g, " ");
}

function describeOutcome(outcome: string): string {
  return OUTCOME_LABELS[outcome] ?? outcome.replace(/[:_]/g, " ");
}

// Payload keys common enough across entries to be worth a one-line
// annotation instead of leaving the reader to guess from raw JSON.
const PAYLOAD_FIELD_HINTS: Record<string, string> = {
  days_overdue: "Days past the invoice due date at the time of this entry",
  p_recovery: "Recovery scorer's output — P(pays within 30 days)",
  model: "Model/scorer version that produced this entry's numbers",
  cap: "The configured policy ceiling this entry was checked against",
  prior_contacts: "Messages already sent for this invoice before this entry",
  link: "Payment link generated for this action",
  simulated: "true = dry-run, nothing was actually sent to the customer",
  code: "Policy rule code — see /policy for the compiled rulebook",
};

function PayloadInspector({ entry }: { entry: DecisionTraceOut }) {
  const [open, setOpen] = useState(false);
  const payload = entry.payload ?? {};
  const knownKeys = Object.keys(payload).filter((k) => PAYLOAD_FIELD_HINTS[k]);
  return (
    <div className="pt-2 border-t border-zinc-200 dark:border-white/5">
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 text-[11px] font-mono text-orange-600 dark:text-orange-400 font-semibold"
      >
        <Braces className="h-3.5 w-3.5" />
        {open ? "Hide payload" : "Inspect payload"}
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden mt-2 space-y-2"
          >
            {knownKeys.length > 0 && (
              <ul className="rounded-lg bg-zinc-50 dark:bg-white/[0.03] border border-zinc-200/60 dark:border-white/10 p-3 text-[11px] leading-relaxed space-y-1">
                {knownKeys.map((k) => (
                  <li key={k} className="flex gap-2">
                    <span className="font-mono font-bold text-zinc-700 dark:text-zinc-300 shrink-0">{k}:</span>
                    <span className="text-zinc-500 dark:text-zinc-400">{PAYLOAD_FIELD_HINTS[k]}</span>
                  </li>
                ))}
              </ul>
            )}
            <pre className="overflow-x-auto rounded-lg bg-zinc-950 text-emerald-300 p-3 text-[11px] font-mono leading-relaxed">
              {JSON.stringify(payload, null, 2)}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function AuditExplorerPage({ params }: { params: Promise<{ invoiceId: string }> }) {
  const { invoiceId } = use(params);
  const decodedId = decodeURIComponent(invoiceId);
  const [explainerOpen, setExplainerOpen] = useState(false);

  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["audit-trail", decodedId],
    queryFn: () => fetchAuditTrail(decodedId),
    staleTime: 10_000,
  });

  const entries = data?.entries ?? [];
  const verified = data?.chain_verified ?? false;

  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[900px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <Link
            href={`/invoices/${encodeURIComponent(decodedId)}`}
            className="inline-flex items-center gap-1.5 text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> back to case file
          </Link>
          <button
            onClick={() => refetch()}
            className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-white/10"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} /> Re-verify
          </button>
        </div>

        {/* chain_verified badge */}
        <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-zinc-950 p-6 space-y-3">
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400 flex items-center gap-1.5">
            <History className="h-3.5 w-3.5" /> Decision Trace Explorer · {decodedId}
          </p>
          <div className="flex flex-col sm:flex-row sm:items-center gap-3">
            <button
              onClick={() => setExplainerOpen((v) => !v)}
              className={cn(
                "inline-flex items-center gap-2 px-4 py-2.5 rounded-2xl font-bold text-sm border",
                verified
                  ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30"
                  : "bg-red-500/10 text-red-700 dark:text-red-300 border-red-500/30"
              )}
            >
              {verified ? <ShieldCheck className="h-5 w-5" /> : <ShieldAlert className="h-5 w-5" />}
              {isLoading ? "Verifying…" : verified ? "Chain verified" : "Chain broken"}
              {explainerOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
            </button>
            <p className="text-xs text-zinc-500 font-mono">
              {data ? `${data.entry_count ?? data.count ?? entries.length} entries` : "—"} · GET /invoices/{decodedId}/audit
            </p>
          </div>
          <AnimatePresence>
            {explainerOpen && (
              <motion.div
                initial={{ opacity: 0, height: 0 }}
                animate={{ opacity: 1, height: "auto" }}
                exit={{ opacity: 0, height: 0 }}
                className="overflow-hidden"
              >
                <div className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-300 rounded-xl bg-white dark:bg-black border border-zinc-200 dark:border-white/10 p-4 space-y-2">
                  <p>
                    Each entry&apos;s hash covers its content plus the previous entry&apos;s hash; editing
                    history breaks every hash after it. The badge re-derives each hash from stored
                    content rather than trusting the stored value.
                  </p>
                  {!isLoading && !verified && (
                    <p className="text-red-600 dark:text-red-400 font-semibold">
                      This chain is currently reporting broken. That means a re-derived hash didn&apos;t
                      match a stored one — either an entry was altered/reordered, or an entry is
                      missing. Do not treat this invoice&apos;s history as reliable until it&apos;s
                      investigated; escalate to whoever owns the ledger rather than re-running actions.
                    </p>
                  )}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </section>

        {/* Event/outcome legend — only the values actually seen in this trace */}
        {!isLoading && entries.length > 0 && (
          <section className="rounded-xl border border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-white/[0.02] p-4">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-2">
              What these entries mean
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1 text-xs">
              {Array.from(new Set(entries.map((e) => e.event))).map((ev) => (
                <div key={ev} className="flex gap-2">
                  <span className="font-mono font-semibold text-zinc-700 dark:text-zinc-300 shrink-0">{ev}</span>
                  <span className="text-zinc-500 dark:text-zinc-400">{describeEvent(ev)}</span>
                </div>
              ))}
              {Array.from(new Set(entries.map((e) => e.outcome))).map((oc) => (
                <div key={oc} className="flex gap-2">
                  <span className="font-mono font-semibold text-zinc-700 dark:text-zinc-300 shrink-0 uppercase">{oc}</span>
                  <span className="text-zinc-500 dark:text-zinc-400">{describeOutcome(oc)}</span>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Chain visualization */}
        {isLoading ? (
          <div className="space-y-4">
            {[0, 1, 2].map((i) => (
              <div key={i} className="rounded-2xl border border-zinc-200 dark:border-white/10 p-5 animate-pulse">
                <div className="h-4 w-48 bg-zinc-200 dark:bg-white/10 rounded mb-2" />
                <div className="h-3 w-full bg-zinc-100 dark:bg-white/5 rounded" />
              </div>
            ))}
          </div>
        ) : isError ? (
          <div className="rounded-2xl border border-red-500/20 p-8 text-center text-sm">
            Could not load the audit trail. <button onClick={() => refetch()} className="underline font-semibold">Retry</button>
          </div>
        ) : entries.length === 0 ? (
          <div className="rounded-2xl border border-zinc-200 dark:border-white/10 p-8 text-center text-sm text-zinc-500">
            No trace records for this invoice yet. Run a decision cycle from the case file.
          </div>
        ) : (
          <div className="relative ml-3 pl-8 space-y-5 before:absolute before:left-0 before:top-2 before:bottom-2 before:w-0.5 before:bg-zinc-200 dark:before:bg-white/10">
            {entries.map((entry, idx) => (
              <motion.div
                key={entry.seq}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: Math.min(idx * 0.05, 0.4) }}
                className="relative"
              >
                {/* SVG connector dot — the hash chain, drawn literally */}
                <div className="absolute -left-8 top-5 -translate-x-1/2 flex flex-col items-center">
                  <span className={cn("h-3.5 w-3.5 rounded-full border-2", verified ? "border-emerald-500 bg-emerald-500/20" : "border-red-500 bg-red-500/20")} />
                  {idx < entries.length - 1 && (
                    <svg width="2" height="48" className="mt-1 overflow-visible">
                      <line x1="1" y1="0" x2="1" y2="48" stroke={verified ? "#10b981" : "#ef4444"} strokeWidth="2" />
                    </svg>
                  )}
                </div>
                <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-5 space-y-2.5 hover:border-orange-500/30 transition-colors">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono font-bold text-orange-600 dark:text-orange-400">#{entry.seq}</span>
                    <span
                      className="px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-900 font-mono text-[11px] font-semibold"
                      title={describeEvent(entry.event)}
                    >
                      {entry.event}
                    </span>
                    <span
                      className="px-2 py-0.5 rounded-full border text-[10px] font-mono font-bold uppercase border-zinc-200 dark:border-white/10 text-zinc-500"
                      title={describeOutcome(entry.outcome)}
                    >
                      {entry.outcome}
                    </span>
                    <span className="text-[10px] font-mono text-zinc-400 ml-auto">
                      {entry.actor} · {new Date(entry.recorded_at).toLocaleString()}
                    </span>
                  </div>
                  <p className="text-sm leading-relaxed">{entry.reason}</p>
                  <div className="font-mono text-[10px] space-y-0.5 text-zinc-500">
                    <p className="truncate" title={entry.prev_hash}>prev_hash: {truncHash(entry.prev_hash)} <span className="opacity-60">{entry.prev_hash}</span></p>
                    <p className="truncate text-emerald-600 dark:text-emerald-400" title={entry.entry_hash}>entry_hash: {truncHash(entry.entry_hash)} <span className="opacity-70">{entry.entry_hash}</span></p>
                  </div>
                  <PayloadInspector entry={entry} />
                </div>
              </motion.div>
            ))}
          </div>
        )}

        {/* Ledger implementation roadmap note */}
        <InfoCallout tone="warning" icon={Database} title="Ledger implementation:">
          Backed by an in-house append-only hash-chained ledger today — the same guarantee the
          backend documents in <span className="font-mono">app/core/audit.py</span>. The documented
          production upgrade path is <span className="font-mono">pyeventsourcing</span>-based
          aggregates with snapshotting and replay. Honest framing, stated confidently.
        </InfoCallout>
      </div>
    </main>
  );
}
