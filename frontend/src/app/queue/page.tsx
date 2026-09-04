"use client";

import React, { useEffect } from "react";
import Link from "next/link";
import { useQuery, keepPreviousData } from "@tanstack/react-query";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { motion, useReducedMotion } from "framer-motion";
import QueueFilterBar, { type FilterState } from "@/components/dashboard/QueueFilterBar";
import QueueTable, { QueueEmptyState } from "@/components/dashboard/QueueTable";
import { fetchInvoiceList, type InvoiceListFilters } from "@/lib/api";
import { ChevronLeft, ChevronRight, ListOrdered, Info, ArrowRight } from "lucide-react";
import { cn } from "@/lib/utils";

export default function QueuePage() {
  const shouldReduce = useReducedMotion();
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const initialFilters: FilterState = {
    q: searchParams.get("q") ?? undefined,
    status: searchParams.get("status") ?? undefined,
    escalation_state: searchParams.get("escalation_state") ?? undefined,
    tier: searchParams.get("tier") ?? undefined,
    sort: searchParams.get("sort") ?? "expected_value",
    sort_dir: (searchParams.get("sort_dir") as "asc" | "desc") ?? "desc",
    page: Number(searchParams.get("page") ?? "1") || 1,
    page_size: Number(searchParams.get("page_size") ?? "25") || 25,
  };

  const [filters, setFilters] = React.useState<FilterState>(initialFilters);

  useEffect(() => {
    setFilters({
      q: searchParams.get("q") ?? undefined,
      status: searchParams.get("status") ?? undefined,
      escalation_state: searchParams.get("escalation_state") ?? undefined,
      tier: searchParams.get("tier") ?? undefined,
      sort: searchParams.get("sort") ?? "expected_value",
      sort_dir: (searchParams.get("sort_dir") as "asc" | "desc") ?? "desc",
      page: Number(searchParams.get("page") ?? "1") || 1,
      page_size: Number(searchParams.get("page_size") ?? "25") || 25,
    });
  }, [searchParams]);

  const { data, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["invoice-list", filters],
    queryFn: async () => {
      const f: InvoiceListFilters = {};
      if (filters.q) f.q = filters.q;
      if (filters.status) f.status = filters.status;
      if (filters.escalation_state) f.escalation_state = filters.escalation_state;
      if (filters.tier) f.tier = filters.tier;
      if (filters.sort) f.sort = filters.sort;
      if (filters.sort_dir) f.sort_dir = filters.sort_dir;
      f.page = filters.page;
      f.page_size = filters.page_size;
      return fetchInvoiceList(f);
    },
    staleTime: 10_000,
    placeholderData: keepPreviousData,
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const page = data?.page ?? filters.page ?? 1;
  const pageSize = data?.page_size ?? filters.page_size ?? 25;
  const totalPages = data?.total_pages ?? Math.max(1, Math.ceil(total / pageSize));

  const handleFiltersChange = (next: FilterState) => {
    setFilters((prev) => ({ ...prev, ...next }));
  };
  const handleClearFilters = () => {
    setFilters({
      sort: "expected_value",
      sort_dir: "desc",
      page: 1,
      page_size: filters.page_size ?? 25,
    });
    router.replace(pathname, { scroll: false });
  };

  const handleSortChange = (sort: string, sort_dir: "asc" | "desc") => {
    const next = { ...filters, sort, sort_dir, page: 1 };
    setFilters(next);
    const params = new URLSearchParams(searchParams.toString());
    if (sort && sort !== "expected_value") params.set("sort", sort); else params.delete("sort");
    if (sort_dir && sort_dir !== "desc") params.set("sort_dir", sort_dir); else params.delete("sort_dir");
    params.delete("page");
    router.replace(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`, { scroll: false });
  };

  const goToPage = (nextPage: number) => {
    const safe = Math.max(1, Math.min(totalPages, nextPage));
    const next = { ...filters, page: safe };
    setFilters(next);
    const params = new URLSearchParams(searchParams.toString());
    if (safe > 1) params.set("page", String(safe)); else params.delete("page");
    router.replace(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`, { scroll: false });
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const startRange = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const endRange = Math.min(total, page * pageSize);
  const isEmpty = !isLoading && total === 0;

  return (
    <main className="p-4 sm:p-6 lg:p-8 max-w-[1600px] mx-auto space-y-6 min-h-full">
      {/* Header */}
      <motion.div
        initial={shouldReduce ? { opacity: 1 } : { opacity: 0, y: -8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: "easeOut" }}
        className="flex flex-col lg:flex-row lg:items-start justify-between gap-5 pb-6 border-b border-zinc-200/60 dark:border-white/10"
      >
        <div className="space-y-2 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex items-center gap-2 text-xs font-mono text-indigo-600 dark:text-indigo-400 font-bold tracking-[0.12em] uppercase">
              <ListOrdered className="w-3.5 h-3.5" />
              <span>Work Queue</span>
            </div>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-zinc-100 dark:bg-white/5 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-white/10 tabular-nums">
              {isLoading ? (
                <>
                  <span className="w-2 h-2 rounded-full bg-zinc-400 animate-pulse" />
                  Loading...
                </>
              ) : (
                <>
                  <span className="w-2 h-2 rounded-full bg-emerald-500" />
                  {total.toLocaleString("en-IN")} open invoices
                </>
              )}
            </span>
          </div>
          <div>
            <h1 className="text-2xl sm:text-3xl font-bold tracking-tight text-zinc-900 dark:text-white leading-tight">
              Every case. Prioritized. Actionable.
            </h1>
            <p className="text-sm sm:text-base text-zinc-500 dark:text-zinc-400 mt-1.5 max-w-2xl leading-relaxed">
              The day-to-day work queue for MSME collections. Rows are sorted by expected
              value so the highest-impact actions surface first. Click a row to drill into
              the invoice's decision trail.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-semibold bg-white dark:bg-neutral-900 hover:bg-zinc-50 dark:hover:bg-neutral-800 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-white/10 shadow-sm hover:shadow transition-all"
          >
            <ListOrdered className="w-4 h-4 text-indigo-500" />
            <span>Command Center</span>
          </Link>
          <button
            onClick={() => refetch()}
            className={cn(
              "inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-bold",
              "bg-gradient-to-br from-indigo-500 to-violet-500 hover:from-indigo-600 hover:to-violet-600 text-white",
              "shadow-md hover:shadow-lg shadow-indigo-500/15",
              "active:scale-[0.97] transition-all",
              "disabled:opacity-70 disabled:active:scale-100"
            )}
            disabled={isFetching}
          >
            <ListOrdered className={cn("w-4 h-4", isFetching && "animate-pulse")} />
            <span>{isFetching ? "Scoring..." : "Refresh Queue"}</span>
          </button>
        </div>
      </motion.div>

      {/* Filter Bar */}
      <QueueFilterBar
        filters={filters}
        onChange={handleFiltersChange}
        onClear={handleClearFilters}
        total={total}
        pageSize={pageSize}
        onPageSizeChange={(n) => handleFiltersChange({ page_size: n, page: 1 })}
      />

      {/* Policy Note */}
      <motion.div
        initial={shouldReduce ? { opacity: 1 } : { opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35, ease: "easeOut", delay: 0.05 }}
        className="rounded-xl px-4 py-3 bg-indigo-50/60 dark:bg-indigo-500/10 border border-indigo-200/50 dark:border-indigo-500/20 flex items-start gap-3"
      >
        <Info className="w-4.5 h-4.5 w-[18px] h-[18px] text-indigo-500 mt-0.5 flex-shrink-0" />
        <div className="text-xs sm:text-sm leading-relaxed text-indigo-900/80 dark:text-indigo-200/90">
          <span className="font-bold">Actions run per-invoice, gated by policy.</span>{" "}
          There are no bulk-select or mass-escalate controls — the policy engine evaluates
          and logs every action individually.{" "}
          <Link
            href="/policy"
            className="font-semibold underline underline-offset-2 hover:text-indigo-800 dark:hover:text-indigo-100 inline-flex items-center gap-0.5"
          >
            See the rulebook <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
      </motion.div>

      {/* Results Count */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
          {isLoading ? (
            <>Loading queue rows…</>
          ) : isEmpty ? (
            <>No rows match the current filters.</>
          ) : (
            <>
              Showing <span className="font-semibold text-zinc-700 dark:text-zinc-300">{startRange}–{endRange}</span>{" "}
              of <span className="font-semibold text-zinc-700 dark:text-zinc-300">{total.toLocaleString("en-IN")}</span>
              {filters.sort && (
                <>
                  {" "}· sorted by{" "}
                  <span className="font-semibold text-zinc-700 dark:text-zinc-300">
                    {filters.sort === "expected_value" || filters.sort === "ev"
                      ? "expected value (priority)"
                      : filters.sort === "days_overdue"
                      ? "days overdue"
                      : filters.sort === "outstanding"
                      ? "outstanding amount"
                      : filters.sort === "tier"
                      ? "tier"
                      : filters.sort === "due_date"
                      ? "due date"
                      : filters.sort === "amount"
                      ? "invoice amount"
                      : "invoice id"
                    }
                  </span>
                  {" "}
                  <span className="font-semibold text-zinc-700 dark:text-zinc-300">
                    {filters.sort_dir === "desc" ? "↓ desc" : "↑ asc"}
                  </span>
                </>
              )}
            </>
          )}
        </div>
        {isFetching && !isLoading && (
          <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 inline-flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
            Updating in background
          </span>
        )}
      </div>

      {/* Table / Empty */}
      {isLoading ? (
        <QueueTable
          data={[]}
          sort={filters.sort ?? "expected_value"}
          sortDir={filters.sort_dir ?? "desc"}
          onSortChange={handleSortChange}
          skeleton
        />
      ) : isEmpty ? (
        <QueueEmptyState />
      ) : (
        <QueueTable
          data={items}
          sort={filters.sort ?? "expected_value"}
          sortDir={filters.sort_dir ?? "desc"}
          onSortChange={handleSortChange}
        />
      )}

      {/* Pagination */}
      {!isEmpty && !isLoading && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
          className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-4"
        >
          <div className="text-xs text-zinc-500 dark:text-zinc-400 tabular-nums">
            Page <span className="font-semibold text-zinc-700 dark:text-zinc-300">{page}</span>{" "}
            of <span className="font-semibold text-zinc-700 dark:text-zinc-300">{totalPages.toLocaleString("en-IN")}</span>
          </div>

          <div className="flex items-center gap-1.5">
            <button
              onClick={() => goToPage(page - 1)}
              disabled={page <= 1}
              className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-semibold bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-neutral-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
              Prev
            </button>

            <div className="flex items-center gap-1 px-1.5 py-1 rounded-lg bg-zinc-100 dark:bg-white/5 border border-zinc-200/60 dark:border-white/10">
              {Array.from({ length: Math.min(7, totalPages) }, (_, i) => {
                let pageNum: number;
                if (totalPages <= 7) {
                  pageNum = i + 1;
                } else if (page <= 4) {
                  pageNum = i + 1;
                  if (i === 5) pageNum = totalPages - 1;
                  if (i === 6) pageNum = totalPages;
                } else if (page >= totalPages - 3) {
                  if (i === 0) pageNum = 1;
                  else if (i === 1) pageNum = 2;
                  else pageNum = totalPages - (6 - i);
                } else {
                  if (i === 0) pageNum = 1;
                  else if (i === 1) pageNum = page - 1;
                  else if (i === 2) pageNum = page;
                  else if (i === 3) pageNum = page + 1;
                  else if (i === 4) pageNum = page + 2;
                  else if (i === 5) pageNum = totalPages - 1;
                  else pageNum = totalPages;
                }
                const isGap = totalPages > 7 && (
                  ((page <= 4 && i === 4) ||
                  (page >= totalPages - 3 && i === 2) ||
                  (page > 4 && page < totalPages - 3 && i === 4))
                );
                return (
                  <React.Fragment key={pageNum + "-" + i}>
                    {isGap && <span key={"gap-" + i} className="px-2 py-1 text-xs text-zinc-400">…</span>}
                    {!isGap && (
                      <button
                        onClick={() => goToPage(pageNum)}
                        className={cn(
                          "min-w-[32px] h-8 px-2 rounded-md text-xs font-bold tabular-nums transition-colors",
                          pageNum === page
                            ? "bg-indigo-500 text-white shadow-sm shadow-indigo-500/20"
                            : "text-zinc-600 dark:text-zinc-300 hover:bg-white dark:hover:bg-neutral-900 border border-transparent hover:border-zinc-200 dark:hover:border-white/10"
                        )}
                      >
                        {pageNum}
                      </button>
                    )}
                  </React.Fragment>
                );
              })}
            </div>

            <button
              onClick={() => goToPage(page + 1)}
              disabled={page >= totalPages}
              className="inline-flex items-center gap-1 px-3 py-2 rounded-lg text-xs font-semibold bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-neutral-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Next
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </motion.div>
      )}
    </main>
  );
}
