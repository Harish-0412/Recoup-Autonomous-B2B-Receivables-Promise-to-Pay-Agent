"use client";

import React, { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion, AnimatePresence, useReducedMotion } from "framer-motion";
import {
  PlayCircle,
  Lock,
  Info,
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Eye,
  FileText,
  Clock,
  History,
  ChevronRight,
  ArrowRight,
  ArrowUpRight,
  Zap,
  Scale,
  TrendingUp,
  Server,
  Activity,
  MinusCircle,
  BarChart3,
} from "lucide-react";
import {
  fetchTaskStatusDetail,
  triggerBatchRunDetailed,
  TaskApiError,
  type RunSummary,
  type TaskStatusResponse,
} from "@/lib/api";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Helpers (pure)
// ---------------------------------------------------------------------------

const SESSION_KEY = "recoup-task-key";
const SESSION_LAST_RUN = "recoup-last-run";

function loadSession(key: string): string | null {
  try {
    if (typeof window === "undefined") return null;
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function humanizeInterval(totalSeconds: number | undefined): string {
  if (totalSeconds == null) return "—";
  if (totalSeconds >= 3600) {
    const h = Math.floor(totalSeconds / 3600);
    const m = Math.round((totalSeconds % 3600) / 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }
  if (totalSeconds >= 60) {
    const m = Math.floor(totalSeconds / 60);
    const s = totalSeconds % 60;
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  return `${totalSeconds}s`;
}

function formatInt(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString("en-IN");
}

function runDurationMs(summary: RunSummary | null): number | null {
  if (!summary?.started_at || !summary?.finished_at) return null;
  const ms = Date.parse(summary.finished_at) - Date.parse(summary.started_at);
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

function maskKey(key: string): string {
  if (key.length <= 3) return "•••";
  return `${key.slice(0, 3)}••••••`;
}

// ---------------------------------------------------------------------------
// Pipeline stage model — the "what / what decision / why" behind every step.
// Text is drawn from the backend's own docstrings (tasks.py, batch_runner.py,
// locks.py, config.py) so the UI never invents a rationale.
// ---------------------------------------------------------------------------

interface StageSpec {
  id: string;
  title: string;
  short: string;
  icon: React.ElementType;
  what: string;
  decision: string;
  why: string;
}

const STAGES: StageSpec[] = [
  {
    id: "lock",
    title: "Advisory lock",
    short: "One run at a time",
    icon: Lock,
    what: "The trigger tries pg_try_advisory_lock on the single lock name recoup:batch-run. It never waits for the lock.",
    decision: "Lock free → this run proceeds. Lock held → ran=false with skipped_reason; the trigger is a no-op, not a queued rerun.",
    why: "Cron retries, double-configured schedulers, and overrunning runs all double-fire. Waiting for the lock would simply do the double-send later — so a collision skips instead of queueing.",
  },
  {
    id: "sweep",
    title: "Promise sweep",
    short: "Promises first",
    icon: History,
    what: "Every PENDING promise is re-assessed against amount_paid — money only the payment webhook can write. Lapsed-but-unpaid becomes BROKEN; paid becomes KEPT.",
    decision: "Broken promises release their invoice from PROMISED back to IN_PROGRESS, re-arming escalation for the scoring step below.",
    why: "The policy gate stays quiet while a promise is open, so a promise nobody ever marks broken silences the agent on that invoice forever. Sweeping before scoring makes a promise that lapsed this morning chaseable this run, not next.",
  },
  {
    id: "score",
    title: "Score & tier",
    short: "EV-ranked",
    icon: TrendingUp,
    what: "Each invoice in the bounded slice (up to the batch cap) is scored for P(recovery) and tiered: WAIT means deliberately left alone, REMIND / ESCALATE mean flagged.",
    decision: "WAIT cases are counted as left_alone and skipped — no contact, no ladder movement. Flagged cases continue to the gate.",
    why: "Expected-value ranking focuses effort where capital is at risk and protects goodwill on self-cure candidates. Left-alone is a decision with a reason, not an omission.",
  },
  {
    id: "gate",
    title: "Policy gate",
    short: "The hard ceiling",
    icon: ShieldCheck,
    what: "Every flagged action passes the deterministic policy engine: discount ceiling, contact-frequency and volume caps, overdue threshold, open-promise quiet, opt-out registry.",
    decision: "Refused actions are counted as blocked_by_policy and logged — never sent, never retried silently. The LLM cannot override this gate.",
    why: "Reasoning drafts words; only the gate decides amounts and contact. A refusal is enforced before anything is sent, and the ledger records what the gate said independently of the caller.",
  },
  {
    id: "execute",
    title: "Execute / halt",
    short: "Send or stop",
    icon: Zap,
    what: "Allowed contact actions execute via Razorpay Payment Links + Resend. HAND_OFF / CLOSE move internal state and send nothing. A failed send buys no ladder rung.",
    decision: "If SENDING_ENABLED is false (kill switch), the run logs execution:halted, advances nothing, and sets sending_halted — resuming later continues where it left off.",
    why: "The kill switch is operational, not developmental: unlike DRY_RUN it advances no rung, so flipping it back on never finds every invoice a step further along. One bad invoice is recorded in errors and the run continues.",
  },
  {
    id: "ledger",
    title: "Trace commit",
    short: "Hash-chained",
    icon: Scale,
    what: "The run-local decision ledger — sweep outcomes, scores, gate verdicts, executions — is persisted to the decision trace and committed with the state changes.",
    decision: "The RunSummary is returned whether or not anything happened, so a cron log can tell “nothing to do” apart from “someone else is doing it”.",
    why: "Every action carries its reason in an append-only, hash-chained trace. Compliance counts and recovery numbers are read from this trace afterwards — counted, not asserted.",
  },
];

// ---------------------------------------------------------------------------
// Small building blocks
// ---------------------------------------------------------------------------

function Tile({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  const valueTone =
    tone === "good"
      ? "text-emerald-600 dark:text-emerald-400"
      : tone === "warn"
        ? "text-amber-600 dark:text-amber-400"
        : tone === "bad"
          ? "text-red-600 dark:text-red-400"
          : "text-zinc-900 dark:text-white";
  return (
    <div className="rounded-xl px-4 py-3.5 bg-zinc-50/80 dark:bg-white/[0.03] border border-zinc-200/60 dark:border-white/[0.06]">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
        {label}
      </div>
      <div className={cn("text-xl font-bold tracking-tight tabular-nums mt-0.5", valueTone)}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-0.5">{sub}</div>}
    </div>
  );
}

function CountCell({ label, value, accent }: { label: string; value: number; accent?: string }) {
  return (
    <div className="rounded-xl px-3.5 py-3 bg-zinc-50/80 dark:bg-white/[0.03] border border-zinc-200/60 dark:border-white/[0.06] text-center">
      <div className={cn("text-2xl font-bold tabular-nums tracking-tight", accent ?? "text-zinc-900 dark:text-white")}>
        {formatInt(value)}
      </div>
      <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mt-1">
        {label}
      </div>
    </div>
  );
}

/** A pipeline node whose tooltip (hover AND keyboard focus) explains the step. */
function StageNode({
  spec,
  index,
  state,
  metric,
  align = "center",
}: {
  spec: StageSpec;
  index: number;
  state: "idle" | "running" | "done";
  metric: string;
  align?: "left" | "center" | "right";
}) {
  const Icon = spec.icon;
  const alignCls =
    align === "left"
      ? "left-0"
      : align === "right"
        ? "right-0"
        : "left-1/2 -translate-x-1/2";
  return (
    <div
      tabIndex={0}
      aria-label={`${spec.title}: ${spec.short}. ${spec.what} ${spec.decision}`}
      className="group relative flex flex-col items-center gap-2 rounded-2xl px-3 py-4 min-w-[118px] flex-1 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm outline-none cursor-help transition-colors hover:border-orange-300 dark:hover:border-orange-500/40 focus-visible:ring-2 focus-visible:ring-orange-500"
    >
      <span
        className={cn(
          "absolute top-2 right-2 text-[10px] font-mono font-bold tabular-nums px-1.5 py-0.5 rounded-md",
          state === "running"
            ? "bg-orange-100 dark:bg-orange-500/15 text-orange-700 dark:text-orange-400 animate-pulse"
            : state === "done"
              ? "bg-emerald-100 dark:bg-emerald-500/15 text-emerald-700 dark:text-emerald-400"
              : "bg-zinc-100 dark:bg-white/5 text-zinc-400 dark:text-zinc-500"
        )}
      >
        {index + 1}
      </span>
      <div
        className={cn(
          "p-2.5 rounded-xl",
          state === "running"
            ? "bg-orange-100 dark:bg-orange-500/15"
            : "bg-zinc-100 dark:bg-white/5"
        )}
      >
        <Icon
          className={cn(
            "w-5 h-5",
            state === "running"
              ? "text-orange-600 dark:text-orange-400"
              : "text-zinc-500 dark:text-zinc-400"
          )}
        />
      </div>
      <div className="text-xs font-bold text-zinc-900 dark:text-white text-center leading-tight">
        {spec.title}
      </div>
      <div className="text-[11px] font-mono font-semibold text-orange-600 dark:text-orange-400 tabular-nums text-center">
        {metric}
      </div>
      {/* Hover / focus tooltip */}
      <div
        className={cn(
          "pointer-events-none absolute bottom-full mb-3 w-72 sm:w-80 z-30",
          "opacity-0 translate-y-1 group-hover:opacity-100 group-hover:translate-y-0",
          "group-focus-within:opacity-100 group-focus-within:translate-y-0",
          "transition-all duration-200",
          alignCls
        )}
      >
        <div className="rounded-xl p-4 bg-zinc-950 dark:bg-black text-left border border-zinc-700 dark:border-white/15 shadow-2xl">
          <p className="text-[10px] font-bold uppercase tracking-widest text-orange-400 mb-1.5">
            What happens
          </p>
          <p className="text-xs text-zinc-200 leading-relaxed mb-3">{spec.what}</p>
          <p className="text-[10px] font-bold uppercase tracking-widest text-sky-400 mb-1.5">
            Decision taken
          </p>
          <p className="text-xs text-zinc-200 leading-relaxed mb-3">{spec.decision}</p>
          <p className="text-[10px] font-bold uppercase tracking-widest text-emerald-400 mb-1.5">
            Why this way
          </p>
          <p className="text-xs text-zinc-300 leading-relaxed">{spec.why}</p>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type StatusState = "locked" | "loading" | "ready" | "error";
type RunState = "idle" | "confirm" | "running" | "done" | "error";

export default function RunsPage() {
  const shouldReduce = useReducedMotion();

  // Task key lives in memory + sessionStorage (tab session only — never
  // localStorage, never a cookie, never logged).
  const [taskKey, setTaskKey] = useState<string>(() => loadSession(SESSION_KEY) ?? "");
  const [keyInput, setKeyInput] = useState("");
  const [showKey, setShowKey] = useState(false);

  const [statusState, setStatusState] = useState<StatusState>("locked");
  const [status, setStatus] = useState<TaskStatusResponse | null>(null);
  const [statusError, setStatusError] = useState<TaskApiError | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const [limitInput, setLimitInput] = useState("");
  const [runState, setRunState] = useState<RunState>("idle");
  const [result, setResult] = useState<RunSummary | null>(() => {
    const raw = loadSession(SESSION_LAST_RUN);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as RunSummary;
    } catch {
      return null;
    }
  });
  const [runError, setRunError] = useState<TaskApiError | null>(null);
  const [ackChecked, setAckChecked] = useState(false);
  const [elapsedMs, setElapsedMs] = useState(0);

  // If a key survived in the tab session, re-validate it on load.
  useEffect(() => {
    const saved = loadSession(SESSION_KEY);
    if (saved) void unlockWith(saved);
  }, []);

  // Elapsed timer while a run is in flight (callback setState — no cascade).
  useEffect(() => {
    if (runState !== "running") return;
    const started = Date.now();
    const id = setInterval(() => setElapsedMs(Date.now() - started), 500);
    return () => clearInterval(id);
  }, [runState]);

  async function unlockWith(key: string) {
    const trimmed = key.trim();
    if (!trimmed) return;
    setStatusState("loading");
    setStatusError(null);
    try {
      const s = await fetchTaskStatusDetail(trimmed);
      setTaskKey(trimmed);
      setStatus(s);
      setStatusState("ready");
      try {
        sessionStorage.setItem(SESSION_KEY, trimmed);
      } catch {
        // Private mode — key simply won't survive a refresh.
      }
      setKeyInput("");
    } catch (err) {
      setStatus(null);
      setStatusState("error");
      setStatusError(err instanceof TaskApiError ? err : new TaskApiError("offline", "Backend unreachable."));
    }
  }

  function lock() {
    setTaskKey("");
    setStatus(null);
    setStatusState("locked");
    setStatusError(null);
    try {
      sessionStorage.removeItem(SESSION_KEY);
    } catch {
      // ignore
    }
  }

  async function refreshStatus() {
    if (!taskKey) return;
    setRefreshing(true);
    try {
      setStatus(await fetchTaskStatusDetail(taskKey));
      setStatusState("ready");
      setStatusError(null);
    } catch (err) {
      setStatusState("error");
      setStatusError(err instanceof TaskApiError ? err : new TaskApiError("offline", "Backend unreachable."));
    } finally {
      setRefreshing(false);
    }
  }

  async function confirmAndRun() {
    const limit = Math.max(1, Number(limitInput) || status?.batch_max_invoices || 200);
    setRunState("running");
    setRunError(null);
    setElapsedMs(0);
    try {
      const summary = await triggerBatchRunDetailed(taskKey, limit);
      setResult(summary);
      setRunState("done");
      try {
        sessionStorage.setItem(SESSION_LAST_RUN, JSON.stringify(summary));
      } catch {
        // ignore
      }
      void refreshStatus();
    } catch (err) {
      setRunError(err instanceof TaskApiError ? err : new TaskApiError("offline", "Backend unreachable — nothing was triggered."));
      setRunState("error");
    }
  }

  const unlocked = statusState === "ready" && status != null;
  const sendingOn = status?.sending_enabled ?? false;
  const dryRun = status?.dry_run ?? true;
  const batchCap = status?.batch_max_invoices ?? 200;
  const effectiveLimit = Math.max(1, Number(limitInput) || batchCap);

  const durationMs = useMemo(() => runDurationMs(result), [result]);

  const stageMetrics: Record<string, string> = useMemo(() => {
    if (!result) {
      return { lock: "—", sweep: "—", score: "—", gate: "—", execute: "—", ledger: "—" };
    }
    if (!result.ran) {
      return {
        lock: "held",
        sweep: "skipped",
        score: "skipped",
        gate: "skipped",
        execute: "skipped",
        ledger: "skipped",
      };
    }
    return {
      lock: "acquired",
      sweep: `${formatInt(result.promises_checked)} chk · ${formatInt(result.promises_broken)} brk`,
      score: `${formatInt(result.scored)} scored · ${formatInt(result.left_alone)} alone`,
      gate: `${formatInt(result.blocked_by_policy)} blocked`,
      execute: result.sending_halted ? "HALTED" : `${formatInt(result.acted)} sent`,
      ledger: result.errors.length > 0 ? `${result.errors.length} err` : "committed",
    };
  }, [result]);

  const stageState: "idle" | "running" | "done" =
    runState === "running" ? "running" : result ? "done" : "idle";

  const errorCard = (err: TaskApiError) => {
    const tone =
      err.code === "wrong-key"
        ? "border-red-200/60 dark:border-red-500/25 bg-red-50/60 dark:bg-red-500/[0.06]"
        : err.code === "disabled"
          ? "border-amber-200/60 dark:border-amber-500/25 bg-amber-50/60 dark:bg-amber-500/[0.06]"
          : "border-zinc-200 dark:border-white/10 bg-zinc-50/60 dark:bg-white/[0.02]";
    const Icon = err.code === "wrong-key" ? XCircle : err.code === "disabled" ? AlertTriangle : Server;
    const title =
      err.code === "wrong-key"
        ? "Wrong task key"
        : err.code === "disabled"
          ? "Task endpoints disabled on the server"
          : err.code === "offline"
            ? "Backend unreachable"
            : `Request failed${err.status ? ` (HTTP ${err.status})` : ""}`;
    return (
      <div className={cn("rounded-xl px-4 py-3.5 border flex items-start gap-3", tone)}>
        <Icon className="w-5 h-5 mt-0.5 flex-shrink-0 text-zinc-500 dark:text-zinc-400" />
        <div>
          <p className="text-sm font-bold text-zinc-900 dark:text-white">{title}</p>
          <p className="text-xs text-zinc-600 dark:text-zinc-300 mt-0.5 leading-relaxed">{err.message}</p>
          {err.code === "wrong-key" && (
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
              The server compares in constant time and says nothing about which half was wrong — check the key and try again.
            </p>
          )}
        </div>
      </div>
    );
  };

  return (
    <main className="min-h-full relative overflow-hidden">
      {/* Ambient backdrop */}
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div className="absolute -top-32 left-1/2 -translate-x-1/2 w-[720px] h-[380px] bg-gradient-to-b from-orange-500/15 via-amber-500/5 to-transparent blur-[120px] rounded-full" />
        <div className="absolute top-40 -left-24 w-[280px] h-[280px] bg-indigo-500/10 blur-[100px] rounded-full" />
        <div className="absolute top-64 -right-24 w-[280px] h-[280px] bg-emerald-500/[0.07] blur-[100px] rounded-full" />
      </div>

      <div className="relative p-4 sm:p-6 lg:p-8 max-w-[1400px] mx-auto space-y-6">
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
                <PlayCircle className="w-3.5 h-3.5" />
                <span>Autonomous Run Control</span>
              </div>
              {unlocked ? (
                <span
                  className={cn(
                    "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold border",
                    sendingOn
                      ? "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border-red-200/60 dark:border-red-500/25"
                      : "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-200/60 dark:border-emerald-500/25"
                  )}
                >
                  <span className={cn("w-1.5 h-1.5 rounded-full", sendingOn ? "bg-red-500 animate-pulse" : "bg-emerald-500")} />
                  {sendingOn ? "SENDING LIVE" : "KILL SWITCH OFF"}
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-zinc-100 dark:bg-white/5 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-white/10">
                  <Lock className="w-3 h-3" />
                  LOCKED
                </span>
              )}
              {dryRun && unlocked && (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-bold bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20">
                  DRY RUN
                </span>
              )}
            </div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 dark:text-white leading-tight">
              The scheduler&apos;s view of the agent
            </h1>
            <p className="text-sm sm:text-base text-zinc-500 dark:text-zinc-400 mt-1.5 max-w-2xl leading-relaxed">
              No in-process scheduler lives here on purpose — an external cron triggers, the app
              defends itself with an advisory lock, and the kill switch answers from outside the
              process. Hover any pipeline stage below to see what it decides and why.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <Link
              href="/reports/batch"
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
            >
              <BarChart3 className="w-4 h-4 text-orange-500" />
              <span>Batch Audit</span>
            </Link>
            <button
              onClick={() => void refreshStatus()}
              disabled={!taskKey || refreshing || statusState === "loading"}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm transition-all disabled:opacity-50"
            >
              <RefreshCw className={cn("w-4 h-4", refreshing && "animate-spin")} />
              <span>{refreshing ? "Checking…" : "Refresh Status"}</span>
            </button>
          </div>
        </motion.div>

        {/* Task-key gate */}
        <section className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5 sm:p-6">
          <div className="flex flex-col lg:flex-row lg:items-center gap-4">
            <div className="flex items-center gap-3.5 flex-1 min-w-0">
              <div className={cn("p-2.5 rounded-xl flex-shrink-0", unlocked ? "bg-emerald-100 dark:bg-emerald-500/15" : "bg-zinc-100 dark:bg-white/5")}>
                <Lock className={cn("w-5 h-5", unlocked ? "text-emerald-600 dark:text-emerald-400" : "text-zinc-400")} />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-900 dark:text-white">
                  {unlocked ? `Authenticated ${maskKey(taskKey)}` : "Task key required"}
                </h2>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
                  {unlocked
                    ? "Key held in memory + this tab's session only — it never touches localStorage, cookies, or logs. Sent as an Authorization: Bearer header."
                    : "These endpoints can mail your entire customer book, so they are bearer-locked ahead of the rest of the API. Enter the cron's TASK_API_KEY to proceed."}
                </p>
              </div>
            </div>
            {unlocked ? (
              <button
                onClick={lock}
                className="inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm transition-all flex-shrink-0"
              >
                Forget key & lock
              </button>
            ) : (
              <form
                className="flex flex-col sm:flex-row gap-2.5 flex-shrink-0 w-full lg:w-auto"
                onSubmit={(e) => {
                  e.preventDefault();
                  void unlockWith(keyInput);
                }}
              >
                <div className="relative flex-1 sm:min-w-[280px]">
                  <input
                    type={showKey ? "text" : "password"}
                    value={keyInput}
                    onChange={(e) => setKeyInput(e.target.value)}
                    placeholder="TASK_API_KEY (bearer token)"
                    autoComplete="off"
                    spellCheck={false}
                    className="w-full pl-4 pr-11 py-2.5 rounded-xl text-sm font-mono bg-zinc-50 dark:bg-white/[0.03] border border-zinc-200 dark:border-white/10 text-zinc-900 dark:text-white placeholder:text-zinc-400 placeholder:font-sans focus:outline-none focus:ring-2 focus:ring-orange-500/60 focus:border-orange-400 transition-all"
                  />
                  <button
                    type="button"
                    onClick={() => setShowKey((v) => !v)}
                    title={showKey ? "Hide key" : "Show key"}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 rounded-md text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors"
                  >
                    <Eye className="w-4 h-4" />
                  </button>
                </div>
                <button
                  type="submit"
                  disabled={!keyInput.trim() || statusState === "loading"}
                  className="inline-flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600 text-white shadow-md hover:shadow-lg shadow-orange-500/15 transition-all active:scale-[0.97] disabled:opacity-60"
                >
                  {statusState === "loading" ? (
                    <>
                      <RefreshCw className="w-4 h-4 animate-spin" />
                      Verifying…
                    </>
                  ) : (
                    <>
                      Unlock
                      <ChevronRight className="w-4 h-4" />
                    </>
                  )}
                </button>
              </form>
            )}
          </div>
          {statusState === "error" && statusError && (
            <div className="mt-4">{errorCard(statusError)}</div>
          )}
        </section>

        {/* Status panel */}
        <section className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5 sm:p-6">
          <div className="flex items-center gap-2.5 mb-1">
            <Activity className="w-4 h-4 text-orange-500" />
            <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-900 dark:text-white">
              Agent status — what it believes about itself
            </h2>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4">
            Served by <span className="font-mono">GET /tasks/status</span>. “Is sending actually
            off?” is answerable here, without reading logs or trusting a config change took effect.
          </p>

          {!unlocked ? (
            <div className="rounded-xl px-4 py-8 text-center bg-zinc-50/80 dark:bg-white/[0.02] border border-dashed border-zinc-300 dark:border-white/10">
              <Lock className="w-6 h-6 text-zinc-300 dark:text-zinc-600 mx-auto mb-2" />
              <p className="text-sm font-semibold text-zinc-500 dark:text-zinc-400">
                Unlock with the task key to read live status
              </p>
              <p className="text-xs text-zinc-400 dark:text-zinc-500 mt-1">
                The status endpoint is authenticated too — the kill switch state is not public information.
              </p>
            </div>
          ) : (
            <>
              {/* Kill-switch hero */}
              <div
                className={cn(
                  "rounded-2xl border p-5 sm:p-6 mb-4 flex flex-col sm:flex-row sm:items-center gap-4",
                  sendingOn
                    ? "bg-gradient-to-br from-red-50 via-white to-orange-50/50 dark:from-red-950/20 dark:via-neutral-900 dark:to-orange-950/10 border-red-200/60 dark:border-red-500/25"
                    : "bg-gradient-to-br from-emerald-50 via-white to-teal-50/50 dark:from-emerald-950/20 dark:via-neutral-900 dark:to-teal-950/10 border-emerald-200/60 dark:border-emerald-500/25"
                )}
              >
                <div className={cn("p-3 rounded-2xl flex-shrink-0", sendingOn ? "bg-red-100 dark:bg-red-500/15" : "bg-emerald-100 dark:bg-emerald-500/15")}>
                  {sendingOn ? (
                    <Zap className="w-7 h-7 text-red-600 dark:text-red-400" />
                  ) : (
                    <ShieldCheck className="w-7 h-7 text-emerald-600 dark:text-emerald-400" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-3">
                    <span className="text-xl sm:text-2xl font-bold tracking-tight text-zinc-900 dark:text-white">
                      {sendingOn ? "Sending is ENABLED" : "Sending is HALTED"}
                    </span>
                    <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                      SENDING_ENABLED={sendingOn ? "true" : "false"}
                    </span>
                  </div>
                  <p className="text-xs sm:text-sm text-zinc-600 dark:text-zinc-300 mt-1 leading-relaxed">
                    {sendingOn
                      ? "The kill switch is off: the next run will deliver real email + payment links for every allowed contact action. The trigger below will make you confirm this explicitly."
                      : "The kill switch is on: runs still score, gate, and log execution:halted — but send nothing and advance no ladder rung. Flipping it back resumes exactly where the agent left off."}
                  </p>
                </div>
                <div className="flex-shrink-0 text-right">
                  <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                    Mode
                  </div>
                  <div className={cn("text-lg font-bold tabular-nums", dryRun ? "text-amber-600 dark:text-amber-400" : "text-zinc-900 dark:text-white")}>
                    {dryRun ? "DRY_RUN" : "LIVE"}
                  </div>
                  <div className="text-[11px] text-zinc-500 dark:text-zinc-400">
                    {dryRun ? "caps exercised, ladder simulated" : "real delivery path"}
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-3">
                <Tile label="Batch cap" value={formatInt(status?.batch_max_invoices)} sub="max invoices / run" />
                <Tile label="Cron cadence" value={humanizeInterval(status?.expected_interval_seconds)} sub="expected trigger interval" />
                <Tile label="Open invoices" value={formatInt(status?.open_invoices)} sub="in the book now" />
                <Tile label="Pending promises" value={formatInt(status?.pending_promises)} sub="watched deadlines" tone={(status?.pending_promises ?? 0) > 0 ? "warn" : "neutral"} />
                <Tile label="Replies to review" value={formatInt(status?.replies_awaiting_review)} sub="need human discretion" tone={(status?.replies_awaiting_review ?? 0) > 0 ? "warn" : "good"} />
                <Link
                  href="/inbox"
                  className="rounded-xl px-4 py-3.5 bg-indigo-50/60 dark:bg-indigo-500/10 border border-indigo-200/50 dark:border-indigo-500/20 hover:bg-indigo-100 dark:hover:bg-indigo-500/15 transition-colors flex flex-col justify-center"
                >
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-indigo-600 dark:text-indigo-400">
                    Review desk
                  </span>
                  <span className="text-sm font-bold text-indigo-700 dark:text-indigo-300 inline-flex items-center gap-1 mt-0.5">
                    Open inbox <ArrowRight className="w-3.5 h-3.5" />
                  </span>
                </Link>
              </div>
            </>
          )}
        </section>

        {/* Workflow pipeline — hover any stage for what / decision / why */}
        <section className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5 sm:p-6">
          <div className="flex items-center gap-2.5 mb-1">
            <FileText className="w-4 h-4 text-indigo-500" />
            <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-900 dark:text-white">
              What one run actually does
            </h2>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mb-4 max-w-3xl leading-relaxed">
            Two sweeps, in this order — promises before scoring, because the order matters.
            Hover or tab-focus any stage to see what happens there, what decision it takes, and
            why it works that way. Metrics below each stage come from the most recent run.
          </p>
          <div className="overflow-x-auto -mx-1 px-1 pb-1">
            <div className="flex items-stretch gap-2 min-w-[860px]">
              {STAGES.map((spec, i) => (
                <React.Fragment key={spec.id}>
                  <StageNode
                    spec={spec}
                    index={i}
                    state={stageState}
                    metric={stageMetrics[spec.id] ?? "—"}
                    align={i === 0 ? "left" : i === STAGES.length - 1 ? "right" : "center"}
                  />
                  {i < STAGES.length - 1 && (
                    <div className="flex items-center flex-shrink-0 px-0.5" aria-hidden>
                      <ChevronRight className={cn("w-4 h-4", runState === "running" ? "text-orange-400 animate-pulse" : "text-zinc-300 dark:text-zinc-600")} />
                    </div>
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>
          {runState === "running" && (
            <div className="mt-4 rounded-xl px-4 py-3 bg-orange-50/70 dark:bg-orange-500/[0.07] border border-orange-200/50 dark:border-orange-500/20 flex items-center gap-3">
              <RefreshCw className="w-4 h-4 text-orange-500 animate-spin flex-shrink-0" />
              <p className="text-xs sm:text-sm text-orange-900/80 dark:text-orange-200/90 tabular-nums">
                <span className="font-bold">Run in flight — {formatDuration(elapsedMs)} elapsed.</span>{" "}
                Stages above pulse as the backend works through them. There is no server-side cancel:
                closing this page will not stop the run.
              </p>
            </div>
          )}
        </section>

        {/* Trigger */}
        <section className="rounded-2xl border border-red-200/60 dark:border-red-500/25 bg-gradient-to-br from-white via-white to-red-50/40 dark:from-neutral-900 dark:via-neutral-900 dark:to-red-950/10 shadow-sm p-5 sm:p-6">
          <div className="flex flex-col lg:flex-row lg:items-center gap-4">
            <div className="flex items-center gap-3.5 flex-1 min-w-0">
              <div className="p-2.5 rounded-xl bg-red-100 dark:bg-red-500/15 flex-shrink-0">
                <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400" />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-bold uppercase tracking-wider text-zinc-900 dark:text-white">
                  Trigger a run — privileged action
                </h2>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
                  {unlocked
                    ? sendingOn
                      ? "Sending is ENABLED: this will deliver real email + payment links to up to the batch cap. Confirmation is mandatory."
                      : "Kill switch is OFF: safe to trigger — the run will score, gate, and log, but send nothing."
                    : "Unlock with the task key first. An open trigger endpoint is an open way to mail an entire customer book."}
                </p>
              </div>
            </div>
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2.5 flex-shrink-0">
              <label className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-zinc-50 dark:bg-white/[0.03] border border-zinc-200 dark:border-white/10 text-sm">
                <span className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                  Limit
                </span>
                <input
                  value={limitInput}
                  onChange={(e) => setLimitInput(e.target.value.replace(/[^0-9]/g, "").slice(0, 4))}
                  placeholder={String(batchCap)}
                  disabled={!unlocked || runState === "running"}
                  inputMode="numeric"
                  className="w-20 bg-transparent font-mono font-bold text-sm text-zinc-900 dark:text-white tabular-nums placeholder:text-zinc-400 focus:outline-none disabled:opacity-50"
                />
              </label>
              <button
                onClick={() => {
                  setAckChecked(false);
                  setRunState("confirm");
                }}
                disabled={!unlocked || runState === "running"}
                className="inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-red-500 to-orange-500 hover:from-red-600 hover:to-orange-600 text-white shadow-md hover:shadow-lg shadow-red-500/20 transition-all active:scale-[0.97] disabled:opacity-50 disabled:active:scale-100"
              >
                <PlayCircle className="w-4 h-4" />
                {runState === "running" ? "Running…" : "Trigger a run"}
              </button>
            </div>
          </div>

          {/* Result */}
          <div className="mt-4">
            {runState === "error" && runError && errorCard(runError)}
            {(runState === "done" || (runState !== "running" && result)) && result && (
              <motion.div
                initial={shouldReduce ? { opacity: 1 } : { opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.4, ease: "easeOut" }}
                className={cn(
                  "rounded-2xl border p-5 sm:p-6",
                  result.ran
                    ? "bg-white dark:bg-neutral-900 border-zinc-200 dark:border-white/10"
                    : "bg-amber-50/50 dark:bg-amber-500/[0.05] border-amber-200/60 dark:border-amber-500/25"
                )}
              >
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-5">
                  <div className="flex items-center gap-2.5">
                    {result.ran ? (
                      <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                    ) : (
                      <Info className="w-5 h-5 text-amber-500" />
                    )}
                    <h3 className="text-base font-bold text-zinc-900 dark:text-white">
                      {result.ran ? "Run completed" : "Run skipped — another run is already in progress"}
                    </h3>
                  </div>
                  <div className="flex items-center gap-2 text-[11px] font-mono text-zinc-500 dark:text-zinc-400 tabular-nums">
                    <Clock className="w-3.5 h-3.5" />
                    {result.started_at ? new Date(result.started_at).toLocaleString("en-IN") : "—"}
                    <span>·</span>
                    <span>took {formatDuration(durationMs)}</span>
                  </div>
                </div>

                {!result.ran ? (
                  <div className="rounded-xl px-4 py-3.5 bg-amber-100/60 dark:bg-amber-500/10 border border-amber-200/60 dark:border-amber-500/20 flex items-start gap-3">
                    <Info className="w-5 h-5 text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" />
                    <div>
                      <p className="text-sm font-bold text-amber-900 dark:text-amber-200">
                        No-op by design, not a failure.
                      </p>
                      <p className="text-xs text-amber-800/90 dark:text-amber-300/90 mt-0.5 leading-relaxed font-mono">
                        {result.skipped_reason || "The advisory lock was already held."}
                      </p>
                      <p className="text-xs text-amber-800/80 dark:text-amber-300/80 mt-1">
                        Wait for the in-flight run to release <span className="font-mono">recoup:batch-run</span>, then trigger again if needed.
                      </p>
                    </div>
                  </div>
                ) : (
                  <>
                    {result.sending_halted && (
                      <div className="rounded-xl px-4 py-3 mb-4 bg-amber-100/60 dark:bg-amber-500/10 border border-amber-200/60 dark:border-amber-500/20 flex items-start gap-3">
                        <ShieldAlert className="w-5 h-5 text-amber-600 dark:text-amber-400 mt-0.5 flex-shrink-0" />
                        <p className="text-xs sm:text-sm text-amber-900 dark:text-amber-200 leading-relaxed">
                          <span className="font-bold">Kill switch was off for this run.</span>{" "}
                          Nothing was sent and no ladder rung advanced — scored, gated, and logged only.
                        </p>
                      </div>
                    )}
                    <p className="text-[11px] font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400 mb-2">
                      Promise sweep
                    </p>
                    <div className="grid grid-cols-3 gap-2.5 mb-5">
                      <CountCell label="Checked" value={result.promises_checked} />
                      <CountCell label="Broken" value={result.promises_broken} accent="text-red-600 dark:text-red-400" />
                      <CountCell label="Kept" value={result.promises_kept} accent="text-emerald-600 dark:text-emerald-400" />
                    </div>
                    <p className="text-[11px] font-bold uppercase tracking-widest text-zinc-500 dark:text-zinc-400 mb-2">
                      Decision cycles
                    </p>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                      <CountCell label="Considered" value={result.invoices_considered} />
                      <CountCell label="Scored" value={result.scored} />
                      <CountCell label="Acted" value={result.acted} accent="text-emerald-600 dark:text-emerald-400" />
                      <CountCell label="Blocked by policy" value={result.blocked_by_policy} accent="text-amber-600 dark:text-amber-400" />
                      <CountCell label="Left alone" value={result.left_alone} />
                      <CountCell label="Delivery failed" value={result.delivery_failed} accent={result.delivery_failed > 0 ? "text-red-600 dark:text-red-400" : undefined} />
                      <CountCell label="Handed off" value={result.handed_off} />
                      <CountCell label="Errors" value={result.errors.length} accent={result.errors.length > 0 ? "text-red-600 dark:text-red-400" : undefined} />
                    </div>
                    {result.errors.length > 0 && (
                      <div className="mt-4 rounded-xl p-4 bg-red-50/60 dark:bg-red-500/[0.06] border border-red-200/50 dark:border-red-500/20 max-h-40 overflow-y-auto">
                        <p className="text-[11px] font-bold uppercase tracking-widest text-red-600 dark:text-red-400 mb-2">
                          Per-invoice errors (run continued past each one)
                        </p>
                        <ul className="space-y-1.5">
                          {result.errors.map((e, i) => (
                            <li key={i} className="text-xs font-mono text-red-800/90 dark:text-red-300/90 break-words">
                              {e}
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </>
                )}
              </motion.div>
            )}
            {runState === "idle" && !result && (
              <p className="text-xs text-zinc-400 dark:text-zinc-500 text-center py-2">
                No run triggered yet this session — the most recent result will appear here.
              </p>
            )}
          </div>
        </section>

        {/* Scheduler wiring + safety model */}
        <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <div className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5">
            <div className="flex items-center gap-2 mb-2">
              <Clock className="w-4 h-4 text-indigo-500" />
              <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Schedule lives outside</h3>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              Render Cron, a GitHub Actions schedule, or a Kubernetes CronJob calls{" "}
              <span className="font-mono text-zinc-700 dark:text-zinc-300">POST /tasks/run-batch</span>{" "}
              every {humanizeInterval(status?.expected_interval_seconds)}. An in-process scheduler would
              double-fire the moment a second replica starts — so there isn&apos;t one.
            </p>
            <pre className="mt-3 p-3 rounded-xl bg-zinc-950 dark:bg-black border border-zinc-800 font-mono text-[11px] leading-relaxed text-zinc-300 overflow-x-auto">
              {`curl -X POST "${"$"}API/tasks/run-batch?limit=${effectiveLimit}" \\\n  -H "Authorization: Bearer ${unlocked ? maskKey(taskKey) : "••••••"}"`}
            </pre>
          </div>
          <div className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5">
            <div className="flex items-center gap-2 mb-2">
              <Lock className="w-4 h-4 text-emerald-500" />
              <h3 className="text-sm font-bold text-zinc-900 dark:text-white">One lock, no queue</h3>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              Every run contends for <span className="font-mono text-zinc-700 dark:text-zinc-300">recoup:batch-run</span> via{" "}
              <span className="font-mono text-zinc-700 dark:text-zinc-300">pg_try_advisory_lock</span> —
              session-scoped, so a crashed run releases it instead of needing cleanup. A colliding trigger
              returns <span className="font-mono text-zinc-700 dark:text-zinc-300">ran=false</span>, rendered
              above as “already in progress”, never as an error.
            </p>
          </div>
          <div className="rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm p-5">
            <div className="flex items-center gap-2 mb-2">
              <MinusCircle className="w-4 h-4 text-amber-500" />
              <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Kill switch ≠ dry run</h3>
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              <span className="font-mono text-zinc-700 dark:text-zinc-300">DRY_RUN</span> is a development
              mode that still exercises caps and the ladder.{" "}
              <span className="font-mono text-zinc-700 dark:text-zinc-300">SENDING_ENABLED=false</span> is
              the operational stop: nothing sent, nothing advanced, resumable with no redeploy and no code change.
            </p>
          </div>
        </section>

        {/* Phase 2 note */}
        <section className="rounded-xl px-4 py-3.5 bg-zinc-50/70 dark:bg-white/[0.02] border border-dashed border-zinc-300 dark:border-white/10 flex items-start gap-3">
          <History className="w-[18px] h-[18px] text-zinc-400 mt-0.5 flex-shrink-0" />
          <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
            <span className="font-bold text-zinc-700 dark:text-zinc-200">Phase 2 — run history.</span>{" "}
            v1 shows only the most recent run&apos;s result in-page (kept in this tab&apos;s session).
            A real history needs the backend to persist <span className="font-mono">RunSummary</span> rows
            somewhere queryable — flagged rather than built against nothing.
          </p>
        </section>

        {/* Confirmation modal */}
        <AnimatePresence>
          {runState === "confirm" && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-zinc-950/60 backdrop-blur-sm"
              onClick={() => setRunState(result ? "done" : "idle")}
            >
              <motion.div
                initial={shouldReduce ? { opacity: 1 } : { opacity: 0, scale: 0.96, y: 12 }}
                animate={{ opacity: 1, scale: 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.96, y: 12 }}
                transition={{ duration: 0.25, ease: "easeOut" }}
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-label="Confirm batch run"
                className="w-full max-w-lg rounded-2xl bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-2xl p-6"
              >
                <div className="flex items-start gap-3.5 mb-4">
                  <div className={cn("p-2.5 rounded-xl flex-shrink-0", sendingOn ? "bg-red-100 dark:bg-red-500/15" : "bg-emerald-100 dark:bg-emerald-500/15")}>
                    {sendingOn ? (
                      <AlertTriangle className="w-5 h-5 text-red-600 dark:text-red-400" />
                    ) : (
                      <ShieldCheck className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
                    )}
                  </div>
                  <div>
                    <h3 className="text-lg font-bold text-zinc-900 dark:text-white">
                      Trigger a batch run?
                    </h3>
                    <p className="text-xs font-mono text-zinc-500 dark:text-zinc-400 mt-0.5">
                      POST /tasks/run-batch?limit={effectiveLimit} · key {maskKey(taskKey)}
                    </p>
                  </div>
                </div>

                <ul className="space-y-2 text-sm text-zinc-600 dark:text-zinc-300 mb-4">
                  <li className="flex gap-2.5">
                    <span className="font-mono font-bold text-zinc-400">1.</span>
                    Promise sweep over {formatInt(status?.pending_promises)} pending promises — lapsed ones break and re-arm escalation.
                  </li>
                  <li className="flex gap-2.5">
                    <span className="font-mono font-bold text-zinc-400">2.</span>
                    Decision cycles over up to {formatInt(effectiveLimit)} open invoices (score → gate → act), bounded by the batch cap.
                  </li>
                  <li className="flex gap-2.5">
                    <span className="font-mono font-bold text-zinc-400">3.</span>
                    Ledger commit + RunSummary. If another run holds the lock, this is a harmless no-op.
                  </li>
                </ul>

                {sendingOn ? (
                  <div className="rounded-xl px-4 py-3 mb-4 bg-red-50 dark:bg-red-500/10 border border-red-200/60 dark:border-red-500/25 flex items-start gap-2.5">
                    <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0" />
                    <p className="text-xs sm:text-sm text-red-900 dark:text-red-200 leading-relaxed">
                      <span className="font-bold">Sending is ENABLED — real emails and Razorpay payment links will go out</span>{" "}
                      for every allowed contact action in this run{dryRun ? ", unless DRY_RUN intercepts delivery" : ""}.
                    </p>
                  </div>
                ) : (
                  <div className="rounded-xl px-4 py-3 mb-4 bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200/60 dark:border-emerald-500/25 flex items-start gap-2.5">
                    <ShieldCheck className="w-4 h-4 text-emerald-600 dark:text-emerald-400 mt-0.5 flex-shrink-0" />
                    <p className="text-xs sm:text-sm text-emerald-900 dark:text-emerald-200 leading-relaxed">
                      Kill switch is OFF — this run will score, gate, and log, but <span className="font-bold">send nothing and advance nothing</span>.
                    </p>
                  </div>
                )}

                {sendingOn && (
                  <label className="flex items-start gap-2.5 mb-5 cursor-pointer rounded-xl px-3 py-2.5 bg-zinc-50 dark:bg-white/[0.03] border border-zinc-200 dark:border-white/10">
                    <input
                      type="checkbox"
                      checked={ackChecked}
                      onChange={(e) => setAckChecked(e.target.checked)}
                      className="mt-0.5 h-4 w-4 rounded accent-red-500"
                    />
                    <span className="text-xs text-zinc-600 dark:text-zinc-300 leading-relaxed">
                      I understand this sends real customer communication to up to {formatInt(effectiveLimit)} invoices.
                    </span>
                  </label>
                )}

                <div className="flex flex-col-reverse sm:flex-row sm:justify-end gap-2.5">
                  <button
                    onClick={() => setRunState(result ? "done" : "idle")}
                    className="px-5 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-800 hover:bg-zinc-50 dark:hover:bg-neutral-700 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => void confirmAndRun()}
                    disabled={sendingOn && !ackChecked}
                    className="inline-flex items-center justify-center gap-2 px-6 py-2.5 rounded-xl text-sm font-bold bg-gradient-to-br from-red-500 to-orange-500 hover:from-red-600 hover:to-orange-600 text-white shadow-md transition-all active:scale-[0.97] disabled:opacity-50"
                  >
                    Confirm & run
                    <ArrowUpRight className="w-4 h-4" />
                  </button>
                </div>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </main>
  );
}
