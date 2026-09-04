"use client";

import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchCashForecast,
  fetchCashForecastCard,
  ForecastApiError,
  type CashWindowForecast,
} from "@/lib/api";
import {
  Wallet,
  RefreshCw,
  Activity,
  Lock,
  Server,
  GraduationCap,
  Info,
} from "lucide-react";
import { cn } from "@/lib/utils";

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

/** Interval bar: p5–p95 band with a median tick, scaled to the book's range. */
function IntervalBar({ stats, scaleMax }: { stats: CashWindowForecast; scaleMax: number }) {
  const pct = (v: number) => `${Math.min(100, Math.max(0, (v / scaleMax) * 100))}%`;
  return (
    <div>
      <div className="relative h-2.5 rounded-full bg-zinc-100 dark:bg-white/5 overflow-visible">
        <div
          className="absolute top-0 h-full rounded-full bg-gradient-to-r from-emerald-400/70 via-emerald-500 to-teal-500"
          style={{ left: pct(stats.p5), width: `calc(${pct(stats.p95)} - ${pct(stats.p5)})` }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 h-4 w-[3px] rounded-full bg-zinc-900 dark:bg-white"
          style={{ left: pct(stats.median) }}
          title={`Median ${formatINR(stats.median)}`}
        />
      </div>
      <div className="flex items-center justify-between mt-1.5 text-[11px] font-mono tabular-nums text-zinc-500 dark:text-zinc-400">
        <span>p5 {formatINR(stats.p5)}</span>
        <span className="font-semibold text-zinc-700 dark:text-zinc-200">
          median {formatINR(stats.median)}
        </span>
        <span>p95 {formatINR(stats.p95)}</span>
      </div>
    </div>
  );
}

function WindowPanel({
  stats,
  scaleMax,
  blurb,
}: {
  stats: CashWindowForecast;
  scaleMax: number;
  blurb: string;
}) {
  return (
    <div className="rounded-xl border border-zinc-200/70 dark:border-white/[0.06] bg-zinc-50/60 dark:bg-white/[0.02] p-5 space-y-4">
      <div className="flex items-baseline justify-between gap-2">
        <h4 className="text-sm font-bold tracking-tight text-zinc-900 dark:text-white">
          Next {stats.window_days} days
        </h4>
        <span className="text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums">
          P(any cash) {(stats.prob_any_cash * 100).toFixed(1)}%
        </span>
      </div>
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
          Expected cash
        </div>
        <div className="text-3xl font-bold tracking-tight text-zinc-900 dark:text-white tabular-nums">
          {formatINR(stats.mean)}
        </div>
      </div>
      <IntervalBar stats={stats} scaleMax={scaleMax} />
      <p className="text-[11px] text-zinc-500 dark:text-zinc-400 leading-relaxed">{blurb}</p>
    </div>
  );
}

export function CashForecastWidget() {
  const {
    data: forecast,
    isLoading,
    isFetching,
    isError,
    error,
    refetch,
  } = useQuery({
    queryKey: ["cash-forecast"],
    queryFn: () => fetchCashForecast(),
    staleTime: 30_000,
    retry: 1,
  });

  const { data: card } = useQuery({
    queryKey: ["cash-forecast-card"],
    queryFn: fetchCashForecastCard,
    staleTime: 120_000,
    retry: 1,
  });

  const status = error instanceof ForecastApiError ? error.status : undefined;
  const windows = forecast?.windows ?? [];
  const w7 = windows.find((w) => w.window_days === 7);
  const w30 = windows.find((w) => w.window_days === 30);
  const scaleMax = Math.max(w30?.p95 ?? 0, forecast?.at_risk_value ?? 0, 1);
  const coverage30 = card?.coverage?.["30"] ?? card?.test_coverage?.["30"];
  const bias30 = card?.bias?.["30"] ?? card?.test_bias?.["30"];

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-neutral-900 p-6 shadow-sm space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-zinc-100 dark:border-zinc-800 pb-4">
        <div className="flex items-center gap-2">
          <span className="p-2 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
            <Wallet className="h-5 w-5" />
          </span>
          <div>
            <h3 className="text-base font-bold tracking-tight">Receivables Cash Forecast</h3>
            <p className="text-xs text-zinc-500">
              Monte Carlo · 10,000 draws · recovery probability × per-segment payment lags
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {typeof coverage30 === "number" && typeof bias30 === "number" ? (
            <div
              className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
              title={`Backtested on held-out invoices: ${(coverage30 * 100).toFixed(1)}% of books landed inside the 90% interval; mean bias ${bias30 >= 0 ? "+" : ""}${(bias30 * 100).toFixed(1)}%`}
            >
              <Activity className="h-3.5 w-3.5" />
              <span>
                {(coverage30 * 100).toFixed(0)}% cover · {bias30 >= 0 ? "+" : ""}
                {(bias30 * 100).toFixed(1)}% bias
              </span>
            </div>
          ) : (
            forecast && (
              <div className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-mono bg-zinc-500/10 text-zinc-500 dark:text-zinc-400 border border-zinc-500/20">
                <GraduationCap className="h-3.5 w-3.5" />
                <span>{forecast.lag_model_version}</span>
              </div>
            )
          )}
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-2 rounded-lg border border-zinc-200 dark:border-white/10 text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-white/5 transition-colors disabled:opacity-50"
            title="Re-run forecast on the current book"
          >
            <RefreshCw className={cn("h-4 w-4", isFetching && "animate-spin")} />
          </button>
        </div>
      </div>

      {/* Body */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 animate-pulse">
          {[0, 1].map((i) => (
            <div key={i} className="h-56 rounded-xl bg-zinc-100 dark:bg-white/5" />
          ))}
        </div>
      ) : isError || !forecast || !w7 || !w30 ? (
        <div className="rounded-xl border border-dashed border-zinc-300 dark:border-white/10 bg-zinc-50/60 dark:bg-white/[0.02] px-4 py-8 text-center">
          {status === 401 ? (
            <>
              <Lock className="h-6 w-6 text-zinc-400 mx-auto mb-2" />
              <p className="text-sm font-semibold text-zinc-600 dark:text-zinc-300">
                Operator key required
              </p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1 max-w-md mx-auto">
                The forecast reads the live receivables book, so it is bearer-locked like the
                other operator routes. Set the operator key to load it.
              </p>
            </>
          ) : status === 503 ? (
            <>
              <GraduationCap className="h-6 w-6 text-zinc-400 mx-auto mb-2" />
              <p className="text-sm font-semibold text-zinc-600 dark:text-zinc-300">
                Forecast model not trained yet
              </p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1 max-w-md mx-auto font-mono">
                python scripts/train_cash_forecast.py
              </p>
            </>
          ) : (
            <>
              <Server className="h-6 w-6 text-zinc-400 mx-auto mb-2" />
              <p className="text-sm font-semibold text-zinc-600 dark:text-zinc-300">
                Backend unreachable
              </p>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                Is the API running? No cached numbers are shown — a forecast must never be stale.
              </p>
            </>
          )}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <WindowPanel
              stats={w7}
              scaleMax={scaleMax}
              blurb="Cash expected to land within a week — the near-certain slice of the book. Narrow intervals here mean timing you can plan payroll against."
            />
            <WindowPanel
              stats={w30}
              scaleMax={scaleMax}
              blurb="Full-horizon recovery: every invoice the recovery model gives a chance, timed by its segment's realised payment lags. The validated window — 83% backtest coverage."
            />
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 rounded-xl bg-zinc-50/70 dark:bg-white/[0.02] border border-zinc-200/60 dark:border-white/[0.06] px-4 py-3">
            <div className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
              <span className="font-semibold text-zinc-700 dark:text-zinc-200">
                {forecast.n_invoices.toLocaleString("en-IN")} invoices
              </span>{" "}
              · {formatINR(forecast.at_risk_value)} at risk · {forecast.draws.toLocaleString("en-IN")} draws ·
              seed {forecast.seed}
            </div>
            <div
              className="flex items-center gap-1.5 text-[11px] text-zinc-500 dark:text-zinc-400"
              title={`P(recovery) from ${forecast.probability_source}${forecast.calibrated ? " (Platt-calibrated)" : ""}; timing from ${forecast.lag_model_version}; ${forecast.scorer_fallbacks} invoices fell back to rules. Generated ${forecast.generated_at}.`}
            >
              <Info className="h-3.5 w-3.5 flex-shrink-0" />
              <span className="font-mono truncate">
                {forecast.probability_source}
                {forecast.calibrated ? " · calibrated" : ""} · {forecast.lag_model_version}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
