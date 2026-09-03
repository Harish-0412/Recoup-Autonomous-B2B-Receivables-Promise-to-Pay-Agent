"use client";

import React, { useState, useEffect } from "react";
import { runInvoiceCycle, RunCycleResponse, triggerBatchRun } from "@/lib/api";
import { AuditLedgerModal } from "./audit-ledger-modal";
import {
  Activity,
  Play,
  CheckCircle2,
  ShieldCheck,
  Mail,
  ExternalLink,
  RefreshCw,
  Workflow
} from "lucide-react";

interface InvoiceScenario {
  id: string;
  customer: string;
  amount: string;
  overdueDays: number;
  expectedRisk: string;
  description: string;
}

const SCENARIOS: InvoiceScenario[] = [
  {
    id: "INV-1044",
    customer: "Acme Cloud Technologies",
    amount: "₹75,000",
    overdueDays: 2,
    expectedRisk: "Self-Cure Candidate",
    description: "Reliable customer with 97% on-time record. 2 days overdue.",
  },
  {
    id: "INV-1042",
    customer: "Nexlink Logistics Pvt Ltd",
    amount: "₹50,000",
    overdueDays: 9,
    expectedRisk: "Tier 1 Reminder",
    description: "Frequency cap cleared (3 days). Ready for initial payment nudge.",
  },
  {
    id: "INV-1043",
    customer: "Apex Retail Solutions",
    amount: "₹2,00,000",
    overdueDays: 24,
    expectedRisk: "High Value At Risk",
    description: "Prior promise broken on 28th. Final notice with authorized waiver.",
  },
];

export function DecisionCycleRunner() {
  const [selectedId, setSelectedId] = useState<string>("INV-1042");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RunCycleResponse | null>(null);
  const [auditModalOpen, setAuditModalOpen] = useState(false);
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchNotice, setBatchNotice] = useState<string | null>(null);

  const executeCycle = async (id: string) => {
    setLoading(true);
    setResult(null);
    try {
      const data = await runInvoiceCycle(id);
      setResult(data);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    executeCycle(selectedId);
  }, [selectedId]);

  const handleRunBatch = async () => {
    setBatchRunning(true);
    setBatchNotice(null);
    const res = await triggerBatchRun(30);
    setBatchNotice(res.message);
    setBatchRunning(false);
    setTimeout(() => setBatchNotice(null), 6000);
  };

  const currentScenario = SCENARIOS.find((s) => s.id === selectedId) || SCENARIOS[1];

  return (
    <div className="space-y-6">
      {/* Top Selector Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 rounded-2xl bg-white dark:bg-black border border-zinc-200 dark:border-white/10 shadow-sm">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-semibold text-zinc-500 uppercase tracking-wider">
            Select Case:
          </span>
          <div className="flex items-center gap-1.5">
            {SCENARIOS.map((s) => (
              <button
                key={s.id}
                onClick={() => setSelectedId(s.id)}
                className={`px-3.5 py-1.5 rounded-xl text-xs font-mono font-semibold transition-all ${
                  selectedId === s.id
                    ? "bg-orange-500 text-black shadow-sm"
                    : "bg-zinc-100 dark:bg-zinc-900 text-zinc-600 dark:text-zinc-400 hover:text-zinc-950 dark:hover:text-white"
                }`}
              >
                {s.id}
              </button>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => executeCycle(selectedId)}
            disabled={loading}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-xl text-xs font-semibold bg-orange-600 hover:bg-orange-500 text-white shadow-sm transition-all"
          >
            {loading ? (
              <RefreshCw className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5 fill-current" />
            )}
            Run Decision Cycle
          </button>

          <button
            onClick={handleRunBatch}
            disabled={batchRunning}
            title="Execute Autonomous Cron Batch Sweep"
            className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-900 hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300 transition-colors"
          >
            <Workflow className={`h-3.5 w-3.5 ${batchRunning ? "animate-spin text-orange-500" : ""}`} />
            Run Batch
          </button>
        </div>
      </div>

      {batchNotice && (
        <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-xs font-mono flex items-center gap-2">
          <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
          <span>{batchNotice}</span>
        </div>
      )}

      {/* Main Simulation Panel */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
        {/* Left: Case & ML Recovery Profile */}
        <div className="lg:col-span-4 p-6 rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black/80 flex flex-col justify-between shadow-sm">
          <div className="space-y-5">
            <div className="flex items-center justify-between">
              <span className="font-mono text-sm font-bold text-zinc-950 dark:text-white">
                {currentScenario.id}
              </span>
              <span className="text-[11px] font-semibold px-2.5 py-0.5 rounded-full border bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20">
                {currentScenario.expectedRisk}
              </span>
            </div>

            <div className="space-y-3">
              <div>
                <p className="text-xs text-zinc-500 uppercase tracking-wider">Customer</p>
                <p className="text-base font-semibold text-zinc-950 dark:text-white">
                  {currentScenario.customer}
                </p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <p className="text-xs text-zinc-500 uppercase tracking-wider">Amount</p>
                  <p className="text-xl font-bold text-orange-600 dark:text-orange-400">
                    {currentScenario.amount}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-zinc-500 uppercase tracking-wider">Overdue</p>
                  <p className="text-xl font-bold text-zinc-800 dark:text-zinc-200">
                    {currentScenario.overdueDays} Days
                  </p>
                </div>
              </div>
              <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed pt-1 border-t border-zinc-100 dark:border-white/5">
                {currentScenario.description}
              </p>
            </div>

            {/* ML Probability Meter */}
            <div className="p-4 rounded-xl bg-zinc-50 dark:bg-zinc-950 border border-zinc-200 dark:border-white/10 space-y-3">
              <div className="flex items-center justify-between text-xs">
                <span className="font-mono font-semibold text-zinc-700 dark:text-zinc-300">
                  P(Recovery within 30d):
                </span>
                <span className="font-mono font-bold text-orange-600 dark:text-orange-400 text-sm">
                  {result?.p_recovery ? `${(result.p_recovery * 100).toFixed(1)}%` : "Calculating..."}
                </span>
              </div>
              <div className="w-full h-2 rounded-full bg-zinc-200 dark:bg-zinc-800 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-orange-500 to-emerald-500 rounded-full transition-all duration-500"
                  style={{ width: `${(result?.p_recovery || 0.5) * 100}%` }}
                ></div>
              </div>

              {/* SHAP Feature Drivers */}
              {result?.top_drivers && result.top_drivers.length > 0 && (
                <div className="space-y-1.5 pt-2 border-t border-zinc-200 dark:border-white/5 text-[11px] font-mono">
                  <span className="text-zinc-500 block text-[10px] uppercase font-bold">
                    Top SHAP Feature Attributions:
                  </span>
                  {result.top_drivers.map((d) => (
                    <div key={d.feature} className="flex items-center justify-between">
                      <span className="text-zinc-600 dark:text-zinc-400 truncate max-w-[170px]" title={d.feature}>
                        {d.feature.replace(/customer_|invoice_/g, "")}
                      </span>
                      <span
                        className={`font-semibold ${
                          d.shap_contribution >= 0
                            ? "text-emerald-600 dark:text-emerald-400"
                            : "text-red-500"
                        }`}
                      >
                        {d.shap_contribution > 0 ? "+" : ""}
                        {d.shap_contribution.toFixed(3)}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          <div className="mt-6 pt-4 border-t border-zinc-200 dark:border-white/10 flex items-center justify-between text-xs">
            <span className="text-zinc-500 font-mono">Scorer Model:</span>
            <span className="font-mono text-emerald-600 dark:text-emerald-400 font-semibold">
              xgb-recovery-calibrated
            </span>
          </div>
        </div>

        {/* Right: Policy Decision & Execution Result */}
        <div className="lg:col-span-8 p-7 rounded-2xl border border-zinc-200 dark:border-white/15 bg-zinc-100/80 dark:bg-gradient-to-br dark:from-zinc-900 dark:to-black flex flex-col justify-between shadow-sm">
          <div className="space-y-6">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-xs font-mono text-orange-600 dark:text-orange-400 font-semibold">
                <Activity className="h-4 w-4" />
                <span>DECISION CYCLE VERDICT</span>
              </div>
              {result?.decision && (
                <span className="px-2.5 py-1 rounded-lg text-xs font-mono font-bold uppercase bg-zinc-900 text-white dark:bg-white dark:text-black">
                  {result.decision.outcome}
                </span>
              )}
            </div>

            {loading ? (
              <div className="flex flex-col items-center justify-center py-16 text-zinc-500 gap-3 font-mono text-xs">
                <RefreshCw className="h-6 w-6 animate-spin text-orange-500" />
                <span>Running policy rules, risk models, and executor gates...</span>
              </div>
            ) : (
              <>
                <div className="space-y-3">
                  <h4 className="text-xl font-bold text-zinc-950 dark:text-white tracking-tight font-mono">
                    {result?.decision?.outcome === "wait"
                      ? "PASS (SUPPRESS CONTACT — GOODWILL PROTECTION)"
                      : result?.decision?.outcome === "escalate"
                      ? `ESCALATE TO ${result.decision.ladder_step.toUpperCase().replace(/_/g, " ")}`
                      : "AUTONOMOUS POLICY VERDICT"}
                  </h4>
                  <p className="text-zinc-700 dark:text-zinc-300 text-sm leading-relaxed">
                    {result?.decision?.reason || "Evaluating case snapshot against policy engine..."}
                  </p>
                </div>

                {/* Execution Details & Razorpay Link */}
                {result?.execution && (
                  <div className="p-4 rounded-xl bg-white dark:bg-black/60 border border-zinc-200 dark:border-white/10 space-y-3 shadow-inner">
                    <div className="flex items-center justify-between text-xs font-mono">
                      <span className="text-zinc-500">Outbound Channel:</span>
                      <span className="font-semibold text-zinc-900 dark:text-white flex items-center gap-1">
                        <Mail className="h-3.5 w-3.5 text-orange-500" />
                        {result.execution.channel.toUpperCase()} (Resend SDK)
                      </span>
                    </div>

                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs font-mono">
                      <span className="text-zinc-500">Razorpay Payment Instrument:</span>
                      {result.execution.payment_link_url ? (
                        <a
                          href={result.execution.payment_link_url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-orange-600 dark:text-orange-400 font-semibold hover:underline"
                        >
                          <span>{result.execution.payment_link_id || "Live Link"}</span>
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-zinc-400">Not required for this step</span>
                      )}
                    </div>

                    <div className="p-3 rounded-lg bg-zinc-50 dark:bg-zinc-950/80 border border-zinc-200 dark:border-white/5 text-xs text-zinc-600 dark:text-zinc-400 font-mono">
                      <p className="text-zinc-900 dark:text-zinc-200 font-semibold mb-1">
                        Subject: {result.execution.subject}
                      </p>
                      <p className="text-[11px] leading-relaxed text-zinc-500">
                        {result.execution.body_preview}
                      </p>
                    </div>
                  </div>
                )}

                {/* Cryptographic Trace Checksum */}
                {result?.trace_entry && (
                  <div className="p-4 rounded-xl bg-emerald-500/5 border border-emerald-500/20 font-mono text-xs flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                    <div>
                      <span className="text-zinc-500 block text-[10px] uppercase font-bold">
                        Decision Trace Checksum (SHA-256):
                      </span>
                      <span className="text-emerald-600 dark:text-emerald-400 font-bold truncate block max-w-sm">
                        {result.trace_entry.entry_hash}
                      </span>
                    </div>
                    <button
                      onClick={() => setAuditModalOpen(true)}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600/10 hover:bg-emerald-600/20 text-emerald-700 dark:text-emerald-300 font-semibold border border-emerald-500/30 transition-colors text-xs self-start sm:self-auto"
                    >
                      <ShieldCheck className="h-3.5 w-3.5" />
                      Inspect Chain
                    </button>
                  </div>
                )}
              </>
            )}
          </div>

          <div className="mt-6 flex items-center justify-between text-xs text-zinc-500 pt-4 border-t border-zinc-200 dark:border-white/10 font-mono">
            <span>Deterministic Policy: business_rules v1.2</span>
            <span className="text-orange-600 dark:text-orange-400 font-medium">
              Zero Unbounded LLM Decision Making
            </span>
          </div>
        </div>
      </div>

      {/* Audit Modal */}
      <AuditLedgerModal
        invoiceId={selectedId}
        isOpen={auditModalOpen}
        onClose={() => setAuditModalOpen(false)}
      />
    </div>
  );
}
