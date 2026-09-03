"use client";

import React, { useState, useEffect } from "react";
import { useTheme } from "next-themes";
import { Preloader } from "@/components/ui/preloader";
import { FluidMorphBg } from "@/components/ui/fluid-morph-bg";
import { AnimatedFooter } from "@/components/ui/animated-footer";
import { ExpandableBentoGrid, BentoItem } from "@/components/ui/expandable-bento-grid";
import { GlassDock, DockItem } from "@/components/ui/glass-dock";
import { motion, AnimatePresence } from "framer-motion";
import {
  ArrowRight,
  ShieldCheck,
  Zap,
  CheckCircle2,
  TrendingUp,
  Lock,
  Layers,
  FileSpreadsheet,
  AlertTriangle,
  RotateCcw,
  Sparkles,
  ExternalLink,
  ChevronRight,
  Receipt,
  Scale,
  Activity,
  CreditCard,
  History,
  Workflow,
  BarChart3,
  Server,
  Users2,
  UserCheck,
  Sun,
  Moon
} from "lucide-react";
import { BackendStatusBadge } from "@/components/live/backend-status-badge";
import { DecisionCycleRunner } from "@/components/live/decision-cycle-runner";
import { HumanReviewDesk } from "@/components/live/human-review-desk";

export default function Home() {
  const [showPreloader, setShowPreloader] = useState(true);
  const [activeTab, setActiveTab] = useState<"cfo" | "founder" | "ar">("cfo");
  const [mounted, setMounted] = useState(false);
  const { resolvedTheme, setTheme } = useTheme();

  useEffect(() => {
    setMounted(true);
  }, []);

  const isDark = mounted ? resolvedTheme === "dark" : true;

  // Single Floating Dock placed at the bottom of the screen
  const dockItems: DockItem[] = [
    { title: "Home", href: "#hero" },
    { title: "Services", icon: Layers, href: "#services" },
    { title: "Simulator", icon: Workflow, href: "#simulator" },
    { title: "Review Desk", icon: UserCheck, href: "#review" },
    { title: "Benefits", icon: Users2, href: "#benefits" },
    { title: "Proof", icon: BarChart3, href: "#metrics" },
    {
      title: mounted && isDark ? "Light Mode" : "Dark Mode",
      icon: mounted && isDark ? Sun : Moon,
      onClick: () => setTheme(isDark ? "light" : "dark"),
    },
    { title: "GitHub", href: "https://github.com/Harish-0412/Recoup-Autonomous-B2B-Receivables-Promise-to-Pay-Agent" },
  ];

  const bentoServices: BentoItem[] = [
    {
      id: 1,
      title: "1. EV Prioritization Engine",
      subtitle: "Calculates P(recovery) × amount × urgency decay to eliminate false interventions.",
      description: "Statistical scoring of open receivables",
      actionLabel: "Simulate Invoices",
      actionHref: "#simulator",
      icon: <TrendingUp className="h-6 w-6" />,
      content: (
        <div className="space-y-3">
          <p>
            Traditional dunning treats a ₹10,00,000 enterprise invoice 2 days overdue the same as a ₹50,000 invoice 45 days past due. Recoup deploys an intelligent Expected Value (EV) scoring model:
          </p>
          <div className="p-3 rounded-xl bg-orange-500/10 border border-orange-500/20 font-mono text-xs text-orange-600 dark:text-orange-400">
            Expected Value = P(recovery) × Invoice Amount × Urgency Decay Factor
          </div>
          <ul className="list-disc list-inside space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <li><strong>P(recovery):</strong> Evaluates debtor payment histories, behavioral clustering, and overdue durations.</li>
            <li><strong>Goodwill Protection:</strong> Low-urgency, dependable accounts (e.g. Net-35 habitual payers) are deliberately left alone to prevent unnecessary churn.</li>
            <li><strong>Targeting Efficiency:</strong> Focuses automated collection firepower where capital is genuinely at risk of slipping into default.</li>
          </ul>
        </div>
      ),
    },
    {
      id: 2,
      title: "2. Policy Gatekeeper Engine",
      subtitle: "Strict business rules, discount ceilings & contact caps no LLM can override.",
      description: "Deterministic business safety gate",
      actionLabel: "Check Policy Rules",
      actionHref: "#simulator",
      icon: <ShieldCheck className="h-6 w-6" />,
      content: (
        <div className="space-y-3">
          <p>
            Recoup separates reasoning from execution. While an LLM can draft contextual messaging, every action MUST pass through a deterministic, hard-coded policy gate:
          </p>
          <div className="grid grid-cols-2 gap-2 text-xs font-mono">
            <div className="p-2.5 rounded-lg bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
              <span className="text-zinc-500 block text-[10px]">DISCOUNT CEILING</span>
              <span className="font-bold text-orange-600 dark:text-orange-400">Max ₹20,000 / 10%</span>
            </div>
            <div className="p-2.5 rounded-lg bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
              <span className="text-zinc-500 block text-[10px]">FREQUENCY CAP</span>
              <span className="font-bold text-orange-600 dark:text-orange-400">Min 3-5 Days Gap</span>
            </div>
          </div>
          <ul className="list-disc list-inside space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <li><strong>Zero Rogue Concessions:</strong> The AI cannot offer unauthorized waivers to close an invoice faster.</li>
            <li><strong>Terminal Escalation:</strong> Outreach follows a finite ladder and automatically terminates with human handoff.</li>
            <li><strong>Opt-Out Registry:</strong> Instant suppression when a debtor requests direct management contact.</li>
          </ul>
        </div>
      ),
    },
    {
      id: 3,
      title: "3. Razorpay Payment Links",
      subtitle: "Acts in the real world with test/live mode links, dynamic expiry & Resend email.",
      description: "Automated payment infrastructure",
      actionLabel: "Test Payment Link",
      actionHref: "#simulator",
      icon: <CreditCard className="h-6 w-6" />,
      content: (
        <div className="space-y-3">
          <p>
            Rather than sending vague requests for bank transfers, Recoup creates actionable, single-click payment instruments directly via the Razorpay API:
          </p>
          <ul className="list-disc list-inside space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <li><strong>Dynamic Amount Calculation:</strong> Auto-computes settlement figures reflecting approved late-fee waivers.</li>
            <li><strong>Synchronized Expiration:</strong> Link expiration matches the negotiated deadline to create authentic urgency.</li>
            <li><strong>Transactional Multi-Channel:</strong> Dispatched via Resend email with customized invoice metadata, with architecture ready for WhatsApp Business API.</li>
            <li><strong>Zero Financial Risk:</strong> Operates seamlessly in Razorpay test mode during pilot evaluations.</li>
          </ul>
        </div>
      ),
    },
    {
      id: 4,
      title: "4. Promise-to-Pay Watchdog",
      subtitle: "Extracts commitments ('Will pay Friday'), halts chasing, and watches deadlines.",
      description: "Semantic intent & deadline memory",
      actionLabel: "Trace P2P Flow",
      actionHref: "#simulator",
      icon: <History className="h-6 w-6" />,
      content: (
        <div className="space-y-3">
          <p>
            When enterprise clients reply "We will process this by Friday post board approval", dumb bots keep nagging. Recoup treats commitments as stateful contractual promises:
          </p>
          <ul className="list-disc list-inside space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <li><strong>Semantic Reply Understanding:</strong> Instructor-backed LLM parses payment intentions and exact target dates.</li>
            <li><strong>Automatic Hold Status:</strong> Outreach is frozen during the commitment window to maintain professional rapport.</li>
            <li><strong>Breach Detection:</strong> If Friday passes without settlement, the agent advances the case to Tier 2 escalation without manual monitoring.</li>
          </ul>
        </div>
      ),
    },
    {
      id: 5,
      title: "5. Webhook Settlement",
      subtitle: "Never trusts claims; relies solely on signature-verified Razorpay webhooks.",
      description: "Undisputed truth verification",
      actionLabel: "Check Verification",
      actionHref: "#simulator",
      icon: <Lock className="h-6 w-6" />,
      content: (
        <div className="space-y-3">
          <p>
            The agent never accepts verbal assurances or receipt screenshots as confirmation of debt satisfaction:
          </p>
          <ul className="list-disc list-inside space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <li><strong>Signature Verification:</strong> Every incoming payload is validated against your Razorpay Webhook Secret using HMAC-SHA256.</li>
            <li><strong>Idempotency Guarantees:</strong> Handlers prevent duplicate processing and state anomalies.</li>
            <li><strong>Event-Driven Lifecycle:</strong> Listening to <code>payment.captured</code> and <code>payment_link.expired</code> triggers case resolution and unblocks ledger accounting.</li>
          </ul>
        </div>
      ),
    },
    {
      id: 6,
      title: "6. Audit Decision Trace",
      subtitle: "Every score, LLM prompt & policy check cryptographically linked in SHA-256 trace.",
      description: "Cryptographic explainability ledger",
      actionLabel: "View Batch Proof",
      actionHref: "#metrics",
      icon: <FileSpreadsheet className="h-6 w-6" />,
      content: (
        <div className="space-y-3">
          <p>
            Enterprise finance directors and CFOs require absolute transparency. Recoup records every single cycle into an immutable, hash-chained ledger:
          </p>
          <div className="p-2.5 rounded-lg bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 font-mono text-[11px] text-emerald-600 dark:text-emerald-400">
            Block #5452: SHA256(prev_hash + cycle_inputs + gate_verdict)
          </div>
          <ul className="list-disc list-inside space-y-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <li><strong>Zero Black-Box Actions:</strong> Every discount offered, reminder sent, or deferral decided has an explicit, queryable rationale.</li>
            <li><strong>Board & Auditor Ready:</strong> Cryptographic verification proves company credit policies were strictly upheld.</li>
            <li><strong>Tamper Detection:</strong> Any retrospective database alteration immediately breaks the verification chain.</li>
          </ul>
        </div>
      ),
    },
  ];

  return (
    <>
      {showPreloader && (
        <Preloader onComplete={() => setShowPreloader(false)} />
      )}

      <main className="min-h-screen bg-white text-zinc-900 dark:bg-black dark:text-white selection:bg-orange-500/30 transition-colors duration-300 font-sans relative pb-28">
        
        {/* Fluid Morph Background - Active ONLY when switched to Light Mode */}
        <AnimatePresence>
          {mounted && !isDark && (
            <motion.div
              key="fluid-morph-light"
              initial={{ opacity: 0 }}
              animate={{ opacity: 0.35 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.7, ease: "easeInOut" }}
              className="fixed inset-0 pointer-events-none z-0 overflow-hidden"
            >
              <FluidMorphBg
                className="w-full h-full"
                duration={6}
                colors={[
                  "#fed7aa",
                  "#fdba74",
                  "#fde047",
                  "#ffedd5",
                  "#fef3c7",
                  "#fef08a",
                  "#fff7ed",
                ]}
                backgroundColor="#ffffff"
              />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Single Floating Glass Dock At Bottom Center */}
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center justify-center">
          <GlassDock items={dockItems} />
        </div>

        {/* Hero Section */}
        <section id="hero" className="relative pt-20 pb-20 md:pt-28 md:pb-28 overflow-hidden">
          {/* Subtle Ambient Backdrops */}
          <div className="absolute top-1/4 left-1/2 -translate-x-1/2 w-[700px] h-[450px] bg-gradient-to-b from-orange-500/15 via-amber-500/5 to-transparent blur-[140px] pointer-events-none rounded-full" />
          <div className="absolute top-10 left-1/4 w-[250px] h-[250px] bg-orange-600/10 blur-[100px] pointer-events-none" />

          <div className="mx-auto max-w-7xl px-6 relative z-10">
            <div className="text-center max-w-4xl mx-auto">
              <motion.div
                initial={{ opacity: 0, y: 15 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.6 }}
                className="flex flex-wrap items-center justify-center gap-3 mb-8"
              >
                <div className="inline-flex items-center gap-2 rounded-full border border-orange-500/30 bg-orange-500/10 px-4 py-1.5 text-xs font-medium text-orange-600 dark:text-orange-400">
                  <span className="relative flex h-2 w-2">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-orange-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-2 w-2 bg-orange-500"></span>
                  </span>
                  Autonomous B2B Receivables & Promise-to-Pay Agent
                </div>
                <BackendStatusBadge />
              </motion.div>

              <motion.h1
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.7, delay: 0.1 }}
                className="text-4xl sm:text-6xl lg:text-7xl font-semibold tracking-tight leading-[1.08] mb-8 text-zinc-950 dark:text-white"
              >
                An AI agent that chases overdue invoices,{" "}
                <span className="bg-gradient-to-r from-orange-500 via-amber-500 to-orange-600 dark:from-orange-400 dark:via-amber-300 dark:to-orange-500 bg-clip-text text-transparent">
                  knows when to stop
                </span>
                , and proves what it recovered.
              </motion.h1>

              <motion.p
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.7, delay: 0.2 }}
                className="text-base sm:text-xl text-zinc-600 dark:text-zinc-400 max-w-2xl mx-auto mb-10 leading-relaxed font-normal"
              >
                Over <span className="text-zinc-900 dark:text-white font-semibold">₹8.1 lakh crore</span> is currently locked in delayed Indian B2B trade credit. 
                Recoup prioritizes by expected recovery value, stays bounded by hard policy guardrails, tracks payment promises, 
                and confirms settlement via cryptographic Razorpay webhooks.
              </motion.p>

              {/* Action Buttons */}
              <motion.div
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.7, delay: 0.3 }}
                className="flex flex-col sm:flex-row items-center justify-center gap-4"
              >
                <a
                  href="#simulator"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-orange-500 to-amber-500 px-7 py-3.5 text-sm font-semibold text-black hover:opacity-95 transition-all shadow-lg shadow-orange-500/20"
                >
                  Explore Agent Decision Loop
                  <ArrowRight className="h-4 w-4" />
                </a>
                <a
                  href="#services"
                  className="w-full sm:w-auto inline-flex items-center justify-center gap-2 rounded-xl border border-zinc-300 dark:border-white/15 bg-zinc-100/80 dark:bg-white/5 px-7 py-3.5 text-sm font-medium text-zinc-800 dark:text-white hover:bg-zinc-200 dark:hover:bg-white/10 transition-all"
                >
                  Review Agent Services
                </a>
              </motion.div>
            </div>

            {/* Quick Proof Metrics Row */}
            <motion.div
              initial={{ opacity: 0, y: 30 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.8, delay: 0.4 }}
              className="mt-16 grid grid-cols-2 lg:grid-cols-4 gap-4 max-w-5xl mx-auto"
            >
              {[
                { label: "Total Book Processed", value: "₹6.96 Cr", desc: "600 invoices in verified batch benchmark" },
                { label: "Recovered Cashflow", value: "₹3.97 Cr", desc: "326 flagged overdue invoices collected" },
                { label: "Targeting Recovery Rate", value: "63.5%", desc: "Focuses only on high expected value" },
                { label: "Compliance Breaches", value: "0", desc: "Policy-enforced caps; tamper-chained ledger" },
              ].map((stat, idx) => (
                <div
                  key={idx}
                  className="p-5 rounded-2xl border border-zinc-200/80 dark:border-white/10 bg-zinc-50/80 dark:bg-zinc-950/70 shadow-sm dark:shadow-none backdrop-blur-md hover:border-zinc-300 dark:hover:border-white/20 transition-all text-left"
                >
                  <p className="text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">{stat.label}</p>
                  <p className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-950 dark:text-white mb-1">{stat.value}</p>
                  <p className="text-[11px] text-zinc-500 leading-tight">{stat.desc}</p>
                </div>
              ))}
            </motion.div>
          </div>
        </section>

        {/* The Problem & The Difference Section */}
        <section className="py-20 border-t border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-zinc-950/40 relative">
          <div className="mx-auto max-w-7xl px-6">
            <div className="max-w-3xl mb-14">
              <h2 className="text-xs font-bold uppercase tracking-widest text-orange-600 dark:text-orange-400 mb-3">The Problem We Solve</h2>
              <p className="text-3xl sm:text-4xl font-semibold tracking-tight text-zinc-950 dark:text-white mb-4">
                Why generic reminder bots destroy enterprise client trust.
              </p>
              <p className="text-zinc-600 dark:text-zinc-400 text-base leading-relaxed">
                The average Indian SME carries <strong className="text-zinc-900 dark:text-zinc-200">₹3.83 crore</strong> in overdue receivables. 
                Finance teams waste dozens of hours manual-dunning spreadsheets or configuring mindless automated bots that treat dependable clients like defaulters.
              </p>
            </div>

            {/* Comparison Grid */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              {/* Dumb Bot */}
              <div className="p-8 rounded-2xl border border-red-200 dark:border-red-500/20 bg-red-50/70 dark:bg-red-950/10 relative overflow-hidden">
                <div className="flex items-center gap-3 mb-6">
                  <div className="p-2 rounded-lg bg-red-100 dark:bg-red-500/20 text-red-600 dark:text-red-400">
                    <AlertTriangle className="h-5 w-5" />
                  </div>
                  <h3 className="text-xl font-semibold text-zinc-950 dark:text-white">Traditional Dunning Scripts & LLM Bots</h3>
                </div>
                <ul className="space-y-4 text-sm text-zinc-700 dark:text-zinc-300">
                  <li className="flex items-start gap-3">
                    <span className="text-red-500 font-bold">✕</span>
                    <span><strong>Uniform Spam:</strong> Sends identical templated reminders to customers who always pay Net-35, irritating key accounts (High False Interventions).</span>
                  </li>
                  <li className="flex items-start gap-3">
                    <span className="text-red-500 font-bold">✕</span>
                    <span><strong>Unbounded Concessions:</strong> Ungoverned LLMs can hallucinate arbitrary discounts to force closure, bleeding business margin.</span>
                  </li>
                  <li className="flex items-start gap-3">
                    <span className="text-red-500 font-bold">✕</span>
                    <span><strong>Zero Memory of Promises:</strong> When a CFO replies "We'll pay on Friday post audit", dumb bots keep sending reminders on Thursday.</span>
                  </li>
                  <li className="flex items-start gap-3">
                    <span className="text-red-500 font-bold">✕</span>
                    <span><strong>Never Stops:</strong> Infinite loop nagging until marked spam or blocked by the customer.</span>
                  </li>
                </ul>
              </div>

              {/* Recoup Agent */}
              <div className="p-8 rounded-2xl border border-orange-300/80 dark:border-orange-500/30 bg-orange-50/50 dark:bg-gradient-to-b dark:from-orange-500/5 dark:to-transparent shadow-sm dark:shadow-none relative overflow-hidden">
                <div className="flex items-center gap-3 mb-6">
                  <div className="p-2 rounded-lg bg-orange-100 dark:bg-orange-500/20 text-orange-600 dark:text-orange-400">
                    <CheckCircle2 className="h-5 w-5" />
                  </div>
                  <h3 className="text-xl font-semibold text-zinc-950 dark:text-white">Recoup Autonomous Agent</h3>
                </div>
                <ul className="space-y-4 text-sm text-zinc-700 dark:text-zinc-300">
                  <li className="flex items-start gap-3">
                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">✓</span>
                    <span><strong>Expected-Value Prioritization:</strong> Deliberately leaves low-urgency, reliable payers alone. Spares goodwill; focuses effort where recovery is at risk.</span>
                  </li>
                  <li className="flex items-start gap-3">
                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">✓</span>
                    <span><strong>Hard Business Gatekeeper:</strong> Strict discount ceilings and contact frequency caps that NO LLM can override or bypass.</span>
                  </li>
                  <li className="flex items-start gap-3">
                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">✓</span>
                    <span><strong>Promise-to-Pay Watchdog:</strong> Records explicit dates ("Friday 15th"), pauses outreach, and auto-escalates only if the deadline lapses unpaid.</span>
                  </li>
                  <li className="flex items-start gap-3">
                    <span className="text-emerald-600 dark:text-emerald-400 font-bold">✓</span>
                    <span><strong>Guaranteed Stop & Handoff:</strong> Strict escalation ladder terminating in automated human account handoff. Instant opt-out compliance.</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </section>

        {/* Detailed Services Breakdown */}
        <section id="services" className="py-24 border-t border-zinc-200 dark:border-white/10 bg-white dark:bg-black relative">
          <div className="mx-auto max-w-7xl px-6">
            <div className="text-center max-w-3xl mx-auto mb-16">
              <h2 className="text-xs font-bold uppercase tracking-widest text-orange-600 dark:text-orange-400 mb-3">Our Core Autonomous Services</h2>
              <p className="text-3xl sm:text-5xl font-semibold tracking-tight text-zinc-950 dark:text-white mb-4">
                What Recoup Actually Does
              </p>
              <p className="text-zinc-600 dark:text-zinc-400 text-base">
                A purpose-built autonomous system spanning discovery, policy enforcement, transactional execution, and cryptographic auditability.
              </p>
            </div>

            {/* Expandable Bento Grid */}
            <ExpandableBentoGrid items={bentoServices} />
          </div>
        </section>

        {/* Interactive Decision Cycle Simulator */}
        <section id="simulator" className="py-24 border-t border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-zinc-950/60 relative">
          <div className="mx-auto max-w-7xl px-6">
            <div className="flex flex-col md:flex-row md:items-end justify-between mb-12 gap-4">
              <div>
                <div className="flex items-center gap-3 mb-3">
                  <h2 className="text-xs font-bold uppercase tracking-widest text-orange-600 dark:text-orange-400">
                    Live Autonomous Engine
                  </h2>
                  <BackendStatusBadge />
                </div>
                <p className="text-3xl sm:text-4xl font-semibold tracking-tight text-zinc-950 dark:text-white">
                  The Decision Cycle in Real-Time
                </p>
                <p className="text-zinc-600 dark:text-zinc-400 text-sm mt-1 max-w-3xl">
                  Inspect how Recoup evaluates recovery probability with SHAP drivers, enforces deterministic policy gates, executes via Razorpay/Resend, and anchors decisions in cryptographic hash chains.
                </p>
              </div>
            </div>

            <DecisionCycleRunner />
          </div>
        </section>

        {/* Human-in-the-Loop Review Desk Section */}
        <section id="review" className="py-20 border-t border-zinc-200 dark:border-white/10 bg-white dark:bg-black relative">
          <div className="mx-auto max-w-7xl px-6">
            <div className="max-w-3xl mb-10">
              <h2 className="text-xs font-bold uppercase tracking-widest text-orange-600 dark:text-orange-400 mb-3">
                Promise-to-Pay Watchdog
              </h2>
              <p className="text-3xl font-semibold tracking-tight text-zinc-950 dark:text-white mb-2">
                Human Supervisor Clearance Queue
              </p>
              <p className="text-zinc-600 dark:text-zinc-400 text-sm">
                When customer replies contain ambiguous payment commitments or disputes requiring manual discretion, the agent halts automatic collection and routes them directly to the human review desk.
              </p>
            </div>

            <HumanReviewDesk />
          </div>
        </section>

        {/* Benefits For Stakeholders */}
        <section id="benefits" className="py-24 border-t border-zinc-200 dark:border-white/10 bg-white dark:bg-black relative">
          <div className="mx-auto max-w-7xl px-6">
            <div className="text-center max-w-3xl mx-auto mb-16">
              <h2 className="text-xs font-bold uppercase tracking-widest text-orange-600 dark:text-orange-400 mb-3">Enterprise Impact</h2>
              <p className="text-3xl sm:text-5xl font-semibold tracking-tight text-zinc-950 dark:text-white mb-4">
                How Your Organization Benefits
              </p>
              <p className="text-zinc-600 dark:text-zinc-400 text-base">
                Tailored advantages across Finance, Executive Leadership, and Collections Operations.
              </p>
            </div>

            {/* Stakeholder Switcher */}
            <div className="flex justify-center mb-12">
              <div className="inline-flex p-1.5 rounded-xl bg-zinc-100 dark:bg-zinc-950 border border-zinc-200 dark:border-white/10 gap-2 shadow-inner">
                {[
                  { id: "cfo", label: "For CFOs & Finance Heads" },
                  { id: "founder", label: "For Founders & CEOs" },
                  { id: "ar", label: "For Credit & AR Teams" },
                ].map((tab) => (
                  <button
                    key={tab.id}
                    onClick={() => setActiveTab(tab.id as any)}
                    className={`px-5 py-2 rounded-lg text-xs sm:text-sm font-semibold transition-all ${
                      activeTab === tab.id
                        ? "bg-white text-zinc-950 shadow-md dark:bg-white dark:text-black"
                        : "text-zinc-600 dark:text-zinc-400 hover:text-zinc-950 dark:hover:text-white"
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Tab Contents */}
            <div className="max-w-4xl mx-auto">
              <AnimatePresence mode="wait">
                {activeTab === "cfo" && (
                  <motion.div
                    key="cfo"
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -15 }}
                    transition={{ duration: 0.3 }}
                    className="p-8 sm:p-10 rounded-3xl border border-zinc-200 dark:border-white/10 bg-zinc-50/80 dark:bg-zinc-950/80 grid grid-cols-1 md:grid-cols-2 gap-8 shadow-sm dark:shadow-none"
                  >
                    <div>
                      <h3 className="text-2xl font-bold text-zinc-950 dark:text-white mb-3">Accelerate Working Capital</h3>
                      <p className="text-zinc-600 dark:text-zinc-400 text-sm leading-relaxed mb-6">
                        Reduce Days Sales Outstanding (DSO) by an average of 18–26 days without compromising client relationships.
                      </p>
                      <ul className="space-y-3 text-sm text-zinc-700 dark:text-zinc-300">
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Audit-ready immutable decision trail</span>
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Zero rogue discounts; strictly capped waivers</span>
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Instant settlement reconciliation via Razorpay</span>
                        </li>
                      </ul>
                    </div>
                    <div className="p-6 rounded-2xl bg-white dark:bg-black/60 border border-zinc-200 dark:border-white/10 flex flex-col justify-center shadow-inner">
                      <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Quantified DSO Impact</p>
                      <p className="text-4xl font-extrabold text-orange-600 dark:text-orange-400 mb-1">-24 Days</p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4">Average acceleration in receivables cycle</p>
                      <div className="h-1.5 w-full bg-zinc-200 dark:bg-zinc-800 rounded-full overflow-hidden">
                        <div className="h-full bg-orange-500 w-3/4 rounded-full" />
                      </div>
                    </div>
                  </motion.div>
                )}

                {activeTab === "founder" && (
                  <motion.div
                    key="founder"
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -15 }}
                    transition={{ duration: 0.3 }}
                    className="p-8 sm:p-10 rounded-3xl border border-zinc-200 dark:border-white/10 bg-zinc-50/80 dark:bg-zinc-950/80 grid grid-cols-1 md:grid-cols-2 gap-8 shadow-sm dark:shadow-none"
                  >
                    <div>
                      <h3 className="text-2xl font-bold text-zinc-950 dark:text-white mb-3">No More Awkward Collections</h3>
                      <p className="text-zinc-600 dark:text-zinc-400 text-sm leading-relaxed mb-6">
                        Free up founding and sales bandwidth. You don't have to ruin rapport with your enterprise clients just to get paid on time.
                      </p>
                      <ul className="space-y-3 text-sm text-zinc-700 dark:text-zinc-300">
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Zero awkward founder-to-client chasing emails</span>
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Deliberate protection of dependable accounts</span>
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Automatic handoff before any formal escalation</span>
                        </li>
                      </ul>
                    </div>
                    <div className="p-6 rounded-2xl bg-white dark:bg-black/60 border border-zinc-200 dark:border-white/10 flex flex-col justify-center shadow-inner">
                      <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Reclaimed Team Time</p>
                      <p className="text-4xl font-extrabold text-orange-600 dark:text-orange-400 mb-1">15+ hrs/wk</p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4">Finance & founder time redirected to growth</p>
                      <div className="h-1.5 w-full bg-zinc-200 dark:bg-zinc-800 rounded-full overflow-hidden">
                        <div className="h-full bg-orange-500 w-4/5 rounded-full" />
                      </div>
                    </div>
                  </motion.div>
                )}

                {activeTab === "ar" && (
                  <motion.div
                    key="ar"
                    initial={{ opacity: 0, y: 15 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -15 }}
                    transition={{ duration: 0.3 }}
                    className="p-8 sm:p-10 rounded-3xl border border-zinc-200 dark:border-white/10 bg-zinc-50/80 dark:bg-zinc-950/80 grid grid-cols-1 md:grid-cols-2 gap-8 shadow-sm dark:shadow-none"
                  >
                    <div>
                      <h3 className="text-2xl font-bold text-zinc-950 dark:text-white mb-3">Autopilot for High-Volume Books</h3>
                      <p className="text-zinc-600 dark:text-zinc-400 text-sm leading-relaxed mb-6">
                        Stop manually tracking who said "check next week" across 400 spreadsheet rows. Recoup handles execution and logs every step.
                      </p>
                      <ul className="space-y-3 text-sm text-zinc-700 dark:text-zinc-300">
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Automatic parsing of debtor reply dates</span>
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>One-click batch runs with clean recovery metrics</span>
                        </li>
                        <li className="flex items-center gap-2">
                          <CheckCircle2 className="h-4 w-4 text-orange-600 dark:text-orange-400" />
                          <span>Automated Razorpay link creation + status tracking</span>
                        </li>
                      </ul>
                    </div>
                    <div className="p-6 rounded-2xl bg-white dark:bg-black/60 border border-zinc-200 dark:border-white/10 flex flex-col justify-center shadow-inner">
                      <p className="text-xs text-zinc-500 uppercase tracking-wider mb-2">Recovery Efficiency</p>
                      <p className="text-4xl font-extrabold text-orange-600 dark:text-orange-400 mb-1">3.8x</p>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4">Speedup in commitment-to-settlement rate</p>
                      <div className="h-1.5 w-full bg-zinc-200 dark:bg-zinc-800 rounded-full overflow-hidden">
                        <div className="h-full bg-orange-500 w-full rounded-full" />
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </section>

        {/* Batch Report Proof / Transparent Metrics */}
        <section id="metrics" className="py-24 border-t border-zinc-200 dark:border-white/10 bg-zinc-50/50 dark:bg-zinc-950/50 relative">
          <div className="mx-auto max-w-7xl px-6">
            <div className="text-center max-w-3xl mx-auto mb-16">
              <h2 className="text-xs font-bold uppercase tracking-widest text-orange-600 dark:text-orange-400 mb-3">Honest Benchmark Evidence</h2>
              <p className="text-3xl sm:text-5xl font-semibold tracking-tight text-zinc-950 dark:text-white mb-4">
                What Success Looks Like in Numbers
              </p>
              <p className="text-zinc-600 dark:text-zinc-400 text-base">
                Direct results from our verified 600-invoice batch simulation run. We report false interventions rather than hiding them.
              </p>
            </div>

            <div className="max-w-4xl mx-auto bg-white dark:bg-black rounded-3xl border border-zinc-200 dark:border-white/10 p-8 sm:p-10 font-mono shadow-md dark:shadow-none">
              <div className="flex items-center justify-between border-b border-zinc-200 dark:border-white/10 pb-4 mb-6">
                <span className="text-xs text-orange-600 dark:text-orange-400 font-bold tracking-wider">EVALUATION METRIC</span>
                <span className="text-xs text-orange-600 dark:text-orange-400 font-bold tracking-wider">BATCH RESULT (600 INVOICES)</span>
              </div>

              <div className="space-y-4 text-sm">
                {[
                  { label: "Invoices processed", val: "600" },
                  { label: "Total overdue book value", val: "₹ 6.96 Crore" },
                  { label: "Decision cycles executed", val: "5 Cycles" },
                  { label: "Cases flagged for intervention", val: "513 Cases" },
                  { label: "Cases correctly left alone (Goodwill saved)", val: "81 Cases" },
                  { label: "Interventions executed (links & email)", val: "505 Touches" },
                  { label: "Actions blocked by strict policy", val: "626 Blocked" },
                  { label: "Recovered amount (flagged book)", val: "₹ 3.97 Crore", highlight: true },
                  { label: "Targeting Recovery Rate", val: "63.5%", highlight: true },
                  { label: "False/unnecessary interventions reported", val: "263 Cases (Reported honestly)" },
                  { label: "Compliance & policy violations", val: "0 Violations", highlight: true },
                  { label: "Decision trace ledger verified", val: "YES (Hash-chained)", highlight: true },
                ].map((row, idx) => (
                  <div key={idx} className="flex items-center justify-between py-1.5 border-b border-zinc-100 dark:border-white/5">
                    <span className="text-zinc-600 dark:text-zinc-400">{row.label}</span>
                    <span className={`font-bold ${row.highlight ? "text-orange-600 dark:text-orange-400" : "text-zinc-900 dark:text-white"}`}>
                      {row.val}
                    </span>
                  </div>
                ))}
              </div>

              <div className="mt-8 pt-4 border-t border-zinc-200 dark:border-white/10 flex flex-col sm:flex-row items-center justify-between text-xs text-zinc-500 gap-3">
                <span>Reproducible via: python scripts/run_batch_demo.py --batch-size 600</span>
                <a
                  href="#hero"
                  className="text-orange-600 dark:text-orange-400 hover:opacity-80 inline-flex items-center gap-1 font-semibold"
                >
                  Back to Top ↑
                </a>
              </div>
            </div>
          </div>
        </section>

        {/* Bottom Animated Footer (Synced with active theme) */}
        <section className="h-[600px] relative w-full border-t border-zinc-200 dark:border-white/10 bg-white dark:bg-black transition-colors duration-300">
          <AnimatedFooter
            headingLines={["RECOUP", "AGENT"]}
            background={mounted && !isDark ? "#ffffff" : "#000000"}
            textColor={mounted && !isDark ? "#09090b" : "#ffffff"}
            charColor={mounted && !isDark ? "#e4e4e7" : "#444444"}
            hoverColor="#f97316"
            hoverCharColor={mounted && !isDark ? "#ffffff" : "#000000"}
            leftImage="/animated-footer/hand-left.jpg"
            rightImage="/animated-footer/hand-right.jpg"
          />
        </section>
      </main>
    </>
  );
}
