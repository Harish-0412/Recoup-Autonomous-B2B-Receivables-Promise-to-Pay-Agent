"use client";

import React, { useEffect, useState, useTransition } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { Search, SlidersHorizontal, X, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

export interface FilterState {
  q?: string;
  status?: string;
  escalation_state?: string;
  tier?: string;
  sort?: string;
  sort_dir?: "asc" | "desc";
  page?: number;
  page_size?: number;
}

const STATUS_OPTIONS = [
  { value: "", label: "All Statuses" },
  { value: "OPEN", label: "Open" },
  { value: "IN_PROGRESS", label: "In Progress" },
  { value: "PROMISED", label: "Promised" },
  { value: "DISPUTED", label: "Disputed" },
  { value: "WRITTEN_OFF", label: "Written Off" },
  { value: "HANDED_OFF", label: "Handoff" },
  { value: "PAID", label: "Paid (Archived)" },
];

const ESCALATION_OPTIONS = [
  { value: "", label: "All Stages" },
  { value: "monitoring", label: "Monitoring" },
  { value: "reminded", label: "Reminded" },
  { value: "escalated", label: "Escalated" },
  { value: "human_handoff", label: "Human Handoff" },
  { value: "closed", label: "Closed" },
];

const TIER_OPTIONS = [
  { value: "", label: "All Tiers" },
  { value: "ESCALATE", label: "ESCALATE" },
  { value: "REMIND", label: "REMIND" },
  { value: "WAIT", label: "WAIT" },
];

function tierBadgeClass(value: string): string {
  switch (value) {
    case "ESCALATE":
      return "bg-red-50 dark:bg-red-500/10 text-red-700 dark:text-red-400 border border-red-200/60 dark:border-red-500/20";
    case "REMIND":
      return "bg-blue-50 dark:bg-blue-500/10 text-blue-700 dark:text-blue-400 border border-blue-200/60 dark:border-blue-500/20";
    case "WAIT":
      return "bg-amber-50 dark:bg-amber-500/10 text-amber-700 dark:text-amber-400 border border-amber-200/60 dark:border-amber-500/20";
    default:
      return "";
  }
}

function statusBadgeClass(value: string): string {
  switch (value) {
    case "OPEN":
      return "bg-zinc-100 dark:bg-white/5 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-white/10";
    case "IN_PROGRESS":
      return "bg-indigo-50 dark:bg-indigo-500/10 text-indigo-700 dark:text-indigo-400 border border-indigo-200/60 dark:border-indigo-500/20";
    case "PROMISED":
      return "bg-emerald-50 dark:bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-200/60 dark:border-emerald-500/20";
    case "DISPUTED":
      return "bg-orange-50 dark:bg-orange-500/10 text-orange-700 dark:text-orange-400 border border-orange-200/60 dark:border-orange-500/20";
    default:
      return "bg-zinc-100 dark:bg-white/5 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-white/10";
  }
}

function SelectField({
  value,
  onChange,
  options,
  placeholder,
  badgeClass,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder: string;
  badgeClass?: (v: string) => string;
}) {
  const selected = options.find((o) => o.value === value);
  return (
    <div className="relative group">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          "w-full appearance-none pr-8 pl-3 py-2.5 rounded-xl text-sm font-medium bg-white dark:bg-neutral-900",
          "border border-zinc-200 dark:border-white/10",
          "focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400",
          "hover:border-zinc-300 dark:hover:border-white/20 transition-colors cursor-pointer",
          value && badgeClass ? badgeClass(value) : "text-zinc-800 dark:text-zinc-200"
        )}
      >
        {options.map((o) => (
          <option key={o.value} value={o.value} className="bg-white dark:bg-neutral-900">
            {o.label}
          </option>
        ))}
      </select>
      <ChevronDown className="w-4 h-4 text-zinc-400 group-hover:text-zinc-600 dark:group-hover:text-zinc-300 absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none transition-colors" />
      {selected && placeholder && !value && (
        <span className="sr-only">{placeholder}</span>
      )}
    </div>
  );
}

export default function QueueFilterBar({
  filters,
  onChange,
  onClear,
  total,
  pageSize,
  onPageSizeChange,
}: {
  filters: FilterState;
  onChange: (next: FilterState) => void;
  onClear: () => void;
  total: number;
  pageSize: number;
  onPageSizeChange: (n: number) => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [qInput, setQInput] = useState<string>(filters.q ?? "");
  const [, startTransition] = useTransition();

  useEffect(() => {
    setQInput(filters.q ?? "");
  }, [filters.q]);

  const activeCount =
    (filters.status ? 1 : 0) +
    (filters.escalation_state ? 1 : 0) +
    (filters.tier ? 1 : 0) +
    (filters.q ? 1 : 0);

  const commit = (next: FilterState) => {
    const params = new URLSearchParams(searchParams.toString());
    const apply = { ...filters, ...next };
    if (apply.q) params.set("q", apply.q); else params.delete("q");
    if (apply.status) params.set("status", apply.status); else params.delete("status");
    if (apply.escalation_state) params.set("escalation_state", apply.escalation_state);
    else params.delete("escalation_state");
    if (apply.tier) params.set("tier", apply.tier); else params.delete("tier");
    if (apply.page && apply.page > 1) params.set("page", String(apply.page));
    else params.delete("page");
    if (apply.page_size && apply.page_size !== 25) params.set("page_size", String(apply.page_size));
    else params.delete("page_size");
    if (apply.sort && apply.sort !== "expected_value") params.set("sort", apply.sort);
    else params.delete("sort");
    if (apply.sort_dir && apply.sort_dir !== "desc") params.set("sort_dir", apply.sort_dir);
    else params.delete("sort_dir");

    // Reset page when filters change, unless the change is page itself
    if (!(next.page && Object.keys(next).length === 1)) {
      params.delete("page");
      next.page = undefined;
    }
    startTransition(() => {
      router.replace(`${pathname}${params.toString() ? `?${params.toString()}` : ""}`, { scroll: false });
    });
    onChange({ ...filters, ...next, page: next.page ?? 1 });
  };

  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onSearchChange = (v: string) => {
    setQInput(v);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => commit({ q: v }), 350);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: "easeOut" }}
      className="flex flex-col gap-4"
    >
      <div className="rounded-2xl p-4 sm:p-5 bg-white dark:bg-neutral-900 border border-zinc-200 dark:border-white/10 shadow-sm">
        <div className="flex items-start justify-between gap-3 mb-4 flex-wrap">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="p-2 rounded-xl bg-zinc-100 dark:bg-white/5 flex-shrink-0">
              <SlidersHorizontal className="w-4.5 h-4.5 w-[18px] h-[18px] text-zinc-600 dark:text-zinc-400" />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <h2 className="text-sm font-bold text-zinc-900 dark:text-white">
                  Filter & Sort
                </h2>
                {activeCount > 0 && (
                  <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full text-[10px] font-bold bg-indigo-500 text-white">
                    {activeCount}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 truncate">
                Showing results <span className="font-semibold text-zinc-700 dark:text-zinc-300 tabular-nums">{total.toLocaleString("en-IN")}</span> invoices
                · URL is shareable
              </p>
            </div>
          </div>

          {activeCount > 0 && (
            <button
              onClick={() => {
                setQInput("");
                onClear();
              }}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-zinc-100 dark:bg-white/5 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-200/60 dark:hover:bg-white/10 border border-zinc-200 dark:border-white/10 transition-colors flex-shrink-0"
            >
              <X className="w-3.5 h-3.5" />
              Clear filters
            </button>
          )}
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-12 gap-3">
          <div className="lg:col-span-5">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1.5">
              Customer / Invoice
            </label>
            <div className="relative">
              <Search className="w-4 h-4 text-zinc-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                value={qInput}
                onChange={(e) => onSearchChange(e.target.value)}
                type="text"
                placeholder="Search INV-1042, Apex Retail, C-112..."
                className={cn(
                  "w-full pl-9 pr-9 py-2.5 rounded-xl text-sm bg-white dark:bg-neutral-900",
                  "border border-zinc-200 dark:border-white/10 placeholder:text-zinc-400 dark:placeholder:text-zinc-500",
                  "focus:outline-none focus:ring-2 focus:ring-indigo-500/20 focus:border-indigo-400 transition-colors"
                )}
              />
              {qInput && (
                <button
                  onClick={() => onSearchChange("")}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 rounded-md hover:bg-zinc-100 dark:hover:bg-white/5 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          </div>

          <div className="lg:col-span-2">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1.5">
              Status
            </label>
            <SelectField
              value={filters.status ?? ""}
              onChange={(v) => commit({ status: v })}
              options={STATUS_OPTIONS}
              placeholder="Status"
              badgeClass={statusBadgeClass}
            />
          </div>

          <div className="lg:col-span-2">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1.5">
              Escalation Stage
            </label>
            <SelectField
              value={filters.escalation_state ?? ""}
              onChange={(v) => commit({ escalation_state: v })}
              options={ESCALATION_OPTIONS}
              placeholder="Stage"
            />
          </div>

          <div className="lg:col-span-2">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1.5">
              Intervention Tier
            </label>
            <SelectField
              value={filters.tier ?? ""}
              onChange={(v) => commit({ tier: v })}
              options={TIER_OPTIONS}
              placeholder="Tier"
              badgeClass={tierBadgeClass}
            />
          </div>

          <div className="lg:col-span-1">
            <label className="block text-[11px] font-semibold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 mb-1.5">
              Per page
            </label>
            <SelectField
              value={String(pageSize)}
              onChange={(v) => {
                const n = Number(v);
                onPageSizeChange(n);
                commit({ page_size: n });
              }}
              options={[
                { value: "25", label: "25" },
                { value: "50", label: "50" },
                { value: "100", label: "100" },
                { value: "200", label: "200" },
              ]}
              placeholder="Rows"
            />
          </div>
        </div>
      </div>
    </motion.div>
  );
}
