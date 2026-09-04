"use client";

import React, { useMemo } from "react";
import Link from "next/link";
import {
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  flexRender,
} from "@tanstack/react-table";
import type { SortingState, Updater } from "@tanstack/react-table";
import { motion, useReducedMotion, AnimatePresence } from "framer-motion";
import {
  ChevronUp,
  ChevronDown,
  ChevronsUpDown,
  Calendar,
  Mail,
  Clock,
  ShieldCheck,
  AlertTriangle,
  Sparkles,
  ArrowRight,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { InvoiceListItem } from "@/lib/api";

function formatINR(amount: number): string {
  const abs = Math.abs(amount);
  if (abs >= 10000000) return `₹${(amount / 10000000).toFixed(2)} Cr`;
  if (abs >= 100000) return `₹${(amount / 100000).toFixed(1)}L`;
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function toRelativeDays(isoDate: string | null, daysOverdue?: number): string {
  if (daysOverdue != null) {
    if (daysOverdue === 0) return "Today";
    return `${daysOverdue} d overdue`;
  }
  if (!isoDate) return "Never";
  const d = new Date(isoDate);
  const ms = Date.now() - d.getTime();
  const days = Math.floor(ms / (1000 * 60 * 60 * 24));
  if (days <= 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days}d ago`;
  return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

function TierBadge({ tier }: { tier: "WAIT" | "REMIND" | "ESCALATE" | null }) {
  const map = {
    ESCALATE: {
      base: "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border border-red-200/60 dark:border-red-500/20",
      dot: "bg-red-500",
    },
    REMIND: {
      base: "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-200/60 dark:border-blue-500/20",
      dot: "bg-blue-500",
    },
    WAIT: {
      base: "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20",
      dot: "bg-amber-500",
    },
  } as const;
  if (!tier)
    return (
      <span className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-bold bg-zinc-100 dark:bg-white/5 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-white/10">
        <span className="w-1.5 h-1.5 rounded-full bg-zinc-400" />
        N/A
      </span>
    );
  const style = map[tier];
  return (
    <span className={cn("inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-bold tracking-wide uppercase", style.base)}>
      <span className={cn("w-1.5 h-1.5 rounded-full", style.dot)} />
      {tier}
    </span>
  );
}

function EscalationBadge({ state }: { state: string }) {
  const map: Record<string, string> = {
    monitoring: "bg-zinc-100 dark:bg-white/5 text-zinc-600 dark:text-zinc-300 border-zinc-200 dark:border-white/10",
    reminded: "bg-sky-50 dark:bg-sky-500/10 text-sky-700 dark:text-sky-400 border-sky-200/60 dark:border-sky-500/20",
    escalated: "bg-rose-50 dark:bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-200/60 dark:border-rose-500/20",
    human_handoff: "bg-violet-50 dark:bg-violet-500/10 text-violet-700 dark:text-violet-400 border-violet-200/60 dark:border-violet-500/20",
    closed: "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-200/60 dark:border-emerald-500/20",
  };
  const label = state === "human_handoff" ? "Human Handoff" : state.charAt(0).toUpperCase() + state.slice(1);
  return (
    <span className={cn("inline-flex items-center px-2 py-1 rounded-lg text-[11px] font-semibold border", map[state] ?? map.monitoring)}>
      {label}
    </span>
  );
}

function PromisePill({ promiseStatus, dueDate }: { promiseStatus: string | null; dueDate: string | null }) {
  if (promiseStatus !== "PENDING") return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const due = dueDate ? new Date(dueDate) : null;
  let tone = "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border-emerald-200/60 dark:border-emerald-500/20";
  let daysLeft: number | null = null;
  if (due) {
    due.setHours(0, 0, 0, 0);
    daysLeft = Math.round((due.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
    if (daysLeft < 0) tone = "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border-red-200/60 dark:border-red-500/20";
    else if (daysLeft <= 2) tone = "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-200/60 dark:border-amber-500/20";
  }
  const label = dueDate
    ? daysLeft != null
      ? daysLeft < 0
        ? `Broken (${Math.abs(daysLeft)} d overdue)`
        : daysLeft === 0
          ? "Due today"
          : `Due in ${daysLeft} d`
      : dueDate
    : "Promised";

  return (
    <span className={cn("inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold border", tone)}>
      <ShieldCheck className="w-3 h-3" />
      {label}
    </span>
  );
}

function PRow({ p_recovery }: { p_recovery: number | null }) {
  if (p_recovery == null) return <span className="text-zinc-400 dark:text-zinc-500 text-xs">—</span>;
  const pct = p_recovery;
  let tone = "text-emerald-600 dark:text-emerald-400";
  let barTone = "bg-emerald-500";
  if (pct < 0.45) { tone = "text-red-600 dark:text-red-400"; barTone = "bg-red-500"; }
  else if (pct < 0.75) { tone = "text-amber-600 dark:text-amber-400"; barTone = "bg-amber-500"; }
  return (
    <div className="flex flex-col gap-1 min-w-[80px]">
      <span className={cn("text-sm font-bold tabular-nums", tone)}>
        {(pct * 100).toFixed(0)}%
      </span>
      <div className="h-1.5 w-full bg-zinc-100 dark:bg-white/5 rounded-full overflow-hidden">
        <div className={cn("h-full rounded-full", barTone)} style={{ width: `${Math.round(pct * 100)}%` }} />
      </div>
    </div>
  );
}

interface QueueTableProps {
  data: InvoiceListItem[];
  sort: string;
  sortDir: "asc" | "desc";
  onSortChange: (sort: string, sortDir: "asc" | "desc") => void;
  skeleton?: boolean;
}

export default function QueueTable({ data, sort, sortDir, onSortChange, skeleton = false }: QueueTableProps) {
  const shouldReduce = useReducedMotion();

  const initialSorting: SortingState = sort === "expected_value" || sort === "ev" || sort == null
    ? [{ id: "expected_value", desc: sortDir === "desc" }]
    : [{ id: sort, desc: sortDir === "desc" }];
  const [sorting, setSorting] = React.useState<SortingState>(initialSorting);

  React.useEffect(() => {
    const id = sort === "ev" ? "expected_value" : sort;
    setSorting([{ id, desc: sortDir === "desc" }]);
  }, [sort, sortDir]);

  const handleSortingChange: ((updater: Updater<SortingState>) => void) = (updater) => {
    setSorting((prev) => {
      const next = typeof updater === "function" ? updater(prev) : updater;
      if (next[0]) {
        onSortChange(next[0].id, next[0].desc ? "desc" : "asc");
      }
      return next;
    });
  };

  const columns = useMemo(() => [
    {
      accessorKey: "invoice_id",
      id: "invoice_id",
      header: "Invoice ID",
      sortingFn: "alphanumeric",
      size: 120,
      cell: ({ row }: { row: { original: InvoiceListItem } }) => {
        const it = row.original;
        return (
          <Link
            href={`/invoices/${it.invoice_id}`}
            className="font-mono font-bold text-[13px] text-indigo-600 dark:text-indigo-400 hover:underline underline-offset-2"
          >
            {it.invoice_id}
          </Link>
        );
      },
    },
    {
      accessorKey: "customer_name",
      id: "customer_name",
      header: "Customer",
      sortingFn: "alphanumeric",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => {
        const it = row.original;
        return (
          <div className="min-w-0 max-w-[240px]">
            <div className="text-sm font-semibold text-zinc-900 dark:text-white truncate">{it.customer_name}</div>
            <div className="text-[11px] font-mono text-zinc-500 dark:text-zinc-400 truncate">{it.customer_id}</div>
          </div>
        );
      },
    },
    {
      accessorKey: "outstanding",
      id: "outstanding",
      header: "Outstanding",
      sortingFn: "basic",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => {
        const it = row.original;
        return (
          <div className="tabular-nums">
            <div className="text-sm font-bold text-zinc-900 dark:text-white">{formatINR(it.outstanding)}</div>
            <div className="text-[10px] text-zinc-500 dark:text-zinc-400 font-mono">of {formatINR(it.amount)}</div>
          </div>
        );
      },
    },
    {
      accessorKey: "expected_value",
      id: "expected_value",
      header: "EV (Priority)",
      sortingFn: "basic",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => {
        const it = row.original;
        return (
          <div className="tabular-nums">
            {it.expected_value != null ? (
              <>
                <div className="text-sm font-bold text-zinc-900 dark:text-white">{formatINR(it.expected_value)}</div>
                <Sparkles className="w-3 h-3 text-indigo-400 mt-0.5" />
              </>
            ) : (
              <span className="text-zinc-400 dark:text-zinc-500 text-xs">—</span>
            )}
          </div>
        );
      },
    },
    {
      accessorKey: "days_overdue",
      id: "days_overdue",
      header: "Overdue",
      sortingFn: "basic",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => {
        const it = row.original;
        const tone = it.days_overdue > 21
          ? "text-red-600 dark:text-red-400"
          : it.days_overdue > 7
            ? "text-amber-600 dark:text-amber-400"
            : "text-zinc-700 dark:text-zinc-300";
        return (
          <div className="flex items-center gap-1.5">
            <Calendar className={cn("w-3.5 h-3.5 flex-shrink-0", tone)} />
            <span className={cn("text-sm font-semibold tabular-nums", tone)}>
              {it.days_overdue > 0 ? `${it.days_overdue}d` : "On time"}
            </span>
          </div>
        );
      },
    },
    {
      accessorKey: "tier",
      id: "tier",
      header: "Tier",
      sortingFn: "basic",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => (
        <TierBadge tier={row.original.tier} />
      ),
    },
    {
      accessorKey: "escalation_state",
      id: "escalation_state",
      header: "Stage",
      sortingFn: "alphanumeric",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => (
        <EscalationBadge state={row.original.escalation_state} />
      ),
    },
    {
      accessorKey: "p_recovery",
      id: "p_recovery",
      header: "P(Recovery)",
      sortingFn: "basic",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => <PRow p_recovery={row.original.p_recovery} />,
    },
    {
      accessorKey: "last_contact_at",
      id: "last_contact_at",
      header: "Last Contact",
      sortingFn: "datetime",
      cell: ({ row }: { row: { original: InvoiceListItem } }) => {
        const it = row.original;
        return (
          <div className="flex items-center gap-1.5 min-w-[100px]">
            <Mail className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0" />
            <span className="text-xs font-medium text-zinc-600 dark:text-zinc-300 tabular-nums whitespace-nowrap">
              {toRelativeDays(it.last_contact_at)}
            </span>
          </div>
        );
      },
    },
    {
      accessorKey: "promise_status",
      id: "promise_status",
      header: "Promise",
      enableSorting: false,
      cell: ({ row }: { row: { original: InvoiceListItem } }) => (
        <PromisePill promiseStatus={row.original.promise_status} dueDate={row.original.promise_due_date} />
      ),
    },
  ], []);

  const table = useReactTable({
    data: skeleton ? [] : data,
    columns: columns as never,
    state: { sorting },
    onSortingChange: handleSortingChange as never,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    manualSorting: false,
    initialState: { sorting: initialSorting },
  });

  const SortIcon = ({ id }: { id: string }) => {
    const column = table.getColumn(id);
    const sorted = column?.getIsSorted();
    if (sorted === "desc") return <ChevronDown className="w-3.5 h-3.5 text-zinc-700 dark:text-zinc-200" />;
    if (sorted === "asc") return <ChevronUp className="w-3.5 h-3.5 text-zinc-700 dark:text-zinc-200" />;
    return <ChevronsUpDown className="w-3.5 h-3.5 text-zinc-400 group-hover:text-zinc-600 dark:group-hover:text-zinc-300" />;
  };

  if (skeleton) {
    return (
      <div className="rounded-2xl overflow-hidden bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="h-[62px] border-b border-zinc-100 dark:border-white/[0.03] px-6 flex items-center gap-3">
            <div className="h-5 w-24 rounded bg-zinc-200/70 dark:bg-white/8 animate-pulse" />
            <div className="flex-1 h-5 max-w-[220px] rounded bg-zinc-200/70 dark:bg-white/8 animate-pulse" />
            <div className="h-5 w-24 rounded bg-zinc-200/70 dark:bg-white/8 animate-pulse" />
            <div className="hidden sm:block h-5 w-20 rounded bg-zinc-200/70 dark:bg-white/8 animate-pulse" />
            <div className="hidden lg:block h-5 w-24 rounded bg-zinc-200/70 dark:bg-white/8 animate-pulse" />
            <div className="hidden md:block h-6 w-20 rounded-lg bg-zinc-200/70 dark:bg-white/8 animate-pulse" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <>
      {/* Desktop: TanStack Table */}
      <div className="hidden md:block rounded-2xl overflow-hidden bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
        <div className="w-full overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="bg-zinc-50/60 dark:bg-white/[0.02] border-b border-zinc-200 dark:border-white/10 text-zinc-500 dark:text-zinc-400">
                {table.getFlatHeaders().map((header: any, idx: number) => (
                  <th
                    key={idx}
                    className={cn(
                      "px-5 py-3.5 text-[11px] font-semibold uppercase tracking-wider whitespace-nowrap",
                      header.column.getCanSort() ? "cursor-pointer select-none group" : ""
                    )}
                    onClick={header.column.getCanSort() ? header.column.getToggleSortingHandler() : undefined}
                    style={{ width: header.getSize() ? header.getSize() : undefined }}
                  >
                    <div className="flex items-center gap-1.5">
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {header.column.getCanSort() && <SortIcon id={header.column.id} />}
                    </div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <AnimatePresence initial={false} mode="popLayout">
                {table.getRowModel().rows.map((row: any, idx: number) => (
                  <motion.tr
                    layout={!shouldReduce ? true : undefined}
                    key={row.original.invoice_id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3, delay: idx * 0.015, ease: "easeOut" }}
                    className={cn(
                      "border-b border-zinc-100 dark:border-white/[0.03] hover:bg-indigo-50/40 dark:hover:bg-indigo-500/[0.04] transition-colors",
                      "group"
                    )}
                  >
                    {row.getVisibleCells().map((cell: any, cIdx: number) => (
                      <td
                        key={cIdx}
                        className="px-5 py-4 align-middle last:pr-6"
                        style={{ width: cell.column.getSize() ? cell.column.getSize() : undefined }}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    ))}
                    <td className="px-4 py-4 align-middle">
                      <Link
                        href={`/invoices/${row.original.invoice_id}`}
                        className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-bold text-indigo-600 dark:text-indigo-400 bg-indigo-50 dark:bg-indigo-500/10 border border-indigo-200/50 dark:border-indigo-500/20 hover:bg-indigo-100 dark:hover:bg-indigo-500/15 transition-colors opacity-0 group-hover:opacity-100"
                      >
                        Open <ArrowRight className="w-3 h-3" />
                      </Link>
                    </td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
        </div>
      </div>

      {/* Mobile: stacked cards */}
      <div className="md:hidden flex flex-col gap-3">
        {data.map((it, idx) => (
          <motion.div
            key={it.invoice_id}
            layout={!shouldReduce ? true : undefined}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: idx * 0.03 }}
          >
            <Link
              href={`/invoices/${it.invoice_id}`}
              className={cn(
                "block rounded-2xl p-4 bg-white dark:bg-neutral-900",
                "border border-zinc-200 dark:border-white/10 shadow-sm",
                "active:scale-[0.995] transition-transform"
              )}
            >
              <div className="flex items-start justify-between gap-3 mb-3">
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-xs font-bold text-indigo-600 dark:text-indigo-400">
                    {it.invoice_id}
                  </div>
                  <div className="text-sm font-bold text-zinc-900 dark:text-white truncate mt-0.5">
                    {it.customer_name}
                  </div>
                  <div className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono mt-0.5">
                    {it.customer_id}
                  </div>
                </div>
                <TierBadge tier={it.tier} />
              </div>

              <div className="grid grid-cols-3 gap-3 mb-3">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1">
                    Outstanding
                  </div>
                  <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums">
                    {formatINR(it.outstanding)}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1">
                    EV
                  </div>
                  <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums">
                    {it.expected_value != null ? formatINR(it.expected_value) : "—"}
                  </div>
                </div>
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1">
                    Overdue
                  </div>
                  <div className="text-sm font-bold text-zinc-900 dark:text-white tabular-nums">
                    {it.days_overdue}d
                  </div>
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <EscalationBadge state={it.escalation_state} />
                <PRow p_recovery={it.p_recovery} />
                <PromisePill promiseStatus={it.promise_status} dueDate={it.promise_due_date} />
                <div className="flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400 ml-auto">
                  <Clock className="w-3.5 h-3.5 flex-shrink-0" />
                  <span className="whitespace-nowrap">
                    {toRelativeDays(it.last_contact_at)}
                  </span>
                  <ArrowRight className="w-3.5 h-3.5 text-indigo-500 ml-1 flex-shrink-0" />
                </div>
              </div>
            </Link>
          </motion.div>
        ))}
      </div>
    </>
  );
}

export function QueueEmptyState() {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.98 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="rounded-2xl p-10 sm:p-14 text-center bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm"
    >
      <div className="mx-auto mb-5 w-16 h-16 rounded-2xl bg-emerald-50 dark:bg-emerald-500/10 flex items-center justify-center">
        <ShieldCheck className="w-8 h-8 text-emerald-500" />
      </div>
      <h3 className="text-lg font-bold tracking-tight text-zinc-900 dark:text-white mb-2">
        Book is clean
      </h3>
      <p className="text-sm text-zinc-500 dark:text-zinc-400 max-w-md mx-auto leading-relaxed">
        No invoices match your filters. Try widening your search, clearing the tier and
        escalation filters, or confirm the batch has been ingested via the Command Center.
      </p>
      <div className="mt-6 inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/50 dark:border-emerald-500/20">
        <AlertTriangle className="w-3.5 h-3.5" />
        If this is unexpected, check your applied filters above
      </div>
    </motion.div>
  );
}
