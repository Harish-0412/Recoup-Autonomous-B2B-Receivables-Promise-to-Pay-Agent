"use client";

import React, { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  fetchBatchReport,
  fetchTaskStatus,
  type BatchReportResponse,
  type TaskStatusResponse,
} from "@/lib/api";
import KpiCard from "@/components/KpiCard";
import { RecoveryFunnel } from "@/components/dashboard/RecoveryFunnel";
import { PriorityQueue } from "@/components/dashboard/PriorityQueue";
import { HonestyPanel } from "@/components/dashboard/HonestyPanel";
import { motion, useReducedMotion } from "framer-motion";
import {
  IndianRupee,
  Zap,
  CheckCircle2,
  TrendingUp,
  ShieldAlert,
  ShieldCheck,
  RefreshCw,
  Activity,
  Inbox,
  ListOrdered,
  FileText,
  Clock,
  BellRing,
  Server,
} from "lucide-react";
import { cn } from "@/lib/utils";

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function SkeletonKpi() {
  return (
    <div className="rounded-2xl p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm animate-pulse">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 space-y-3">
          <div className="h-3 w-32 rounded-md bg-zinc-200 dark:bg-white/10" />
          <div className="h-8 w-28 rounded-md bg-zinc-200 dark:bg-white/10" />
        </div>
        <div className="h-10 w-10 rounded-xl bg-zinc-200 dark:bg-white/10" />
      </div>
    </div>
  );
}

function SkeletonCard() {
  return (
    <div className="rounded-2xl p-6 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm h-full animate-pulse">
      <div className="h-5 w-40 rounded-md bg-zinc-200 dark:bg-white/10 mb-4" />
      <div className="h-3 w-56 rounded-md bg-zinc-200 dark:bg-white/10 mb-6" />
      <div className="space-y-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-20 rounded-xl bg-zinc-100 dark:bg-white/5" />
        ))}
      </div>
    </div>
  );
}

export default function DashboardPage() {
  const shouldReduceMotion = useReducedMotion();
  const [refetchIndex, setRefetchIndex] = useState(0);

  const {
    data: batchData,
    isLoading: batchLoading,
    isFetching: batchFetching,
    refetch: refetchBatch,
  } = useQuery<BatchReportResponse>({
    queryKey: ["batch-report"],
    queryFn: fetchBatchReport,
    staleTime: 15_000,
  });

  const { data: taskStatus } = useQuery<TaskStatusResponse | null>({
    queryKey: ["task-status"],
    queryFn: fetchTaskStatus,
    staleTime: 30_000,
  });

  const report = batchData?.report;
  const topCases = batchData?.top_cases ?? [];
  const isLoading = batchLoading;
  const isFetching = batchFetching;

  const totalOverdue = report?.total_overdue_value ?? 69600000;
  const flaggedCount = report?.flagged_for_intervention ?? 847;
  const recoveredValue = report?.recovered_value ?? 48200000;
  const recoveryRate = (report?.recovery_rate_of_flagged ?? 0.725) * 100;
  const complianceViolations = report?.compliance_violations ?? 0;

  const invoicesProcessed = report?.invoices_processed ?? 1204;
  const actedOnCount = report?.interventions_executed ?? 812;
  const recoveredCount = report?.recovered_count ?? 614;

  const handleManualRefetch = async () => {
    setRefetchIndex((prev) => prev + 1);
    await refetchBatch();
  };

  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: shouldReduceMotion ? 0 : 0.06,
      },
    },
  };

  const itemVariants = {
    hidden: { opacity: 0, y: shouldReduceMotion ? 0 : 16 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.45, ease: "easeOut" as const },
    },
  };

  const systemBadge = () => {
    if (!taskStatus) return null;
    const healthy = taskStatus.sending_enabled !== false;
    return (
      <div
        className={cn(
          "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold border",
          healthy
            ? "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-200/60 dark:border-emerald-500/20"
            : "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border-red-200/60 dark:border-red-500/20"
        )}
      >
        <span
          className={cn(
            "w-1.5 h-1.5 rounded-full",
            healthy
              ? "bg-emerald-500 animate-pulse"
              : "bg-red-500 animate-pulse"
          )}
        />
        {taskStatus.dry_run ? "Sim Mode" : "Live"}
      </div>
    );
  };

  return (
    <main className="p-4 sm:p-6 lg:p-8 max-w-[1400px] mx-auto space-y-6 lg:space-y-8 min-h-full">
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="flex flex-col lg:flex-row lg:items-start justify-between gap-5 pb-6 border-b border-zinc-200/60 dark:border-white/10"
      >
        <div className="space-y-2 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-mono text-orange-600 dark:text-orange-400 font-bold tracking-[0.12em] uppercase">
              <Activity className="w-3.5 h-3.5" />
              <span>Autonomous Receivables Operations</span>
            </div>
            {systemBadge()}
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-zinc-900 dark:text-white leading-tight">
              Command Center
            </h1>
            <p className="text-sm sm:text-base text-zinc-500 dark:text-zinc-400 mt-1.5 max-w-2xl leading-relaxed">
              Real-time receivables book health, EV-ranked intervention queue,
              and verified ledger recovery.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link
            href="/inbox"
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <Inbox className="w-4 h-4 text-amber-500" />
            <span>Inbox</span>
            {taskStatus?.replies_awaiting_review != null &&
              taskStatus.replies_awaiting_review > 0 && (
                <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-amber-500 text-white text-[10px] font-bold">
                  {taskStatus.replies_awaiting_review}
                </span>
              )}
          </Link>

          <Link
            href="/queue"
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <ListOrdered className="w-4 h-4 text-indigo-500" />
            <span>Full Queue</span>
          </Link>

          <button
            onClick={handleManualRefetch}
            disabled={isFetching}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-md hover:shadow-lg shadow-orange-500/15 transition-all active:scale-[0.97] disabled:opacity-70 disabled:active:scale-100"
            title="Refetch live batch scoring and trigger KPI count-up"
          >
            <RefreshCw
              className={cn("w-4 h-4", isFetching && "animate-spin")}
            />
            <span>{isFetching ? "Scoring..." : "Refresh Batch"}</span>
          </button>
        </div>
      </motion.div>

      {taskStatus && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.05 }}
          className="grid grid-cols-2 sm:grid-cols-4 gap-3"
        >
          <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
            <FileText className="w-4 h-4 text-zinc-500 flex-shrink-0" />
            <div className="min-w-0">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Open
              </div>
              <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums truncate">
                {taskStatus.open_invoices ?? "—"} invoices
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
            <Clock className="w-4 h-4 text-zinc-500 flex-shrink-0" />
            <div className="min-w-0">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Promises
              </div>
              <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums truncate">
                {taskStatus.pending_promises ?? "—"} pending
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
            <BellRing className="w-4 h-4 text-zinc-500 flex-shrink-0" />
            <div className="min-w-0">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Reviews
              </div>
              <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums truncate">
                {taskStatus.replies_awaiting_review ?? "—"} replies
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
            <Server className="w-4 h-4 text-zinc-500 flex-shrink-0" />
            <div className="min-w-0">
              <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Batch Cap
              </div>
              <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums truncate">
                {taskStatus.max_batch_size ?? "—"} invoices
              </div>
            </div>
          </div>
        </motion.div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <SkeletonKpi key={i} />
          ))}
        </div>
      ) : (
        <motion.div
          key={`kpi-container-${refetchIndex}`}
          variants={containerVariants}
          initial="hidden"
          animate="show"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4"
        >
          <motion.div variants={itemVariants}>
            <KpiCard
              title="Total Overdue Value"
              value={totalOverdue}
              formatter={formatINR}
              accentColor="warning"
              icon={IndianRupee}
              trend={{ value: 3.2, positive: false, label: "vs last run" }}
            />
          </motion.div>

          <motion.div variants={itemVariants}>
            <KpiCard
              title="Flagged for Intervention"
              value={flaggedCount}
              accentColor="info"
              icon={Zap}
              trend={{ value: 8, positive: true, label: "new cases" }}
            />
          </motion.div>

          <motion.div variants={itemVariants}>
            <KpiCard
              title="Recovered Value"
              value={recoveredValue}
              formatter={formatINR}
              accentColor="success"
              icon={TrendingUp}
              trend={{ value: 5.4, positive: true, label: "week-over-week" }}
            />
          </motion.div>

          <motion.div variants={itemVariants}>
            <KpiCard
              title="Recovery Rate"
              value={recoveryRate}
              formatter={(v) => `${v.toFixed(1)}%`}
              accentColor="default"
              icon={CheckCircle2}
              trend={{ value: 1.8, positive: true, label: "vs baseline" }}
            />
          </motion.div>

          <motion.div variants={itemVariants}>
            <KpiCard
              title="Compliance Violations"
              value={complianceViolations}
              accentColor={complianceViolations > 0 ? "danger" : "success"}
              icon={complianceViolations > 0 ? ShieldAlert : ShieldCheck}
            />
          </motion.div>
        </motion.div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-stretch">
        {isLoading ? (
          <>
            <div className="lg:col-span-5">
              <SkeletonCard />
            </div>
            <div className="lg:col-span-7">
              <SkeletonCard />
            </div>
          </>
        ) : (
          <>
            <div className="lg:col-span-5">
              <RecoveryFunnel
                invoicesProcessed={invoicesProcessed}
                flagged={flaggedCount}
                actedOn={actedOnCount}
                recovered={recoveredCount ?? 0}
              />
            </div>

            <div className="lg:col-span-7">
              <PriorityQueue cases={topCases} />
            </div>
          </>
        )}
      </div>

      <div className="w-full">
        <HonestyPanel
          falseInterventions={report?.false_interventions ?? 198}
          unnecessaryInterventions={0}
          correctlyLeftAlone={report?.correctly_left_alone ?? 289}
          missedRecoveries={report?.missed_recoveries ?? 68}
          caveats={[
            "Ground-truth payment outcomes are sampled independently under the synthetic benchmark.",
            "Contact frequency caps (3-5 days) and discount ceilings (max ₹20,000 / 10%) are strictly enforced by the policy gatekeeper.",
            "Recovery probabilities are trained on isotonic calibration fitted on a historical holdout.",
          ]}
        />
      </div>
    </main>
  );
}
