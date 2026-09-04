"use client";

import React, { use, useEffect, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Play,
  RefreshCw,
  FileText,
  ShieldAlert,
  Clock,
  IndianRupee,
  CheckCircle2,
  XCircle,
  PauseCircle,
  Ban,
} from "lucide-react";
import {
  fetchInvoiceDetail,
  runInvoiceCycle,
  type InvoiceOut,
  type PromiseOut,
  type RunCycleResponse,
} from "@/lib/api";
import { DecisionCycleVisualizer } from "@/components/case/DecisionCycleVisualizer";
import { cn } from "@/lib/utils";

const LADDER = ["monitoring", "reminded", "escalated", "human_handoff", "closed"] as const;

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function promiseBorder(status: PromiseOut["status"]): string {
  switch (status) {
    case "KEPT":
      return "border-l-emerald-500";
    case "BROKEN":
      return "border-l-red-500";
    case "SUPERSEDED":
      return "border-l-zinc-400";
    case "PENDING":
    default:
      return "border-l-amber-500";
  }
}

function promiseBadge(status: PromiseOut["status"]): string {
  switch (status) {
    case "KEPT":
      return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20";
    case "BROKEN":
      return "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20";
    case "SUPERSEDED":
      return "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border-zinc-500/20";
    default:
      return "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20";
  }
}

function EscalationStepper({ current }: { current: string }) {
  const normalized = current.toLowerCase();
  const activeIdx = LADDER.findIndex((s) => s === normalized);
  const safeIdx = activeIdx === -1 ? 0 : activeIdx;
  return (
    <div className="flex items-center gap-1.5 flex-wrap">
      {LADDER.map((step, idx) => {
        const done = idx < safeIdx;
        const active = idx === safeIdx;
        return (
          <div key={step} className="flex items-center gap-1.5">
            <div
              className={cn(
                "flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono font-semibold border",
                done && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20",
                active && "bg-orange-500 text-black border-orange-500",
                !done && !active && "bg-zinc-100 dark:bg-zinc-900 text-zinc-500 border-zinc-200 dark:border-white/10"
              )}
            >
              {done ? <CheckCircle2 className="h-3 w-3" /> : null}
              <span>{step}</span>
            </div>
            {idx < LADDER.length - 1 && <ArrowRight className="h-3 w-3 text-zinc-400" />}
          </div>
        );
      })}
    </div>
  );
}

export default function CaseFilePage({ params }: { params: Promise<{ invoiceId: string }> }) {
  const { invoiceId } = use(params);
  const decodedId = decodeURIComponent(invoiceId);

  const {
    data: invoice,
    isLoading,
    isError,
    refetch: refetchInvoice,
  } = useQuery<InvoiceOut>({
    queryKey: ["invoice-detail", decodedId],
    queryFn: () => fetchInvoiceDetail(decodedId),
    staleTime: 10_000,
  });

  const [cycleRunning, setCycleRunning] = useState(false);
  const [cycleResult, setCycleResult] = useState<RunCycleResponse | null>(null);
  const [cycleError, setCycleError] = useState<string | null>(null);

  const handleRunCycle = async () => {
    setCycleRunning(true);
    setCycleError(null);
    try {
      const res = await runInvoiceCycle(decodedId);
      setCycleResult(res);
      // Refresh header state (escalation_state, payment link, counters) after the cycle persists.
      await refetchInvoice();
    } catch (e) {
      setCycleError(e instanceof Error ? e.message : "Cycle failed");
    } finally {
      setCycleRunning(false);
    }
  };

  useEffect(() => {
    setCycleResult(null);
    setCycleError(null);
  }, [decodedId]);

  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        {/* Breadcrumb */}
        <div className="flex items-center justify-between">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> dashboard
          </Link>
          <Link
            href={`/invoices/${encodeURIComponent(decodedId)}/audit`}
            className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-white/10 hover:border-orange-500/40"
          >
            <FileText className="h-3.5 w-3.5" /> Decision Trace Explorer <ArrowRight className="h-3 w-3" />
          </Link>
        </div>

        {isLoading ? (
          <div className="rounded-2xl border border-zinc-200 dark:border-white/10 p-8 animate-pulse space-y-4">
            <div className="h-6 w-64 bg-zinc-200 dark:bg-white/10 rounded" />
            <div className="h-4 w-96 bg-zinc-200 dark:bg-white/10 rounded" />
            <div className="h-24 bg-zinc-100 dark:bg-white/5 rounded-xl" />
          </div>
        ) : isError || !invoice ? (
          <div className="rounded-2xl border border-red-500/20 bg-red-50 dark:bg-red-500/5 p-8 text-center space-y-3">
            <XCircle className="h-8 w-8 mx-auto text-red-500" />
            <p className="font-semibold">Could not load {decodedId}</p>
            <p className="text-xs text-zinc-500 font-mono">GET /invoices/{decodedId} failed and no fallback matched.</p>
            <button
              onClick={() => refetchInvoice()}
              className="px-4 py-2 rounded-xl bg-zinc-900 dark:bg-white text-white dark:text-black text-xs font-bold"
            >
              Retry
            </button>
          </div>
        ) : (
          <>
            {/* Header: the demo centerpiece */}
            <motion.section
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-zinc-950 p-6 space-y-5"
            >
              <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
                <div className="space-y-1.5">
                  <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400">
                    Case File · {invoice.invoice_id}
                  </p>
                  <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">{invoice.customer_name}</h1>
                  <p className="text-xs font-mono text-zinc-500">
                    {invoice.customer_id} · issued {invoice.issue_date} · due {invoice.due_date} · {invoice.status}
                  </p>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-3">
                    <p className="text-[10px] uppercase tracking-wider text-zinc-500 flex items-center gap-1">
                      <IndianRupee className="h-3 w-3" /> Outstanding
                    </p>
                    <p className="text-xl font-extrabold text-orange-600 dark:text-orange-400">{formatINR(invoice.outstanding)}</p>
                    <p className="text-[10px] font-mono text-zinc-400">
                      {formatINR(invoice.amount_paid)} paid of {formatINR(invoice.amount)}
                    </p>
                  </div>
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-3">
                    <p className="text-[10px] uppercase tracking-wider text-zinc-500 flex items-center gap-1">
                      <Clock className="h-3 w-3" /> Overdue
                    </p>
                    <p className="text-xl font-extrabold">{invoice.days_overdue}d</p>
                    <p className="text-[10px] font-mono text-zinc-400">{invoice.prior_reminders_sent} reminders sent</p>
                  </div>
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-3 col-span-2 sm:col-span-1">
                    <p className="text-[10px] uppercase tracking-wider text-zinc-500">Payment link</p>
                    {invoice.payment_link_url ? (
                      <a
                        href={invoice.payment_link_url}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 text-sm font-bold text-orange-600 dark:text-orange-400 underline"
                      >
                        Open link <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    ) : (
                      <p className="text-sm text-zinc-400">None yet — run a cycle.</p>
                    )}
                  </div>
                </div>
              </div>
              <EscalationStepper current={invoice.escalation_state} />
            </motion.section>

            {/* Promise history */}
            <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-bold uppercase tracking-widest">Promise history</h2>
                <span className="text-[11px] font-mono text-zinc-500">{invoice.promises.length} recorded</span>
              </div>
              {invoice.promises.length === 0 ? (
                <p className="text-sm text-zinc-500">No promises recorded for this invoice. A commitment parsed from a reply would appear here as PENDING, then resolve to KEPT / BROKEN.</p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  {invoice.promises.map((p) => (
                    <div key={p.promise_id} className={cn("rounded-xl border border-zinc-200 dark:border-white/10 border-l-4 bg-zinc-50/60 dark:bg-black p-4 space-y-1.5", promiseBorder(p.status))}>
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-xs font-bold">{p.promise_id}</span>
                        <span className={cn("text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border", promiseBadge(p.status))}>
                          {p.status}
                        </span>
                      </div>
                      <p className="text-lg font-extrabold">
                        {p.currency} {Number(p.promised_amount).toLocaleString("en-IN")} <span className="text-xs font-normal text-zinc-500">by {p.promised_date}</span>
                      </p>
                      <p className="text-[10px] font-mono text-zinc-400">
                        created {new Date(p.created_at).toLocaleString()}
                        {p.resolved_at ? ` · resolved ${new Date(p.resolved_at).toLocaleString()}` : ""}
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Run Decision Cycle — signature interaction */}
            <section className="rounded-2xl border border-orange-500/25 bg-gradient-to-b from-orange-500/5 to-transparent p-6 space-y-4">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <h2 className="text-sm font-bold uppercase tracking-widest flex items-center gap-2">
                    <ShieldAlert className="h-4 w-4 text-orange-500" /> Run Decision Cycle
                  </h2>
                  <p className="text-xs text-zinc-500 mt-1 max-w-2xl">
                    The one place to watch score → propose → gate → transition → execute with the reason at each step.
                    POST /invoices/{decodedId}/run-cycle is one round-trip today; stages reveal sequentially below.
                  </p>
                </div>
                <button
                  onClick={handleRunCycle}
                  disabled={cycleRunning}
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-r from-orange-500 to-amber-500 text-black shadow-lg shadow-orange-500/20 disabled:opacity-60"
                >
                  {cycleRunning ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
                  {cycleRunning ? "Running…" : "Run Decision Cycle"}
                </button>
              </div>

              {cycleError && (
                <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-xs font-mono flex items-center gap-2">
                  <Ban className="h-4 w-4" /> {cycleError}
                </div>
              )}

              {(cycleResult || cycleRunning) && (
                <DecisionCycleVisualizer result={cycleResult} running={cycleRunning} />
              )}

              {cycleResult && !cycleRunning && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 text-xs">
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-4 space-y-2">
                    <p className="font-mono uppercase text-[10px] tracking-widest text-zinc-500">Rationale</p>
                    <p className="leading-relaxed">{cycleResult.rationale}</p>
                    <p className="font-mono text-[11px] text-zinc-500">{cycleResult.reason}</p>
                    {cycleResult.top_drivers.length > 0 && (
                      <div className="pt-2 border-t border-zinc-100 dark:border-white/5 space-y-1 font-mono text-[11px]">
                        {cycleResult.top_drivers.slice(0, 3).map((d) => (
                          <div key={d.feature} className="flex justify-between gap-2">
                            <span className="truncate text-zinc-500">{d.feature}</span>
                            <span className={d.shap_contribution >= 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500"}>
                              {d.shap_contribution > 0 ? "+" : ""}{d.shap_contribution.toFixed(3)}
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-4 space-y-2">
                    <p className="font-mono uppercase text-[10px] tracking-widest text-zinc-500">Transition</p>
                    <p className="font-mono text-[11px]">
                      {cycleResult.state_before} → {cycleResult.state_after} · transitioned: {String(cycleResult.transitioned)} · terminal: {String(cycleResult.terminal)}
                    </p>
                    {cycleResult.execution && (
                      <div className="rounded-lg bg-zinc-50 dark:bg-zinc-900 p-3 font-mono text-[11px] space-y-1">
                        <p>status: {cycleResult.execution.status} · delivered: {String(cycleResult.execution.delivered)}{cycleResult.execution.dry_run ? " · DRY_RUN" : ""}</p>
                        {cycleResult.execution.subject && <p className="text-zinc-600 dark:text-zinc-300">subject: {cycleResult.execution.subject}</p>}
                        {cycleResult.execution.body_preview && <p className="text-zinc-500">{cycleResult.execution.body_preview}</p>}
                      </div>
                    )}
                    <Link href={`/invoices/${encodeURIComponent(decodedId)}/audit`} className="inline-flex items-center gap-1 text-orange-600 dark:text-orange-400 font-semibold">
                      Open Decision Trace <PauseCircle className="h-3 w-3" />
                    </Link>
                  </div>
                </div>
              )}
            </section>
          </>
        )}
      </div>
    </main>
  );
}
