"use client";

import React, { useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "framer-motion";
import {
  fetchPolicy,
  type PolicyResponse,
  type PolicyRule,
  type PolicyCondition,
} from "@/lib/api";
import KpiCard from "@/components/KpiCard";
import {
  ShieldCheck,
  ShieldAlert,
  Shield,
  Lock,
  Percent,
  Clock3,
  Mail,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  ArrowRight,
  LockKeyhole,
  Scale,
  Workflow,
  ChevronRight,
  Eye,
  RefreshCw,
  Info,
} from "lucide-react";
import { cn } from "@/lib/utils";

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

const OPERATOR_LABELS: Record<string, string> = {
  is_true: "is TRUE",
  is_false: "is FALSE",
  equal_to: "=",
  not_equal_to: "≠",
  less_than: "<",
  less_than_or_equal_to: "≤",
  greater_than: ">",
  greater_than_or_equal_to: "≥",
  contains: "contains",
  does_not_contain: "does NOT contain",
  starts_with: "starts with",
  ends_with: "ends with",
};

const FIELD_LABELS: Record<string, string> = {
  is_contact_action: "action is a customer contact",
  is_opted_out: "customer has opted out of this channel",
  days_since_last_contact: "days since last contact",
  contacts_sent: "messages already sent for this invoice",
  days_overdue: "days overdue",
  has_open_undue_promise: "customer has an undue open promise to pay",
  action_type: "proposed action type",
  requested_discount_pct: "requested discount %",
  ladder_index: "position in escalation ladder",
};

const ACTION_TYPE_LABELS: Record<string, string> = {
  SEND_REMINDER: "SEND REMINDER",
  ESCALATE: "ESCALATE",
  OFFER_SETTLEMENT: "OFFER SETTLEMENT",
  HAND_OFF: "HAND OFF TO HUMAN",
  CLOSE: "CLOSE CASE",
};

function describeCondition(c: PolicyCondition): string {
  const field = FIELD_LABELS[c.name] ?? c.name;
  const op = OPERATOR_LABELS[c.operator] ?? c.operator;

  if (c.operator === "is_true" || c.operator === "is_false") {
    return field;
  }

  if (c.name === "action_type") {
    const val = ACTION_TYPE_LABELS[String(c.value)] ?? String(c.value);
    return `action type = ${val}`;
  }

  if (typeof c.value === "boolean") {
    return `${field} ${op}`;
  }

  return `${field} ${op} ${c.value}`;
}

function ruleToSentence(rule: PolicyRule): {
  ifClause: string;
  thenActions: { kind: "block" | "clamp"; text: string }[];
} {
  const allConds = rule.conditions.all ?? [];
  const anyConds = rule.conditions.any ?? [];

  const parts: string[] = [];
  if (allConds.length) {
    parts.push(allConds.map(describeCondition).join(" AND "));
  }
  if (anyConds.length) {
    parts.push("(" + anyConds.map(describeCondition).join(" OR ") + ")");
  }

  const ifClause = parts.length ? parts.join(" AND ") : "always";

  const thenActions = rule.actions.map((a) => {
    if (a.name === "block") {
      const message = typeof a.params.message === "string" ? a.params.message : "";
      const code = typeof a.params.code === "string" ? a.params.code : "unknown";
      return {
        kind: "block" as const,
        text: message || `BLOCK (${code})`,
      };
    }
    if (a.name === "clamp_discount") {
      const ceil =
        typeof a.params.ceiling === "number"
          ? a.params.ceiling
          : Number(a.params.ceiling) || 0;
      const reason = typeof a.params.reason === "string" ? a.params.reason : "";
      return {
        kind: "clamp" as const,
        text: `CLAMP discount to ceiling of ${ceil}% — ${reason}`,
      };
    }
    return {
      kind: "block" as const,
      text: `${a.name.toUpperCase()}: ${JSON.stringify(a.params)}`,
    };
  });

  return { ifClause, thenActions };
}

function SkeletonCeiling() {
  return (
    <div className="rounded-2xl p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm animate-pulse">
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1 space-y-3">
          <div className="h-3 w-40 rounded-md bg-zinc-200 dark:bg-white/10" />
          <div className="h-8 w-32 rounded-md bg-zinc-200 dark:bg-white/10" />
        </div>
        <div className="h-10 w-10 rounded-xl bg-zinc-200 dark:bg-white/10" />
      </div>
    </div>
  );
}

function SkeletonTable() {
  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-neutral-900 shadow-sm overflow-hidden">
      <div className="p-5 border-b border-zinc-200/60 dark:border-white/5 animate-pulse space-y-3">
        <div className="h-5 w-48 rounded-md bg-zinc-200 dark:bg-white/10" />
        <div className="h-3 w-72 rounded-md bg-zinc-200 dark:bg-white/10" />
      </div>
      <div className="p-4 space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-24 rounded-xl bg-zinc-100 dark:bg-white/5 animate-pulse" />
        ))}
      </div>
    </div>
  );
}

export default function PolicyPage() {
  const shouldReduce = useReducedMotion();

  const {
    data: policy,
    isLoading,
    isFetching,
    refetch,
  } = useQuery<PolicyResponse>({
    queryKey: ["policy"],
    queryFn: fetchPolicy,
    staleTime: 60_000,
  });

  const cfg = policy?.config;
  const enforcement = policy?.enforcement;
  const ruleCount = policy?.rule_count ?? (policy?.rules?.length ?? 0);

  const renderedRules = useMemo(
    () => (policy?.rules ?? []).map(ruleToSentence),
    [policy]
  );

  const containerVariants = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: shouldReduce ? 0 : 0.08,
      },
    },
  };

  const itemVariants = {
    hidden: { opacity: 0, y: shouldReduce ? 0 : 16 },
    show: {
      opacity: 1,
      y: 0,
      transition: { duration: 0.45, ease: "easeOut" as const },
    },
  };

  return (
    <main className="p-4 sm:p-6 lg:p-8 max-w-[1400px] mx-auto space-y-6 lg:space-y-8 min-h-full">
      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="flex flex-col lg:flex-row lg:items-start justify-between gap-5 pb-6 border-b border-zinc-200/60 dark:border-white/10"
      >
        <div className="space-y-2 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-mono text-emerald-600 dark:text-emerald-400 font-bold tracking-[0.12em] uppercase">
              <Shield className="w-3.5 h-3.5" />
              <span>Policy & Guardrails Console</span>
            </div>
            {!isLoading && (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20 tabular-nums">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                {ruleCount} rules compiled · live from engine
              </span>
            )}
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold tracking-tight text-zinc-900 dark:text-white leading-tight">
              What this agent is actually allowed to do.
            </h1>
            <p className="text-sm sm:text-base text-zinc-500 dark:text-zinc-400 mt-1.5 max-w-2xl leading-relaxed">
              The compiled rule set and ceilings below are served straight from the same{" "}
              <code className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-white/5 text-zinc-700 dark:text-zinc-300 font-mono text-xs">
                PolicyEngine
              </code>{" "}
              the agent gates against — no documentation drift, no copy in a wiki that diverged.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <Scale className="w-4 h-4 text-emerald-500" />
            <span>Command Center</span>
          </Link>

          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white shadow-md hover:shadow-lg shadow-emerald-500/15 transition-all active:scale-[0.97] disabled:opacity-70 disabled:active:scale-100"
          >
            <RefreshCw className={cn("w-4 h-4", isFetching && "animate-spin")} />
            <span>{isFetching ? "Reloading…" : "Refresh Policy"}</span>
          </button>
        </div>
      </motion.div>

      {/* Read-only Banner */}
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: "easeOut", delay: 0.05 }}
        className="rounded-2xl border border-amber-200/60 dark:border-amber-500/20 bg-gradient-to-br from-amber-50/80 to-amber-50/40 dark:from-amber-500/10 dark:to-amber-500/5 p-5 flex flex-col sm:flex-row items-start gap-4"
      >
        <div className="flex-shrink-0 p-3 rounded-xl bg-amber-500/15 border border-amber-500/20">
          <LockKeyhole className="w-5 h-5 text-amber-600 dark:text-amber-400" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm sm:text-base font-bold text-amber-900 dark:text-amber-200 mb-1">
            Read-only by design — not a missing feature, a stated principle.
          </p>
          <p className="text-xs sm:text-sm leading-relaxed text-amber-900/75 dark:text-amber-200/80 max-w-3xl">
            Policy changes are a business decision with an audit requirement. Ceilings and rules
            move through configuration and a deploy — which leaves a deployment record and is
            signed off before it ships. An unauthenticated web form is not where a discount cap
            should change.
          </p>
        </div>
        <div className="hidden lg:flex items-center gap-2 px-3 py-1.5 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-700 dark:text-amber-300 text-[11px] font-mono font-semibold whitespace-nowrap">
          <Eye className="w-3.5 h-3.5" />
          VIEW ONLY · NO MUTATIONS
        </div>
      </motion.div>

      {/* Ceiling Cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCeiling key={i} />
          ))}
        </div>
      ) : (
        cfg && (
          <motion.div
            variants={containerVariants}
            initial="hidden"
            animate="show"
            className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4"
          >
            <motion.div variants={itemVariants}>
              <KpiCard
                title="Discount Ceiling (% of invoice)"
                value={cfg.discount_ceiling_pct}
                suffix="%"
                accentColor="success"
                icon={Percent}
                trend={{
                  value: 0,
                  positive: true,
                  label: "max settlement discount",
                }}
              />
            </motion.div>

            <motion.div variants={itemVariants}>
              <KpiCard
                title="Discount Ceiling (Absolute)"
                value={cfg.max_discount_amount ?? 0}
                formatter={(v) => (cfg.max_discount_amount != null ? formatINR(v) : "None")}
                accentColor="success"
                icon={ShieldCheck}
                trend={{
                  value: 0,
                  positive: true,
                  label: cfg.max_discount_amount != null ? "applied on top of % cap" : "unbounded by rupee cap",
                }}
              />
            </motion.div>

            <motion.div variants={itemVariants}>
              <KpiCard
                title="Contact Frequency Cap"
                value={cfg.min_contact_gap_days}
                suffix=" day gap"
                accentColor="info"
                icon={Clock3}
                trend={{
                  value: 0,
                  positive: true,
                  label: "minimum between messages",
                }}
              />
            </motion.div>

            <motion.div variants={itemVariants}>
              <KpiCard
                title="Max Contacts Per Invoice"
                value={cfg.max_contacts_per_invoice}
                suffix=" messages"
                accentColor="warning"
                icon={Mail}
                trend={{
                  value: 0,
                  positive: true,
                  label: "hard cap per invoice, ever",
                }}
              />
            </motion.div>
          </motion.div>
        )
      )}

      {/* Secondary Ceilings Row */}
      {!isLoading && cfg && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
          className="grid grid-cols-1 sm:grid-cols-3 gap-4"
        >
          <div className="rounded-2xl p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-2">
                  Minimum Days Overdue To Contact
                </p>
                <p className="text-2xl font-bold tracking-tight text-zinc-900 dark:text-white tabular-nums">
                  {cfg.min_days_overdue_to_contact} day
                  {cfg.min_days_overdue_to_contact !== 1 ? "s" : ""}
                </p>
                <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  Grace period — nothing sends until an invoice is at least this far past its due date.
                </p>
              </div>
              <div className="p-2.5 rounded-xl bg-blue-50 dark:bg-blue-500/10 text-blue-600 dark:text-blue-400 flex-shrink-0">
                <AlertTriangle size={22} strokeWidth={2} />
              </div>
            </div>
          </div>

          <div className="rounded-2xl p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-2">
                  Quiet While Promise Open
                </p>
                <p className="text-2xl font-bold tracking-tight text-zinc-900 dark:text-white tabular-nums">
                  {cfg.quiet_while_promise_open ? (
                    <span className="inline-flex items-center gap-2">
                      <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                      Enabled
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-2">
                      <XCircle className="w-5 h-5 text-zinc-500" />
                      Disabled
                    </span>
                  )}
                </p>
                <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  While a customer has a live, undue promise to pay, reminders are silenced — chasing
                  someone who already committed erodes goodwill fast.
                </p>
              </div>
              <div className="p-2.5 rounded-xl bg-emerald-50 dark:bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex-shrink-0">
                <ShieldCheck size={22} strokeWidth={2} />
              </div>
            </div>
          </div>

          <div className="rounded-2xl p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow-md transition-shadow">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0 flex-1">
                <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-2">
                  Opt-Out Honouring Window
                </p>
                <p className="text-2xl font-bold tracking-tight text-zinc-900 dark:text-white tabular-nums">
                  {cfg.optout_days} days
                </p>
                <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  Once a customer asks to stop, that instruction is binding for this many days
                  before the agent may contact them again.
                </p>
              </div>
              <div className="p-2.5 rounded-xl bg-rose-50 dark:bg-rose-500/10 text-rose-600 dark:text-rose-400 flex-shrink-0">
                <Lock size={22} strokeWidth={2} />
              </div>
            </div>
          </div>
        </motion.div>
      )}

      {/* Escalation Ladder */}
      {!isLoading && cfg && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.15 }}
          className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-neutral-900 shadow-sm overflow-hidden"
        >
          <div className="p-5 border-b border-zinc-200/60 dark:border-white/5">
            <div className="flex items-center gap-2 mb-1">
              <Workflow className="w-4 h-4 text-indigo-500" />
              <h2 className="text-lg font-bold text-zinc-900 dark:text-white">
                Escalation Ladder ({cfg.escalation_ladder.length} steps)
              </h2>
            </div>
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              The finite sequence of actions the agent runs through. After the final step, no
              further automated contact is made — the case goes to a person.
            </p>
          </div>
          <div className="p-5">
            <div className="flex flex-wrap items-stretch gap-2 sm:gap-3">
              {cfg.escalation_ladder.map((step, idx) => (
                <React.Fragment key={step}>
                  <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-gradient-to-br from-zinc-50 to-white dark:from-white/5 dark:to-transparent border border-zinc-200 dark:border-white/10 flex-1 min-w-[160px]">
                    <div className="flex-shrink-0 w-9 h-9 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-white flex items-center justify-center font-bold text-sm shadow-sm shadow-indigo-500/20">
                      {idx + 1}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                        Step {idx + 1}
                      </p>
                      <p className="text-sm font-bold text-zinc-900 dark:text-white truncate capitalize tracking-tight">
                        {step.replace(/_/g, " ")}
                      </p>
                    </div>
                  </div>
                  {idx < cfg.escalation_ladder.length - 1 && (
                    <ChevronRight className="text-zinc-400 w-5 h-5 flex-shrink-0 self-center hidden sm:block" />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
        </motion.div>
      )}

      {/* Rule Table */}
      {isLoading ? (
        <SkeletonTable />
      ) : (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.2 }}
          className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-neutral-900 shadow-sm overflow-hidden"
        >
          <div className="p-5 border-b border-zinc-200/60 dark:border-white/5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <Scale className="w-4 h-4 text-amber-500" />
                  <h2 className="text-lg font-bold text-zinc-900 dark:text-white">
                    Compiled Business Rules
                  </h2>
                  <span className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold bg-zinc-100 dark:bg-white/5 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-white/10 font-mono tracking-wide">
                    {renderedRules.length} RULES
                  </span>
                </div>
                <p className="text-sm text-zinc-500 dark:text-zinc-400 max-w-2xl">
                  Readable sentences, generated from the engine structured rule payload — the same
                  data that <code className="px-1 py-0.5 rounded bg-zinc-100 dark:bg-white/5 text-xs font-mono">run_all</code> evaluates.
                </p>
              </div>
            </div>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-zinc-50/70 dark:bg-white/[0.02] text-left text-xs font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 border-b border-zinc-200/60 dark:border-white/5">
                  <th className="px-5 py-3 w-14">#</th>
                  <th className="px-5 py-3 min-w-[280px]">IF</th>
                  <th className="px-5 py-3 min-w-[340px]">THEN</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-200/60 dark:divide-white/5">
                {renderedRules.map((r, idx) => {
                  return (
                    <motion.tr
                      key={idx}
                      initial={{ opacity: 0, x: -6 }}
                      animate={{ opacity: 1, x: 0 }}
                      transition={{ duration: 0.3, delay: 0.25 + idx * 0.04 }}
                      className="hover:bg-zinc-50/60 dark:hover:bg-white/[0.02] transition-colors"
                    >
                      <td className="px-5 py-4 align-top">
                        <div className="w-8 h-8 rounded-lg bg-zinc-100 dark:bg-white/5 border border-zinc-200 dark:border-white/10 flex items-center justify-center text-xs font-bold text-zinc-700 dark:text-zinc-300 tabular-nums">
                          {idx + 1}
                        </div>
                      </td>
                      <td className="px-5 py-4 align-top">
                        <div className="flex items-start gap-2.5">
                          <span className="mt-0.5 inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-200/60 dark:border-blue-500/20 font-mono uppercase shrink-0">
                            IF
                          </span>
                          <code className="text-[13px] leading-relaxed text-zinc-800 dark:text-zinc-200 font-mono bg-zinc-50 dark:bg-white/5 border border-zinc-200/60 dark:border-white/10 rounded-md px-3 py-2 flex-1 whitespace-pre-wrap break-words">
                            {r.ifClause}
                          </code>
                        </div>
                      </td>
                      <td className="px-5 py-4 align-top">
                        <div className="space-y-2">
                          {r.thenActions.map((action, ai) => (
                            <div key={ai} className="flex items-start gap-2.5">
                              <span
                                className={cn(
                                  "mt-0.5 inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-bold tracking-wider border font-mono uppercase shrink-0",
                                  action.kind === "block"
                                    ? "bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-200/60 dark:border-rose-500/20"
                                    : "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-200/60 dark:border-amber-500/20"
                                )}
                              >
                                {action.kind === "block" ? (
                                  <span className="inline-flex items-center gap-1">
                                    <XCircle className="w-3 h-3" /> BLOCK
                                  </span>
                                ) : (
                                  <span className="inline-flex items-center gap-1">
                                    <ArrowRight className="w-3 h-3" /> CLAMP
                                  </span>
                                )}
                              </span>
                              <span
                                className={cn(
                                  "text-[13px] leading-relaxed rounded-md px-3 py-2 border flex-1 whitespace-pre-wrap break-words",
                                  action.kind === "block"
                                    ? "text-rose-900/90 dark:text-rose-100/90 bg-rose-50/50 dark:bg-rose-500/5 border-rose-200/50 dark:border-rose-500/15"
                                    : "text-amber-900/90 dark:text-amber-100/90 bg-amber-50/50 dark:bg-amber-500/5 border-amber-200/50 dark:border-amber-500/15"
                                )}
                              >
                                {action.text}
                              </span>
                            </div>
                          ))}
                        </div>
                      </td>
                    </motion.tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </motion.div>
      )}

      {/* Enforcement Diagram */}
      {!isLoading && enforcement && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.45, delay: 0.3 }}
          className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-gradient-to-br from-zinc-50 via-white to-emerald-50/40 dark:from-neutral-900 dark:via-neutral-900 dark:to-emerald-500/[0.04] shadow-sm overflow-hidden"
        >
          <div className="p-5 border-b border-zinc-200/60 dark:border-white/5">
            <div className="flex items-center gap-2 mb-1">
              <Workflow className="w-4 h-4 text-emerald-500" />
              <h2 className="text-lg font-bold text-zinc-900 dark:text-white">
                Enforcement Architecture
              </h2>
            </div>
            <p className="text-sm text-zinc-500 dark:text-zinc-400 max-w-3xl leading-relaxed">
              {enforcement.note}
            </p>
          </div>

          <div className="p-5 sm:p-8">
            <div className="relative max-w-5xl mx-auto">
              <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr_auto_1fr] items-center gap-4 lg:gap-6">
                {/* Agent / Scorer proposes */}
                <div className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 p-5 shadow-sm">
                  <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold tracking-wider uppercase bg-orange-50 dark:bg-orange-500/10 text-orange-700 dark:text-orange-400 border border-orange-200/60 dark:border-orange-500/20 mb-3">
                    <ZapIcon /> SOURCE
                  </div>
                  <h3 className="font-bold text-zinc-900 dark:text-white mb-1.5">
                    Agent / Scorer / FSM
                  </h3>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                    Proposes actions: send reminder, escalate, offer settlement discount, hand off.
                    <br />
                    <span className="font-semibold text-zinc-600 dark:text-zinc-300">It cannot send anything on its own.</span>
                  </p>
                  <div className="mt-4 space-y-1.5">
                    {["Proposed action", "Requested discount %", "Ladder step"].map((t) => (
                      <div key={t} className="flex items-center gap-2">
                        <ArrowRight className="w-3 h-3 text-orange-400 shrink-0" />
                        <span className="text-xs text-zinc-600 dark:text-zinc-300">{t}</span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Arrow 1 */}
                <div className="flex lg:justify-center items-center lg:h-full">
                  <div className="flex lg:flex-col items-center gap-1.5">
                    <div className="h-px w-12 lg:h-12 lg:w-px bg-gradient-to-r from-orange-300 via-zinc-300 to-emerald-300 dark:from-orange-500/40 dark:via-white/20 dark:to-emerald-500/40 lg:bg-gradient-to-b" />
                    <ArrowRight className="w-4 h-4 text-zinc-400 lg:rotate-90" />
                  </div>
                </div>

                {/* Central PolicyEngine Box */}
                <div className="relative rounded-2xl bg-gradient-to-br from-emerald-500 via-teal-500 to-emerald-600 dark:from-emerald-600 dark:via-teal-600 dark:to-emerald-700 p-[1.5px] shadow-lg shadow-emerald-500/20">
                  <div className="rounded-[calc(1rem-1px)] bg-white dark:bg-neutral-900 p-5">
                    <div className="flex items-center justify-between gap-3 mb-3">
                      <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold tracking-wider uppercase bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20">
                        <ShieldCheck className="w-3 h-3" /> SINGLE GATE
                      </div>
                      <ShieldAlert className="w-5 h-5 text-emerald-500" />
                    </div>
                    <h3 className="font-bold text-zinc-900 dark:text-white mb-1.5 text-base">
                      PolicyEngine
                    </h3>
                    <p className="text-[11px] font-mono text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200/60 dark:border-emerald-500/20 rounded-md px-2 py-1.5 mb-3 truncate">
                      .{enforcement.gate.split(".").slice(-2).join(".")}
                    </p>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed mb-4">
                      Evaluates every proposal against the {ruleCount} compiled rules above.
                      There is no bypass.
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                      <div className="rounded-lg border border-rose-200/60 dark:border-rose-500/20 bg-rose-50/50 dark:bg-rose-500/5 p-2.5">
                        <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-rose-600 dark:text-rose-400 mb-1">
                          <XCircle className="w-3 h-3" /> Block
                        </div>
                        <p className="text-[11px] text-rose-800/80 dark:text-rose-200/80 leading-snug">
                          Action does not happen. Audit-trace reason recorded.
                        </p>
                      </div>
                      <div className="rounded-lg border border-amber-200/60 dark:border-amber-500/20 bg-amber-50/50 dark:bg-amber-500/5 p-2.5">
                        <div className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wider text-amber-700 dark:text-amber-400 mb-1">
                          <ArrowRight className="w-3 h-3" /> Clamp
                        </div>
                        <p className="text-[11px] text-amber-900/80 dark:text-amber-100/80 leading-snug">
                          Discount lowered to ceiling; action otherwise proceeds.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Arrow 2 */}
                <div className="flex lg:justify-center items-center lg:h-full">
                  <div className="flex lg:flex-col items-center gap-1.5">
                    <div className="h-px w-12 lg:h-12 lg:w-px bg-gradient-to-r from-emerald-300 via-zinc-300 to-blue-300 dark:from-emerald-500/40 dark:via-white/20 dark:to-blue-500/40 lg:bg-gradient-to-b" />
                    <ArrowRight className="w-4 h-4 text-zinc-400 lg:rotate-90" />
                  </div>
                </div>

                {/* Output side */}
                <div className="space-y-3">
                  <div className="rounded-2xl bg-white dark:bg-neutral-900 border border-emerald-200/60 dark:border-emerald-500/20 p-4 shadow-sm">
                    <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider uppercase bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20 mb-2">
                      <CheckCircle2 className="w-3 h-3" /> ALLOWED PATH
                    </div>
                    <h4 className="font-bold text-zinc-900 dark:text-white text-sm mb-1">Executor + Delivery</h4>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                      Rendered, signed, dispatched. Only actions that passed the gate reach this
                      box.
                    </p>
                  </div>

                  <div className="rounded-2xl bg-white dark:bg-neutral-900 border border-indigo-200/60 dark:border-indigo-500/20 p-4 shadow-sm">
                    <div className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider uppercase bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border border-indigo-200/60 dark:border-indigo-500/20 mb-2">
                      <Scale className="w-3 h-3" /> SAME ENGINE · GUARDS
                    </div>
                    <h4 className="font-bold text-zinc-900 dark:text-white text-sm mb-1">
                      Escalation FSM Guards
                    </h4>
                    <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed mb-2">
                      The state machine calls the identical{" "}
                      <span className="font-mono text-[10px] bg-zinc-100 dark:bg-white/5 px-1.5 py-0.5 rounded border border-zinc-200 dark:border-white/10">
                        PolicyEngine
                      </span>{" "}
                      for its own transition guards, so it cannot move a case into a state whose
                      action the gate would then refuse.
                    </p>
                    <div className="flex flex-wrap gap-1.5">
                      {enforcement.escalation_guards.map((g) => (
                        <span
                          key={g}
                          className="inline-flex items-center px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border border-indigo-200/50 dark:border-indigo-500/20"
                        >
                          {g}()
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-6 pt-5 border-t border-dashed border-zinc-200 dark:border-white/10 flex items-start gap-3">
                <Info className="w-4 h-4 text-zinc-400 mt-0.5 shrink-0" />
                <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed max-w-3xl">
                  Every evaluation (blocked, clamped, or approved) writes one Decision Trace
                  entry, so a refused action leaves exactly as much auditable evidence as a sent
                  one.
                </p>
              </div>
            </div>
          </div>
        </motion.div>
      )}
    </main>
  );
}

function ZapIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="w-3 h-3">
      <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" />
    </svg>
  );
}
