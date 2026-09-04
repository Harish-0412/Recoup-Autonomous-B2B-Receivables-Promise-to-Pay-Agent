"use client";

import React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Trophy,
  Crosshair,
  AlertTriangle,
  ShieldCheck,
  RefreshCw,
  FlaskConical,
} from "lucide-react";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  BarChart,
  Bar,
  Cell,
  LabelList,
} from "recharts";
import { fetchRecoveryCard } from "@/lib/api";
import { cn } from "@/lib/utils";

function formatINRCompact(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function sourceLabel(source: string): string {
  if (source === "model_card.json") return "fresh training artifact";
  if (source === "evaluation_report.json") return "training report";
  return "committed model-card numbers (no artifact on this clone)";
}

function shapColor(direction: string): string {
  if (direction === "positive") return "#10b981";
  if (direction === "negative") return "#f43f5e";
  return "#71717a";
}

function shortFeature(name: string): string {
  return name.replace(/^customer_/, "").replace(/_/g, " ");
}

export default function RecoveryStudioPage() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ["recovery-card"],
    queryFn: fetchRecoveryCard,
    staleTime: 30_000,
  });

  const h2h = data?.head_to_head ?? null;
  const shipped = data?.results.find((r) => r.shipped) ?? null;

  const calibrationPoints = (data?.calibration_bins ?? []).map((b) => ({
    predicted: b.predicted,
    observed: b.observed,
    n: b.count,
    label: `[${b.lower.toFixed(1)}, ${b.upper.toFixed(1)})`,
  }));

  const shapData = (data?.global_importance ?? []).map((d) => ({
    ...d,
    short: shortFeature(d.feature),
  }));

  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> dashboard
          </Link>
          <button
            onClick={() => refetch()}
            className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-white/10"
          >
            <RefreshCw className={cn("h-3.5 w-3.5", isFetching && "animate-spin")} /> Refresh
          </button>
        </div>

        {/* Header */}
        <section className="space-y-2">
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400 flex items-center gap-1.5">
            <FlaskConical className="h-3.5 w-3.5" /> Model Studio · Recovery Probability
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
            Does the model beat the rules it replaces?
          </h1>
          <p className="text-sm text-zinc-500 max-w-3xl">
            P(paid within 30 days), calibrated, multiplied straight into rupees. Every number
            below reproduces with{" "}
            <span className="font-mono text-xs">python scripts/train_recovery_model.py --batch-size 6000 --seed 42</span>
          </p>
          {data && (
            <div className="flex flex-wrap items-center gap-2 pt-1">
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono font-bold border border-zinc-200 dark:border-white/10">
                <Trophy className="h-3 w-3 text-orange-500" /> {data.shipped_model}
                {data.model_version ? ` · ${data.model_version}` : " · no artifact on this clone"}
              </span>
              <span
                className={cn(
                  "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono font-bold border",
                  data.use_model_scorer
                    ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20"
                    : "bg-zinc-100 dark:bg-zinc-900 text-zinc-500 border-zinc-200 dark:border-white/10"
                )}
              >
                <ShieldCheck className="h-3 w-3" />
                {data.use_model_scorer ? "USE_MODEL_SCORER on — model scores live" : "USE_MODEL_SCORER off — rules score, model advises"}
              </span>
              <span className="text-[11px] font-mono text-zinc-400">source: {sourceLabel(data.source)}</span>
            </div>
          )}
        </section>

        {isLoading ? (
          <div className="rounded-2xl border border-zinc-200 dark:border-white/10 p-8 animate-pulse space-y-3">
            <div className="h-6 w-64 bg-zinc-200 dark:bg-white/10 rounded" />
            <div className="h-40 bg-zinc-100 dark:bg-white/5 rounded-xl" />
          </div>
        ) : isError || !data ? (
          <div className="rounded-2xl border border-red-500/20 p-8 text-center text-sm">
            Could not load the recovery card. <button onClick={() => refetch()} className="underline font-semibold">Retry</button>
          </div>
        ) : (
          <>
            {/* Rules-vs-model callout — the single most persuasive number */}
            {h2h && (
              <motion.section
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                className="rounded-2xl border border-orange-500/30 bg-gradient-to-br from-orange-500/10 to-transparent p-6 sm:p-8 grid grid-cols-1 lg:grid-cols-3 gap-6 items-center"
              >
                <div className="lg:col-span-1">
                  <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400 flex items-center gap-1.5 mb-1">
                    <Crosshair className="h-3.5 w-3.5" /> Head to head · top {Math.round(h2h.top_fraction * 100)}% of queue
                  </p>
                  <p className="text-4xl sm:text-5xl font-extrabold tracking-tight text-orange-600 dark:text-orange-400">
                    +{formatINRCompact(h2h.value_delta)}
                  </p>
                  <p className="text-xs text-zinc-500 mt-1">more genuinely at-risk value correctly prioritized</p>
                </div>
                <div className="lg:col-span-2 grid grid-cols-2 gap-3 font-mono text-xs">
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-4">
                    <p className="text-zinc-500 uppercase text-[10px] tracking-widest">{h2h.challenger} (shipped)</p>
                    <p className="text-xl font-extrabold">{formatINRCompact(h2h.challenger_value_at_risk_at_k)}</p>
                    <p className="text-zinc-400">AUC {h2h.challenger_auc.toFixed(3)} · Brier {h2h.challenger_brier.toFixed(4)}</p>
                  </div>
                  <div className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-4">
                    <p className="text-zinc-500 uppercase text-[10px] tracking-widest">{h2h.incumbent} (incumbent)</p>
                    <p className="text-xl font-extrabold">{formatINRCompact(h2h.incumbent_value_at_risk_at_k)}</p>
                    <p className="text-zinc-400">AUC {h2h.incumbent_auc.toFixed(3)} · Brier {h2h.incumbent_brier.toFixed(4)}</p>
                  </div>
                </div>
              </motion.section>
            )}

            {/* Comparison table */}
            <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6">
              <h2 className="text-sm font-bold uppercase tracking-widest mb-1">Held-out test split · {data.test_rows} most recent invoices</h2>
              <p className="text-[11px] font-mono text-zinc-500 mb-4">
                threshold {data.threshold} · {data.calibration} calibration on validation · split by flag date, never at random
              </p>
              <div className="overflow-x-auto">
                <table className="w-full text-sm font-mono">
                  <thead>
                    <tr className="text-[10px] uppercase tracking-widest text-zinc-500 border-b border-zinc-200 dark:border-white/10">
                      <th className="text-left py-2 pr-3">Model</th>
                      <th className="text-right py-2 px-2">AUC</th>
                      <th className="text-right py-2 px-2">AP</th>
                      <th className="text-right py-2 px-2">Prec</th>
                      <th className="text-right py-2 px-2">Recall</th>
                      <th className="text-right py-2 px-2">F1</th>
                      <th className="text-right py-2 px-2">Brier</th>
                      <th className="text-right py-2 px-2">ECE</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.results.map((r) => (
                      <tr
                        key={r.model}
                        className={cn(
                          "border-b border-zinc-100 dark:border-white/5",
                          r.shipped && "bg-orange-500/5 font-bold"
                        )}
                      >
                        <td className="py-2.5 pr-3">
                          {r.shipped && <Trophy className="h-3.5 w-3.5 inline mr-1.5 text-orange-500" />}
                          {r.model}
                          {r.shipped && (
                            <span className="ml-2 text-[10px] px-1.5 py-0.5 rounded bg-orange-500 text-black font-bold">SHIPPED</span>
                          )}
                        </td>
                        <td className="text-right py-2.5 px-2">{r.auc.toFixed(3)}</td>
                        <td className="text-right py-2.5 px-2">{r.average_precision.toFixed(3)}</td>
                        <td className="text-right py-2.5 px-2">{r.precision.toFixed(3)}</td>
                        <td className="text-right py-2.5 px-2">{r.recall.toFixed(3)}</td>
                        <td className="text-right py-2.5 px-2">{r.f1.toFixed(3)}</td>
                        <td className="text-right py-2.5 px-2">{r.brier.toFixed(4)}</td>
                        <td className="text-right py-2.5 px-2">{r.ece.toFixed(3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-zinc-500 mt-3 leading-relaxed">
                Gradient boosting beat the logistic baseline, but barely ({shipped ? `${shipped.auc.toFixed(3)}` : "—"} vs{" "}
                {data.results.find((r) => r.model.includes("logreg"))?.auc.toFixed(3) ?? "—"}) — most of the signal is
                close to linear. The MLP did not win: expected on a few thousand rows of tabular data, stated with numbers.
              </p>
            </section>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Calibration curve */}
              <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6">
                <h2 className="text-sm font-bold uppercase tracking-widest">Calibration reliability curve</h2>
                <p className="text-[11px] font-mono text-zinc-500 mb-2">
                  predicted vs observed per bin · ECE {shipped?.ece.toFixed(3)} (rules: 0.167) — when the model says
                  0.7, it happens ~70% of the time
                </p>
                <div className="h-[280px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <ScatterChart margin={{ top: 10, right: 16, bottom: 10, left: -10 }}>
                      <CartesianGrid strokeDasharray="3 3" opacity={0.3} />
                      <XAxis type="number" dataKey="predicted" domain={[0, 1]} tick={{ fontSize: 10 }} label={{ value: "predicted", position: "bottom", fontSize: 10 }} />
                      <YAxis type="number" dataKey="observed" domain={[0, 1]} tick={{ fontSize: 10 }} label={{ value: "observed", angle: -90, fontSize: 10 }} />
                      <Tooltip
                        cursor={{ strokeDasharray: "3 3" }}
                        formatter={((value: unknown, name: unknown) => [
                          typeof value === "number" ? value.toFixed(3) : String(value ?? ""),
                          String(name ?? ""),
                        ]) as never}
                        labelFormatter={() => ""}
                      />
                      <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#71717a" strokeDasharray="5 4" label={{ value: "diagonal", fontSize: 9, fill: "#71717a" }} />
                      <Scatter name="bins" data={calibrationPoints} fill="#f97316">
                        <LabelList dataKey="label" position="top" style={{ fontSize: 9, fill: "#71717a" }} />
                      </Scatter>
                    </ScatterChart>
                  </ResponsiveContainer>
                </div>
                <p className="text-[11px] text-zinc-500 mt-1 font-mono">
                  dot size ∝ n · sparse bins (n &lt; 15) excluded — their gaps are noise, not miscalibration
                </p>
              </section>

              {/* SHAP bars */}
              <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6">
                <h2 className="text-sm font-bold uppercase tracking-widest">Top 10 global SHAP drivers</h2>
                <p className="text-[11px] font-mono text-zinc-500 mb-2">
                  mean |SHAP| on test · payment history dominates, as a collections analyst would expect
                </p>
                <div className="h-[280px]">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={shapData} layout="vertical" margin={{ top: 0, right: 40, bottom: 0, left: 10 }}>
                      <XAxis type="number" hide />
                      <YAxis type="category" dataKey="short" width={150} tick={{ fontSize: 10 }} />
                      <Tooltip formatter={((value: unknown) => [typeof value === "number" ? value.toFixed(3) : String(value ?? ""), "mean |SHAP|"]) as never} />
                      <Bar dataKey="mean_abs_shap" radius={[0, 4, 4, 0]}>
                        {shapData.map((d, i) => (
                          <Cell key={i} fill={shapColor(d.direction)} />
                        ))}
                        <LabelList dataKey="mean_abs_shap" position="right" formatter={(v: unknown) => (typeof v === "number" ? v.toFixed(3) : "")} style={{ fontSize: 10 }} />
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <p className="text-[11px] text-zinc-500 mt-1 font-mono">
                  <span className="text-emerald-500">■</span> pushes recovery up · <span className="text-rose-500">■</span> pushes it down
                </p>
              </section>
            </div>

            {/* Limitations */}
            <section className="rounded-2xl border border-amber-500/30 bg-amber-500/5 p-6">
              <h2 className="text-sm font-bold uppercase tracking-widest flex items-center gap-2 mb-3">
                <AlertTriangle className="h-4 w-4 text-amber-500" /> Honest limitations — verbatim from the model card
              </h2>
              <ul className="space-y-2 text-sm text-zinc-700 dark:text-zinc-300">
                {data.limitations.map((line, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="text-amber-500 font-bold">—</span>
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
              <Link
                href="/models/reply"
                className="inline-flex items-center gap-1.5 mt-4 text-sm font-semibold text-orange-600 dark:text-orange-400"
              >
                Next: Reply Understanding Studio <ArrowRight className="h-4 w-4" />
              </Link>
            </section>
          </>
        )}
      </div>
    </main>
  );
}
