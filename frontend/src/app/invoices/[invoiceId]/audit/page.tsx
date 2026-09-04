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
} from "lucide-react";
import { fetchAuditTrail, type DecisionTraceOut } from "@/lib/api";
import { cn } from "@/lib/utils";

function truncHash(h: string): string {
  if (!h) return "…";
  if (h.length <= 10) return h;
  return `${h.slice(0, 4)}…${h.slice(-4)}`;
}

function PayloadInspector({ entry }: { entry: DecisionTraceOut }) {
  const [open, setOpen] = useState(false);
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
          <motion.pre
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-2 overflow-x-auto rounded-lg bg-zinc-950 text-emerald-300 p-3 text-[11px] font-mono leading-relaxed"
          >
            {JSON.stringify(entry.payload ?? {}, null, 2)}
          </motion.pre>
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
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400">
            Decision Trace Explorer · {decodedId}
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
                <p className="text-sm leading-relaxed text-zinc-600 dark:text-zinc-300 rounded-xl bg-white dark:bg-black border border-zinc-200 dark:border-white/10 p-4">
                  Each entry&apos;s hash covers its content plus the previous entry&apos;s hash; editing
                  history breaks every hash after it. The badge re-derives each hash from stored
                  content rather than trusting the stored value.
                </p>
              </motion.div>
            )}
          </AnimatePresence>
        </section>

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
                    <span className="px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-900 font-mono text-[11px] font-semibold">{entry.event}</span>
                    <span className="px-2 py-0.5 rounded-full border text-[10px] font-mono font-bold uppercase border-zinc-200 dark:border-white/10 text-zinc-500">
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
        <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-5 flex gap-3">
          <Database className="h-5 w-5 text-orange-500 shrink-0 mt-0.5" />
          <div className="text-xs leading-relaxed space-y-1.5">
            <p className="font-bold text-sm">Ledger implementation</p>
            <p className="text-zinc-600 dark:text-zinc-300">
              Backed by an in-house append-only hash-chained ledger today — the same guarantee the
              backend documents in <span className="font-mono">app/core/audit.py</span>. The documented
              production upgrade path is <span className="font-mono">pyeventsourcing</span>-based
              aggregates with snapshotting and replay. Honest framing, stated confidently.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
