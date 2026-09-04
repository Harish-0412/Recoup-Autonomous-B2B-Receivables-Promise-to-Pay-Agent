"use client";

import React, { useMemo, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  Play,
  RefreshCw,
  RotateCcw,
  FlaskConical,
  AlertTriangle,
  ArrowRight,
  ArrowUpRight,
  ArrowDownRight,
  Minus,
  Braces,
} from "lucide-react";
import {
  fetchPolicyDefaults,
  simulatePolicy,
  SimulationNotAvailableError,
  PRODUCTION_POLICY_DEFAULTS,
  type PolicyDefaults,
  type PolicySimulateResponse,
  type AffectedCase,
} from "@/lib/api";
import { useQuery } from "@tanstack/react-query";
import { cn } from "@/lib/utils";

type Overrides = {
  discount_ceiling_pct: number;
  min_contact_gap_days: number;
  max_contacts_per_invoice: number;
  min_days_overdue_to_contact: number;
  self_cure_probability: number;
};

const BASELINE = {
  recovery_rate: 0.635,
  recovered_value: 39700000,
  false_interventions: 263,
  compliance_violations: 0,
  cases_replayed: 600,
};

const CASE_POOL = [
  "INV-1042", "INV-1043", "INV-1038", "INV-1051", "INV-1066",
  "INV-1079", "INV-1084", "INV-1091", "INV-1103", "INV-1117",
  "INV-1120", "INV-1135",
];

function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return Math.abs(h);
}

function formatINRCompact(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

/** Deterministic illustrative preview: direction-aware, never claimed as engine output. */
function previewSimulation(overrides: Overrides, prod: PolicyDefaults): PolicySimulateResponse {
  const dCeil = overrides.discount_ceiling_pct - prod.discount_ceiling_pct;
  const dGap = prod.min_contact_gap_days - overrides.min_contact_gap_days; // lower gap = more contact
  const dMax = overrides.max_contacts_per_invoice - prod.max_contacts_per_invoice;
  const dOverdue = prod.min_days_overdue_to_contact - overrides.min_days_overdue_to_contact;
  const dCure = overrides.self_cure_probability - prod.self_cure_probability; // higher = fewer left alone

  const pressure = dCeil * 0.4 + dGap * 1.2 + dMax * 0.8 + dOverdue * 0.6 + dCure * 22;
  const recoveredDelta = Math.round(pressure * 42000);
  const falseDelta = Math.round(pressure * 3.1);
  const rateDelta = Math.round((pressure * 0.00042) * 10000) / 10000;

  const simulated = {
    recovery_rate: Math.max(0, Math.min(1, BASELINE.recovery_rate + rateDelta)),
    recovered_value: Math.max(0, BASELINE.recovered_value + recoveredDelta),
    false_interventions: Math.max(0, BASELINE.false_interventions + falseDelta),
    compliance_violations: 0,
  };

  const seed = hashStr(JSON.stringify(overrides));
  const nAffected = Math.min(CASE_POOL.length, 2 + (Math.abs(Math.round(pressure)) % 7));
  const cases: AffectedCase[] = CASE_POOL.slice(0, nAffected).map((id, i) => {
    const up = (seed >> i) % 2 === 0 ? pressure >= 0 : pressure < 0;
    const tiers: Record<string, string> = { WAIT: "REMIND", REMIND: "ESCALATE", ESCALATE: "ESCALATE" };
    const down: Record<string, string> = { ESCALATE: "REMIND", REMIND: "WAIT", WAIT: "WAIT" };
    const base = ["WAIT", "REMIND", "ESCALATE"][(seed + i) % 3];
    const sim = up ? tiers[base] : down[base];
    return {
      invoice_id: id,
      baseline_tier: base,
      simulated_tier: sim,
      reason_changed:
        sim === base
          ? "At ladder cap — pressure absorbed by contact caps, tier unchanged in rank but earlier in queue."
          : up
            ? "Higher contact pressure clears the gate where production policy blocked."
            : "Stricter gate holds this case back where production policy acted.",
    };
  });

  return {
    baseline: { ...BASELINE },
    simulated,
    delta: {
      recovery_rate: Math.round((simulated.recovery_rate - BASELINE.recovery_rate) * 10000) / 10000,
      recovered_value: simulated.recovered_value - BASELINE.recovered_value,
      false_interventions: simulated.false_interventions - BASELINE.false_interventions,
      compliance_violations: 0,
    },
    cases_affected: cases,
    cases_replayed: BASELINE.cases_replayed,
    engine_version: "preview-illustrative",
  };
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  prod,
  format,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  prod: number;
  format: (v: number) => string;
  onChange: (v: number) => void;
}) {
  const changed = value !== prod;
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold">{label}</span>
        <span className="font-mono">
          <strong className={changed ? "text-orange-600 dark:text-orange-400" : ""}>{format(value)}</strong>
          <span className="text-zinc-400"> · prod {format(prod)}</span>
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-orange-500"
      />
    </div>
  );
}

function DeltaBadge({ value, goodWhenUp, format }: { value: number; goodWhenUp: boolean; format: (v: number) => string }) {
  const good = value === 0 ? null : (value > 0) === goodWhenUp;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-mono font-bold",
        good === null && "bg-zinc-100 dark:bg-zinc-900 text-zinc-500",
        good === true && "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
        good === false && "bg-red-500/10 text-red-700 dark:text-red-400"
      )}
    >
      {value === 0 ? <Minus className="h-3 w-3" /> : value > 0 ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
      {value > 0 ? "+" : ""}{format(value)}
    </span>
  );
}

export default function SimulatePage() {
  const { data: policyData } = useQuery({
    queryKey: ["policy-defaults"],
    queryFn: fetchPolicyDefaults,
    staleTime: 60_000,
  });
  const prod: PolicyDefaults = policyData?.defaults ?? PRODUCTION_POLICY_DEFAULTS;

  const [overrides, setOverrides] = useState<Overrides>({
    discount_ceiling_pct: 15,
    min_contact_gap_days: 3,
    max_contacts_per_invoice: 4,
    min_days_overdue_to_contact: 1,
    self_cure_probability: 0.95,
  });
  const [from, setFrom] = useState("2026-06-01");
  const [to, setTo] = useState("2026-09-01");
  const [previewMode, setPreviewMode] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<PolicySimulateResponse | null>(null);
  const [usedPreview, setUsedPreview] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [caseFilter, setCaseFilter] = useState<"all" | "up" | "down" | "same">("all");
  const [showContract, setShowContract] = useState(false);

  const set = (key: keyof Overrides) => (v: number) =>
    setOverrides((o) => ({ ...o, [key]: v }));

  const handleReplay = async () => {
    setRunning(true);
    setError(null);
    try {
      const out = await simulatePolicy({
        policy_overrides: { ...overrides },
        replay_window: { from, to },
      });
      setResult(out);
      setUsedPreview(false);
    } catch (e) {
      if ((e instanceof SimulationNotAvailableError || /unreachable|501/.test((e as Error).message)) && previewMode) {
        setResult(previewSimulation(overrides, prod));
        setUsedPreview(true);
      } else {
        setError(e instanceof Error ? e.message : "Replay failed");
      }
    } finally {
      setRunning(false);
    }
  };

  const filteredCases = useMemo(() => {
    if (!result) return [];
    const rank: Record<string, number> = { WAIT: 0, REMIND: 1, ESCALATE: 2 };
    return result.cases_affected.filter((c) => {
      if (caseFilter === "all") return true;
      const d = (rank[c.simulated_tier] ?? 0) - (rank[c.baseline_tier] ?? 0);
      if (caseFilter === "up") return d > 0;
      if (caseFilter === "down") return d < 0;
      return d === 0;
    });
  }, [result, caseFilter]);

  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <Link href="/dashboard" className="inline-flex items-center gap-1.5 text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-white">
            <ArrowLeft className="h-3.5 w-3.5" /> dashboard
          </Link>
          <label className="inline-flex items-center gap-2 text-xs font-mono cursor-pointer">
            <span className={previewMode ? "text-orange-600 dark:text-orange-400 font-bold" : "text-zinc-400"}>
              Preview — connect backend
            </span>
            <button
              role="switch"
              aria-checked={previewMode}
              onClick={() => setPreviewMode((v) => !v)}
              className={cn("w-10 h-5.5 h-[22px] rounded-full p-0.5 transition-colors", previewMode ? "bg-orange-500" : "bg-zinc-300 dark:bg-zinc-700")}
            >
              <span className={cn("block h-4 w-4 rounded-full bg-white transition-transform", previewMode && "translate-x-[18px]")} />
            </button>
          </label>
        </div>

        <section className="space-y-2">
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400 flex items-center gap-1.5">
            <FlaskConical className="h-3.5 w-3.5" /> Policy Simulation Studio
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">What if we moved the ceilings?</h1>
          <p className="text-sm text-zinc-500 max-w-3xl">
            Counterfactual replay over last quarter&apos;s decision traces with swapped policy ceilings.
            The replay engine is not built yet — this page runs in labelled preview mode against the
            real contract (<span className="font-mono text-xs">POST /policy/simulate</span>, currently 501),
            so backend and frontend can ship in parallel without faking results.
          </p>
        </section>

        <AnimatePresence>
          {usedPreview && result && (
            <motion.div
              initial={{ opacity: 0, y: -6 }}
              animate={{ opacity: 1, y: 0 }}
              className="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 text-xs flex gap-2"
            >
              <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
              <span>
                <strong>Preview numbers — illustrative, not engine output.</strong> Direction-aware mock
                computed from your overrides so the layout, deltas, and table are reviewable today.
                Flip the toggle off and hit Replay to call the real endpoint (501 until the engine ships).
              </span>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          {/* Override panel */}
          <section className="lg:col-span-4 rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6 space-y-5">
            <h2 className="text-sm font-bold uppercase tracking-widest">Policy overrides</h2>
            <Slider label="Discount ceiling" value={overrides.discount_ceiling_pct} min={0} max={20} step={0.5} prod={prod.discount_ceiling_pct} format={(v) => `${v}%`} onChange={set("discount_ceiling_pct")} />
            <Slider label="Min contact gap" value={overrides.min_contact_gap_days} min={0} max={14} step={1} prod={prod.min_contact_gap_days} format={(v) => `${v}d`} onChange={set("min_contact_gap_days")} />
            <Slider label="Max contacts / invoice" value={overrides.max_contacts_per_invoice} min={0} max={8} step={1} prod={prod.max_contacts_per_invoice} format={(v) => `${v}`} onChange={set("max_contacts_per_invoice")} />
            <Slider label="Min days overdue" value={overrides.min_days_overdue_to_contact} min={0} max={14} step={1} prod={prod.min_days_overdue_to_contact} format={(v) => `${v}d`} onChange={set("min_days_overdue_to_contact")} />
            <Slider label="Self-cure threshold" value={overrides.self_cure_probability} min={0.6} max={0.99} step={0.01} prod={prod.self_cure_probability} format={(v) => v.toFixed(2)} onChange={set("self_cure_probability")} />
            <div className="grid grid-cols-2 gap-2 pt-1">
              <label className="text-xs space-y-1">
                <span className="font-mono text-zinc-500">from</span>
                <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className="w-full rounded-lg border border-zinc-200 dark:border-white/10 bg-transparent px-2 py-1.5 text-xs font-mono" />
              </label>
              <label className="text-xs space-y-1">
                <span className="font-mono text-zinc-500">to</span>
                <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className="w-full rounded-lg border border-zinc-200 dark:border-white/10 bg-transparent px-2 py-1.5 text-xs font-mono" />
              </label>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleReplay}
                disabled={running}
                className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-r from-orange-500 to-amber-500 text-black disabled:opacity-60"
              >
                {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
                {running ? "Replaying…" : "Replay"}
              </button>
              <button
                onClick={() => setOverrides({ discount_ceiling_pct: prod.discount_ceiling_pct, min_contact_gap_days: prod.min_contact_gap_days, max_contacts_per_invoice: prod.max_contacts_per_invoice, min_days_overdue_to_contact: prod.min_days_overdue_to_contact, self_cure_probability: prod.self_cure_probability })}
                title="Reset to production values"
                className="px-3 py-2.5 rounded-xl border border-zinc-200 dark:border-white/10"
              >
                <RotateCcw className="h-4 w-4" />
              </button>
            </div>
            {error && <p className="text-xs font-mono text-red-500">{error}</p>}
            <button onClick={() => setShowContract((v) => !v)} className="inline-flex items-center gap-1.5 text-[11px] font-mono text-orange-600 dark:text-orange-400">
              <Braces className="h-3.5 w-3.5" /> {showContract ? "hide" : "show"} the contract
            </button>
            {showContract && (
              <pre className="rounded-lg bg-zinc-950 text-emerald-300 p-3 text-[10px] font-mono overflow-x-auto leading-relaxed">
{`POST /policy/simulate
{ "policy_overrides": { "discount_ceiling_pct": 15, ... },
  "replay_window": { "from": "${from}", "to": "${to}" } }
→ { baseline: {...}, simulated: {...},
    delta: {...}, cases_affected: [...] }
501 until the replay engine ships.`}
              </pre>
            )}
          </section>

          {/* Before/after */}
          <section className="lg:col-span-8 space-y-4">
            {!result ? (
              <div className="rounded-2xl border border-dashed border-zinc-300 dark:border-white/15 p-12 text-center text-sm text-zinc-500">
                Set overrides and hit <strong>Replay</strong> — baseline vs simulated appears here with per-metric deltas.
              </div>
            ) : (
              <>
                <div className="grid grid-cols-3 gap-3">
                  <div className="rounded-2xl border border-zinc-200 dark:border-white/10 p-4">
                    <p className="text-[10px] font-mono uppercase tracking-widest text-zinc-500 mb-2">Baseline · production policy</p>
                    <MetricRows m={result.baseline} compact={false} />
                  </div>
                  <div className="rounded-2xl border border-orange-500/30 bg-orange-500/5 p-4">
                    <p className="text-[10px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400 mb-2">Δ simulated − baseline</p>
                    <div className="space-y-2.5">
                      <DeltaBadge value={result.delta.recovered_value} goodWhenUp format={(v) => formatINRCompact(v)} />
                      <DeltaBadge value={result.delta.recovery_rate * 100} goodWhenUp format={(v) => `${v.toFixed(2)}pp`} />
                      <DeltaBadge value={result.delta.false_interventions} goodWhenUp={false} format={(v) => `${v}`} />
                      <DeltaBadge value={result.delta.compliance_violations} goodWhenUp={false} format={(v) => `${v}`} />
                    </div>
                    <p className="text-[10px] font-mono text-zinc-400 mt-3">green = helps · red = hurts, per metric</p>
                  </div>
                  <div className="rounded-2xl border border-zinc-200 dark:border-white/10 p-4">
                    <p className="text-[10px] font-mono uppercase tracking-widest text-zinc-500 mb-2">Simulated · your overrides</p>
                    <MetricRows m={result.simulated} compact={false} />
                  </div>
                </div>
                <p className="text-[11px] font-mono text-zinc-400">
                  {result.cases_replayed} cases replayed · engine: {result.engine_version}
                  {usedPreview ? " (preview)" : ""}
                </p>

                {/* Affected cases */}
                <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6">
                  <div className="flex flex-wrap items-center justify-between gap-2 mb-3">
                    <h2 className="text-sm font-bold uppercase tracking-widest">
                      Affected cases · {filteredCases.length}/{result.cases_affected.length}
                    </h2>
                    <div className="flex gap-1.5 text-[11px] font-mono">
                      {(["all", "up", "down", "same"] as const).map((f) => (
                        <button
                          key={f}
                          onClick={() => setCaseFilter(f)}
                          className={cn("px-2.5 py-1 rounded-lg border", caseFilter === f ? "bg-orange-500 text-black border-orange-500 font-bold" : "border-zinc-200 dark:border-white/10 text-zinc-500")}
                        >
                          {f === "all" ? "all" : f === "up" ? "escalated ↑" : f === "down" ? "held back ↓" : "re-ranked ="}
                        </button>
                      ))}
                    </div>
                  </div>
                  {filteredCases.length === 0 ? (
                    <p className="text-sm text-zinc-500">No cases in this bucket — the caps absorbed the change.</p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm">
                        <thead>
                          <tr className="text-[10px] uppercase tracking-widest text-zinc-500 border-b border-zinc-200 dark:border-white/10">
                            <th className="text-left py-2 pr-3">Invoice</th>
                            <th className="text-left py-2 px-2">Baseline</th>
                            <th className="text-left py-2 px-2">Simulated</th>
                            <th className="text-left py-2 pl-2">Why it changed</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filteredCases.map((c) => (
                            <tr key={c.invoice_id} className="border-b border-zinc-100 dark:border-white/5">
                              <td className="py-2.5 pr-3 font-mono font-bold text-[13px]">
                                <Link href={`/invoices/${c.invoice_id}`} className="text-indigo-600 dark:text-indigo-400 hover:underline">
                                  {c.invoice_id}
                                </Link>
                              </td>
                              <td className="py-2.5 px-2 font-mono text-[11px]">{c.baseline_tier}</td>
                              <td className="py-2.5 px-2 font-mono text-[11px] font-bold">{c.simulated_tier}</td>
                              <td className="py-2.5 pl-2 text-xs text-zinc-500">{c.reason_changed}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}

function MetricRows({ m, compact }: { m: { recovery_rate: number; recovered_value: number; false_interventions: number; compliance_violations: number }; compact: boolean }) {
  void compact;
  return (
    <div className="space-y-2.5 text-sm">
      <div>
        <p className="text-[10px] font-mono text-zinc-400 uppercase">Recovered</p>
        <p className="font-extrabold">{formatINRCompact(m.recovered_value)}</p>
      </div>
      <div>
        <p className="text-[10px] font-mono text-zinc-400 uppercase">Recovery rate</p>
        <p className="font-extrabold">{(m.recovery_rate * 100).toFixed(1)}%</p>
      </div>
      <div>
        <p className="text-[10px] font-mono text-zinc-400 uppercase">False interventions</p>
        <p className="font-extrabold">{m.false_interventions}</p>
      </div>
      <div>
        <p className="text-[10px] font-mono text-zinc-400 uppercase">Violations</p>
        <p className={cn("font-extrabold", m.compliance_violations === 0 ? "text-emerald-600 dark:text-emerald-400" : "text-red-500")}>
          {m.compliance_violations}
        </p>
      </div>
    </div>
  );
}
