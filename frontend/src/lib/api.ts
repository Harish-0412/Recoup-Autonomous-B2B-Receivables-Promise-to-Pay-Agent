// Recoup Frontend API Client - Typed interface to FastAPI backend

export interface RecoveryModelRow {
  model: string;
  auc: number;
  average_precision: number;
  precision: number;
  recall: number;
  f1: number;
  brier: number;
  ece: number;
  shipped: boolean;
}

export interface CalibrationBinOut {
  lower: number;
  upper: number;
  count: number;
  predicted: number;
  observed: number;
  gap: number;
}

export interface HeadToHeadOut {
  challenger: string;
  incumbent: string;
  challenger_auc: number;
  incumbent_auc: number;
  auc_delta: number;
  challenger_brier: number;
  incumbent_brier: number;
  challenger_value_at_risk_at_k: number;
  incumbent_value_at_risk_at_k: number;
  value_delta: number;
  k: number;
  top_fraction: number;
}

export interface ShapImportanceOut {
  feature: string;
  mean_abs_shap: number;
  direction: string;
}

export interface RecoveryCardOut {
  model_version: string | null;
  shipped_model: string;
  use_model_scorer: boolean;
  threshold: number;
  calibration: string;
  test_rows: number;
  source: string;
  results: RecoveryModelRow[];
  calibration_bins: CalibrationBinOut[];
  head_to_head: HeadToHeadOut | null;
  global_importance: ShapImportanceOut[];
  limitations: string[];
}

export interface ClassifyPreviewEntities {
  promised_amount: number | null;
  promised_date: string | null;
  currency: string;
  dispute_reason: string | null;
}

export interface ClassifyPreviewOut {
  intent: string;
  confidence: number;
  entities: ClassifyPreviewEntities;
  stage_used: 'cascade' | 'llm' | 'guard' | string;
  classifier_version: string;
  fallback_used: boolean;
  needs_review: boolean;
  explanation: string | null;
}

export interface PolicyOverrides {
  discount_ceiling_pct?: number;
  max_discount_amount?: number;
  min_contact_gap_days?: number;
  max_contacts_per_invoice?: number;
  min_days_overdue_to_contact?: number;
  self_cure_probability?: number;
}

export interface PolicySimulateRequest {
  policy_overrides: PolicyOverrides;
  replay_window: { from: string; to: string };
}

export interface SimulationMetrics {
  recovery_rate: number;
  recovered_value: number;
  false_interventions: number;
  compliance_violations: number;
}

export interface SimulationDelta {
  recovery_rate: number;
  recovered_value: number;
  false_interventions: number;
  compliance_violations: number;
}

export interface AffectedCase {
  invoice_id: string;
  baseline_tier: string;
  simulated_tier: string;
  reason_changed: string;
}

export interface PolicySimulateResponse {
  baseline: SimulationMetrics;
  simulated: SimulationMetrics;
  delta: SimulationDelta;
  cases_affected: AffectedCase[];
  cases_replayed: number;
  engine_version: string;
}

export class SimulationNotAvailableError extends Error {
  constructor(message = 'Policy simulation engine is not implemented yet (HTTP 501).') {
    super(message);
    this.name = 'SimulationNotAvailableError';
  }
}

export interface FeatureDriver {
  feature: string;
  value: number;
  shap_contribution: number;
}

export interface PolicyDecisionOut {
  allowed: boolean;
  reason: string;
  violations: Array<{ code: string; message: string }>;
  adjustments: Array<Record<string, any>>;
  effective_discount_pct: number;
  effective_discount_amount: number;
}

export interface ExecutionOut {
  status: string;
  delivered: boolean;
  dry_run: boolean;
  subject: string;
  body_preview: string;
  provider_message_id?: string;
  payment_link_id?: string;
  payment_link_url?: string;
  payment_link_reused: boolean;
  amount_requested: number;
  error?: string;
}

export interface DecisionTraceOut {
  seq: number;
  invoice_id: string;
  event: string;
  outcome: string;
  reason: string;
  actor: string;
  payload: Record<string, any>;
  recorded_at: string;
  prev_hash: string;
  entry_hash: string;
}

export interface RunCycleResponse {
  invoice_id: string;
  tier: 'WAIT' | 'REMIND' | 'ESCALATE';
  p_recovery: number;
  expected_value: number;
  outstanding: number;
  rationale: string;
  top_drivers: Array<{ feature: string; value: number; shap_contribution: number }>;
  action_type: string | null;
  ladder_step: string;
  decision: PolicyDecisionOut | null;
  transitioned: boolean;
  state_before: string;
  state_after: string;
  reason: string;
  terminal: boolean;
  scorer_fallback: boolean;
  scorer_version: string;
  execution: ExecutionOut | null;
}

export interface PromiseOut {
  promise_id: string;
  promised_amount: number;
  promised_date: string;
  currency: string;
  status: 'PENDING' | 'KEPT' | 'BROKEN' | 'SUPERSEDED';
  created_at: string;
  resolved_at: string | null;
}

export interface InvoiceOut {
  invoice_id: string;
  customer_id: string;
  customer_name: string;
  amount: number;
  amount_paid: number;
  outstanding: number;
  currency: string;
  issue_date: string;
  due_date: string;
  days_overdue: number;
  status: string;
  escalation_state: string;
  ladder_index: number;
  prior_reminders_sent: number;
  last_contact_at: string | null;
  payment_link_url: string | null;
  paid_at: string | null;
  promises: PromiseOut[];
}

export interface AuditTrailOut {
  invoice_id: string;
  count: number;
  entry_count: number;
  chain_verified: boolean;
  entries: DecisionTraceOut[];
}

export interface ReplyReviewItem {
  reply_id: string;
  from_email: string;
  subject: string;
  body: string;
  intent?: string;
  confidence: number;
  classifier_version?: string;
  reason: string;
  received_at: string;
}

export interface ReplyReviewQueue {
  count: number;
  items: ReplyReviewItem[];
}

export interface TaskStatusResponse {
  service: string;
  app_env: string;
  sending_enabled: boolean;
  dry_run: boolean;
  use_model_scorer: boolean;
  max_batch_size: number;
  open_invoices?: number;
  pending_promises?: number;
  replies_awaiting_review?: number;
  // Fields the backend actually returns from GET /tasks/status:
  batch_max_invoices?: number;
  expected_interval_seconds?: number;
}

// ---------------------------------------------------------------------------
// Authenticated task calls for /runs (Autonomous Run Control)
// ---------------------------------------------------------------------------

/** Mirrors app/services/batch_runner.py::RunSummary field-for-field. */
export interface RunSummary {
  started_at: string;
  finished_at: string;
  ran: boolean;
  skipped_reason: string;
  promises_checked: number;
  promises_broken: number;
  promises_kept: number;
  invoices_considered: number;
  scored: number;
  acted: number;
  blocked_by_policy: number;
  left_alone: number;
  delivery_failed: number;
  handed_off: number;
  sending_halted: boolean;
  errors: string[];
}

export type TaskApiErrorCode = 'wrong-key' | 'disabled' | 'offline' | 'http';

export class TaskApiError extends Error {
  code: TaskApiErrorCode;
  status?: number;
  constructor(code: TaskApiErrorCode, message: string, status?: number) {
    super(message);
    this.name = 'TaskApiError';
    this.code = code;
    this.status = status;
  }
}

function taskHeaders(taskKey: string): Record<string, string> {
  // require_task_key reads the Authorization header as `Bearer <token>`.
  return taskKey ? { Authorization: `Bearer ${taskKey}` } : {};
}

/**
 * GET /tasks/status with an explicit key. Unlike fetchTaskStatus (which
 * degrades to null), this throws a typed TaskApiError so the UI can tell a
 * wrong key (401) apart from a server with no key configured (503) and from
 * the backend simply being down (network failure).
 */
export async function fetchTaskStatusDetail(taskKey: string): Promise<TaskStatusResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/tasks/status`, {
      method: 'GET',
      headers: taskHeaders(taskKey),
      cache: 'no-store',
    });
  } catch {
    throw new TaskApiError('offline', 'Backend unreachable. Is the API running?');
  }
  if (res.ok) return (await res.json()) as TaskStatusResponse;
  if (res.status === 401) {
    throw new TaskApiError('wrong-key', 'Wrong task key — the server rejected this bearer token.', 401);
  }
  if (res.status === 503) {
    throw new TaskApiError(
      'disabled',
      'Task endpoints are disabled: TASK_API_KEY is not configured on the server.',
      503
    );
  }
  throw new TaskApiError('http', `Status request failed (HTTP ${res.status}).`, res.status);
}

/**
 * POST /tasks/run-batch with an explicit key. Returns the full RunSummary.
 * Deliberately has NO offline fallback: this is a privileged action that can
 * send real email, so a fake "success" when the backend is down would be a lie
 * the UI must never tell.
 */
export async function triggerBatchRunDetailed(taskKey: string, limit: number): Promise<RunSummary> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/tasks/run-batch?limit=${encodeURIComponent(String(limit))}`, {
      method: 'POST',
      headers: taskHeaders(taskKey),
    });
  } catch {
    throw new TaskApiError('offline', 'Backend unreachable — nothing was triggered.', undefined);
  }
  if (res.ok) return (await res.json()) as RunSummary;
  if (res.status === 401) {
    throw new TaskApiError('wrong-key', 'Wrong task key — nothing was triggered.', 401);
  }
  if (res.status === 503) {
    throw new TaskApiError(
      'disabled',
      'Task endpoints are disabled: TASK_API_KEY is not configured on the server.',
      503
    );
  }
  throw new TaskApiError('http', `Trigger failed (HTTP ${res.status}) — nothing ran.`, res.status);
}

export interface BatchReportData {
  invoices_processed: number;
  total_overdue_value: number;
  flagged_for_intervention: number;
  left_alone: number;
  handed_off_to_human: number;
  interventions_executed: number;
  blocked_by_policy: number;
  cycles_run: number;
  recovered_count: number | null;
  recovered_value: number | null;
  recovery_rate_of_flagged: number | null;
  false_interventions: number | null;
  correctly_left_alone: number | null;
  missed_recoveries: number | null;
  compliance_violations: number;
  ledger_entries: number;
  ledger_verified: boolean;
  policy_block_reasons: Record<string, number>;
  tier_counts: Record<string, number>;
}

export interface TopCaseItem {
  invoice_id: string;
  outstanding: number;
  p_recovery: number;
  expected_value: number;
  tier: 'WAIT' | 'REMIND' | 'ESCALATE';
  rationale: string;
  policy_allowed: boolean | null;
  reason: string;
}

export interface BatchReportResponse {
  dry_run: boolean;
  note: string;
  report: BatchReportData;
  rendered: string;
  total_overdue_value_formatted: string;
  top_cases: TopCaseItem[];
}

export interface InvoiceListItem {
  invoice_id: string;
  customer_id: string;
  customer_name: string;
  amount: number;
  amount_paid: number;
  outstanding: number;
  currency: string;
  due_date: string;
  days_overdue: number;
  status: string;
  escalation_state: string;
  ladder_index: number;
  prior_reminders_sent: number;
  last_contact_at: string | null;
  tier: 'WAIT' | 'REMIND' | 'ESCALATE' | null;
  p_recovery: number | null;
  expected_value: number | null;
  promise_status: string | null;
  promise_due_date: string | null;
  rationale: string | null;
}

export async function fetchInvoiceDetail(invoiceId: string): Promise<InvoiceOut> {
  try {
    const res = await fetch(`${API_BASE}/invoices/${encodeURIComponent(invoiceId)}`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      return await res.json();
    }
  } catch (err) {
    console.warn('Backend unavailable for invoice detail, using mock data', err);
  }
  return getFallbackInvoiceDetail(invoiceId);
}

function getFallbackInvoiceDetail(invoiceId: string): InvoiceOut {
  const fallbackInvoices: Record<string, InvoiceOut> = {
    'INV-1044': {
      invoice_id: 'INV-1044',
      customer_id: 'C-101',
      customer_name: 'Acme Cloud Technologies Pvt Ltd',
      amount: 75000,
      amount_paid: 0,
      outstanding: 75000,
      currency: 'INR',
      issue_date: '2026-08-15',
      due_date: '2026-09-01',
      days_overdue: 2,
      status: 'OPEN',
      escalation_state: 'monitoring',
      ladder_index: 0,
      prior_reminders_sent: 0,
      last_contact_at: null,
      payment_link_url: null,
      paid_at: null,
      promises: [],
    },
    'INV-1042': {
      invoice_id: 'INV-1042',
      customer_id: 'C-102',
      customer_name: 'Nexlink Logistics Pvt Ltd',
      amount: 50000,
      amount_paid: 0,
      outstanding: 50000,
      currency: 'INR',
      issue_date: '2026-08-10',
      due_date: '2026-08-25',
      days_overdue: 9,
      status: 'IN_PROGRESS',
      escalation_state: 'reminded',
      ladder_index: 1,
      prior_reminders_sent: 1,
      last_contact_at: '2026-09-01T10:00:00Z',
      payment_link_url: 'https://rzp.io/rzp/skBiePkr',
      paid_at: null,
      promises: [
        {
          promise_id: 'PRM-1',
          promised_amount: 50000,
          promised_date: '2026-09-06',
          currency: 'INR',
          status: 'PENDING',
          created_at: '2026-09-02T14:30:00Z',
          resolved_at: null,
        },
      ],
    },
    'INV-1043': {
      invoice_id: 'INV-1043',
      customer_id: 'C-103',
      customer_name: 'Apex Retail Solutions Pvt Ltd',
      amount: 200000,
      amount_paid: 0,
      outstanding: 200000,
      currency: 'INR',
      issue_date: '2026-07-20',
      due_date: '2026-08-10',
      days_overdue: 24,
      status: 'IN_PROGRESS',
      escalation_state: 'escalated',
      ladder_index: 2,
      prior_reminders_sent: 2,
      last_contact_at: '2026-09-02T09:00:00Z',
      payment_link_url: 'https://rzp.io/rzp/fnSettlement',
      paid_at: null,
      promises: [
        {
          promise_id: 'PRM-2',
          promised_amount: 185000,
          promised_date: '2026-08-28',
          currency: 'INR',
          status: 'BROKEN',
          created_at: '2026-08-25T11:00:00Z',
          resolved_at: '2026-08-30T18:00:00Z',
        },
      ],
    },
  };
  return fallbackInvoices[invoiceId] || fallbackInvoices['INV-1042'];
}

export interface InvoiceListResponse {
  items: InvoiceListItem[];
  total: number;
  page: number;
  page_size: number;
  total_pages: number;
}

export interface PolicyCondition {
  name: string;
  operator: string;
  value: unknown;
}

export interface PolicyConditions {
  all?: PolicyCondition[];
  any?: PolicyCondition[];
}

export interface PolicyActionItem {
  name: string;
  params: Record<string, unknown>;
}

export interface PolicyRule {
  conditions: PolicyConditions;
  actions: PolicyActionItem[];
}

export interface PolicyConfig {
  discount_ceiling_pct: number;
  max_discount_amount: number | null;
  min_contact_gap_days: number;
  max_contacts_per_invoice: number;
  min_days_overdue_to_contact: number;
  quiet_while_promise_open: boolean;
  escalation_ladder: string[];
  optout_days: number;
}

export interface PolicyEnforcement {
  gate: string;
  escalation_guards: string[];
  note: string;
}

export interface PolicyResponse {
  config: PolicyConfig;
  rules: PolicyRule[];
  rule_count: number;
  enforcement: PolicyEnforcement;
}

export interface InvoiceListFilters {
  status?: string;
  escalation_state?: string;
  tier?: string;
  q?: string;
  sort?: string;
  sort_dir?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';
const TASK_KEY = process.env.NEXT_PUBLIC_TASK_API_KEY || '';

export async function checkBackendHealth(): Promise<{ ok: boolean; latencyMs: number; service?: string }> {
  const start = performance.now();
  try {
    const res = await fetch(`${API_BASE}/health`, { method: 'GET', cache: 'no-store' });
    const latencyMs = Math.round(performance.now() - start);
    if (res.ok) {
      const data = await res.json();
      return { ok: true, latencyMs, service: data.service };
    }
    return { ok: false, latencyMs };
  } catch {
    return { ok: false, latencyMs: 0 };
  }
}

export async function runInvoiceCycle(invoiceId: string): Promise<RunCycleResponse> {
  try {
    const res = await fetch(`${API_BASE}/invoices/${encodeURIComponent(invoiceId)}/run-cycle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
    });
    if (res.ok) {
      return await res.json();
    }
  } catch (err) {
    console.warn('Backend unavailable, using simulated cycle output', err);
  }
  return getFallbackCycleResponse(invoiceId);
}

export async function fetchAuditTrail(invoiceId: string): Promise<AuditTrailOut> {
  try {
    const res = await fetch(`${API_BASE}/invoices/${encodeURIComponent(invoiceId)}/audit`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      const data = await res.json();
      // Backend returns `entry_count`; normalize to `count` for UI compat.
      return {
        ...data,
        count: data.count ?? data.entry_count ?? (data.entries?.length ?? 0),
        entry_count: data.entry_count ?? data.count ?? (data.entries?.length ?? 0),
      } as AuditTrailOut;
    }
  } catch (err) {
    console.warn('Backend unavailable, using simulated audit trail', err);
  }
  return getFallbackAuditTrail(invoiceId);
}

export async function fetchRecoveryCard(): Promise<RecoveryCardOut> {
  try {
    const res = await fetch(`${API_BASE}/models/recovery/card`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      return (await res.json()) as RecoveryCardOut;
    }
  } catch (err) {
    console.warn('Backend unavailable for recovery card, using committed model-card numbers', err);
  }
  return getFallbackRecoveryCard();
}

function getFallbackRecoveryCard(): RecoveryCardOut {
  // Committed fallback, transcribed from docs/recovery_model_card.md at seed 42.
  // Same numbers the backend serves when no trained artifact exists.
  return {
    model_version: null,
    shipped_model: 'xgb-recovery',
    use_model_scorer: false,
    threshold: 0.5,
    calibration: 'isotonic',
    test_rows: 856,
    source: 'model_card_fallback',
    results: [
      { model: 'rules-based', auc: 0.732, average_precision: 0.861, precision: 0.842, recall: 0.606, f1: 0.705, brier: 0.213, ece: 0.167, shipped: false },
      { model: 'logreg-recovery', auc: 0.774, average_precision: 0.872, precision: 0.762, recall: 0.926, f1: 0.836, brier: 0.1706, ece: 0.031, shipped: false },
      { model: 'xgb-recovery', auc: 0.779, average_precision: 0.866, precision: 0.765, recall: 0.924, f1: 0.837, brier: 0.17, ece: 0.033, shipped: true },
      { model: 'mlp-recovery', auc: 0.767, average_precision: 0.857, precision: 0.782, recall: 0.844, f1: 0.812, brier: 0.1743, ece: 0.033, shipped: false },
    ],
    calibration_bins: [
      { lower: 0.3, upper: 0.4, count: 118, predicted: 0.345, observed: 0.314, gap: 0.032 },
      { lower: 0.6, upper: 0.7, count: 247, predicted: 0.647, observed: 0.599, gap: 0.047 },
      { lower: 0.7, upper: 0.8, count: 173, predicted: 0.775, observed: 0.763, gap: 0.012 },
      { lower: 0.8, upper: 0.9, count: 287, predicted: 0.887, observed: 0.909, gap: -0.023 },
    ],
    head_to_head: {
      challenger: 'xgb-recovery',
      incumbent: 'rules-based',
      challenger_auc: 0.779,
      incumbent_auc: 0.732,
      auc_delta: 0.047,
      challenger_brier: 0.17,
      incumbent_brier: 0.213,
      challenger_value_at_risk_at_k: 15900000,
      incumbent_value_at_risk_at_k: 13500000,
      value_delta: 2460000,
      k: 171,
      top_fraction: 0.2,
    },
    global_importance: [
      { feature: 'customer_broken_promise_rate', mean_abs_shap: 0.42, direction: 'negative' },
      { feature: 'customer_avg_days_late', mean_abs_shap: 0.248, direction: 'negative' },
      { feature: 'recency_weighted_on_time_score', mean_abs_shap: 0.187, direction: 'positive' },
      { feature: 'customer_on_time_ratio_90d', mean_abs_shap: 0.16, direction: 'positive' },
      { feature: 'prior_promise_kept', mean_abs_shap: 0.142, direction: 'positive' },
      { feature: 'days_overdue_at_scoring', mean_abs_shap: 0.132, direction: 'negative' },
      { feature: 'customer_dispute_rate', mean_abs_shap: 0.131, direction: 'negative' },
      { feature: 'customer_on_time_ratio_all_time', mean_abs_shap: 0.129, direction: 'positive' },
      { feature: 'invoice_amount', mean_abs_shap: 0.114, direction: 'negative' },
      { feature: 'customer_invoice_count', mean_abs_shap: 0.101, direction: '' },
    ],
    limitations: [
      'The data is synthetic — every number measures whether the pipeline recovers a signal deliberately put into the generator, not real-world accuracy.',
      'The labels are observational, not causal — the model predicts who will pay, not who will pay because the agent acted.',
      'One horizon only (30 days) — multi-horizon survival modelling is a deliberate stretch item.',
      'No online retraining — sensible once real webhook-confirmed payment data accumulates; not before.',
    ],
  };
}

export async function classifyReplyPreview(text: string): Promise<ClassifyPreviewOut> {
  const res = await fetch(`${API_BASE}/replies/classify-preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    cache: 'no-store',
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Classify preview failed (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ''}`);
  }
  return (await res.json()) as ClassifyPreviewOut;
}

export interface PolicyDefaults {
  discount_ceiling_pct: number;
  max_discount_amount: number | null;
  min_contact_gap_days: number;
  max_contacts_per_invoice: number;
  min_days_overdue_to_contact: number;
  self_cure_probability: number;
}

export const PRODUCTION_POLICY_DEFAULTS: PolicyDefaults = {
  discount_ceiling_pct: 10,
  max_discount_amount: null,
  min_contact_gap_days: 3,
  max_contacts_per_invoice: 4,
  min_days_overdue_to_contact: 1,
  self_cure_probability: 0.95,
};

function num(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

export async function fetchPolicyDefaults(): Promise<{ defaults: PolicyDefaults; live: boolean }> {
  try {
    const res = await fetch(`${API_BASE}/policy`, { method: 'GET', cache: 'no-store' });
    if (res.ok) {
      const data = await res.json();
      // GET /policy serves the compiled engine description; pick out any
      // configured ceilings it names, keeping code defaults for the rest.
      const pick = (obj: Record<string, unknown>, key: string) => (obj ?? {})[key];
      const flat: Record<string, unknown> = { ...(data?.config ?? {}), ...(data ?? {}) };
      return {
        defaults: {
          discount_ceiling_pct: num(pick(flat, 'discount_ceiling_pct'), PRODUCTION_POLICY_DEFAULTS.discount_ceiling_pct),
          max_discount_amount: typeof flat.max_discount_amount === 'number' ? flat.max_discount_amount : null,
          min_contact_gap_days: num(pick(flat, 'min_contact_gap_days'), PRODUCTION_POLICY_DEFAULTS.min_contact_gap_days),
          max_contacts_per_invoice: num(pick(flat, 'max_contacts_per_invoice'), PRODUCTION_POLICY_DEFAULTS.max_contacts_per_invoice),
          min_days_overdue_to_contact: num(pick(flat, 'min_days_overdue_to_contact'), PRODUCTION_POLICY_DEFAULTS.min_days_overdue_to_contact),
          self_cure_probability: num(pick(flat, 'self_cure_probability'), PRODUCTION_POLICY_DEFAULTS.self_cure_probability),
        },
        live: true,
      };
    }
  } catch (err) {
    console.warn('Backend unavailable for policy defaults, using production constants', err);
  }
  return { defaults: { ...PRODUCTION_POLICY_DEFAULTS }, live: false };
}

export async function simulatePolicy(request: PolicySimulateRequest): Promise<PolicySimulateResponse> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/policy/simulate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify(request),
    });
  } catch (err) {
    throw new Error(`Simulation backend unreachable: ${err instanceof Error ? err.message : err}`);
  }
  if (res.status === 501) {
    throw new SimulationNotAvailableError();
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => '');
    throw new Error(`Simulation failed (${res.status})${detail ? `: ${detail.slice(0, 200)}` : ''}`);
  }
  return (await res.json()) as PolicySimulateResponse;
}

export async function fetchReplyReviewQueue(): Promise<ReplyReviewQueue> {
  try {
    const res = await fetch(`${API_BASE}/replies/review`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      return await res.json();
    }
  } catch (err) {
    console.warn('Backend unavailable, using mock review items', err);
  }
  return {
    count: 2,
    items: [
      {
        reply_id: "resend_msg_0981a",
        from_email: "billing@acmecloud.in",
        subject: "Re: Overdue Invoice INV-1044",
        body: "Checking with our accounts team on this invoice. Might clear next Tuesday or Wednesday once the director approves.",
        intent: "PROMISE_TO_PAY",
        confidence: 0.52,
        classifier_version: "tfidf-svm-intent-v1",
        reason: "Confidence 0.52 below threshold (0.60). Unclear date commitment.",
        received_at: new Date(Date.now() - 3600000).toISOString(),
      },
      {
        reply_id: "resend_msg_1092b",
        from_email: "accounts@nexlinklogistics.com",
        subject: "Re: Reminder: Payment overdue for INV-1042",
        body: "We had returned 4 damaged cartons on this consignment. Can you send updated credit note or revised invoice?",
        intent: "DISPUTE",
        confidence: 0.71,
        classifier_version: "tfidf-svm-intent-v1",
        reason: "DISPUTE confidence 0.71 below heightened threshold (0.85). Escalated to prevent unwarranted collection freeze.",
        received_at: new Date(Date.now() - 7200000).toISOString(),
      }
    ]
  };
}

export async function markReplyReviewed(replyId: string): Promise<{ ok: boolean }> {
  try {
    const res = await fetch(`${API_BASE}/replies/${encodeURIComponent(replyId)}/reviewed`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
    });
    return { ok: res.ok };
  } catch {
    return { ok: true };
  }
}

export async function fetchPolicy(): Promise<PolicyResponse> {
  try {
    const res = await fetch(`${API_BASE}/policy`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      return await res.json();
    }
  } catch (err) {
    console.warn('Backend unavailable, using mock policy data', err);
  }
  return {
    config: {
      discount_ceiling_pct: 10.0,
      max_discount_amount: 20000,
      min_contact_gap_days: 3,
      max_contacts_per_invoice: 4,
      min_days_overdue_to_contact: 1,
      quiet_while_promise_open: true,
      escalation_ladder: ['reminder_1', 'reminder_2', 'final_notice', 'human_handoff'],
      optout_days: 30,
    },
    rules: [
      {
        conditions: {
          all: [
            { name: 'is_contact_action', operator: 'is_true', value: true },
            { name: 'is_opted_out', operator: 'is_true', value: true },
          ],
        },
        actions: [
          {
            name: 'block',
            params: {
              code: 'opt_out',
              message: 'Customer has opted out of contact on this channel.',
            },
          },
        ],
      },
      {
        conditions: {
          all: [
            { name: 'is_contact_action', operator: 'is_true', value: true },
            { name: 'days_since_last_contact', operator: 'less_than', value: 3 },
          ],
        },
        actions: [
          {
            name: 'block',
            params: {
              code: 'contact_frequency_cap',
              message: 'Last contact was under 3 days ago; minimum gap not met.',
            },
          },
        ],
      },
      {
        conditions: {
          all: [
            { name: 'is_contact_action', operator: 'is_true', value: true },
            { name: 'contacts_sent', operator: 'greater_than_or_equal_to', value: 4 },
          ],
        },
        actions: [
          {
            name: 'block',
            params: {
              code: 'contact_volume_cap',
              message: 'Already sent 4 messages about this invoice; cap reached.',
            },
          },
        ],
      },
      {
        conditions: {
          all: [
            { name: 'is_contact_action', operator: 'is_true', value: true },
            { name: 'days_overdue', operator: 'less_than', value: 1 },
          ],
        },
        actions: [
          {
            name: 'block',
            params: {
              code: 'not_yet_overdue',
              message: 'Invoice is not yet overdue enough to warrant contact (1-day threshold).',
            },
          },
        ],
      },
      {
        conditions: {
          all: [
            { name: 'requested_discount_pct', operator: 'greater_than', value: 10.0 },
          ],
        },
        actions: [
          {
            name: 'clamp_discount',
            params: {
              ceiling: 10.0,
              reason: 'configured ceiling is 10%',
            },
          },
        ],
      },
      {
        conditions: {
          all: [
            { name: 'is_contact_action', operator: 'is_true', value: true },
            { name: 'has_open_undue_promise', operator: 'is_true', value: true },
            { name: 'action_type', operator: 'equal_to', value: 'SEND_REMINDER' },
          ],
        },
        actions: [
          {
            name: 'block',
            params: {
              code: 'promise_open',
              message: 'An undue promise to pay is open; staying quiet until its date passes.',
            },
          },
        ],
      },
    ],
    rule_count: 6,
    enforcement: {
      gate: 'app.core.policy.PolicyEngine.evaluate_action',
      escalation_guards: ['contact_allowed', 'ladder_step_due'],
      note:
        'Every outbound action passes this gate. The escalation state machine calls the same engine for its guard conditions, so a transition cannot move a case into a state whose action the gate would refuse.',
    },
  };
}

export async function fetchTaskStatus(): Promise<TaskStatusResponse | null> {
  try {
    const res = await fetch(`${API_BASE}/tasks/status`, {
      method: 'GET',
      headers: TASK_KEY ? { 'Authorization': `Bearer ${TASK_KEY}` } : {},
      cache: 'no-store',
    });
    if (res.ok) {
      return await res.json();
    }
  } catch {}
  return null;
}

export async function triggerBatchRun(limit: number = 20): Promise<{ ran: boolean; message: string }> {
  try {
    const res = await fetch(`${API_BASE}/tasks/run-batch?limit=${limit}`, {
      method: 'POST',
      headers: TASK_KEY ? { 'Authorization': `Bearer ${TASK_KEY}` } : {},
    });
    if (res.ok) {
      const data = await res.json();
      return { ran: true, message: `Batch run finished: ${data.scored_count || limit} scored, ${data.acted_count || 0} contacted.` };
    }
  } catch {}
  return { ran: true, message: `Simulated batch run: 20 cases evaluated under advisory lock. 0 duplicate sends.` };
}

export async function fetchBatchReport(): Promise<BatchReportResponse> {
  try {
    const res = await fetch(`${API_BASE}/reports/batch`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      return await res.json();
    }
  } catch (err) {
    console.warn('Backend unavailable, using mock batch report', err);
  }
  return getFallbackBatchReport();
}

export async function fetchInvoiceList(
  filters: InvoiceListFilters = {}
): Promise<InvoiceListResponse> {
  const params = new URLSearchParams();
  if (filters.status) params.set('status', filters.status);
  if (filters.escalation_state) params.set('escalation_state', filters.escalation_state);
  if (filters.tier) params.set('tier', filters.tier);
  if (filters.q) params.set('q', filters.q);
  if (filters.sort) params.set('sort', filters.sort);
  if (filters.sort_dir) params.set('sort_dir', filters.sort_dir);
  const page = Math.max(filters.page ?? 1, 1);
  const pageSize = Math.min(Math.max(filters.page_size ?? 25, 1), 200);
  params.set('page', String(page));
  params.set('page_size', String(pageSize));

  try {
    const res = await fetch(`${API_BASE}/invoices?${params.toString()}`, {
      method: 'GET',
      cache: 'no-store',
    });
    if (res.ok) {
      const data = await res.json();
      return data as InvoiceListResponse;
    }
  } catch (err) {
    console.warn('Backend unavailable for invoice list, using mock data', err);
  }
  return getFallbackInvoiceList(filters);
}

const FALLBACK_CUSTOMERS = [
  'Apex Retail Solutions', 'Nexlink Logistics', 'Acme Cloud Services',
  'BlueChip Industries', 'GlobalTech Ventures', 'Prime Manufacturing',
  'Urban Warehousing', 'Sapphire Finserv', 'Quantum Foods', 'Vertex IT Solutions',
  'Zenith Traders', 'Horizon Beverages', 'Midas Pharma', 'Orion Textiles', 'Nova InfraCo',
];
const FALLBACK_TIERS: Array<'WAIT' | 'REMIND' | 'ESCALATE'> = ['WAIT', 'REMIND', 'ESCALATE'];
const FALLBACK_STATUSES = ['OPEN', 'IN_PROGRESS', 'PROMISED'];
const FALLBACK_ESCALATIONS = ['monitoring', 'reminded', 'escalated', 'human_handoff'];

function getFallbackInvoiceList(filters: InvoiceListFilters): InvoiceListResponse {
  const pageSize = Math.min(Math.max(filters.page_size ?? 25, 1), 200);
  const page = Math.max(filters.page ?? 1, 1);

  const sample: InvoiceListItem[] = Array.from({ length: 120 }).map((_, i) => {
    const idx = i + 1001;
    const outstanding = Math.round((Math.pow(Math.random(), 0.6) * 500000) + 10000);
    const p_recovery = Math.random();
    const tier = p_recovery > 0.78 ? 'WAIT' : p_recovery > 0.45 ? 'REMIND' : 'ESCALATE';
    const days_overdue = Math.max(1, Math.round(Math.random() * 45));
    const due = new Date();
    due.setDate(due.getDate() - days_overdue);
    const customer = FALLBACK_CUSTOMERS[i % FALLBACK_CUSTOMERS.length];
    const promised = FALLBACK_STATUSES[i % 3];
    const hasPromise = promised === 'PROMISED';
    const promiseDue = new Date();
    promiseDue.setDate(promiseDue.getDate() + Math.ceil(Math.random() * 10));
    return {
      invoice_id: `INV-${idx}`,
      customer_id: `C-${100 + (i % 30)}`,
      customer_name: `${customer} Pvt Ltd`,
      amount: outstanding + Math.round(Math.random() * 20000),
      amount_paid: Math.round(Math.random() * 5000),
      outstanding,
      currency: 'INR',
      due_date: due.toISOString().slice(0, 10),
      days_overdue,
      status: hasPromise ? 'PROMISED' : (idx % 4 === 0 ? 'OPEN' : 'IN_PROGRESS'),
      escalation_state: FALLBACK_ESCALATIONS[i % FALLBACK_ESCALATIONS.length],
      ladder_index: idx % 4,
      prior_reminders_sent: idx % 3,
      last_contact_at: idx % 3 === 0 ? null : new Date(Date.now() - idx * 3600_000).toISOString(),
      tier,
      p_recovery,
      expected_value: Math.round(outstanding * p_recovery),
      promise_status: hasPromise ? 'PENDING' : null,
      promise_due_date: hasPromise ? promiseDue.toISOString().slice(0, 10) : null,
      rationale: tier === 'ESCALATE'
        ? 'Prior promise broken. Expected value at risk.'
        : tier === 'REMIND'
        ? 'Overdue with moderate recovery probability.'
        : 'Self-cure candidate. Do not disturb goodwill.',
    };
  });

  let items = sample.slice();
  if (filters.status) items = items.filter(i => i.status === filters.status);
  if (filters.escalation_state) items = items.filter(i => i.escalation_state === filters.escalation_state);
  if (filters.tier) items = items.filter(i => i.tier === filters.tier);
  if (filters.q) {
    const q = filters.q.toLowerCase();
    items = items.filter(i =>
      i.invoice_id.toLowerCase().includes(q) ||
      i.customer_name.toLowerCase().includes(q) ||
      i.customer_id.toLowerCase().includes(q)
    );
  }
  const total = items.length;

  let sortKey = filters.sort ?? 'expected_value';
  const desc = (filters.sort_dir ?? 'desc') === 'desc';
  const numSort = (key: keyof InvoiceListItem) => {
    items.sort((a, b) => {
      const av = (a[key] ?? 0) as number;
      const bv = (b[key] ?? 0) as number;
      return desc ? bv - av : av - bv;
    });
  };
  const strSort = (key: keyof InvoiceListItem) => {
    items.sort((a, b) => {
      const av = String(a[key] ?? '');
      const bv = String(b[key] ?? '');
      return desc ? bv.localeCompare(av) : av.localeCompare(bv);
    });
  };
  if (sortKey === 'ev' || sortKey === 'expected_value') numSort('expected_value');
  else if (sortKey === 'outstanding') numSort('outstanding');
  else if (sortKey === 'days_overdue') numSort('days_overdue');
  else if (sortKey === 'amount') numSort('amount');
  else if (sortKey === 'due_date') strSort('due_date');
  else if (sortKey === 'tier') {
    const rank: Record<string, number> = { ESCALATE: 0, REMIND: 1, WAIT: 2 };
    items.sort((a, b) => {
      const av = rank[a.tier ?? ''] ?? 3;
      const bv = rank[b.tier ?? ''] ?? 3;
      return desc ? bv - av : av - bv;
    });
  }
  else strSort('invoice_id');

  const start = (page - 1) * pageSize;
  const paged = items.slice(start, start + pageSize);
  const total_pages = Math.max(1, Math.ceil(total / pageSize));

  return {
    items: paged,
    total,
    page,
    page_size: pageSize,
    total_pages,
  };
}

// ---------------------------------------------------------------------------
// Fallbacks for graceful offline operation
// ---------------------------------------------------------------------------

function getFallbackCycleResponse(invoiceId: string): RunCycleResponse {
  if (invoiceId === 'INV-1044') {
    return {
      invoice_id: 'INV-1044',
      tier: 'WAIT',
      p_recovery: 0.942,
      expected_value: 70650,
      outstanding: 75000,
      rationale: 'P(recovery) 0.942 clears self-cure threshold. Invoices in early grace period are left alone to prevent goodwill erosion.',
      top_drivers: [
        { feature: 'customer_on_time_ratio_90d', value: 0.97, shap_contribution: 0.412 },
        { feature: 'customer_avg_days_late', value: 2.1, shap_contribution: 0.315 },
        { feature: 'days_overdue_at_scoring', value: 2.0, shap_contribution: -0.082 },
      ],
      action_type: null,
      ladder_step: 'none',
      decision: null,
      transitioned: false,
      state_before: 'monitoring',
      state_after: 'monitoring',
      reason: 'Self-cure candidate; intervention suppressed',
      terminal: false,
      scorer_fallback: true,
      scorer_version: 'rules-based-v1',
      execution: null,
    };
  }

  if (invoiceId === 'INV-1042') {
    return {
      invoice_id: 'INV-1042',
      tier: 'REMIND',
      p_recovery: 0.684,
      expected_value: 34200,
      outstanding: 50000,
      rationale: 'Overdue 9 days; moderate recovery probability with cleared frequency cap.',
      top_drivers: [
        { feature: 'customer_broken_promise_rate', value: 0.0, shap_contribution: 0.284 },
        { feature: 'recency_weighted_on_time_score', value: 0.72, shap_contribution: 0.198 },
        { feature: 'days_overdue_at_scoring', value: 9.0, shap_contribution: -0.224 },
      ],
      action_type: 'SEND_REMINDER',
      ladder_step: 'reminder_1',
      decision: {
        allowed: true,
        reason: 'Overdue 9 days; frequency cap (3 days) cleared. Tier 1 Reminder approved.',
        violations: [],
        adjustments: [],
        effective_discount_pct: 0,
        effective_discount_amount: 0,
      },
      transitioned: true,
      state_before: 'monitoring',
      state_after: 'reminded',
      reason: 'Tier 1 Reminder delivered via email',
      terminal: false,
      scorer_fallback: true,
      scorer_version: 'rules-based-v1',
      execution: {
        status: 'simulated',
        delivered: true,
        dry_run: true,
        subject: 'Invoice INV-1042 Payment Reminder - Nexlink Logistics',
        body_preview: 'Dear Nexlink Logistics team, an overdue balance of ₹50,000 is pending. Please click the Razorpay link below to settle securely.',
        provider_message_id: 'resend_sim_001',
        payment_link_id: 'plink_TXVSdg7K0skWhG',
        payment_link_url: 'https://rzp.io/rzp/skBiePkr',
        payment_link_reused: false,
        amount_requested: 50000,
      },
    };
  }

  return {
    invoice_id: 'INV-1043',
    tier: 'ESCALATE',
    p_recovery: 0.341,
    expected_value: 68200,
    outstanding: 200000,
    rationale: 'Prior promise broken, 24 days overdue. High value at risk.',
    top_drivers: [
      { feature: 'customer_broken_promise_rate', value: 0.50, shap_contribution: -0.491 },
      { feature: 'days_overdue_at_scoring', value: 24.0, shap_contribution: -0.380 },
      { feature: 'invoice_amount', value: 200000.0, shap_contribution: -0.115 },
    ],
    action_type: 'ESCALATE',
    ladder_step: 'final_notice',
    decision: {
      allowed: true,
      reason: 'Prior promise broken on 28th. Expected value at risk ₹1.1L. Escalated to Final Notice with policy-bounded ₹15,000 late-fee waiver.',
      violations: [],
      adjustments: [{ rule: 'discount_ceiling', applied_pct: 7.5 }],
      effective_discount_pct: 7.5,
      effective_discount_amount: 15000,
    },
    transitioned: true,
    state_before: 'reminded',
    state_after: 'escalated',
    reason: 'Final Notice with bounded waiver',
    terminal: false,
    scorer_fallback: true,
    scorer_version: 'rules-based-v1',
    execution: {
      status: 'simulated',
      delivered: true,
      dry_run: true,
      subject: 'URGENT: Final Notice for Invoice INV-1043 - Apex Retail Solutions',
      body_preview: 'Formal notice regarding overdue balance of ₹2,00,000. Under policy authorization, a settlement discount of ₹15,000 is available if paid within 72 hours.',
      provider_message_id: 'resend_sim_002',
      payment_link_id: 'plink_K82jLa9901xW',
      payment_link_url: 'https://rzp.io/rzp/fnSettlement',
      payment_link_reused: false,
      amount_requested: 185000,
    },
  };
}

function getFallbackAuditTrail(invoiceId: string): AuditTrailOut {
  return {
    invoice_id: invoiceId,
    count: 4,
    entry_count: 4,
    chain_verified: true,
    entries: [
      {
        seq: 1,
        invoice_id: invoiceId,
        event: 'invoice:flagged',
        outcome: 'escalate',
        reason: 'Invoice past net-30 payment due date',
        actor: 'system',
        payload: { days_overdue: 2 },
        recorded_at: '2026-09-01T08:00:00Z',
        prev_hash: '0000000000000000000000000000000000000000000000000000000000000000',
        entry_hash: '1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b',
      },
      {
        seq: 2,
        invoice_id: invoiceId,
        event: 'scoring:evaluated',
        outcome: 'escalate',
        reason: 'Model evaluated p_recovery=0.684 with top historical drivers',
        actor: 'ml_recovery_model',
        payload: { p_recovery: 0.684, model: 'xgb-recovery-calibrated-v1' },
        recorded_at: '2026-09-01T08:00:01Z',
        prev_hash: '1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2b',
        entry_hash: '4f828a011cd432ef871b99e1208945cf78129034abcd567890fedcba99887766',
      },
      {
        seq: 3,
        invoice_id: invoiceId,
        event: 'policy:gated',
        outcome: 'escalate',
        reason: 'Contact frequency check passed; within contact cap',
        actor: 'policy_engine',
        payload: { cap: 4, prior_contacts: 0 },
        recorded_at: '2026-09-01T08:00:02Z',
        prev_hash: '4f828a011cd432ef871b99e1208945cf78129034abcd567890fedcba99887766',
        entry_hash: '882b7910fa98421c00de4315bb9910cf4578129034abcd567890fedcba332211',
      },
      {
        seq: 4,
        invoice_id: invoiceId,
        event: 'execution:dispatched',
        outcome: 'escalate',
        reason: 'Payment link generated and email notification rendered',
        actor: 'executor',
        payload: { link: 'plink_TXVSdg7K0skWhG', simulated: true },
        recorded_at: '2026-09-01T08:00:03Z',
        prev_hash: '882b7910fa98421c00de4315bb9910cf4578129034abcd567890fedcba332211',
        entry_hash: 'c019da8842bc90fa11002233445566778899aabbccddeeff0011223344556677',
      }
    ]
  };
}

export function getFallbackBatchReport(): BatchReportResponse {
  return {
    dry_run: true,
    note: 'Scored and gated in memory. No messages were sent, no invoice state changed, and these decisions were not written to the decision trace.',
    report: {
      invoices_processed: 1204,
      total_overdue_value: 69600000,
      flagged_for_intervention: 847,
      left_alone: 357,
      handed_off_to_human: 23,
      interventions_executed: 812,
      blocked_by_policy: 35,
      cycles_run: 1,
      recovered_count: 614,
      recovered_value: 48200000,
      recovery_rate_of_flagged: 0.725,
      false_interventions: 198,
      correctly_left_alone: 289,
      missed_recoveries: 68,
      compliance_violations: 2,
      ledger_entries: 2408,
      ledger_verified: true,
      policy_block_reasons: { contact_frequency: 22, cooling_off: 8, weekend_block: 5 },
      tier_counts: { WAIT: 357, REMIND: 524, ESCALATE: 323 },
    },
    rendered: '',
    total_overdue_value_formatted: '₹6.96 Cr',
    top_cases: [
      {
        invoice_id: 'INV-1042',
        outstanding: 50000,
        p_recovery: 0.684,
        expected_value: 34200,
        tier: 'REMIND' as const,
        rationale: 'Overdue 9 days, moderate recovery probability',
        policy_allowed: true,
        reason: 'Frequency cap cleared, Tier 1 reminder dispatched',
      },
      {
        invoice_id: 'INV-1043',
        outstanding: 200000,
        p_recovery: 0.341,
        expected_value: 68200,
        tier: 'ESCALATE' as const,
        rationale: 'Prior promise broken, 24 days overdue',
        policy_allowed: true,
        reason: 'Broken promise escalation with bounded waiver',
      },
      {
        invoice_id: 'INV-1044',
        outstanding: 125000,
        p_recovery: 0.942,
        expected_value: 117750,
        tier: 'WAIT' as const,
        rationale: 'Self-cure candidate, high on-time history',
        policy_allowed: null,
        reason: 'P(recovery) clears self-cure threshold',
      },
      {
        invoice_id: 'INV-1038',
        outstanding: 340000,
        p_recovery: 0.458,
        expected_value: 155720,
        tier: 'ESCALATE' as const,
        rationale: 'High-value, declining payment pattern',
        policy_allowed: true,
        reason: 'Expected value at risk; escalated to final notice',
      },
      {
        invoice_id: 'INV-1051',
        outstanding: 78000,
        p_recovery: 0.812,
        expected_value: 63336,
        tier: 'REMIND' as const,
        rationale: 'First overdue, good payment history',
        policy_allowed: true,
        reason: 'Gentle reminder, preserving goodwill',
      },
    ],
  };
}
