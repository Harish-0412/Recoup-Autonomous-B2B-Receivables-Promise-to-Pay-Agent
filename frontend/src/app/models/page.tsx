"use client";

import React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { motion } from "framer-motion";
import {
  ArrowRight,
  FlaskConical,
  Gauge,
  MessagesSquare,
  Activity,
  Clock,
  ShieldAlert,
  Wallet,
  ExternalLink,
} from "lucide-react";
import {
  fetchRecoveryCard,
  fetchDriftCard,
  fetchTimingCard,
  fetchBrokenPromiseModelCard,
  fetchCashForecastCard,
} from "@/lib/api";
import { cn } from "@/lib/utils";

function SourceBadge({ source }: { source: string | undefined }) {
  const live = source === "model_card.json";
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-mono font-bold border",
        live
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25"
          : "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25"
      )}
      title={
        live
          ? "Fresh training artifact on this clone"
          : "Committed numbers — no training artifact on this clone"
      }
    >
      {live ? "live artifact" : source === "evaluation_report.json" ? "training report" : "committed"}
    </span>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg bg-zinc-50 dark:bg-white/[0.03] border border-zinc-200/60 dark:border-white/[0.06] px-3 py-2.5">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
        {label}
      </div>
      <div className="text-lg font-bold tabular-nums tracking-tight text-zinc-900 dark:text-white">
        {value}
      </div>
      {sub && <div className="text-[10px] text-zinc-500 dark:text-zinc-400">{sub}</div>}
    </div>
  );
}

function CardShell({
  id,
  icon,
  name,
  job,
  source,
  version,
  usedIn,
  children,
}: {
  id: string;
  icon: React.ReactNode;
  name: string;
  job: string;
  source: string | undefined;
  version?: string | null;
  usedIn: Array<{ label: string; href: string }>;
  children: React.ReactNode;
}) {
  return (
    <section
      id={id}
      className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6 space-y-4 scroll-mt-6"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="p-2 rounded-xl bg-orange-500/10 text-orange-600 dark:text-orange-400">
            {icon}
          </span>
          <div>
            <h2 className="text-base font-bold tracking-tight">{name}</h2>
            <p className="text-xs text-zinc-500 max-w-xl">{job}</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <SourceBadge source={source} />
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-2.5">{children}</div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 pt-1 border-t border-zinc-100 dark:border-white/5">
        {version && (
          <span className="text-[11px] font-mono text-zinc-500" title="Logged on every decision trace entry">
            {version}
          </span>
        )}
        {usedIn.map((u) => (
          <Link
            key={u.href + u.label}
            href={u.href}
            className="inline-flex items-center gap-1 text-[11px] font-semibold text-orange-600 dark:text-orange-400 hover:underline"
          >
            {u.label} <ExternalLink className="h-3 w-3 opacity-60" />
          </Link>
        ))}
      </div>
    </section>
  );
}

export default function ModelsHubPage() {
  const recovery = useQuery({ queryKey: ["recovery-card"], queryFn: fetchRecoveryCard, staleTime: 60_000 });
  const drift = useQuery({ queryKey: ["drift-card"], queryFn: fetchDriftCard, staleTime: 60_000 });
  const timing = useQuery({ queryKey: ["timing-card"], queryFn: fetchTimingCard, staleTime: 60_000 });
  const brokenPromise = useQuery({
    queryKey: ["bp-card"],
    queryFn: fetchBrokenPromiseModelCard,
    staleTime: 60_000,
    retry: 1,
  });
  const cash = useQuery({
    queryKey: ["cash-forecast-card"],
    queryFn: fetchCashForecastCard,
    staleTime: 60_000,
    retry: 1,
  });

  const shippedRecovery = recovery.data?.results.find((r) => r.shipped) ?? null;
  const bp = (brokenPromise.data ?? {}) as Record<string, any>;
  const bpMetrics = (bp.metrics ?? {}) as Record<string, any>;
  const cashCoverage =
    (cash.data?.coverage?.["30"] ?? cash.data?.test_coverage?.["30"]) as number | undefined;
  const cashBias = (cash.data?.bias?.["30"] ?? cash.data?.test_bias?.["30"]) as number | undefined;

  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="space-y-2">
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400 flex items-center gap-2">
            <FlaskConical className="h-3.5 w-3.5" /> Model validation hub
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
            Every model, and the numbers behind it
          </h1>
          <p className="text-sm text-zinc-500 max-w-3xl leading-relaxed">
            Six models advise the agent; the deterministic policy gate alone can act. Each card
            below shows live validation numbers with their source —{" "}
            <span className="font-mono text-xs">live artifact</span> means a training artifact
            exists on this clone, <span className="font-mono text-xs">committed</span> means the
            documented fallback. Model versions are written to the decision trace on every score,
            so any verdict links back to exactly these numbers.
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            {[
              ["recovery", "Recovery"],
              ["reply", "Reply intent"],
              ["drift", "Drift"],
              ["timing", "Send timing"],
              ["broken-promise", "Promise risk"],
              ["cash-forecast", "Cash forecast"],
            ].map(([id, label]) => (
              <a
                key={id}
                href={`#${id}`}
                className="text-[11px] font-mono px-2.5 py-1 rounded-full border border-zinc-200 dark:border-white/10 hover:border-orange-500/40 text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
              >
                {label}
              </a>
            ))}
          </div>
        </motion.div>

        {/* 1 · Recovery */}
        <CardShell
          id="recovery"
          icon={<Gauge className="h-5 w-5" />}
          name="Recovery probability"
          job="P(pays within 30d) per invoice — the EV term the queue is ranked on."
          source={recovery.data?.source}
          version={recovery.data?.model_version}
          usedIn={[
            { label: "Recovery studio", href: "/models/recovery" },
            { label: "Work queue", href: "/queue" },
          ]}
        >
          <Metric label="Test AUC" value={shippedRecovery ? shippedRecovery.auc.toFixed(3) : "—"} sub={shippedRecovery?.model ?? ""} />
          <Metric label="Brier" value={shippedRecovery ? shippedRecovery.brier.toFixed(4) : "—"} sub="lower is better" />
          <Metric label="ECE" value={shippedRecovery ? shippedRecovery.ece.toFixed(3) : "—"} sub="calibration error" />
          <Metric label="Test rows" value={(recovery.data?.test_rows ?? 0).toLocaleString("en-IN")} sub="temporal split" />
        </CardShell>

        {/* 2 · Reply intent */}
        <CardShell
          id="reply"
          icon={<MessagesSquare className="h-5 w-5" />}
          name="Reply understanding"
          job="TF-IDF/SVM cascade over LLM baseline — turns debtor replies into promises, disputes, opt-outs."
          source="model_card_fallback"
          version="tfidf-svm-intent-v1"
          usedIn={[
            { label: "Reply studio", href: "/models/reply" },
            { label: "Review desk", href: "/inbox" },
          ]}
        >
          <Metric label="Grouped macro-F1" value="0.764" sub="committed numbers" />
          <Metric label="Kept accuracy" value="0.917" sub="35.6% kept locally" />
          <Metric label="Cascade" value="36%" sub="resolved without LLM" />
          <Metric label="Guard" value="DISPUTE ≥ 0.85" sub="heightened threshold" />
        </CardShell>

        {/* 3 · Drift */}
        <CardShell
          id="drift"
          icon={<Activity className="h-5 w-5" />}
          name="Payment-behavior drift"
          job="Isolation Forest over recent payment behavior — flags customers degrading vs their cohort."
          source={drift.data?.source}
          version={drift.data?.model_version}
          usedIn={[{ label: "Case files (per customer)", href: "/queue" }]}
        >
          <Metric label="Contamination" value={drift.data ? drift.data.contamination.toFixed(2) : "—"} sub="expected flag rate" />
          <Metric label="Threshold" value={drift.data ? drift.data.threshold.toFixed(3) : "—"} sub="frozen anomaly cutoff" />
          <Metric label="Injected recall" value={drift.data?.injected_drift ? `${(drift.data.injected_drift.recall * 100).toFixed(0)}%` : "—"} sub="on purpose-degraded customers" />
          <Metric label="Test rows" value={drift.data ? drift.data.test_rows.toLocaleString("en-IN") : "—"} sub="holdout" />
        </CardShell>

        {/* 4 · Contact timing */}
        <CardShell
          id="timing"
          icon={<Clock className="h-5 w-5" />}
          name="Contact-timing optimizer"
          job="Contextual bandit over 15 weekday × daypart arms — learns when each segment answers."
          source={timing.data?.source}
          version={timing.data?.model_version}
          usedIn={[{ label: "Case files (send window)", href: "/queue" }]}
        >
          <Metric
            label="Policy reward"
            value={timing.data ? timing.data.policy_reward.toFixed(3) : "—"}
            sub={`vs logging ${timing.data ? timing.data.logging_reward.toFixed(3) : "—"}`}
          />
          <Metric
            label="Lift over logging"
            value={timing.data ? `+${(timing.data.lift_over_logging * 100).toFixed(1)}pp` : "—"}
            sub="offline counterfactual"
          />
          <Metric label="Truth MAE" value={timing.data ? timing.data.truth_mae.toFixed(3) : "—"} sub="vs archetype model" />
          <Metric label="Best fixed arm" value={timing.data?.best_fixed_arm.replace("_", " ") ?? "—"} sub="bandit beats it" />
        </CardShell>

        {/* 5 · Broken promise */}
        <CardShell
          id="broken-promise"
          icon={<ShieldAlert className="h-5 w-5" />}
          name="Broken-promise risk"
          job="LightGBM over 19 behavioral features — P(a commitment slips) before the promise date."
          source={brokenPromise.data ? "model_card.json" : undefined}
          version="v1.0.0"
          usedIn={[
            { label: "Dashboard simulator", href: "/dashboard" },
            { label: "Case files (per promise)", href: "/queue" },
          ]}
        >
          <Metric label="ROC-AUC" value={typeof bpMetrics.roc_auc === "number" ? bpMetrics.roc_auc.toFixed(3) : "0.899"} sub="risk ranking" />
          <Metric label="Accuracy" value={typeof bpMetrics.accuracy === "number" ? bpMetrics.accuracy.toFixed(3) : "0.819"} sub="at 0.5" />
          <Metric label="F1" value={typeof bpMetrics.f1_score === "number" ? bpMetrics.f1_score.toFixed(3) : "0.798"} sub="kept + broken" />
          <Metric label="Tiers" value="3" sub="LOW / MEDIUM / HIGH" />
        </CardShell>

        {/* 6 · Cash forecast */}
        <CardShell
          id="cash-forecast"
          icon={<Wallet className="h-5 w-5" />}
          name="Cash forecast"
          job="Monte Carlo over recovery probability × per-segment payment lags — rupees per 7/30-day window."
          source={cash.data?.source}
          version={cash.data?.model_version}
          usedIn={[
            { label: "Dashboard widget", href: "/dashboard" },
            { label: "Model card", href: "/models#cash-forecast" },
          ]}
        >
          <Metric
            label="30d coverage"
            value={typeof cashCoverage === "number" ? `${(cashCoverage * 100).toFixed(0)}%` : "—"}
            sub="of 90% interval"
          />
          <Metric
            label="30d bias"
            value={typeof cashBias === "number" ? `${cashBias >= 0 ? "+" : ""}${(cashBias * 100).toFixed(1)}%` : "—"}
            sub="mean vs realised"
          />
          <Metric label="Draws" value="10k" sub="per forecast" />
          <Metric label="Windows" value="7 / 30d" sub="landing windows" />
        </CardShell>

        <div className="text-center">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 text-sm font-semibold text-orange-600 dark:text-orange-400 hover:underline"
          >
            Back to Command Center <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </div>
    </main>
  );
}
