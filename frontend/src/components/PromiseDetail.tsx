"use client";

import React from "react";
import { PromiseOut } from "@/lib/api";
import { BrokenPromiseRiskBadge } from "@/components/BrokenPromiseRiskBadge";
import { cn } from "@/lib/utils";
import { Clock, Calendar, CheckCircle2, XCircle, AlertCircle, Ban } from "lucide-react";

export interface PromiseDetailProps {
  promise: PromiseOut;
  className?: string;
}

function promiseBadge(status: PromiseOut["status"]): string {
  switch (status) {
    case "KEPT":
      return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-500/20";
    case "BROKEN":
      return "bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20";
    case "SUPERSEDED":
      return "bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border-zinc-500/20";
    default:
      return "bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20";
  }
}

function promiseBorder(status: PromiseOut["status"]): string {
  switch (status) {
    case "KEPT":
      return "border-l-emerald-500";
    case "BROKEN":
      return "border-l-red-500";
    case "SUPERSEDED":
      return "border-l-zinc-400";
    case "PENDING":
    default:
      return "border-l-amber-500";
  }
}

export function PromiseDetail({ promise, className }: PromiseDetailProps) {
  const statusIcon = () => {
    switch (promise.status) {
      case "KEPT":
        return <CheckCircle2 className="h-4 w-4 text-emerald-500" />;
      case "BROKEN":
        return <XCircle className="h-4 w-4 text-red-500" />;
      case "SUPERSEDED":
        return <Ban className="h-4 w-4 text-zinc-400" />;
      default:
        return <Clock className="h-4 w-4 text-amber-500" />;
    }
  };

  return (
    <div
      className={cn(
        "rounded-xl border border-zinc-200 dark:border-white/10 border-l-4 bg-zinc-50/60 dark:bg-black p-4 space-y-3",
        promiseBorder(promise.status),
        className
      )}
    >
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2">
          {statusIcon()}
          <span className="font-mono text-xs font-bold">{promise.promise_id}</span>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border",
              promiseBadge(promise.status)
            )}
          >
            {promise.status}
          </span>
          {promise.broken_promise_score !== undefined && promise.broken_promise_score !== null && (
            <BrokenPromiseRiskBadge score={promise.broken_promise_score} showBar={true} />
          )}
        </div>
      </div>

      <div>
        <p className="text-xl font-extrabold text-zinc-900 dark:text-zinc-50">
          {promise.currency} {Number(promise.promised_amount).toLocaleString("en-IN")}{" "}
          <span className="text-xs font-normal text-zinc-500 dark:text-zinc-400">
            by {promise.promised_date}
          </span>
        </p>
      </div>

      <div className="flex items-center justify-between text-[10px] font-mono text-zinc-400 border-t border-zinc-100 dark:border-zinc-800/60 pt-2 flex-wrap gap-1">
        <span className="flex items-center gap-1">
          <Calendar className="h-3 w-3" />
          Created: {new Date(promise.created_at).toLocaleDateString()}
        </span>
        {promise.resolved_at && (
          <span>Resolved: {new Date(promise.resolved_at).toLocaleDateString()}</span>
        )}
      </div>
    </div>
  );
}

export default PromiseDetail;
