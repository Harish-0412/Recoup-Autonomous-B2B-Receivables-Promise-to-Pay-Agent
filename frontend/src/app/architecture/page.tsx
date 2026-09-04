"use client";

import React from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  Gauge,
  ShieldCheck,
  Send,
  History,
  Lock,
  FileSpreadsheet,
  FlaskConical,
  MessagesSquare,
  Scale,
  ArrowRight,
  Ban,
  KeyRound,
} from "lucide-react";
import { DecisionCycleVisualizer } from "@/components/case/DecisionCycleVisualizer";
import type { RunCycleResponse } from "@/lib/api";

const SAMPLE_CYCLE: RunCycleResponse = {
  invoice_id: "INV-1042",
  tier: "REMIND",
  p_recovery: 0.684,
  expected_value: 34200,
  outstanding: 50000,
  rationale: "Overdue 9 days; moderate recovery probability with cleared frequency cap.",
  top_drivers: [
    { feature: "customer_broken_promise_rate", value: 0.0, shap_contribution: 0.284 },
    { feature: "days_overdue_at_scoring", value: 9.0, shap_contribution: -0.224 },
  ],
  action_type: "SEND_REMINDER",
  ladder_step: "reminder_1",
  decision: {
    allowed: true,
    reason: "Frequency cap cleared; Tier 1 reminder approved within ceilings.",
    violations: [],
    adjustments: [],
    effective_discount_pct: 0,
    effective_discount_amount: 0,
  },
  transitioned: true,
  state_before: "monitoring",
  state_after: "reminded",
  reason: "Tier 1 reminder dispatched.",
  terminal: false,
  scorer_fallback: true,
  scorer_version: "rules-based-v1",
  execution: {
    status: "sent",
    delivered: true,
    dry_run: true,
    subject: "Invoice INV-1042 Payment Reminder",
    body_preview: "Single-click Razorpay link inside.",
    payment_link_id: "plink_TXVSdg7K0skWhG",
    payment_link_url: "https://rzp.io/rzp/skBiePkr",
    payment_link_reused: false,
    amount_requested: 50000,
  },
};

function Reveal({ children, delay = 0 }: { children: React.ReactNode; delay?: number }) {
  return (
    <motion.section
      initial={{ opacity: 0, y: 24 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-80px" }}
      transition={{ duration: 0.55, delay, ease: "easeOut" }}
    >
      {children}
    </motion.section>
  );
}

function SectionHead({ kicker, title, body }: { kicker: string; title: string; body: string }) {
  return (
    <div className="max-w-3xl space-y-2 mb-8">
      <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400">{kicker}</p>
      <h2 className="text-2xl sm:text-3xl font-bold tracking-tight">{title}</h2>
      <p className="text-sm text-zinc-500 leading-relaxed">{body}</p>
    </div>
  );
}

const RAILS = [
  {
    icon: <ShieldCheck className="h-5 w-5" />,
    title: "The gate, not the LLM, decides money",
    body: "Discount ceilings, contact caps, and opt-outs are enforced by a deterministic policy engine. The LLM drafts words — it never approves amounts, and execution only accepts an approved PolicyDecision, never a raw proposal.",
  },
  {
    icon: <Ban className="h-5 w-5" />,
    title: "DRY_RUN on by default, kill switch always armed",
    body: "Messages render and log instead of delivering until DRY_RUN is turned off — and a separate kill switch stops every outbound message with no redeploy. Turning it back on resumes where the agent left off.",
  },
  {
    icon: <KeyRound className="h-5 w-5" />,
    title: "No double-sends, ever",
    body: "Batch runs take a Postgres advisory lock — a second concurrent trigger is a no-op, not a queued rerun. Webhooks dedupe on event id, so a retried payment.captured can never double-count revenue.",
  },
  {
    icon: <History className="h-5 w-5" />,
    title: "A promise is intent, never payment",
    body: "“I’ll pay Friday” buys quiet until Friday — it moves no money. Only a signature-verified Razorpay webhook writes PAID. Broken promises escalate one rung per the ladder, never a retry loop.",
  },
  {
    icon: <Lock className="h-5 w-5" />,
    title: "Verification over assumption, everywhere",
    body: "Paid comes from webhooks. Opt-outs bind at any classifier confidence via a deterministic guard. Low-confidence replies route to a human rather than being guessed at.",
  },
  {
    icon: <FileSpreadsheet className="h-5 w-5" />,
    title: "Every decision leaves a hash-chained trace",
    body: "Each ledger entry hashes its content plus the previous hash — editing history breaks every hash after it. Compliance is counted from the trace, not from the orchestrator's own return values.",
  },
];

export default function ArchitecturePage() {
  return (
    <main className="min-h-screen bg-white dark:bg-black text-zinc-900 dark:text-white">
      <div className="max-w-[1100px] mx-auto px-4 sm:px-6 py-6 space-y-20">
        <Link href="/dashboard" className="inline-flex items-center gap-1.5 text-xs font-mono text-zinc-500 hover:text-zinc-900 dark:hover:text-white">
          <ArrowLeft className="h-3.5 w-3.5" /> dashboard
        </Link>

        {/* Hero */}
        <motion.header
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="space-y-3 pt-4"
        >
          <p className="text-[11px] font-mono uppercase tracking-widest text-orange-600 dark:text-orange-400">
            How it works · for judges, not users
          </p>
          <h1 className="text-3xl sm:text-5xl font-bold tracking-tight max-w-3xl leading-tight">
            An agent that chases invoices, knows when to stop, and proves what it recovered.
          </h1>
          <p className="text-sm sm:text-base text-zinc-500 max-w-2xl leading-relaxed">
            ₹8.1 lakh crore sits stuck in delayed payments to Indian MSMEs. Recoup takes the
            judgment call — who to chase, how hard, when to stop — off a founder&apos;s plate,
            without taking away their control over it.
          </p>
        </motion.header>

        {/* 1. The loop */}
        <Reveal>
          <div>
            <SectionHead
              kicker="01 · The agentic loop"
              title="Score → propose → GATE → transition → execute"
              body="The order is the safety property, not a style choice. Execution receives an approved PolicyDecision, never a raw proposal — skipping the gate would require fabricating an approval object."
            />
            <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-zinc-950 p-6">
              <DecisionCycleVisualizer result={SAMPLE_CYCLE} running={false} variant="static" />
            </div>
          </div>
        </Reveal>

        {/* 2. Not another reminder bot */}
        <Reveal>
          <div>
            <SectionHead
              kicker="02 · Why this is not a reminder bot"
              title="Generic dunning scripts nag. Recoup prioritizes, gates, and stops."
              body="Same inbox, opposite philosophy: every row below is a behavior a judge can verify in the ledger, not a marketing claim."
            />
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
              <div className="rounded-2xl border border-red-500/20 bg-red-500/5 p-6 space-y-3">
                <p className="font-bold">A generic dunning script</p>
                {[
                  "Same email to every overdue customer",
                  "Reminds until someone tells it to stop",
                  "Any discount an LLM feels like offering",
                  "“Reminder sent” is the whole log",
                  "Success = “we sent emails”",
                  "No memory of what was promised",
                ].map((t) => (
                  <p key={t} className="text-zinc-500 flex gap-2"><span className="text-red-500 font-bold">✕</span>{t}</p>
                ))}
              </div>
              <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 p-6 space-y-3">
                <p className="font-bold">Recoup</p>
                {[
                  "Ranks by expected recovery value; low-risk invoices are deliberately left alone",
                  "Hard escalation ladder with an automatic stop — never nags indefinitely",
                  "Policy-enforced discount ceiling the LLM cannot exceed",
                  "Every action carries a reason; the full trail is queryable",
                  "Success = batch-level ₹ recovered, measured not claimed",
                  "Promises recorded, watched against deadlines, driving the next action",
                ].map((t) => (
                  <p key={t} className="flex gap-2"><span className="text-emerald-500 font-bold">✓</span>{t}</p>
                ))}
              </div>
            </div>
          </div>
        </Reveal>

        {/* 3. Vs Razorpay Agent Studio */}
        <Reveal>
          <div>
            <SectionHead
              kicker="03 · The gap next to Razorpay, not a copy of it"
              title="Subscription Recovery chases failed consumer billing. Recoup works B2B trade credit."
              body="A smaller copy of a product Razorpay already sells demonstrates nothing new. An adjacent gap does."
            />
            <div className="overflow-x-auto rounded-2xl border border-zinc-200 dark:border-white/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-widest text-zinc-500 border-b border-zinc-200 dark:border-white/10">
                    <th className="text-left p-4"></th>
                    <th className="text-left p-4">Subscription Recovery (Razorpay)</th>
                    <th className="text-left p-4">Recoup</th>
                  </tr>
                </thead>
                <tbody className="[&_tr]:border-b [&_tr]:border-zinc-100 dark:[&_tr]:border-white/5 [&_tr:last-child]:border-0">
                  {[
                    ["Domain", "Consumer subscriptions, recurring billing", "B2B invoices, net-30/60 trade credit"],
                    ["Core mechanism", "Retry logic + reminder nudges", "Prioritize → policy-bounded negotiate → track promise → verify → escalate/stop"],
                    ["Decision basis", "Payment failure reason", "Expected-value ranking across the book"],
                    ["What's remembered", "Payment success / fail", "Explicit promises and whether they were honored"],
                    ["Evidence produced", "—", "Recovery rate, ₹ recovered, false interventions, compliance"],
                  ].map(([a, b, c]) => (
                    <tr key={a}>
                      <td className="p-4 font-mono text-xs text-zinc-500 whitespace-nowrap">{a}</td>
                      <td className="p-4 text-zinc-500">{b}</td>
                      <td className="p-4 font-medium">{c}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </Reveal>

        {/* 4. Safety rails */}
        <Reveal>
          <div>
            <SectionHead
              kicker="04 · Safety rails"
              title="The part that makes this brand-safe to actually run."
              body="Each rail below maps to code and a test — propose→dispose separation, DRY_RUN, kill switch, advisory locks, promise semantics, and the hash-chained audit."
            />
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {RAILS.map((r, i) => (
                <motion.div
                  key={r.title}
                  initial={{ opacity: 0, y: 16 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true, margin: "-40px" }}
                  transition={{ duration: 0.45, delay: (i % 3) * 0.08 }}
                  className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-5 space-y-2"
                >
                  <span className="inline-block p-2 rounded-lg bg-orange-500/10 text-orange-600 dark:text-orange-400">{r.icon}</span>
                  <p className="font-bold text-sm">{r.title}</p>
                  <p className="text-xs text-zinc-500 leading-relaxed">{r.body}</p>
                </motion.div>
              ))}
            </div>
          </div>
        </Reveal>

        {/* 5. Studios */}
        <Reveal>
          <div>
            <SectionHead
              kicker="05 · See the evidence, don’t take our word"
              title="Three studios, one honesty standard."
              body="Each studio renders backend-served numbers with its source labelled — committed card, fresh artifact, or labelled preview. Nothing is faked to look live."
            />
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {[
                { icon: <Gauge className="h-5 w-5" />, href: "/models/recovery", title: "Recovery Model Studio", body: "AUC 0.779 vs 0.732, calibration curve, SHAP drivers — and the +₹24.6L head-to-head number." },
                { icon: <MessagesSquare className="h-5 w-5" />, href: "/models/reply", title: "Reply Understanding Studio", body: "Cascade diagram plus a try-it box: type a reply, see the intent, confidence, and stage." },
                { icon: <Scale className="h-5 w-5" />, href: "/simulate", title: "Policy Simulation Studio", body: "What-if ceilings replayed over last quarter — in labelled preview until the engine ships." },
              ].map((s) => (
                <Link key={s.href} href={s.href} className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-zinc-950 p-6 space-y-2 hover:border-orange-500/40 transition-colors group">
                  <span className="inline-block p-2 rounded-lg bg-orange-500/10 text-orange-600 dark:text-orange-400">{s.icon}</span>
                  <p className="font-bold flex items-center gap-1.5">{s.title}<ArrowRight className="h-4 w-4 group-hover:translate-x-0.5 transition-transform" /></p>
                  <p className="text-xs text-zinc-500 leading-relaxed">{s.body}</p>
                </Link>
              ))}
            </div>
            <div className="mt-6 rounded-2xl border border-zinc-200 dark:border-white/10 p-5 flex gap-3 text-xs">
              <FlaskConical className="h-5 w-5 text-orange-500 shrink-0" />
              <p className="text-zinc-500 leading-relaxed">
                Honest metrics, stated on every surface: the scorer card says rules-based where it is; the batch
                report counts false interventions as a cost; recovery rate measures targeting, not causation, until a
                holdout arm exists. <Link href="/dashboard" className="text-orange-600 dark:text-orange-400 font-semibold">Verify on the dashboard →</Link>
              </p>
            </div>
          </div>
        </Reveal>
      </div>
    </main>
  );
}
