"use client";

import React, { useEffect, useState } from "react";
import { fetchAuditTrail, AuditTrailOut } from "@/lib/api";
import { ShieldCheck, Lock, X, CheckCircle2, ChevronRight, FileSpreadsheet, RefreshCw } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

interface AuditLedgerModalProps {
  invoiceId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function AuditLedgerModal({ invoiceId, isOpen, onClose }: AuditLedgerModalProps) {
  const [data, setData] = useState<AuditTrailOut | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isOpen && invoiceId) {
      setLoading(true);
      fetchAuditTrail(invoiceId).then((trail) => {
        setData(trail);
        setLoading(false);
      });
    }
  }, [isOpen, invoiceId]);

  if (!isOpen) return null;

  return (
    <AnimatePresence>
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
        <motion.div
          initial={{ opacity: 0, scale: 0.95, y: 10 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.95, y: 10 }}
          className="relative w-full max-w-3xl max-h-[85vh] flex flex-col rounded-2xl bg-white dark:bg-zinc-950 border border-zinc-200 dark:border-white/10 shadow-2xl overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-center justify-between p-6 border-b border-zinc-200 dark:border-white/10 bg-zinc-50/50 dark:bg-black/40">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-lg bg-orange-500/10 border border-orange-500/20 text-orange-600 dark:text-orange-400">
                <FileSpreadsheet className="h-5 w-5" />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-bold text-lg text-zinc-950 dark:text-white font-mono">
                    Decision Trace Ledger: {invoiceId}
                  </h3>
                  {data?.chain_verified && (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-mono font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                      <ShieldCheck className="h-3.5 w-3.5" />
                      Chain Verified
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-500 mt-0.5">
                  Append-only, SHA-256 hash-chained cryptographic proof of every agent action
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-950 dark:hover:text-white hover:bg-zinc-100 dark:hover:bg-zinc-900 transition-colors"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          {/* Content / Entries */}
          <div className="flex-1 overflow-y-auto p-6 space-y-4 font-mono text-xs">
            {loading ? (
              <div className="flex flex-col items-center justify-center py-16 text-zinc-500 gap-3">
                <RefreshCw className="h-6 w-6 animate-spin text-orange-500" />
                <span>Verifying cryptographic chain...</span>
              </div>
            ) : data && data.entries.length > 0 ? (
              <div className="relative border-l-2 border-orange-500/30 ml-4 pl-6 space-y-6">
                {data.entries.map((entry, idx) => (
                  <div key={entry.seq} className="relative group">
                    {/* Step Marker Dot */}
                    <div className="absolute -left-[31px] top-1.5 h-3.5 w-3.5 rounded-full bg-zinc-100 dark:bg-black border-2 border-orange-500 flex items-center justify-center">
                      <span className="h-1.5 w-1.5 rounded-full bg-orange-500"></span>
                    </div>

                    <div className="p-4 rounded-xl bg-zinc-50 dark:bg-black/50 border border-zinc-200 dark:border-white/10 hover:border-orange-500/30 transition-all">
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-2">
                          <span className="font-bold text-orange-600 dark:text-orange-400">
                            #{entry.seq}
                          </span>
                          <span className="px-2 py-0.5 rounded bg-zinc-200 dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200 font-semibold text-[11px]">
                            {entry.event}
                          </span>
                          <span className="text-[10px] text-zinc-400">
                            actor: {entry.actor}
                          </span>
                        </div>
                        <span className="text-[10px] text-zinc-500">
                          {new Date(entry.recorded_at).toLocaleTimeString()}
                        </span>
                      </div>

                      <p className="text-zinc-700 dark:text-zinc-300 mb-3 font-sans text-xs">
                        {entry.reason}
                      </p>

                      <div className="space-y-1.5 pt-2 border-t border-zinc-200 dark:border-white/5 text-[10px] text-zinc-500">
                        <div className="flex items-center gap-1.5 truncate">
                          <span className="text-zinc-400">prev_hash:</span>
                          <span className="font-mono text-zinc-600 dark:text-zinc-400 truncate">
                            {entry.prev_hash}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 truncate">
                          <span className="text-emerald-500 font-semibold">entry_hash:</span>
                          <span className="font-mono text-emerald-600 dark:text-emerald-400 font-semibold truncate">
                            {entry.entry_hash}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-zinc-500">No trace records found.</div>
            )}
          </div>

          {/* Footer */}
          <div className="p-4 border-t border-zinc-200 dark:border-white/10 bg-zinc-50/80 dark:bg-black/60 flex items-center justify-between text-xs text-zinc-500">
            <span>Guaranteed immutable. Silent edits break the SHA-256 chain.</span>
            <button
              onClick={onClose}
              className="px-4 py-1.5 rounded-lg bg-zinc-200 dark:bg-zinc-800 hover:bg-zinc-300 dark:hover:bg-zinc-700 text-zinc-900 dark:text-white font-semibold transition-colors"
            >
              Close
            </button>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}
