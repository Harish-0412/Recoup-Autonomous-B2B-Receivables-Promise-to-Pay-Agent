"use client";

import React, { useRef, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  Zap,
  Brain,
  ShieldCheck,
  Send,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import { classifyReplyPreview, type ClassifyPreviewOut } from "@/lib/api";
import { cn } from "@/lib/utils";

type Stage = "guard" | "cascade" | "llm";

const STAGE_META: Record<Stage, { label: string; desc: string; icon: React.ReactNode }> = {
  guard: {
    label: "Opt-out guard",
    desc: "Deterministic regex — binding at any confidence, never consults a model",
    icon: <ShieldCheck className="h-5 w-5" />,
  },
  cascade: {
    label: "Stage C · fast classifier",
    desc: "TF-IDF + calibrated linear SVM — milliseconds, n-gram explanations",
    icon: <Zap className="h-5 w-5" />,
  },
  llm: {
    label: "Stage A · LLM fallback",
    desc: "Instructor-bound LLM — only the ambiguous replies Stage C escalates",
    icon: <Brain className="h-5 w-5" />,
  },
};

const TAXONOMY: Array<{ intent: string; example: string; happens: string }> = [
  { intent: "PROMISE_TO_PAY", example: "“We'll pay by Friday, just had a cashflow issue.”", happens: "Promise recorded → chasing pauses till Friday" },
  { intent: "DISPUTE", example: "“The GST amount on this invoice looks wrong.”", happens: "Needs ≥ 0.85 confidence, else LLM → human; escalation freezes" },
  { intent: "OPT_OUT", example: "“Stop sending me these reminders.”", happens: "Guard fires — contact suppressed, binding at any confidence" },
  { intent: "NEGOTIATION_REQUEST", example: "“Can we settle at 90% if we pay today?”", happens: "Below bar → human; policy ceiling still caps any offer" },
  { intent: "PARTIAL_PAYMENT_CLAIM", example: "“We already paid half of this last week.”", happens: "Weak recall (0.58) — chased amount adjusted only on review" },
  { intent: "ALREADY_PAID_CLAIM", example: "“Paid on the 12th, UTR 482910.”", happens: "Checked against webhook truth — never taken on word alone" },
  { intent: "GENERAL_QUERY", example: "“Is GST 12% or 18% on this HSN code?”", happens: "Answered, no collection action — dispute vocabulary, not a dispute" },
  { intent: "OTHER", example: "“Thanks, noted.”", happens: "Residual class — routed to human rather than guessed" },
];

const EXAMPLE_CHIPS = [
  "We'll pay by Friday, just had a cashflow issue",
  "Stop sending me these reminders",
  "The GST amount on this invoice looks wrong",
  "Paid on the 12th, UTR 482910",
];

const RATE_LIMIT_MS = 2000;

function intentTone(intent: string): string {
  switch (intent) {
    case "PROMISE_TO_PAY":
      return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20";
    case "DISPUTE":
      return "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20";
    case "OPT_OUT":
      return "bg-zinc-800 text-white dark:bg-white dark:text-black border-transparent";
    case "NEGOTIATION_REQUEST":
      return "bg-blue-500/10 text-blue-700 dark:text-blue-400 border-blue-500/20";
    default:
      return "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20";
  }
}

export default function ReplyStudioPage() {
  const [text, setText] = useState(EXAMPLE_CHIPS[0]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<ClassifyPreviewOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const lastSent = useRef(0);
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const doSubmit = async (value: string) => {
    const now = Date.now();
    if (now - lastSent.current < RATE_LIMIT_MS || loading) return;
    const trimmed = value.trim();
    if (!trimmed) return;
    lastSent.current = now;
    setLoading(true);
    setError(null);
    setBackendDown(false);
    try {
      const out = await classifyReplyPreview(trimmed);
      setResult(out);
    } catch (e) {
      setBackendDown(true);
      setError(e instanceof Error ? e.message : "Preview failed");
    } finally {
      setLoading(false);
    }
  };

  const onChange = (value: string) => {
    setText(value);
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    // Debounced auto-preview while typing stays client-side cheap; the actual
    // POST only fires after a pause AND respects the rate limit above.
    debounceTimer.current = setTimeout(() => {
      if (value.trim().length > 12) void doSubmit(value);
    }, 1200);
  };

  const lastStage: Stage | null =
    result && (result.stage_used === "guard" || result.stage_used === "cascade" || result.stage_used === "llm")
      ? result.stage_used
      : null;

  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[1200px] mx-auto px-4 sm:px-6 py-6 space-y-6">
        <div className="flex items-center justify-between">
          <Link
            href="/models/recovery"
            className="inline-flex items-center gap-1.5 text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-white"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> recovery studio
          </Link>
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-white/10"
          >
            dashboard <ArrowRight className="h-3 w-3" />
          </Link>
        </div>

        <section className="space-y-2">
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400">
            Model Studio · Reply Understanding
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
            Cheap answers fast, expensive answers rarely.
          </h1>
          <p className="text-sm text-zinc-500 max-w-3xl">
            Stage C resolves the clear cases locally in milliseconds; only the ambiguous ones pay
            LLM latency. Try it yourself below — the preview is explicitly non-mutating: no
            signature check, no DB write, no promise created.{" "}
            <span className="font-mono text-xs">POST /replies/classify-preview</span>
          </p>
        </section>

        {/* Cascade diagram with live stage indicator */}
        <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-zinc-950 p-6">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {(Object.keys(STAGE_META) as Stage[]).map((stage, idx) => {
              const active = lastStage === stage;
              return (
                <div key={stage} className="relative">
                  <motion.div
                    animate={active ? { scale: [1, 1.03, 1] } : { scale: 1 }}
                    transition={{ duration: 0.5 }}
                    className={cn(
                      "rounded-2xl border p-4 space-y-1.5 bg-white dark:bg-black",
                      active
                        ? "border-orange-500/60 shadow-lg shadow-orange-500/10"
                        : "border-zinc-200 dark:border-white/10"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span
                        className={cn(
                          "p-1.5 rounded-lg",
                          active
                            ? "bg-orange-500/15 text-orange-600 dark:text-orange-400"
                            : "bg-zinc-100 dark:bg-zinc-900 text-zinc-400"
                        )}
                      >
                        {STAGE_META[stage].icon}
                      </span>
                      {active ? (
                        <span className="inline-flex items-center gap-1 text-[10px] font-mono font-bold text-orange-600 dark:text-orange-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-orange-500 animate-pulse" /> handled last request
                        </span>
                      ) : (
                        <span className="text-[10px] font-mono text-zinc-400">stage {idx + 1}</span>
                      )}
                    </div>
                    <p className="text-sm font-bold">{STAGE_META[stage].label}</p>
                    <p className="text-[11px] text-zinc-500 leading-snug">{STAGE_META[stage].desc}</p>
                  </motion.div>
                  {idx < 2 && (
                    <ArrowRight className="hidden md:block absolute top-1/2 -right-4 h-4 w-4 text-zinc-400 -translate-y-1/2 z-10" />
                  )}
                </div>
              );
            })}
          </div>
          <p className="text-[11px] font-mono text-zinc-400 mt-3">
            routing: guard → Stage C (≥ 0.60, ≥ 0.85 for DISPUTE) → Stage A → human review · cascade resolves ~36% locally at 0.917 accuracy on what it keeps
          </p>
        </section>

        {/* Try-it-yourself box */}
        <section className="rounded-2xl border border-orange-500/25 bg-gradient-to-b from-orange-500/5 to-transparent p-6 space-y-4">
          <h2 className="text-sm font-bold uppercase tracking-widest">Try it yourself</h2>
          <div className="flex flex-wrap gap-2">
            {EXAMPLE_CHIPS.map((chip) => (
              <button
                key={chip}
                onClick={() => {
                  setText(chip);
                  void doSubmit(chip);
                }}
                className="text-[11px] font-mono px-2.5 py-1.5 rounded-lg border border-zinc-200 dark:border-white/10 hover:border-orange-500/40 text-zinc-600 dark:text-zinc-300"
              >
                {chip.length > 42 ? `${chip.slice(0, 42)}…` : chip}
              </button>
            ))}
          </div>
          <textarea
            value={text}
            onChange={(e) => onChange(e.target.value)}
            rows={3}
            maxLength={2000}
            placeholder="We'll pay by Friday, just had a cashflow issue"
            className="w-full rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-4 text-sm leading-relaxed focus:outline-none focus:border-orange-500/60"
          />
          <div className="flex items-center justify-between">
            <p className="text-[11px] font-mono text-zinc-400">
              debounced 1.2s · rate-limited to 1 request / 2s client-side · nothing is written
            </p>
            <button
              onClick={() => void doSubmit(text)}
              disabled={loading || !text.trim()}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-r from-orange-500 to-amber-500 text-black disabled:opacity-60"
            >
              {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {loading ? "Classifying…" : "Classify"}
            </button>
          </div>

          <AnimatePresence>
            {backendDown && (
              <motion.div
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs flex gap-2"
              >
                <AlertTriangle className="h-4 w-4 text-amber-500 shrink-0 mt-0.5" />
                <span>
                  Backend unreachable at <span className="font-mono">POST /replies/classify-preview</span> — start
                  the API to try live classification. The taxonomy below still documents every intent.{" "}
                  <span className="font-mono text-zinc-500">{error}</span>
                </span>
              </motion.div>
            )}
            {result && !backendDown && (
              <motion.div
                key={`${result.intent}-${result.confidence}`}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className="rounded-xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-5 space-y-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className={cn("px-3 py-1 rounded-full text-xs font-mono font-bold border", intentTone(result.intent))}>
                    {result.intent}
                  </span>
                  <span className="text-[11px] font-mono px-2 py-1 rounded bg-zinc-100 dark:bg-zinc-900 text-zinc-500">
                    via {result.stage_used} · {result.classifier_version}
                  </span>
                  {result.needs_review && (
                    <span className="text-[11px] font-mono px-2 py-1 rounded bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-500/20">
                      needs human review
                    </span>
                  )}
                  {result.fallback_used && !result.needs_review && (
                    <span className="text-[11px] font-mono px-2 py-1 rounded bg-zinc-100 dark:bg-zinc-900 text-zinc-500">
                      fallback path
                    </span>
                  )}
                </div>
                <div>
                  <div className="flex justify-between text-[11px] font-mono text-zinc-500 mb-1">
                    <span>confidence</span>
                    <span className="font-bold text-zinc-800 dark:text-zinc-100">{(result.confidence * 100).toFixed(1)}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-zinc-100 dark:bg-zinc-900 overflow-hidden">
                    <motion.div
                      initial={{ width: 0 }}
                      animate={{ width: `${Math.round(result.confidence * 100)}%` }}
                      transition={{ duration: 0.5 }}
                      className={cn("h-full rounded-full", result.confidence >= 0.85 ? "bg-emerald-500" : result.confidence >= 0.6 ? "bg-amber-500" : "bg-zinc-400")}
                    />
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {result.entities.promised_amount != null && (
                    <span className="text-[11px] font-mono px-2 py-1 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
                      ₹{Number(result.entities.promised_amount).toLocaleString("en-IN")}
                    </span>
                  )}
                  {result.entities.promised_date && (
                    <span className="text-[11px] font-mono px-2 py-1 rounded bg-emerald-500/10 text-emerald-700 dark:text-emerald-400">
                      by {result.entities.promised_date}
                    </span>
                  )}
                  {result.entities.dispute_reason && (
                    <span className="text-[11px] font-mono px-2 py-1 rounded bg-red-500/10 text-red-700 dark:text-red-400">
                      dispute: {result.entities.dispute_reason}
                    </span>
                  )}
                  {result.entities.promised_amount == null && !result.entities.promised_date && !result.entities.dispute_reason && (
                    <span className="text-[11px] font-mono text-zinc-400">no entities — intent gate keeps dates in small talk from becoming promises</span>
                  )}
                </div>
                {result.explanation && (
                  <p className="text-xs text-zinc-500 leading-relaxed border-t border-zinc-100 dark:border-white/5 pt-2">
                    {result.explanation}
                  </p>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </section>

        {/* Taxonomy reference */}
        <section className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6">
          <h2 className="text-sm font-bold uppercase tracking-widest mb-1">Intent taxonomy — all 8 labels</h2>
          <p className="text-[11px] font-mono text-zinc-500 mb-4">what each label means, and what the system does with it</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {TAXONOMY.map((t) => (
              <div key={t.intent} className="rounded-xl border border-zinc-200 dark:border-white/10 p-4 space-y-1.5 hover:border-orange-500/30 transition-colors">
                <span className={cn("inline-block px-2.5 py-0.5 rounded-full text-[11px] font-mono font-bold border", intentTone(t.intent))}>
                  {t.intent}
                </span>
                <p className="text-xs text-zinc-500 italic">{t.example}</p>
                <p className="text-xs flex gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500 shrink-0 mt-0.5" />
                  {t.happens}
                </p>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}
