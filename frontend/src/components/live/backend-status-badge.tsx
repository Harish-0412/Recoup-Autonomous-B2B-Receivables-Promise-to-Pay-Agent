"use client";

import React, { useEffect, useState } from "react";
import { checkBackendHealth } from "@/lib/api";
import { RefreshCw } from "lucide-react";

export function BackendStatusBadge() {
  const [status, setStatus] = useState<{ ok: boolean; latencyMs: number; service?: string } | null>(null);
  const [checking, setChecking] = useState(false);

  const verifyHealth = async () => {
    setChecking(true);
    const res = await checkBackendHealth();
    setStatus(res);
    setChecking(false);
  };

  useEffect(() => {
    verifyHealth();
    const interval = setInterval(verifyHealth, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-mono border backdrop-blur-md transition-all shadow-sm bg-white/80 dark:bg-black/60 border-zinc-200 dark:border-white/10">
      <span className="relative flex h-2 w-2">
        {status?.ok ? (
          <>
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
            <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
          </>
        ) : (
          <span className="relative inline-flex rounded-full h-2 w-2 bg-amber-500"></span>
        )}
      </span>

      <span className="font-semibold text-zinc-800 dark:text-zinc-200">
        {status?.ok ? "FastAPI Live" : "Demo Simulation"}
      </span>

      {status?.ok && (
        <span className="text-[10px] text-zinc-500">
          ({status.latencyMs}ms)
        </span>
      )}

      <button
        onClick={verifyHealth}
        disabled={checking}
        title="Check Backend Health"
        className="text-zinc-400 hover:text-zinc-600 dark:hover:text-white transition-colors"
      >
        <RefreshCw className={`h-3 w-3 ${checking ? "animate-spin" : ""}`} />
      </button>
    </div>
  );
}
