"use client";

import React, { useState } from "react";
import Link from "next/link";
import {
  Gauge,
  Activity,
  ShieldAlert,
  Clock,
  ShieldCheck,
  Send,
  ChevronDown,
  ChevronUp,
  FlaskConical,
  ExternalLink,
} from "lucide-react";
import type { MLWorkflowStep } from "@/lib/api";
import { cn } from "@/lib/utils";

type Tone = "good" | "bad" | "warn" | "neutral";

function toneFor(status: string): Tone {
  const s = status.toLowerCase();
  if (["completed", "normal", "low_risk", "optimized", "allowed", "delivered"].includes(s)) {
    return "good";
  }
  if (["high_risk", "drift_flagged", "blocked"].includes(s)) return "bad";
  if (["moderate_risk"].includes(s)) return "warn";
  return "neutral";
}

const TONE_CLASSES: Record<Tone, { dot: string; border: string; chip: string; text: string }> = {
  good: {
    dot: "bg-emerald-500",
    border: "border-emerald-500/40",
    chip: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/25",
    text: "text-emerald-700 dark:text-emerald-400",
  },
  bad: {
    dot: "bg-red-500",
    border: "border-red-500/40",
    chip: "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/25",
    text: "text-red-700 dark:text-red-400",
  },
  warn: {
    dot: "bg-amber-500",
    border: "border-amber-500/40",
    chip: "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/25",
    text: "text-amber-700 dark:text-amber-400",
  },
  neutral: {
    dot: "bg-zinc-400",
    border: "border-zinc-200 dark:border-white/10",
    chip: "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border-zinc-500/20",
    text: "text-zinc-500 dark:text-zinc-400",
  },
};

function StepIcon({ id }: { id: string }) {
  const cls = "h-4 w-4";
  if (id === "recovery_scorer") return <Gauge className={cls} />;
  if (id === "drift_detector") return <Activity className={cls} />;
  if (id === "broken_promise_scorer") return <ShieldAlert className={cls} />;
  if (id === "contact_timing") return <Clock className={cls} />;
  if (id === "policy_gate") return <ShieldCheck className={cls} />;
  return <Send className={cls} />;
}

/** Where each model proves its numbers. Anchors land on the /models hub. */
export const MODEL_VALIDATION_LINKS: Record<string, { label: string; href: string }> = {
  recovery_scorer: { label: "Recovery validation", href: "/models#recovery" },
  drift_detector: { label: "Drift validation", href: "/models#drift" },
  broken_promise_scorer: { label: "Promise-risk validation", href: "/models#broken-promise" },
  contact_timing: { label: "Timing validation", href: "/models#timing" },
  policy_gate: { label: "Policy rulebook", href: "/policy" },
  autonomous_execution: { label: "Run control", href: "/runs" },
};

function humanizeFeature(name: string): string {
  return name.replace(/^customer_/, "").replace(/_/g, " ");
}

function formatNum(value: unknown, digits = 4): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "—");
  return value.toFixed(digits).replace(/\.?0+$/, "");
}

function formatScore(step: MLWorkflowStep): string | null {
  if (step.score == null || !step.score_label) return null;
  const label = step.score_label.toLowerCase();
  if (/(recovery|response|risk|probability|rate)/.test(label)) {
    return `${(step.score * 100).toFixed(1)}%`;
  }
  if (label.includes("anomaly")) return step.score.toFixed(2);
  if (label.includes("verdict") || label.includes("delivery")) {
    return step.score >= 0.5 ? "yes" : "no";
  }
  return String(step.score);
}

function DriverBars({ drivers }: { drivers: Array<Record<string, any>> }) {
  const rows = drivers.filter((d) => d && typeof d.feature === "string").slice(0, 3);
  if (rows.length === 0) return null;
  const maxAbs = Math.max(
    1e-9,
    ...rows.map((d) =>
      Math.abs(
        typeof d.shap_contribution === "number"
          ? d.shap_contribution
          : typeof d.deviation === "number"
            ? d.deviation
            : 0
      )
    )
  );
  return (
    <div className="space-y-1.5">
      {rows.map((d, i) => {
        const raw =
          typeof d.shap_contribution === "number"
            ? d.shap_contribution
            : typeof d.deviation === "number"
              ? d.deviation
              : 0;
        const width = `${Math.min(100, (Math.abs(raw) / maxAbs) * 100)}%`;
        return (
          <div key={`${d.feature}-${i}`} className="font-mono text-[11px]">
            <div className="flex justify-between gap-2 mb-0.5">
              <span className="truncate text-zinc-500 dark:text-zinc-400">
                {humanizeFeature(d.feature)}
                {d.value !== undefined && (
                  <span className="opacity-70"> · {formatNum(d.value, 3)}</span>
                )}
              </span>
              <span
                className={cn(
                  "font-bold tabular-nums",
                  raw >= 0
                    ? "text-emerald-600 dark:text-emerald-400"
                    : "text-red-500 dark:text-red-400"
                )}
              >
                {raw > 0 ? "+" : ""}
                {formatNum(raw, 3)}
              </span>
            </div>
            <div className="h-1 rounded-full bg-zinc-100 dark:bg-white/5 overflow-hidden">
              <div
                className={cn(
                  "h-full rounded-full",
                  raw >= 0 ? "bg-emerald-500" : "bg-red-500"
                )}
                style={{ width }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function StepDetails({ step }: { step: MLWorkflowStep }) {
  const d = step.details ?? {};
  const rec = typeof d.recommendation === "string" ? d.recommendation : null;
  const rationale = typeof d.rationale === "string" && d.rationale ? d.rationale : null;

  return (
    <div className="space-y-3 pt-1">
      {(step.id === "recovery_scorer" || step.id === "drift_detector") &&
        Array.isArray(d.top_drivers ?? d.drivers) && (
          <DriverBars drivers={(d.top_drivers ?? d.drivers) as Array<Record<string, any>>} />
        )}

      {rationale && <p className="text-xs leading-relaxed">{rationale}</p>}
      {rec && (
        <p className="text-xs leading-relaxed text-zinc-500 dark:text-zinc-400">
          <span className="font-semibold text-zinc-700 dark:text-zinc-300">Why it matters: </span>
          {rec}
        </p>
      )}

      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 font-mono text-[11px]">
        {d.model_version != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">model</dt>
            <dd className="truncate font-semibold">{String(d.model_version)}</dd>
          </div>
        )}
        {d.fallback_used === true && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">scorer</dt>
            <dd className="font-semibold text-amber-600 dark:text-amber-400">
              rules fallback (no trained model)
            </dd>
          </div>
        )}
        {d.promise_status != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">promise</dt>
            <dd className="font-semibold">{String(d.promise_status)}</dd>
          </div>
        )}
        {d.arm != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">send window</dt>
            <dd className="font-semibold">{String(d.arm)}</dd>
          </div>
        )}
        {d.segment != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">segment</dt>
            <dd className="truncate font-semibold">{String(d.segment)}</dd>
          </div>
        )}
        {d.scheduled_for != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">scheduled</dt>
            <dd className="truncate font-semibold">{String(d.scheduled_for)}</dd>
          </div>
        )}
        {d.backed_off_to_global === true && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">coverage</dt>
            <dd className="font-semibold">global prior (thin segment)</dd>
          </div>
        )}
        {d.reason != null && d.reason !== "" && (
          <div className="flex justify-between gap-2 sm:col-span-2">
            <dt className="text-zinc-400">reason</dt>
            <dd className="text-right font-semibold">{String(d.reason)}</dd>
          </div>
        )}
        {Array.isArray(d.violations) && d.violations.length > 0 && (
          <div className="sm:col-span-2 space-y-1">
            <dt className="text-zinc-400">violations</dt>
            {d.violations.map((v: any, i: number) => (
              <dd
                key={i}
                className="px-2 py-1 rounded-md bg-red-500/10 border border-red-500/20 text-red-700 dark:text-red-300 font-semibold"
              >
                [{v.code}] {v.message}
              </dd>
            ))}
          </div>
        )}
        {d.effective_discount_pct != null && Number(d.effective_discount_pct) > 0 && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">waiver</dt>
            <dd className="font-semibold">{d.effective_discount_pct}% (policy-capped)</dd>
          </div>
        )}
        {d.channel != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">channel</dt>
            <dd className="font-semibold">{String(d.channel)}</dd>
          </div>
        )}
        {d.dry_run === true && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">delivery</dt>
            <dd className="font-semibold text-amber-600 dark:text-amber-400">SIMULATED (dry run)</dd>
          </div>
        )}
        {d.payment_link_id != null && (
          <div className="flex justify-between gap-2">
            <dt className="text-zinc-400">pay link</dt>
            <dd className="truncate font-semibold">{String(d.payment_link_id)}</dd>
          </div>
        )}
      </dl>

      {MODEL_VALIDATION_LINKS[step.id] && (
        <Link
          href={MODEL_VALIDATION_LINKS[step.id].href}
          className="inline-flex items-center gap-1 text-[11px] font-semibold text-orange-600 dark:text-orange-400 hover:underline"
        >
          <FlaskConical className="h-3 w-3" />
          {MODEL_VALIDATION_LINKS[step.id].label}
          <ExternalLink className="h-3 w-3 opacity-60" />
        </Link>
      )}
    </div>
  );
}

export function MLWorkflowTrace({
  steps,
  variant = "full",
}: {
  steps: MLWorkflowStep[] | null | undefined;
  variant?: "full" | "compact";
}) {
  const [expanded, setExpanded] = useState<string | null>(
    variant === "full" ? "recovery_scorer" : null
  );

  if (!steps || steps.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-zinc-300 dark:border-white/10 bg-zinc-50/60 dark:bg-white/[0.02] px-4 py-6 text-center">
        <p className="text-xs font-semibold text-zinc-500 dark:text-zinc-400">
          No ML workflow trace on this response
        </p>
        <p className="text-[11px] text-zinc-400 dark:text-zinc-500 mt-1">
          Re-run the cycle (invoice page) or trigger a fresh batch run — the trace is attached to
          every new run.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[11px] font-mono uppercase tracking-widest text-zinc-500">
          ML validation trace · {steps.length} models & gates
        </span>
      </div>
      <ol className="relative space-y-3 before:absolute before:left-[17px] before:top-2 before:bottom-2 before:w-px before:bg-zinc-200 dark:before:bg-white/10">
        {steps.map((step) => {
          const tone = toneFor(step.status);
          const t = TONE_CLASSES[tone];
          const isOpen = expanded === step.id;
          const scoreText = formatScore(step);
          return (
            <li key={step.id} className="relative pl-10">
              <span
                className={cn(
                  "absolute left-[11px] top-4 h-[13px] w-[13px] rounded-full border-[3px] border-white dark:border-black",
                  t.dot
                )}
              />
              <div
                className={cn(
                  "rounded-xl border bg-white dark:bg-zinc-950 overflow-hidden",
                  isOpen ? t.border : "border-zinc-200 dark:border-white/10"
                )}
              >
                <button
                  onClick={() => setExpanded(isOpen ? null : step.id)}
                  className="w-full text-left px-4 py-3 flex items-start gap-3"
                >
                  <span className={cn("p-1.5 rounded-lg shrink-0", t.chip, "border")}>
                    <StepIcon id={step.id} />
                  </span>
                  <span className="flex-1 min-w-0">
                    <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-[13px] font-bold">{step.name}</span>
                      <span className="text-[10px] font-mono text-zinc-400">{step.model_type}</span>
                    </span>
                    <span className="block text-xs mt-0.5 text-zinc-600 dark:text-zinc-300">
                      {step.verdict}
                    </span>
                  </span>
                  <span className="flex flex-col items-end gap-1 shrink-0">
                    {scoreText && (
                      <span
                        className={cn(
                          "text-[11px] font-mono font-bold px-2 py-0.5 rounded-full border tabular-nums",
                          t.chip
                        )}
                        title={step.score_label ?? undefined}
                      >
                        {scoreText}
                      </span>
                    )}
                    {isOpen ? (
                      <ChevronUp className="h-3.5 w-3.5 text-zinc-400" />
                    ) : (
                      <ChevronDown className="h-3.5 w-3.5 text-zinc-400" />
                    )}
                  </span>
                </button>
                {isOpen && (
                  <div className="px-4 pb-4 pt-1 border-t border-zinc-100 dark:border-white/5 text-zinc-600 dark:text-zinc-300">
                    <div className="pt-3">
                      <StepDetails step={step} />
                    </div>
                  </div>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
