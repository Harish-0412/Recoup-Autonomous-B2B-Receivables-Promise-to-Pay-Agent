"use client";

import React, { useEffect, useState } from "react";
import { fetchReplyReviewQueue, markReplyReviewed, ReplyReviewItem } from "@/lib/api";
import { CheckCircle2, RefreshCw, UserCheck, AlertTriangle } from "lucide-react";

export function HumanReviewDesk() {
  const [items, setItems] = useState<ReplyReviewItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [clearingId, setClearingId] = useState<string | null>(null);

  const loadQueue = async () => {
    setLoading(true);
    const queue = await fetchReplyReviewQueue();
    setItems(queue.items);
    setLoading(false);
  };

  useEffect(() => {
    loadQueue();
  }, []);

  const handleResolve = async (replyId: string) => {
    setClearingId(replyId);
    await markReplyReviewed(replyId);
    setItems((prev) => prev.filter((i) => i.reply_id !== replyId));
    setClearingId(null);
  };

  return (
    <div className="rounded-2xl border border-zinc-200 dark:border-white/10 bg-white dark:bg-black p-6 shadow-sm">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6 pb-4 border-b border-zinc-200 dark:border-white/10">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-orange-500/10 border border-orange-500/20 text-orange-600 dark:text-orange-400">
            <UserCheck className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-base font-bold text-zinc-950 dark:text-white">
                Human-in-the-Loop Review Desk
              </h3>
              <span className="px-2 py-0.5 rounded-full text-xs font-mono font-semibold bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/20">
                {items.length} Pending
              </span>
            </div>
            <p className="text-xs text-zinc-500 mt-0.5">
              Customer replies where ML intent confidence fell below threshold or required supervisor clearance
            </p>
          </div>
        </div>

        <button
          onClick={loadQueue}
          disabled={loading}
          className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-900 hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-700 dark:text-zinc-300 transition-colors self-start sm:self-auto"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh Queue
        </button>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-12 text-zinc-500 gap-2 font-mono text-xs">
          <RefreshCw className="h-5 w-5 animate-spin text-orange-500" />
          <span>Polling inbound review queue...</span>
        </div>
      ) : items.length > 0 ? (
        <div className="space-y-4">
          {items.map((item) => (
            <div
              key={item.reply_id}
              className="p-4 rounded-xl border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-zinc-950/60 hover:border-orange-500/30 transition-all flex flex-col md:flex-row md:items-start justify-between gap-4"
            >
              <div className="space-y-2 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-mono font-bold text-xs text-zinc-900 dark:text-zinc-100">
                    {item.from_email}
                  </span>
                  {item.intent && (
                    <span className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold bg-orange-500/10 text-orange-600 dark:text-orange-400 border border-orange-500/20">
                      {item.intent} ({(item.confidence * 100).toFixed(1)}%)
                    </span>
                  )}
                  <span className="text-[10px] font-mono text-zinc-400">
                    ID: {item.reply_id}
                  </span>
                </div>

                <p className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                  {item.subject}
                </p>

                <div className="p-3 rounded-lg bg-white dark:bg-black/60 border border-zinc-200 dark:border-white/5 text-xs text-zinc-700 dark:text-zinc-300 font-mono leading-relaxed">
                  &quot;{item.body}&quot;
                </div>

                <div className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
                  <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
                  <span>{item.reason}</span>
                </div>
              </div>

              <div className="flex sm:flex-col items-end gap-2 pt-2 md:pt-0">
                <button
                  onClick={() => handleResolve(item.reply_id)}
                  disabled={clearingId === item.reply_id}
                  className="w-full sm:w-auto px-4 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white shadow-sm flex items-center justify-center gap-1.5 transition-colors"
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  {clearingId === item.reply_id ? "Saving..." : "Clear Review"}
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="text-center py-10 rounded-xl bg-zinc-50 dark:bg-zinc-950/40 border border-dashed border-zinc-200 dark:border-white/10 text-zinc-500">
          <CheckCircle2 className="h-8 w-8 text-emerald-500 mx-auto mb-2" />
          <p className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">Review Queue Clear</p>
          <p className="text-xs text-zinc-500 mt-1">All incoming customer replies were resolved autonomously by Stage C / LLM cascade.</p>
        </div>
      )}
    </div>
  );
}
