"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "framer-motion";
import {
  BarChart3,
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  ArrowRight,
  ArrowUpRight,
  CheckCircle2,
  XCircle,
  MinusCircle,
  Eye,
  FileText,
  Layers,
  IndianRupee,
  Zap,
  Scale,
  Lock,
  TrendingUp,
  Info,
} from "lucide-react";
import {
  ResponsiveContainer,
  ComposedChart,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
  Area,
  Line,
  Legend,
} from "recharts";
import { fetchBatchReport, type BatchReportResponse, type TopCaseItem } from "@/lib/api";
import { cn } from "@/lib/utils";
import InfoTooltip from "@/components/ui/InfoTooltip";
import { describePolicyCode } from "@/lib/policyLabels";

// ---------------------------------------------------------------------------
// Formatting helpers (same conventions as /dashboard and /queue)
// ---------------------------------------------------------------------------

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function formatInt(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-IN");
}

// ---------------------------------------------------------------------------
// Tier + policy styling — identical mapping to /queue (QueueTable) so the
// leaderboard reads as the same system, not a second design language.
// ---------------------------------------------------------------------------

function TierBadge({ tier }: { tier: TopCaseItem["tier"] }) {
  const map = {
    ESCALATE: {
      base: "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border border-red-200/60 dark:border-red-500/20",
      dot: "bg-red-500",
    },
    REMIND: {
      base: "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-200/60 dark:border-blue-500/20",
      dot: "bg-blue-500",
    },
    WAIT: {
      base: "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20",
      dot: "bg-amber-500",
    },
  } as const;
  const style = map[tier];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold tracking-wide uppercase",
        style.base
      )}
    >
      <span className={cn("w-1.5 h-1.5 rounded-full", style.dot)} />
      {tier}
    </span>
  );
}

function PolicyBadge({ allowed }: { allowed: boolean | null }) {
  if (allowed === true)
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
        <CheckCircle2 className="w-4 h-4" />
        Passed
      </span>
    );
  if (allowed === false)
    return (
      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-red-600 dark:text-red-400">
        <XCircle className="w-4 h-4" />
        Blocked
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
      <MinusCircle className="w-4 h-4" />
      N/A
    </span>
  );
}

function PRecovery({ p }: { p: number }) {
  const tone =
    p >= 0.75
      ? "text-emerald-600 dark:text-emerald-400"
      : p >= 0.45
        ? "text-amber-600 dark:text-amber-400"
        : "text-red-600 dark:text-red-400";
  const bar =
    p >= 0.75 ? "bg-emerald-500" : p >= 0.45 ? "bg-amber-500" : "bg-red-500";
  return (
    <div className="flex flex-col gap-1 min-w-[84px]">
      <span className={cn("text-sm font-bold tabular-nums", tone)}>
        {(p * 100).toFixed(0)}%
      </span>
      <div className="h-1.5 w-full bg-zinc-100 dark:bg-white/5 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", bar)} style={{ width: `${Math.round(p * 100)}%` }} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Cycle-over-cycle history (v1: local snapshots, non-blocking)
// ---------------------------------------------------------------------------

interface HistoryPoint {
  ts: number;
  label: string;
  recoveryRate: number | null; // 0..1
  falseInterventions: number | null;
  flagged: number;
  recovered: number | null;
}

const HISTORY_KEY = "recoup-batch-history-v1";

function loadHistory(): HistoryPoint[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Small building blocks
// ---------------------------------------------------------------------------

function StatCell({
  label,
  value,
  sub,
  tooltip,
}: {
  label: string;
  value: string;
  sub?: string;
  tooltip?: React.ReactNode;
}) {
  return (
    <div className="rounded-xl px-4 py-3.5 bg-zinc-50/80 dark:bg-white/[0.03] border border-zinc-200/60 dark:border-white/[0.06]">
      <div className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
        {label}
        {tooltip && <InfoTooltip>{tooltip}</InfoTooltip>}
      </div>
      <div className="text-xl font-bold tracking-tight text-zinc-900 dark:text-white tabular-nums mt-0.5">
        {value}
      </div>
      {sub && (
        <div className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-0.5 tabular-nums">{sub}</div>
      )}
    </div>
  );
}

function CategorySection({
  icon: Icon,
  title,
  blurb,
  children,
}: {
  icon: React.ElementType;
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  return (
    <section className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5 sm:p-6">
      <div className="flex items-center gap-2.5 mb-1">
        <Icon className="w-4 h-4 text-orange-500" />
        <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-900 dark:text-white">
          {title}
        </h2>
      </div>
      <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4">{blurb}</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">{children}</div>
    </section>
  );
}

function SkeletonBlock({ className }: { className?: string }) {
  return (
    <div className={cn("rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 p-6 animate-pulse", className)}>
      <div className="h-4 w-48 rounded-md bg-zinc-200 dark:bg-white/10 mb-4" />
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 rounded-xl bg-zinc-100 dark:bg-white/5" />
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function BatchReportPage() {
  const shouldReduce = useReducedMotion();
  // v1 trend source: snapshots persisted in this browser. Lazy initializer
  // (not an effect) so there is no cascading render, and SSR-safe via the
  // window guard — localStorage only exists on the client.
  const [baseHistory] = useState<HistoryPoint[]>(() =>
    typeof window === "undefined" ? [] : loadHistory()
  );

  const { data, isLoading, isFetching, isError, refetch } = useQuery<BatchReportResponse>({
    queryKey: ["batch-report"],
    queryFn: fetchBatchReport,
    staleTime: 15_000,
  });

  const report = data?.report;
  const topCases = useMemo(() => {
    const cases = data?.top_cases ?? [];
    return [...cases].sort((a, b) => b.expected_value - a.expected_value);
  }, [data]);

  // Merge stored snapshots with the live report. Pure derivation — no
  // setState, no wall-clock reads, so nothing here can cascade or go stale.
  // Points are labelled sequentially: this is a v1 local trend, and a
  // sequence number is honest about what it is.
  const mergedHistory: HistoryPoint[] = useMemo(() => {
    const merged = [...baseHistory];
    if (report) {
      const last = merged[merged.length - 1];
      const sameAsLast =
        last &&
        last.flagged === report.flagged_for_intervention &&
        last.falseInterventions === report.false_interventions &&
        last.recovered === report.recovered_count &&
        last.recoveryRate === report.recovery_rate_of_flagged;
      if (!sameAsLast) {
        merged.push({
          ts: (last?.ts ?? 0) + 1,
          label: `Snapshot ${merged.length + 1}`,
          recoveryRate: report.recovery_rate_of_flagged,
          falseInterventions: report.false_interventions,
          flagged: report.flagged_for_intervention,
          recovered: report.recovered_count,
        });
      }
    }
    return merged.slice(-12);
  }, [baseHistory, report]);

  const trendData = useMemo(
    () =>
      mergedHistory.map((h) => ({
        ...h,
        recoveryPct: h.recoveryRate != null ? +(h.recoveryRate * 100).toFixed(1) : null,
      })),
    [mergedHistory]
  );

  // Persist the merged trend for future visits. This effect only writes to
  // the external system (localStorage) — it never calls setState.
  useEffect(() => {
    if (mergedHistory.length === 0 || typeof window === "undefined") return;
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(mergedHistory));
    } catch {
      // Private mode / quota — the page works fine without history.
    }
  }, [mergedHistory]);

  // The skeptic's one-click verify: a flagged (actioned) case, so the audit
  // trail actually shows scoring + gating + execution, not a WAIT no-op.
  const verifyCase =
    topCases.find((c) => c.tier !== "WAIT") ?? topCases[0] ?? null;

  const violations = report?.compliance_violations ?? 0;
  const recoveryPct =
    report?.recovery_rate_of_flagged != null
      ? `${(report.recovery_rate_of_flagged * 100).toFixed(1)}%`
      : "—";

  return (
    <main className="p-4 sm:p-6 lg:p-8 max-w-[1400px] mx-auto space-y-6 min-h-full">
      {/* Header */}
      <motion.div
        initial={shouldReduce ? { opacity: 1 } : { opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="flex flex-col lg:flex-row lg:items-start justify-between gap-5 pb-6 border-b border-zinc-200/60 dark:border-white/10"
      >
        <div className="space-y-2 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-mono text-orange-600 dark:text-orange-400 font-bold tracking-[0.12em] uppercase">
              <BarChart3 className="w-3.5 h-3.5" />
              <span>Recovery Analytics</span>
            </div>
            {data?.dry_run && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20">
                <Eye className="w-3 h-3" />
                DRY RUN — read-only
              </span>
            )}
            {report?.ledger_verified && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20 tabular-nums">
                <Lock className="w-3 h-3" />
                Ledger verified · {formatInt(report.ledger_entries)} entries
              </span>
            )}
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 dark:text-white leading-tight">
              Batch evaluation report
            </h1>
            <p className="text-sm sm:text-base text-zinc-500 dark:text-zinc-400 mt-1.5 max-w-2xl leading-relaxed">
              The full evaluation, visualized — every field of the batch report,
              grouped by what it means. Refreshing this page never mutates state:
              the report is scored and gated in memory, nothing is sent, and no
              decisions are written to the trace.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link
            href="/queue"
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <Zap className="w-4 h-4 text-indigo-500" />
            <span>Full Queue</span>
          </Link>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-md hover:shadow-lg shadow-orange-500/15 transition-all active:scale-[0.97] disabled:opacity-70 disabled:active:scale-100"
          >
            <RefreshCw className={cn("w-4 h-4", isFetching && "animate-spin")} />
            <span>{isFetching ? "Scoring…" : "Refresh Report"}</span>
          </button>
        </div>
      </motion.div>

      {/* Dry-run honesty strip (one line, same as the backend note) */}
      {data?.note && (
        <div className="rounded-xl px-4 py-3 bg-amber-50/60 dark:bg-amber-500/[0.07] border border-amber-200/50 dark:border-amber-500/20 flex items-start gap-3">
          <Info className="w-[18px] h-[18px] text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs sm:text-sm leading-relaxed text-amber-900/80 dark:text-amber-200/90">
            <span className="font-bold">Refreshing this page never mutates state.</span>{" "}
            {data.note}
          </p>
        </div>
      )}

      {isLoading ? (
        <>
          <SkeletonBlock />
          <SkeletonBlock />
          <SkeletonBlock />
        </>
      ) : isError || !report ? (
        <div className="rounded-2xl p-10 text-center bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
          <ShieldAlert className="w-8 h-8 text-red-500 mx-auto mb-3" />
          <h2 className="text-lg font-bold text-zinc-900 dark:text-white mb-1">
            Couldn&apos;t load the batch report
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mb-5">
            The backend didn&apos;t answer at <span className="font-mono">GET /reports/batch</span>. Is it running?
          </p>
          <button
            onClick={() => refetch()}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-orange-500 to-amber-500 text-white shadow-md"
          >
            <RefreshCw className="w-4 h-4" />
            Retry
          </button>
        </div>
      ) : (
        <>
          {/* Headline stat grid — by category, not one long list */}
          <CategorySection
            icon={Layers}
            title="Volume — how much book was judged"
            blurb="Case counts from each invoice's final decision. Per-case, not per-message."
          >
            <StatCell label="Invoices processed" value={formatInt(report.invoices_processed)} />
            <StatCell
              label="Flagged for intervention"
              value={formatInt(report.flagged_for_intervention)}
              sub={`${formatInt(report.left_alone)} left alone`}
            />
            <StatCell
              label="Handed off to human"
              value={formatInt(report.handed_off_to_human)}
              sub="ladder exhausted → person takes over"
            />
            <StatCell
              label="Decision cycles run"
              value={formatInt(report.cycles_run)}
              tooltip="One invoice can run the score → propose → gate → execute cycle more than once (e.g. a reminder, then later an escalation), so this can exceed invoices processed above."
              sub={`${formatInt(report.ledger_entries)} trace entries`}
            />
          </CategorySection>

          <CategorySection
            icon={IndianRupee}
            title="Value — what the targeting bought"
            blurb="Ground truth exists only in the synthetic demo; against real data these rows are absent rather than guessed."
          >
            <StatCell
              label="Total overdue value"
              value={formatINR(report.total_overdue_value)}
              sub={data.total_overdue_value_formatted || undefined}
            />
            <StatCell
              label="Recovered value (of flagged)"
              value={report.recovered_value != null ? formatINR(report.recovered_value) : "—"}
              sub={report.recovered_count != null ? `${formatInt(report.recovered_count)} invoices` : undefined}
            />
            <StatCell
              label="Recovery rate (of flagged)"
              value={recoveryPct}
              sub="targeting measure, not causation"
            />
            <StatCell
              label="False interventions"
              value={formatInt(report.false_interventions)}
              sub={`${formatInt(report.correctly_left_alone)} correctly left alone · ${formatInt(report.missed_recoveries)} missed`}
            />
          </CategorySection>

          <CategorySection
            icon={Zap}
            title="Actions — what fired vs what the gate refused"
            blurb="Counted from the decision trace across every cycle — messages and refusals, not invoices, so these can exceed case counts."
          >
            <StatCell
              label="Interventions executed"
              value={formatInt(report.interventions_executed)}
              sub="send_reminder + escalate transitions"
            />
            <StatCell
              label="Blocked by policy"
              value={formatInt(report.blocked_by_policy)}
              tooltip="An action the scorer proposed, refused before any outbound contact happened. The policy gate can only block or clamp — it never approves or escalates on its own."
              sub={
                Object.keys(report.policy_block_reasons ?? {}).length > 0
                  ? Object.entries(report.policy_block_reasons)
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 2)
                      .map(([k, v]) => `${describePolicyCode(k)}: ${v}`)
                      .join(" · ")
                  : "no blocks recorded"
              }
            />
            <StatCell
              label="Tier mix"
              value={Object.values(report.tier_counts ?? {}).length > 0 ? formatInt(Object.values(report.tier_counts).reduce((a, b) => a + b, 0)) : "—"}
              tooltip="WAIT: below the self-cure/EV threshold to act. REMIND: a reminder is worth sending. ESCALATE: highest urgency — next rung on the escalation ladder. See /policy for the exact ceilings."
              sub={
                Object.entries(report.tier_counts ?? {})
                  .map(([k, v]) => `${k} ${v}`)
                  .join(" · ") || "no tiers recorded"
              }
            />
            <StatCell
              label="Policy block reasons"
              value={formatInt(Object.keys(report.policy_block_reasons ?? {}).length)}
              sub={
                Object.entries(report.policy_block_reasons ?? {})
                  .sort((a, b) => b[1] - a[1])
                  .map(([k, v]) => `${describePolicyCode(k)} ×${v}`)
                  .join(", ") || "—"
              }
            />
          </CategorySection>

          {/* Compliance assertion — a counted claim, with a one-click verify */}
          <section
            className={cn(
              "rounded-2xl border shadow-sm p-5 sm:p-6",
              violations > 0
                ? "bg-red-50/60 dark:bg-red-500/[0.06] border-red-200/60 dark:border-red-500/25"
                : "bg-gradient-to-br from-emerald-50/70 via-white to-emerald-50/40 dark:from-emerald-950/15 dark:via-neutral-900 dark:to-emerald-950/10 border-emerald-200/60 dark:border-emerald-500/25"
            )}
          >
            <div className="flex flex-col lg:flex-row lg:items-center gap-5">
              <div className="flex items-center gap-4 flex-1 min-w-0">
                <div
                  className={cn(
                    "p-3 rounded-2xl flex-shrink-0",
                    violations > 0
                      ? "bg-red-100 dark:bg-red-500/15"
                      : "bg-emerald-100 dark:bg-emerald-500/15"
                  )}
                >
                  {violations > 0 ? (
                    <ShieldAlert className="w-7 h-7 text-red-600 dark:text-red-400" />
                  ) : (
                    <ShieldCheck className="w-7 h-7 text-emerald-600 dark:text-emerald-400" />
                  )}
                </div>
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="text-3xl sm:text-4xl font-bold tracking-tight tabular-nums text-zinc-900 dark:text-white">
                      {violations}
                    </span>
                    <span className="text-sm font-bold uppercase tracking-wider text-zinc-700 dark:text-zinc-200">
                      compliance violations
                    </span>
                  </div>
                  <p className="text-xs sm:text-sm text-zinc-600 dark:text-zinc-300 mt-1 leading-relaxed">
                    Counted from the decision trace, not asserted — a contact sent
                    while the standing policy verdict for that invoice was a block.
                    Ledger hash chain{" "}
                    <span className="font-semibold">
                      {report.ledger_verified ? "verified" : "NOT verified"}
                    </span>{" "}
                    over {formatInt(report.ledger_entries)} entries.
                  </p>
                </div>
              </div>
              {verifyCase && (
                <Link
                  href={`/invoices/${verifyCase.invoice_id}/audit`}
                  className={cn(
                    "inline-flex items-center justify-center gap-2 px-5 py-3 rounded-xl text-sm font-bold flex-shrink-0 transition-all active:scale-[0.97]",
                    violations > 0
                      ? "bg-red-600 hover:bg-red-700 text-white shadow-md"
                      : "bg-gradient-to-br from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white shadow-md hover:shadow-lg"
                  )}
                >
                  <Scale className="w-4 h-4" />
                  <span>Verify: audit {verifyCase.invoice_id}</span>
                  <ArrowUpRight className="w-4 h-4" />
                </Link>
              )}
            </div>
            {verifyCase && (
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-4 leading-relaxed">
                Don&apos;t take the zero on faith — open the flagged case{" "}
                <Link
                  href={`/invoices/${verifyCase.invoice_id}/audit`}
                  className="font-mono font-semibold text-emerald-700 dark:text-emerald-400 underline underline-offset-2"
                >
                  {verifyCase.invoice_id}
                </Link>{" "}
                ({verifyCase.tier}, EV {formatINR(verifyCase.expected_value)}) and read
                its policy verdicts yourself, in one click.
              </p>
            )}
          </section>

          {/* Cycle-over-cycle trend (v1: local snapshots, never blocks) */}
          <section className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5 sm:p-6">
            <div className="flex items-center gap-2.5 mb-1">
              <TrendingUp className="w-4 h-4 text-orange-500" />
              <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-900 dark:text-white">
                Cycle-over-cycle trend
              </h2>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4 max-w-3xl leading-relaxed">
              Recovery rate and false-intervention count per snapshot. The backend
              reports a single dry-run snapshot per call, so v1 builds the trend
              from snapshots stored locally in this browser as you refresh across
              runs — the page never waits on history to render.
            </p>
            {trendData.length === 0 ? (
              <div className="rounded-xl px-4 py-8 text-center bg-zinc-50/80 dark:bg-white/[0.02] border border-zinc-200/60 dark:border-white/[0.06]">
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No snapshots yet — refresh the report after future runs and the
                  trend will accumulate here.
                </p>
              </div>
            ) : (
              <div className="h-[260px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={trendData} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
                    <CartesianGrid strokeDasharray="3 3" className="stroke-zinc-200 dark:stroke-white/10" />
                    <XAxis
                      dataKey="label"
                      tick={{ fontSize: 11 }}
                      className="fill-zinc-500"
                      tickLine={false}
                      axisLine={false}
                    />
                    <YAxis
                      yAxisId="left"
                      tick={{ fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      domain={[0, 100]}
                      tickFormatter={(v: number) => `${v}%`}
                    />
                    <YAxis
                      yAxisId="right"
                      orientation="right"
                      tick={{ fontSize: 11 }}
                      tickLine={false}
                      axisLine={false}
                      allowDecimals={false}
                    />
                    <Tooltip
                      contentStyle={{
                        borderRadius: 12,
                        fontSize: 12,
                        border: "1px solid rgba(0,0,0,0.08)",
                      }}
                      formatter={(value, name) => {
                        if (name === "Recovery rate") return [`${value}%`, name];
                        return [value, name];
                      }}
                      labelFormatter={(_, payload) => {
                        const p = payload?.[0]?.payload as HistoryPoint | undefined;
                        return p
                          ? `${p.label} · ${p.flagged} flagged${p.recovered != null ? `, ${p.recovered} recovered` : ""}`
                          : "";
                      }}
                    />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Area
                      yAxisId="left"
                      type="monotone"
                      dataKey="recoveryPct"
                      name="Recovery rate"
                      stroke="#f97316"
                      fill="#fb923c"
                      fillOpacity={0.18}
                      strokeWidth={2}
                      dot={{ r: 3, fill: "#f97316" }}
                      connectNulls
                    />
                    <Line
                      yAxisId="right"
                      type="monotone"
                      dataKey="falseInterventions"
                      name="False interventions"
                      stroke="#6366f1"
                      strokeWidth={2}
                      dot={{ r: 3, fill: "#6366f1" }}
                      connectNulls
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            )}
            {trendData.length === 1 && (
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-3">
                Single snapshot so far — the line needs at least two points. Run the
                demo again (or just refresh later) and watch both series move.
              </p>
            )}
          </section>

          {/* Case leaderboard — EV-sorted, same tier/policy language as /queue */}
          <section className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm overflow-hidden">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-5 sm:p-6 pb-4 border-b border-zinc-100 dark:border-white/5">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <FileText className="w-4 h-4 text-indigo-500" />
                  <h2 className="text-lg font-bold tracking-tight text-zinc-900 dark:text-white">
                    Case leaderboard
                  </h2>
                </div>
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  Top {topCases.length} cases, ranked by expected value — the same
                  ordering the agent itself acts on.
                </p>
              </div>
              <Link
                href="/queue?sort=expected_value&sort_dir=desc"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border border-indigo-200/50 dark:border-indigo-500/20 hover:bg-indigo-100 dark:hover:bg-indigo-500/15 transition-colors self-start sm:self-auto"
              >
                Open in queue <ArrowRight className="w-3 h-3" />
              </Link>
            </div>

            {topCases.length === 0 ? (
              <div className="p-10 text-center">
                <p className="text-sm text-zinc-500 dark:text-zinc-400">
                  No cases in this snapshot — the open book may be empty.
                </p>
              </div>
            ) : (
              <>
                {/* Desktop table */}
                <div className="hidden md:block w-full overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="bg-zinc-50/60 dark:bg-white/[0.02] border-b border-zinc-200 dark:border-white/10 text-zinc-500 dark:text-zinc-400">
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider w-14">#</th>
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider">Invoice</th>
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                          <span className="inline-flex items-center gap-1">
                            Outstanding / EV
                            <InfoTooltip>
                              EV = P(recovery) × outstanding × urgency weight − intervention cost.
                              The mirror of expected-loss underwriting, run to estimate expected
                              recovery instead. The queue and this leaderboard both rank on it.
                            </InfoTooltip>
                          </span>
                        </th>
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider">
                          <span className="inline-flex items-center gap-1">
                            P(Recovery)
                            <InfoTooltip>
                              Output of the rules-based recovery scorer (not a trained/calibrated
                              model — see /models for validation numbers). It answers &quot;will this
                              invoice pay within 30 days&quot;, independent of any action taken.
                            </InfoTooltip>
                          </span>
                        </th>
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider">Tier</th>
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider">Policy</th>
                        <th className="px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider">Rationale</th>
                      </tr>
                    </thead>
                    <tbody>
                      {topCases.map((c, idx) => (
                        <tr
                          key={c.invoice_id}
                          className="border-b border-zinc-100 dark:border-white/[0.03] hover:bg-indigo-50/40 dark:hover:bg-indigo-500/[0.04] transition-colors group"
                        >
                          <td className="px-5 py-4 align-top">
                            <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg text-xs font-bold bg-zinc-100 dark:bg-white/5 text-zinc-500 dark:text-zinc-400 tabular-nums">
                              {idx + 1}
                            </span>
                          </td>
                          <td className="px-5 py-4 align-top">
                            <Link
                              href={`/invoices/${c.invoice_id}`}
                              className="font-mono font-bold text-[13px] text-indigo-600 dark:text-indigo-400 hover:underline underline-offset-2"
                            >
                              {c.invoice_id}
                            </Link>
                            <div className="mt-1">
                              <Link
                                href={`/invoices/${c.invoice_id}/audit`}
                                className="text-[11px] font-semibold text-zinc-400 dark:text-zinc-500 hover:text-indigo-500 dark:hover:text-indigo-400 transition-colors"
                              >
                                audit →
                              </Link>
                            </div>
                          </td>
                          <td className="px-5 py-4 align-top tabular-nums">
                            <div className="text-sm font-bold text-zinc-900 dark:text-white">
                              {formatINR(c.outstanding)}
                            </div>
                            <div className="text-[11px] font-mono text-zinc-500 dark:text-zinc-400">
                              EV {formatINR(c.expected_value)}
                            </div>
                          </td>
                          <td className="px-5 py-4 align-top">
                            <PRecovery p={c.p_recovery} />
                          </td>
                          <td className="px-5 py-4 align-top">
                            <TierBadge tier={c.tier} />
                          </td>
                          <td className="px-5 py-4 align-top">
                            <PolicyBadge allowed={c.policy_allowed} />
                          </td>
                          <td className="px-5 py-4 align-top max-w-[320px]">
                            <p className="text-xs text-zinc-600 dark:text-zinc-300 leading-relaxed line-clamp-2">
                              {c.rationale}
                            </p>
                            {c.reason && (
                              <p className="text-[11px] font-mono text-zinc-400 dark:text-zinc-500 mt-1 line-clamp-1">
                                {c.reason}
                              </p>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Mobile cards */}
                <div className="md:hidden flex flex-col gap-3 p-4">
                  {topCases.map((c, idx) => (
                    <Link
                      key={c.invoice_id}
                      href={`/invoices/${c.invoice_id}`}
                      className="block rounded-2xl p-4 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm active:scale-[0.995] transition-transform"
                    >
                      <div className="flex items-start justify-between gap-3 mb-3">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="inline-flex items-center justify-center w-6 h-6 rounded-md text-[10px] font-bold bg-zinc-100 dark:bg-white/5 text-zinc-500 dark:text-zinc-400 tabular-nums flex-shrink-0">
                            {idx + 1}
                          </span>
                          <span className="font-mono text-xs font-bold text-indigo-600 dark:text-indigo-400 truncate">
                            {c.invoice_id}
                          </span>
                        </div>
                        <TierBadge tier={c.tier} />
                      </div>
                      <div className="grid grid-cols-3 gap-3 mb-3">
                        <div>
                          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1">O/S</div>
                          <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums">{formatINR(c.outstanding)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1">EV</div>
                          <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums">{formatINR(c.expected_value)}</div>
                        </div>
                        <div>
                          <PRecovery p={c.p_recovery} />
                        </div>
                      </div>
                      <div className="flex items-center justify-between gap-2">
                        <PolicyBadge allowed={c.policy_allowed} />
                        <span className="text-[11px] text-zinc-400 dark:text-zinc-500 truncate flex-1 text-right">
                          {c.reason || c.rationale}
                        </span>
                      </div>
                    </Link>
                  ))}
                </div>
              </>
            )}
          </section>

          {/* Raw rendered block — the exact text the README carries */}
          {data.rendered && (
            <details className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm overflow-hidden group">
              <summary className="cursor-pointer list-none p-5 sm:p-6 flex items-center justify-between gap-3 hover:bg-zinc-50/60 dark:hover:bg-white/[0.02] transition-colors">
                <div className="flex items-center gap-2.5">
                  <FileText className="w-4 h-4 text-zinc-400" />
                  <span className="text-sm font-bold text-zinc-900 dark:text-white">
                    Raw report text
                  </span>
                  <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                    the fixed-width block the README quotes
                  </span>
                </div>
                <span className="text-xs font-mono text-zinc-400">expand</span>
              </summary>
              <div className="px-5 sm:px-6 pb-5 sm:pb-6">
                <pre className="p-4 rounded-xl bg-zinc-950 dark:bg-black text-zinc-100 font-mono text-xs leading-relaxed overflow-x-auto border border-zinc-800">
                  {data.rendered}
                </pre>
              </div>
            </details>
          )}
        </>
      )}
    </main>
  );
}
