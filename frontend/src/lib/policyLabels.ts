/**
 * Plain-English translations for the policy gate's rule/violation codes.
 *
 * These codes come verbatim from `app/core/policy.py` (`PolicyViolation.code`,
 * `PolicyRule.code`) and `GET /policy`'s compiled rule set. They are shared
 * across `/policy`, `/runs`, `/invoices/[invoiceId]`, and `/reports/batch` so
 * a code always reads the same way everywhere it appears, instead of each
 * page inventing its own label or showing the raw snake_case string.
 */

export const POLICY_CODE_LABELS: Record<string, string> = {
  opt_out: "Customer opted out",
  contact_frequency_cap: "Contacted too recently",
  contact_volume_cap: "Contact limit reached",
  not_yet_overdue: "Not overdue enough yet",
  promise_open: "Open promise — staying quiet",
  discount_ceiling: "Discount above ceiling",
  discount_ceiling_pct: "Discount % above ceiling",
  max_discount_amount: "Discount amount above ceiling",
};

export const POLICY_CODE_DESCRIPTIONS: Record<string, string> = {
  opt_out:
    "The customer has opted out of contact on this channel. The gate blocks the action outright — there is no override.",
  contact_frequency_cap:
    "Another message was sent too recently. The minimum gap between contacts is a configured policy ceiling.",
  contact_volume_cap:
    "The maximum number of contacts allowed for this invoice has already been sent.",
  not_yet_overdue:
    "The invoice hasn't crossed the minimum days-overdue threshold that policy requires before contact is allowed.",
  promise_open:
    "An undue promise to pay is open for this invoice. Policy keeps the case quiet until the promise date passes or breaks.",
  discount_ceiling:
    "The requested settlement discount exceeded the configured ceiling, so it was clamped down rather than blocked.",
  discount_ceiling_pct:
    "The requested discount percentage exceeded the configured ceiling, so it was clamped down rather than blocked.",
  max_discount_amount:
    "The requested discount amount exceeded the configured absolute rupee ceiling, so it was clamped down.",
};

/** Humanized label for a policy code, falling back to a readable version of the raw code. */
export function describePolicyCode(code: string | undefined | null): string {
  if (!code) return "Unknown rule";
  return POLICY_CODE_LABELS[code] ?? code.replace(/_/g, " ");
}

/** Longer explanation of what a policy code means and why it fires, when known. */
export function policyCodeDetail(code: string | undefined | null): string | null {
  if (!code) return null;
  return POLICY_CODE_DESCRIPTIONS[code] ?? null;
}
