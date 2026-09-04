"use client";

import React, { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  Landmark,
  Layers,
  ArrowRight,
  CheckCircle2,
  AlertTriangle,
  RefreshCw,
  Upload,
  Link as LinkIcon,
  ShieldCheck,
  FileSpreadsheet,
  FileCode,
  Search,
  ExternalLink,
  ChevronRight,
  Database,
  Building2,
  FileText,
  Clock,
  Sparkles,
  Info,
  DollarSign,
  Receipt,
  Check,
  X,
  AlertCircle,
} from "lucide-react";
import {
  postBankPayment,
  fetchUnmatchedPayments,
  confirmAllocation,
  fetchIntegrationStatus,
  connectIntegration,
  syncIntegration,
  importTallyFile,
  triggerSyncErp,
  fetchInvoiceDetail,
  type BankPaymentIn,
  type BankPaymentOut,
  type UnmatchedPaymentOut,
  type SyncResponse,
  type IntegrationStatusOut,
  type TallyImportResponse,
  type InvoiceOut,
} from "@/lib/api";
import { cn } from "@/lib/utils";

import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

function formatCurrency(amount: number | null | undefined): string {
  if (amount == null) return "₹0";
  return `₹${Math.round(amount).toLocaleString("en-IN")}`;
}

function PaymentsContent() {
  const searchParams = useSearchParams();
  const initialInvoiceId = searchParams.get("invoice_id") || "";

  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<"utr" | "erp" | "ledger">("utr");

  // --- UTR / Bank Payment Form State ---
  const [utrForm, setUtrForm] = useState<BankPaymentIn>({
    utr: "",
    amount: 0,
    paid_on: new Date().toISOString().split("T")[0],
    payer_account: "",
    suggested_invoice_id: initialInvoiceId,
    notes: "",
  });
  const [lastMatchResult, setLastMatchResult] = useState<BankPaymentOut | null>(null);
  const [utrError, setUtrError] = useState<string | null>(null);

  // --- Allocate Modal State ---
  const [allocatingItem, setAllocatingItem] = useState<UnmatchedPaymentOut | null>(null);
  const [targetInvoiceId, setTargetInvoiceId] = useState<string>(initialInvoiceId);
  const [allocationSuccess, setAllocationSuccess] = useState<string | null>(null);
  const [allocationError, setAllocationError] = useState<string | null>(null);

  // --- ERP State ---
  const [syncAllStatus, setSyncAllStatus] = useState<string | null>(null);
  const [zohoModalOpen, setZohoModalOpen] = useState(false);
  const [qboModalOpen, setQboModalOpen] = useState(false);
  const [zohoCode, setZohoCode] = useState("");
  const [zohoOrgId, setZohoOrgId] = useState("");
  const [qboCode, setQboCode] = useState("");
  const [qboRealmId, setQboRealmId] = useState("");
  const [syncResult, setSyncResult] = useState<SyncResponse | null>(null);
  const [tallyResult, setTallyResult] = useState<TallyImportResponse | null>(null);

  // --- Invoice Lookup State (Ledger Tab) ---
  const [lookupInvoiceId, setLookupInvoiceId] = useState(initialInvoiceId);
  const [searchedInvoiceId, setSearchedInvoiceId] = useState(initialInvoiceId);

  useEffect(() => {
    if (initialInvoiceId) {
      setUtrForm((prev) => ({ ...prev, suggested_invoice_id: initialInvoiceId }));
      setTargetInvoiceId(initialInvoiceId);
      setLookupInvoiceId(initialInvoiceId);
      setSearchedInvoiceId(initialInvoiceId);
    }
  }, [initialInvoiceId]);

  // Queries
  const { data: unmatchedData, isLoading: loadingUnmatched, refetch: refetchUnmatched } = useQuery({
    queryKey: ["unmatched-payments"],
    queryFn: () => fetchUnmatchedPayments(100),
    refetchInterval: 15000,
  });

  const { data: zohoStatus, refetch: refetchZoho } = useQuery({
    queryKey: ["integration-status", "zoho"],
    queryFn: () => fetchIntegrationStatus("zoho").catch(() => null),
  });

  const { data: qboStatus, refetch: refetchQbo } = useQuery({
    queryKey: ["integration-status", "quickbooks"],
    queryFn: () => fetchIntegrationStatus("quickbooks").catch(() => null),
  });

  const { data: rzpStatus, refetch: refetchRzp } = useQuery({
    queryKey: ["integration-status", "razorpay"],
    queryFn: () => fetchIntegrationStatus("razorpay").catch(() => null),
  });

  const { data: tallyStatus, refetch: refetchTally } = useQuery({
    queryKey: ["integration-status", "tally"],
    queryFn: () => fetchIntegrationStatus("tally").catch(() => null),
  });

  const { data: searchedInvoice, isFetching: loadingInvoice } = useQuery<InvoiceOut | null>({
    queryKey: ["invoice-detail", searchedInvoiceId],
    queryFn: () => (searchedInvoiceId ? fetchInvoiceDetail(searchedInvoiceId) : null),
    enabled: !!searchedInvoiceId,
  });

  // Mutations
  const postPaymentMutation = useMutation({
    mutationFn: postBankPayment,
    onSuccess: (data) => {
      setLastMatchResult(data);
      setUtrError(null);
      setUtrForm({
        utr: "",
        amount: 0,
        paid_on: new Date().toISOString().split("T")[0],
        payer_account: "",
        suggested_invoice_id: "",
        notes: "",
      });
      queryClient.invalidateQueries({ queryKey: ["unmatched-payments"] });
    },
    onError: (err: Error) => {
      setUtrError(err.message);
      setLastMatchResult(null);
    },
  });

  const allocateMutation = useMutation({
    mutationFn: ({ allocId, invId }: { allocId: number; invId: string }) =>
      confirmAllocation(allocId, invId),
    onSuccess: (data) => {
      setAllocationSuccess(`Payment successfully allocated to ${data.invoice_id}! Amount paid: ₹${data.new_amount_paid.toLocaleString("en-IN")}, Status: ${data.invoice_status}`);
      setAllocationError(null);
      setAllocatingItem(null);
      queryClient.invalidateQueries({ queryKey: ["unmatched-payments"] });
      if (lastMatchResult?.allocation_id === data.allocation_id) {
        setLastMatchResult(null);
      }
    },
    onError: (err: Error) => {
      setAllocationError(err.message);
      setAllocationSuccess(null);
    },
  });

  const syncErpMutation = useMutation({
    mutationFn: triggerSyncErp,
    onSuccess: (data) => {
      setSyncAllStatus(`ERP sync complete: ${JSON.stringify(data.providers || {})}`);
      refetchZoho();
      refetchQbo();
      refetchRzp();
      refetchTally();
      queryClient.invalidateQueries({ queryKey: ["invoice-list"] });
    },
    onError: (err: Error) => {
      setSyncAllStatus(`ERP sync failed: ${err.message}`);
    },
  });

  const syncProviderMutation = useMutation({
    mutationFn: (provider: "zoho" | "quickbooks" | "razorpay") => syncIntegration(provider),
    onSuccess: (data) => {
      setSyncResult(data);
      refetchZoho();
      refetchQbo();
      refetchRzp();
      queryClient.invalidateQueries({ queryKey: ["invoice-list"] });
    },
    onError: (err: Error) => {
      alert(`Sync failed: ${err.message}`);
    },
  });

  const handleTallyUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const res = await importTallyFile(file);
      setTallyResult(res);
      refetchTally();
      queryClient.invalidateQueries({ queryKey: ["invoice-list"] });
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Upload failed");
    }
  };

  return (
    <div className="min-h-screen bg-zinc-50 dark:bg-black text-zinc-900 dark:text-zinc-100 pb-16">
      {/* Top Banner */}
      <div className="border-b border-zinc-200/80 dark:border-white/10 bg-white dark:bg-zinc-900/60 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <div className="flex items-center gap-2.5 mb-1.5">
              <div className="h-6 w-6 rounded-md bg-gradient-to-br from-emerald-500 to-teal-600 flex items-center justify-center text-white text-xs font-bold shadow-sm">
                ₹
              </div>
              <span className="text-xs font-mono font-semibold tracking-wider text-emerald-600 dark:text-emerald-400 uppercase">
                Wave 2 • Money Truth
              </span>
            </div>
            <h1 className="text-2xl font-bold tracking-tight text-zinc-950 dark:text-white">
              Payments, Allocations & ERP Sync
            </h1>
            <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1 max-w-2xl">
              Canonical money ledger, real-time UTR / bank transfer matching with operator confirmation, and automated two-way sync with Zoho Books, QuickBooks, Razorpay, and TallyPrime.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <button
              onClick={() => syncErpMutation.mutate()}
              disabled={syncErpMutation.isPending}
              className="inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-medium bg-emerald-600 hover:bg-emerald-700 text-white shadow-sm transition-all disabled:opacity-50 cursor-pointer"
            >
              <RefreshCw className={cn("w-3.5 h-3.5", syncErpMutation.isPending && "animate-spin")} />
              {syncErpMutation.isPending ? "Syncing ERPs..." : "Sync All ERPs"}
            </button>
          </div>
        </div>

        {/* Global Nav Tabs */}
        <div className="max-w-7xl mx-auto px-4 sm:px-6 flex gap-2 border-t border-zinc-100 dark:border-white/5 pt-2">
          <button
            onClick={() => setActiveTab("utr")}
            className={cn(
              "px-4 py-2 text-xs font-medium rounded-t-lg transition-colors border-b-2 flex items-center gap-2",
              activeTab === "utr"
                ? "border-emerald-600 text-emerald-600 dark:text-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/20 font-semibold"
                : "border-transparent text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-white"
            )}
          >
            <Landmark className="w-3.5 h-3.5" />
            Bank Transfers & UTR Matching
            {unmatchedData && unmatchedData.total > 0 && (
              <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] bg-amber-500/10 text-amber-600 dark:text-amber-400 font-mono">
                {unmatchedData.total}
              </span>
            )}
          </button>

          <button
            onClick={() => setActiveTab("erp")}
            className={cn(
              "px-4 py-2 text-xs font-medium rounded-t-lg transition-colors border-b-2 flex items-center gap-2",
              activeTab === "erp"
                ? "border-emerald-600 text-emerald-600 dark:text-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/20 font-semibold"
                : "border-transparent text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-white"
            )}
          >
            <Layers className="w-3.5 h-3.5" />
            ERP Integrations (Zoho, QBO, Tally, Razorpay)
          </button>

          <button
            onClick={() => setActiveTab("ledger")}
            className={cn(
              "px-4 py-2 text-xs font-medium rounded-t-lg transition-colors border-b-2 flex items-center gap-2",
              activeTab === "ledger"
                ? "border-emerald-600 text-emerald-600 dark:text-emerald-400 bg-emerald-50/50 dark:bg-emerald-950/20 font-semibold"
                : "border-transparent text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-white"
            )}
          >
            <Database className="w-3.5 h-3.5" />
            Money Truth & Allocations Ledger
          </button>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-8">
        {/* TAB 1: UTR & Bank Transfers */}
        {activeTab === "utr" && (
          <div className="space-y-8">
            {/* Action Feedback Banner */}
            {allocationSuccess && (
              <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-emerald-800 dark:text-emerald-300 text-xs flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                  <span>{allocationSuccess}</span>
                </div>
                <button onClick={() => setAllocationSuccess(null)} className="text-emerald-600 hover:text-emerald-800 dark:hover:text-white">
                  <X className="w-4 h-4" />
                </button>
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
              {/* Column 1: Record Bank Payment Form */}
              <div className="lg:col-span-1 bg-white dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-white/10 rounded-2xl p-6 shadow-sm">
                <div className="flex items-center gap-2.5 mb-4">
                  <div className="p-2 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400">
                    <Receipt className="w-4 h-4" />
                  </div>
                  <div>
                    <h2 className="text-sm font-bold text-zinc-900 dark:text-white">Record Bank Payment</h2>
                    <p className="text-[11px] text-zinc-500 dark:text-zinc-400">Paste UTR from bank statement</p>
                  </div>
                </div>

                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!utrForm.utr || utrForm.amount <= 0) return;
                    postPaymentMutation.mutate(utrForm);
                  }}
                  className="space-y-4 text-xs"
                >
                  <div>
                    <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">
                      UTR / Transaction Reference *
                    </label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. HDFC001294829104"
                      value={utrForm.utr}
                      onChange={(e) => setUtrForm({ ...utrForm, utr: e.target.value.trim() })}
                      className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-zinc-900 dark:text-white font-mono text-xs focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                  </div>

                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">
                        Amount (INR) *
                      </label>
                      <input
                        type="number"
                        step="0.01"
                        min="0.01"
                        required
                        placeholder="50000"
                        value={utrForm.amount || ""}
                        onChange={(e) => setUtrForm({ ...utrForm, amount: parseFloat(e.target.value) || 0 })}
                        className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-zinc-900 dark:text-white text-xs focus:ring-2 focus:ring-emerald-500 outline-none"
                      />
                    </div>

                    <div>
                      <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">
                        Value Date *
                      </label>
                      <input
                        type="date"
                        required
                        value={utrForm.paid_on}
                        onChange={(e) => setUtrForm({ ...utrForm, paid_on: e.target.value })}
                        className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-zinc-900 dark:text-white text-xs focus:ring-2 focus:ring-emerald-500 outline-none"
                      />
                    </div>
                  </div>

                  <div>
                    <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">
                      Payer Account / Customer ID (Optional hint)
                    </label>
                    <input
                      type="text"
                      placeholder="e.g. CUST-0042 or sender name"
                      value={utrForm.payer_account || ""}
                      onChange={(e) => setUtrForm({ ...utrForm, payer_account: e.target.value })}
                      className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-zinc-900 dark:text-white text-xs focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                  </div>

                  <div>
                    <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">
                      Suggested Invoice ID (Optional)
                    </label>
                    <input
                      type="text"
                      placeholder="e.g. INV-1042"
                      value={utrForm.suggested_invoice_id || ""}
                      onChange={(e) => setUtrForm({ ...utrForm, suggested_invoice_id: e.target.value })}
                      className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-zinc-900 dark:text-white text-xs focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                  </div>

                  <div>
                    <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">
                      Operator Notes
                    </label>
                    <input
                      type="text"
                      placeholder="NEFT transfer from client bank statement"
                      value={utrForm.notes || ""}
                      onChange={(e) => setUtrForm({ ...utrForm, notes: e.target.value })}
                      className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-zinc-900 dark:text-white text-xs focus:ring-2 focus:ring-emerald-500 outline-none"
                    />
                  </div>

                  {utrError && (
                    <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-[11px]">
                      {utrError}
                    </div>
                  )}

                  <button
                    type="submit"
                    disabled={postPaymentMutation.isPending}
                    className="w-full py-2.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs transition-all shadow-sm flex items-center justify-center gap-2 cursor-pointer disabled:opacity-50"
                  >
                    {postPaymentMutation.isPending ? (
                      <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Sparkles className="w-3.5 h-3.5" />
                    )}
                    Record & Match Bank Transfer
                  </button>
                </form>

                {/* Match Result Callout */}
                {lastMatchResult && (
                  <div className="mt-4 p-4 rounded-xl border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-zinc-800/40 text-xs space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-zinc-900 dark:text-white">Matcher Verdict</span>
                      <span
                        className={cn(
                          "px-2 py-0.5 rounded-full text-[10px] font-mono uppercase font-bold",
                          lastMatchResult.match_confidence === "probable"
                            ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                            : lastMatchResult.match_confidence === "ambiguous"
                            ? "bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20"
                            : "bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20"
                        )}
                      >
                        {lastMatchResult.match_confidence}
                      </span>
                    </div>

                    <p className="text-[11px] text-zinc-600 dark:text-zinc-300">{lastMatchResult.match_reason}</p>

                    {lastMatchResult.suggested_invoice_id && (
                      <div className="pt-2 flex items-center justify-between border-t border-zinc-200 dark:border-white/10">
                        <span className="text-[11px] text-zinc-500">Suggested: <strong className="text-zinc-900 dark:text-white font-mono">{lastMatchResult.suggested_invoice_id}</strong></span>
                        <button
                          onClick={() => {
                            allocateMutation.mutate({
                              allocId: lastMatchResult.allocation_id,
                              invId: lastMatchResult.suggested_invoice_id!,
                            });
                          }}
                          className="px-2.5 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white text-[11px] font-medium transition-colors cursor-pointer"
                        >
                          Confirm Match
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Column 2 & 3: Review Queue of Unmatched Bank Transfers */}
              <div className="lg:col-span-2 space-y-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <h2 className="text-sm font-bold text-zinc-900 dark:text-white">Unmatched Payments Review Queue</h2>
                    <span className="px-2 py-0.5 rounded-full text-[11px] font-mono bg-amber-500/10 text-amber-600 dark:text-amber-400 font-bold">
                      {unmatchedData?.total ?? 0} Pending
                    </span>
                  </div>

                  <button
                    onClick={() => refetchUnmatched()}
                    className="p-1.5 rounded-lg border border-zinc-200 dark:border-white/10 text-zinc-500 hover:text-zinc-900 dark:hover:text-white hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                </div>

                {loadingUnmatched ? (
                  <div className="p-12 text-center text-xs text-zinc-500">Loading review queue...</div>
                ) : !unmatchedData || unmatchedData.items.length === 0 ? (
                  <div className="p-12 rounded-2xl border border-dashed border-zinc-200 dark:border-white/10 text-center bg-white/40 dark:bg-zinc-900/40">
                    <CheckCircle2 className="w-8 h-8 text-emerald-500 mx-auto mb-2 opacity-80" />
                    <p className="text-xs font-semibold text-zinc-900 dark:text-white">All bank payments matched</p>
                    <p className="text-[11px] text-zinc-500 dark:text-zinc-400 mt-0.5">
                      No unallocated NEFT/RTGS/UPI transfers in the review queue.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-3">
                    {unmatchedData.items.map((item) => (
                      <div
                        key={item.allocation_id}
                        className="p-4 rounded-xl bg-white dark:bg-zinc-900/80 border border-zinc-200/80 dark:border-white/10 shadow-sm flex flex-col sm:flex-row sm:items-center justify-between gap-4"
                      >
                        <div className="space-y-1">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs font-bold text-zinc-900 dark:text-white tracking-wide">
                              {item.utr}
                            </span>
                            <span className="text-[10px] px-2 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 font-mono">
                              ID #{item.allocation_id}
                            </span>
                          </div>

                          <div className="flex items-center gap-3 text-[11px] text-zinc-500 dark:text-zinc-400">
                            <span>Amount: <strong className="text-emerald-600 dark:text-emerald-400 font-mono text-xs">₹{item.amount.toLocaleString("en-IN")}</strong></span>
                            <span>•</span>
                            <span>Paid: {item.paid_on}</span>
                            {item.payer_account && (
                              <>
                                <span>•</span>
                                <span>Payer: {item.payer_account}</span>
                              </>
                            )}
                          </div>

                          {item.notes && (
                            <p className="text-[11px] text-zinc-400 italic">Notes: {item.notes}</p>
                          )}
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          <button
                            onClick={() => {
                              setAllocatingItem(item);
                              setTargetInvoiceId("");
                              setAllocationError(null);
                            }}
                            className="px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-medium text-xs shadow-sm flex items-center gap-1.5 transition-colors cursor-pointer"
                          >
                            <LinkIcon className="w-3 h-3" />
                            Allocate to Invoice
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: ERP Integrations */}
        {activeTab === "erp" && (
          <div className="space-y-8">
            {/* Sync Result Notification */}
            {syncResult && (
              <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/20 text-xs text-emerald-900 dark:text-emerald-200 flex items-center justify-between">
                <div>
                  <strong className="uppercase font-mono">{syncResult.provider}</strong> sync completed:{" "}
                  {syncResult.invoices_created} created, {syncResult.invoices_updated} updated,{" "}
                  {syncResult.credit_notes_applied ?? 0} credit notes applied.
                  {syncResult.errors.length > 0 && ` (${syncResult.errors.length} warnings)`}
                </div>
                <button onClick={() => setSyncResult(null)}><X className="w-4 h-4" /></button>
              </div>
            )}

            {tallyResult && (
              <div className="p-4 rounded-xl bg-blue-500/10 border border-blue-500/20 text-xs text-blue-900 dark:text-blue-200 flex items-center justify-between">
                <div>
                  <strong className="uppercase font-mono">Tally {tallyResult.format.toUpperCase()}</strong> import completed:{" "}
                  {tallyResult.invoices_created} invoices created, {tallyResult.customers_created} customers created.
                  {tallyResult.errors.length > 0 && ` (${tallyResult.errors.length} skipped)`}
                </div>
                <button onClick={() => setTallyResult(null)}><X className="w-4 h-4" /></button>
              </div>
            )}

            {syncAllStatus && (
              <div className="p-3.5 rounded-xl bg-zinc-100 dark:bg-zinc-800 text-xs text-zinc-700 dark:text-zinc-300 flex items-center justify-between">
                <span>{syncAllStatus}</span>
                <button onClick={() => setSyncAllStatus(null)}><X className="w-4 h-4" /></button>
              </div>
            )}

            {/* Providers Grid */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Card 1: Zoho Books */}
              <div className="p-6 rounded-2xl bg-white dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-white/10 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-red-500/10 flex items-center justify-center font-bold text-red-600 dark:text-red-400">
                        Z
                      </div>
                      <div>
                        <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Zoho Books</h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">OAuth2 Invoices & Credit Notes Sync</p>
                      </div>
                    </div>

                    <span
                      className={cn(
                        "px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold uppercase",
                        zohoStatus?.connected
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                          : "bg-zinc-200/60 dark:bg-zinc-800 text-zinc-500"
                      )}
                    >
                      {zohoStatus?.connected ? "Connected" : "Not Connected"}
                    </span>
                  </div>

                  <p className="text-xs text-zinc-600 dark:text-zinc-300 mb-4 leading-relaxed">
                    Pulls overdue invoices and applies credit notes as signed allocations. Preserves Recoup collection state while ERP updates amounts and due dates.
                  </p>

                  <div className="text-[11px] text-zinc-500 dark:text-zinc-400 space-y-1 mb-6">
                    <div className="flex justify-between">
                      <span>Last sync:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{zohoStatus?.last_sync_at ? new Date(zohoStatus.last_sync_at).toLocaleString() : "Never"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Invoices synced:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{zohoStatus?.last_sync_invoices ?? 0}</span>
                    </div>
                  </div>
                </div>

                <div className="flex gap-2 pt-4 border-t border-zinc-100 dark:border-white/5">
                  <button
                    onClick={() => setZohoModalOpen(true)}
                    className="flex-1 py-2 px-3 rounded-lg border border-zinc-200 dark:border-white/10 hover:bg-zinc-50 dark:hover:bg-zinc-800 text-xs font-medium text-zinc-900 dark:text-white transition-colors cursor-pointer"
                  >
                    Configure OAuth
                  </button>
                  <button
                    onClick={() => syncProviderMutation.mutate("zoho")}
                    disabled={!zohoStatus?.connected || syncProviderMutation.isPending}
                    className="flex-1 py-2 px-3 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-xs font-medium text-white transition-colors cursor-pointer flex items-center justify-center gap-1.5"
                  >
                    <RefreshCw className={cn("w-3 h-3", syncProviderMutation.isPending && "animate-spin")} />
                    Sync Invoices
                  </button>
                </div>
              </div>

              {/* Card 2: QuickBooks Online */}
              <div className="p-6 rounded-2xl bg-white dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-white/10 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-green-500/10 flex items-center justify-center font-bold text-green-600 dark:text-green-400">
                        Q
                      </div>
                      <div>
                        <h3 className="text-sm font-bold text-zinc-900 dark:text-white">QuickBooks Online</h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">Intuit V3 Query API</p>
                      </div>
                    </div>

                    <span
                      className={cn(
                        "px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold uppercase",
                        qboStatus?.connected
                          ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                          : "bg-zinc-200/60 dark:bg-zinc-800 text-zinc-500"
                      )}
                    >
                      {qboStatus?.connected ? "Connected" : "Not Connected"}
                    </span>
                  </div>

                  <p className="text-xs text-zinc-600 dark:text-zinc-300 mb-4 leading-relaxed">
                    Syncs invoices with open balances and credit memos from Intuit QBO sandbox or production companies.
                  </p>

                  <div className="text-[11px] text-zinc-500 dark:text-zinc-400 space-y-1 mb-6">
                    <div className="flex justify-between">
                      <span>Last sync:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{qboStatus?.last_sync_at ? new Date(qboStatus.last_sync_at).toLocaleString() : "Never"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Invoices synced:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{qboStatus?.last_sync_invoices ?? 0}</span>
                    </div>
                  </div>
                </div>

                <div className="flex gap-2 pt-4 border-t border-zinc-100 dark:border-white/5">
                  <button
                    onClick={() => setQboModalOpen(true)}
                    className="flex-1 py-2 px-3 rounded-lg border border-zinc-200 dark:border-white/10 hover:bg-zinc-50 dark:hover:bg-zinc-800 text-xs font-medium text-zinc-900 dark:text-white transition-colors cursor-pointer"
                  >
                    Configure OAuth
                  </button>
                  <button
                    onClick={() => syncProviderMutation.mutate("quickbooks")}
                    disabled={!qboStatus?.connected || syncProviderMutation.isPending}
                    className="flex-1 py-2 px-3 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-xs font-medium text-white transition-colors cursor-pointer flex items-center justify-center gap-1.5"
                  >
                    <RefreshCw className={cn("w-3 h-3", syncProviderMutation.isPending && "animate-spin")} />
                    Sync Invoices
                  </button>
                </div>
              </div>

              {/* Card 3: Razorpay Invoices API */}
              <div className="p-6 rounded-2xl bg-white dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-white/10 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-blue-500/10 flex items-center justify-center font-bold text-blue-600 dark:text-blue-400">
                        R
                      </div>
                      <div>
                        <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Razorpay Invoices API</h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">Native Razorpay Invoices Product</p>
                      </div>
                    </div>

                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold uppercase bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">
                      Active (Key Pair)
                    </span>
                  </div>

                  <p className="text-xs text-zinc-600 dark:text-zinc-300 mb-4 leading-relaxed">
                    Syncs issued/draft invoices created in Razorpay. Skips paid invoices automatically to avoid double-counting against the webhook allocation ledger.
                  </p>

                  <div className="text-[11px] text-zinc-500 dark:text-zinc-400 space-y-1 mb-6">
                    <div className="flex justify-between">
                      <span>Last sync:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{rzpStatus?.last_sync_at ? new Date(rzpStatus.last_sync_at).toLocaleString() : "Never"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Invoices synced:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{rzpStatus?.last_sync_invoices ?? 0}</span>
                    </div>
                  </div>
                </div>

                <div className="pt-4 border-t border-zinc-100 dark:border-white/5">
                  <button
                    onClick={() => syncProviderMutation.mutate("razorpay")}
                    disabled={syncProviderMutation.isPending}
                    className="w-full py-2 px-3 rounded-lg bg-blue-600 hover:bg-blue-700 text-xs font-medium text-white transition-colors cursor-pointer flex items-center justify-center gap-1.5"
                  >
                    <RefreshCw className={cn("w-3 h-3", syncProviderMutation.isPending && "animate-spin")} />
                    Sync Unpaid Invoices
                  </button>
                </div>
              </div>

              {/* Card 4: Tally File Import */}
              <div className="p-6 rounded-2xl bg-white dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-white/10 shadow-sm flex flex-col justify-between">
                <div>
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-amber-500/10 flex items-center justify-center font-bold text-amber-600 dark:text-amber-400">
                        T
                      </div>
                      <div>
                        <h3 className="text-sm font-bold text-zinc-900 dark:text-white">TallyPrime / ERP 9</h3>
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">CSV & XML Export Import</p>
                      </div>
                    </div>

                    <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-semibold uppercase bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                      File Drop
                    </span>
                  </div>

                  <p className="text-xs text-zinc-600 dark:text-zinc-300 mb-4 leading-relaxed">
                    Export vouchers from Gateway of Tally as CSV or XML and drop here. Real parser without fake cloud REST clients.
                  </p>

                  <div className="text-[11px] text-zinc-500 dark:text-zinc-400 space-y-1 mb-6">
                    <div className="flex justify-between">
                      <span>Last file:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{tallyStatus?.last_sync_at ? new Date(tallyStatus.last_sync_at).toLocaleString() : "None"}</span>
                    </div>
                    <div className="flex justify-between">
                      <span>Vouchers imported:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{tallyStatus?.last_sync_invoices ?? 0}</span>
                    </div>
                  </div>
                </div>

                <div className="pt-4 border-t border-zinc-100 dark:border-white/5">
                  <label className="w-full py-2 px-3 rounded-lg border border-dashed border-zinc-300 dark:border-white/20 hover:border-amber-500 text-xs font-medium text-zinc-700 dark:text-zinc-300 hover:text-amber-600 dark:hover:text-amber-400 transition-colors cursor-pointer flex items-center justify-center gap-2">
                    <Upload className="w-3.5 h-3.5" />
                    Upload Tally CSV / XML
                    <input type="file" accept=".csv,.xml" onChange={handleTallyUpload} className="hidden" />
                  </label>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* TAB 3: Money Truth & Allocations Ledger */}
        {activeTab === "ledger" && (
          <div className="space-y-8">
            {/* Architectural Invariants Card */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="p-5 rounded-xl bg-white dark:bg-zinc-900/60 border border-zinc-200/80 dark:border-white/10 space-y-2">
                <div className="flex items-center gap-2 text-emerald-600 dark:text-emerald-400 font-bold text-xs">
                  <Database className="w-4 h-4" />
                  Single Source of Truth
                </div>
                <h4 className="text-sm font-semibold text-zinc-900 dark:text-white">
                  amount_paid = SUM(allocations)
                </h4>
                <p className="text-[11px] text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  No more <code className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">invoice.amount_paid += amount</code>. Every rupee lives in the <code className="font-mono text-zinc-700 dark:text-zinc-300">payment_allocations</code> ledger table.
                </p>
              </div>

              <div className="p-5 rounded-xl bg-white dark:bg-zinc-900/60 border border-zinc-200/80 dark:border-white/10 space-y-2">
                <div className="flex items-center gap-2 text-blue-600 dark:text-blue-400 font-bold text-xs">
                  <ShieldCheck className="w-4 h-4" />
                  Double-Count Defense
                </div>
                <h4 className="text-sm font-semibold text-zinc-900 dark:text-white">
                  unique(business_id, source, provider_ref)
                </h4>
                <p className="text-[11px] text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  When Razorpay fires both <code className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">payment_link.paid</code> and <code className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">payment.captured</code>, the second INSERT is safely rejected at the database level.
                </p>
              </div>

              <div className="p-5 rounded-xl bg-white dark:bg-zinc-900/60 border border-zinc-200/80 dark:border-white/10 space-y-2">
                <div className="flex items-center gap-2 text-purple-600 dark:text-purple-400 font-bold text-xs">
                  <Receipt className="w-4 h-4" />
                  1 INR Promise Tolerance
                </div>
                <h4 className="text-sm font-semibold text-zinc-900 dark:text-white">
                  paid &gt;= promised - 1.0 INR
                </h4>
                <p className="text-[11px] text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  Accounts for Indian TDS rounding and bank value date nuances. Invoices maintain <code className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">IN_PROGRESS</code> on partial payments and transition to <code className="font-mono bg-zinc-100 dark:bg-zinc-800 px-1 py-0.5 rounded">PAID</code> strictly upon full settlement.
                </p>
              </div>
            </div>

            {/* Live Invoice Lookup & Ledger Audit Tool */}
            <div className="p-6 rounded-2xl bg-white dark:bg-zinc-900/70 border border-zinc-200/80 dark:border-white/10 space-y-6">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                <div>
                  <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Live Invoice Money Truth Inspector</h3>
                  <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                    Lookup any invoice to audit its settled allocations, outstanding balance, and collections state.
                  </p>
                </div>

                <div className="flex gap-2">
                  <input
                    type="text"
                    placeholder="Enter invoice ID (e.g. INV-1042)"
                    value={lookupInvoiceId}
                    onChange={(e) => setLookupInvoiceId(e.target.value.trim())}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && lookupInvoiceId) {
                        setSearchedInvoiceId(lookupInvoiceId);
                      }
                    }}
                    className="px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-xs text-zinc-900 dark:text-white font-mono outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                  <button
                    onClick={() => {
                      if (lookupInvoiceId) setSearchedInvoiceId(lookupInvoiceId);
                    }}
                    disabled={loadingInvoice || !lookupInvoiceId}
                    className="px-3 py-1.5 rounded-lg bg-zinc-900 dark:bg-white text-white dark:text-black font-medium text-xs hover:opacity-90 transition-opacity cursor-pointer disabled:opacity-50"
                  >
                    Inspect
                  </button>
                </div>
              </div>

              {loadingInvoice ? (
                <div className="p-8 text-center text-xs text-zinc-500">Fetching invoice details...</div>
              ) : searchedInvoice ? (
                <div className="p-5 rounded-xl bg-zinc-50 dark:bg-black/50 border border-zinc-200/60 dark:border-white/10 space-y-4">
                  <div className="flex flex-wrap items-center justify-between gap-3 border-b border-zinc-200 dark:border-white/10 pb-4">
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-bold text-zinc-900 dark:text-white">
                          {searchedInvoice.invoice_id}
                        </span>
                        <span
                          className={cn(
                            "px-2 py-0.5 rounded-full text-[10px] font-mono font-bold uppercase",
                            searchedInvoice.status === "PAID"
                              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20"
                              : "bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20"
                          )}
                        >
                          {searchedInvoice.status}
                        </span>
                      </div>
                      <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                        Customer: <strong className="text-zinc-900 dark:text-white">{searchedInvoice.customer_name}</strong> ({searchedInvoice.customer_id})
                      </p>
                    </div>

                    <div className="flex items-center gap-4 text-right">
                      <div>
                        <div className="text-[10px] uppercase text-zinc-400">Total Billed</div>
                        <div className="font-mono text-sm font-bold text-zinc-900 dark:text-white">
                          ₹{searchedInvoice.amount.toLocaleString("en-IN")}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase text-zinc-400">Total Settled</div>
                        <div className="font-mono text-sm font-bold text-emerald-600 dark:text-emerald-400">
                          ₹{searchedInvoice.amount_paid.toLocaleString("en-IN")}
                        </div>
                      </div>
                      <div>
                        <div className="text-[10px] uppercase text-zinc-400">Outstanding</div>
                        <div className="font-mono text-sm font-bold text-amber-600 dark:text-amber-400">
                          ₹{searchedInvoice.outstanding.toLocaleString("en-IN")}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-[11px]">
                    <div>
                      <span className="text-zinc-400 block">Due Date:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{searchedInvoice.due_date}</span>
                    </div>
                    <div>
                      <span className="text-zinc-400 block">Days Overdue:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{searchedInvoice.days_overdue} days</span>
                    </div>
                    <div>
                      <span className="text-zinc-400 block">Escalation State:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{searchedInvoice.escalation_state} (Step {searchedInvoice.ladder_index})</span>
                    </div>
                    <div>
                      <span className="text-zinc-400 block">Reminders Sent:</span>
                      <span className="font-mono text-zinc-900 dark:text-white">{searchedInvoice.prior_reminders_sent}</span>
                    </div>
                  </div>
                </div>
              ) : searchedInvoiceId ? (
                <div className="p-8 text-center text-xs text-zinc-500">
                  Invoice <code className="font-mono text-zinc-700 dark:text-zinc-300">{searchedInvoiceId}</code> not found for this tenant.
                </div>
              ) : (
                <div className="p-8 text-center text-xs text-zinc-400">
                  Enter an invoice ID above to audit money allocations and status.
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* --- ALLOCATE TO INVOICE MODAL --- */}
      <AnimatePresence>
        {allocatingItem && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-white/10 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <div className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-600">
                    <LinkIcon className="w-4 h-4" />
                  </div>
                  <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Confirm Payment Allocation</h3>
                </div>
                <button
                  onClick={() => setAllocatingItem(null)}
                  className="text-zinc-400 hover:text-zinc-600 dark:hover:text-white cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="p-3.5 rounded-xl bg-zinc-50 dark:bg-black/50 border border-zinc-200 dark:border-white/10 text-xs space-y-1">
                <div className="flex justify-between">
                  <span className="text-zinc-500">UTR:</span>
                  <span className="font-mono font-bold text-zinc-900 dark:text-white">{allocatingItem.utr}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Amount:</span>
                  <span className="font-mono font-bold text-emerald-600 dark:text-emerald-400">
                    ₹{allocatingItem.amount.toLocaleString("en-IN")}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Value Date:</span>
                  <span className="text-zinc-900 dark:text-white">{allocatingItem.paid_on}</span>
                </div>
                {allocatingItem.payer_account && (
                  <div className="flex justify-between">
                    <span className="text-zinc-500">Payer Hint:</span>
                    <span className="text-zinc-900 dark:text-white">{allocatingItem.payer_account}</span>
                  </div>
                )}
              </div>

              <div>
                <label className="block text-xs font-medium text-zinc-700 dark:text-zinc-300 mb-1">
                  Target Open Invoice ID *
                </label>
                <input
                  type="text"
                  placeholder="e.g. INV-1042"
                  value={targetInvoiceId}
                  onChange={(e) => setTargetInvoiceId(e.target.value.trim())}
                  className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-xs text-zinc-900 dark:text-white font-mono outline-none focus:ring-2 focus:ring-emerald-500"
                />
                <p className="text-[11px] text-zinc-400 mt-1">
                  Invoice must belong to this tenant and be open (OPEN, IN_PROGRESS, or PROMISED).
                </p>
              </div>

              {allocationError && (
                <div className="p-2.5 rounded-lg bg-red-500/10 border border-red-500/20 text-red-600 dark:text-red-400 text-xs">
                  {allocationError}
                </div>
              )}

              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => setAllocatingItem(null)}
                  className="flex-1 py-2 px-3 rounded-lg border border-zinc-200 dark:border-white/10 hover:bg-zinc-50 dark:hover:bg-zinc-800 text-xs font-medium text-zinc-700 dark:text-zinc-300 transition-colors cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    if (!targetInvoiceId) return;
                    allocateMutation.mutate({
                      allocId: allocatingItem.allocation_id,
                      invId: targetInvoiceId,
                    });
                  }}
                  disabled={!targetInvoiceId || allocateMutation.isPending}
                  className="flex-1 py-2 px-3 rounded-lg bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-xs font-semibold text-white transition-colors cursor-pointer flex items-center justify-center gap-1.5"
                >
                  {allocateMutation.isPending ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : null}
                  Confirm & Link
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* --- ZOHO OAUTH MODAL --- */}
      <AnimatePresence>
        {zohoModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-white/10 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Connect Zoho Books</h3>
                <button onClick={() => setZohoModalOpen(false)} className="text-zinc-400 hover:text-white cursor-pointer">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <p className="text-xs text-zinc-500">
                Generate an authorization code from Zoho Developer Console with scope <code className="font-mono">ZohoBooks.fullaccess.all</code> and submit below.
              </p>

              <div className="space-y-3 text-xs">
                <div>
                  <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">Authorization Code *</label>
                  <input
                    type="text"
                    placeholder="1000.xxxxxxxxx"
                    value={zohoCode}
                    onChange={(e) => setZohoCode(e.target.value.trim())}
                    className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black font-mono text-xs outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">Zoho Organization ID (Optional)</label>
                  <input
                    type="text"
                    placeholder="e.g. 60012345678"
                    value={zohoOrgId}
                    onChange={(e) => setZohoOrgId(e.target.value.trim())}
                    className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-xs outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => setZohoModalOpen(false)}
                  className="flex-1 py-2 px-3 rounded-lg border border-zinc-200 dark:border-white/10 text-xs text-zinc-700 dark:text-zinc-300"
                >
                  Cancel
                </button>
                <button
                  onClick={async () => {
                    try {
                      await connectIntegration("zoho", {
                        code: zohoCode,
                        redirect_uri: "http://localhost:8000/api/v1/integrations/zoho/connect",
                        extras: { org_id: zohoOrgId },
                      });
                      setZohoModalOpen(false);
                      refetchZoho();
                      alert("Zoho Books successfully connected!");
                    } catch (err: unknown) {
                      alert(err instanceof Error ? err.message : "Connect failed");
                    }
                  }}
                  disabled={!zohoCode}
                  className="flex-1 py-2 px-3 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs disabled:opacity-50"
                >
                  Exchange & Connect
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>

      {/* --- QUICKBOOKS OAUTH MODAL --- */}
      <AnimatePresence>
        {qboModalOpen && (
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="w-full max-w-md bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-white/10 rounded-2xl p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold text-zinc-900 dark:text-white">Connect QuickBooks Online</h3>
                <button onClick={() => setQboModalOpen(false)} className="text-zinc-400 hover:text-white cursor-pointer">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <p className="text-xs text-zinc-500">
                Obtain authorization code from Intuit OAuth playground or redirect URI with scope <code className="font-mono">com.intuit.quickbooks.accounting</code>.
              </p>

              <div className="space-y-3 text-xs">
                <div>
                  <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">Authorization Code *</label>
                  <input
                    type="text"
                    placeholder="AB11xxxxxxxx"
                    value={qboCode}
                    onChange={(e) => setQboCode(e.target.value.trim())}
                    className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black font-mono text-xs outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>

                <div>
                  <label className="block text-zinc-700 dark:text-zinc-300 font-medium mb-1">Realm ID (Company ID) *</label>
                  <input
                    type="text"
                    placeholder="e.g. 4620816365312345"
                    value={qboRealmId}
                    onChange={(e) => setQboRealmId(e.target.value.trim())}
                    className="w-full px-3 py-2 rounded-lg border border-zinc-200 dark:border-white/10 bg-zinc-50 dark:bg-black text-xs outline-none focus:ring-2 focus:ring-emerald-500"
                  />
                </div>
              </div>

              <div className="flex gap-2 pt-2">
                <button
                  onClick={() => setQboModalOpen(false)}
                  className="flex-1 py-2 px-3 rounded-lg border border-zinc-200 dark:border-white/10 text-xs text-zinc-700 dark:text-zinc-300"
                >
                  Cancel
                </button>
                <button
                  onClick={async () => {
                    try {
                      await connectIntegration("quickbooks", {
                        code: qboCode,
                        redirect_uri: "http://localhost:8000/api/v1/integrations/quickbooks/connect",
                        extras: { realm_id: qboRealmId },
                      });
                      setQboModalOpen(false);
                      refetchQbo();
                      alert("QuickBooks Online successfully connected!");
                    } catch (err: unknown) {
                      alert(err instanceof Error ? err.message : "Connect failed");
                    }
                  }}
                  disabled={!qboCode || !qboRealmId}
                  className="flex-1 py-2 px-3 rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white font-semibold text-xs disabled:opacity-50"
                >
                  Exchange & Connect
                </button>
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}

export default function PaymentsPage() {
  return (
    <Suspense fallback={<div className="min-h-screen p-8 text-xs text-zinc-500">Loading Payments & ERP...</div>}>
      <PaymentsContent />
    </Suspense>
  );
}

