# Legal Opinion & Compliance Review: Autonomous B2B Dunning Cadence & Communications

**To:** Recoup Engineering & Product Architecture Teams  
**From:** Legal Counsel & Regulatory Compliance (India Practice)  
**Date:** 4 September 2026  
**Subject:** Statutory Review of Autonomous Dunning Cadence, Outreach Templates, WhatsApp Guidelines, and Policy Freeze under Indian Law  
**Status:** **APPROVED WITH MANDATORY STATUTORY FREEZE & CONSTRAINTS**

---

## 1. Executive Summary & Regulatory Authority

This memorandum sets forth the legal and regulatory boundaries governing Recoup's autonomous B2B receivables agent operating across Indian jurisdiction. Autonomous and programmatic collections must strictly conform to:

1. **Reserve Bank of India (RBI) Directives**:
   - *Master Direction – Non-Banking Financial Company – Systemically Important Non-Deposit taking Company and Deposit taking Company (Reserve Bank) Directions, 2016* (RBI/DNBR/2016-17/45)
   - *Guidelines on Fair Practices Code for Lenders* (DNBR.PD.CC.No.054/03.10.119/2015-16)
   - *RBI Circular on Outsourcing of Financial Services / Recovery Agents* (RBI/2022-23/108 DOR.ORG.REC.65/21.04.158/2022-23)
2. **State Moneylending and Debt Collection Statutes**:
   - Maharashtra Money-Lending (Regulation) Act, 2014; Karnataka Money-Lenders Act, 1961; and related state-level enactments prohibiting harassment, intimidation, extortionate collection practices, and unauthorized contact.
3. **Information Technology Act, 2000 & Data Protection**:
   - Information Technology (Reasonable Security Practices and Procedures and Sensitive Personal Data or Information) Rules, 2011 (SPDI Rules)
   - Digital Personal Data Protection Act, 2023 (DPDP Act) — Purpose limitation, confidentiality of financial status, right to withdraw consent, and immediate erasure/opt-out honouring.
4. **Telecom Regulatory Authority of India (TRAI)**:
   - Telecom Commercial Communications Customer Preference Regulations, 2018 (TCCCPR 2018) — National Do Not Call (NDNC/DND) registry, Distributed Ledger Technology (DLT) template registration, and Telemarketer licensing.
5. **Meta Platforms WhatsApp Business Messaging Policy**:
   - WhatsApp Business Terms of Service and Commerce Policy — Prohibiting aggressive collection, mandatory opt-in, mandatory opt-out keyword support, and pre-approved Highly Structured Message (HSM) utility templates.

---

## 2. Permissible Hours & Calling/Contact Windows

Under the RBI Recovery Agent Circular (Section 3.2) and TRAI TCCCPR Regulations, outbound digital communications (SMS, WhatsApp, and phone contacts) are subject to strictly enforced contact windows:

* **Permissible Window:** **08:00 to 19:00 IST** (Monday through Saturday).
* **Prohibited Windows:**
  - Any contact before 08:00 IST or after 19:00 IST is strictly **prohibited** and constitutes statutory harassment.
  - Sundays and Central/State Gazetted Public Holidays: Outbound automated debtor communications are **prohibited**.
* **Engine Enforcement:** Batch dispatch schedules (e.g. cron triggers) and timing bandits must never dispatch SMS or WhatsApp messages outside this 08:00–19:00 window. Automated contacts initiated outside this window must be queued or deferred until the next business morning at 08:30 IST.

---

## 3. Opt-Out Latency & Revocation of Consent

Under the SPDI Rules (Rule 5(7)), DPDP Act 2023 (Section 6(4)), and TRAI TCCCPR Regulations:

* **Latency Standard:** **IMMEDIATE (Zero Latency)**.
* **Operational Rule:** The moment a recipient conveys an intent to stop receiving messages (e.g., replying with `"STOP"`, `"UNSUBSCRIBE"`, `"OPT OUT"`, or equivalent intent recognized by Stage A/B classification), all automated outbound communication to that customer on that channel must cease immediately.
* **Architecture Validation:** Recoup's architecture satisfies this requirement by recording the opt-out synchronously in the tenant's `OptOutRegistry` and raising an immediate blocking constraint in `PolicyEngine.evaluate_action`. There is **no allowable grace period** (e.g., "allow 48 hours to process") for promotional or automated debt follow-ups.

---

## 4. Allowed Escalation Ladder & Contact Cadence

### 4.1 Statutory Cadence Floor (`LEGAL_MIN_CONTACT_GAP_DAYS`)

Under Indian common law and RBI Fair Practice mandates against persistent badgering:
* **Statutory Floor:** **2 days** (`LEGAL_MIN_CONTACT_GAP_DAYS = 2`). Contacting a business debtor more frequently than every 48 hours for the same commercial invoice creates prima facie exposure to harassment claims.
* **Approved Default Freeze:** **3 days** (`DEFAULT_MIN_CONTACT_GAP_DAYS = 3`).
* **Simulation Constraint:** Neither production policy nor what-if simulation (`POST /api/v1/policy/simulate`) may propose or test a contact gap below **2 days**. Any attempt to simulate a gap < 2 days must be rejected with HTTP 422.

### 4.2 Approved Escalation Ladder (`ESCALATION_LADDER_DAYS = 1,4,8,15`)

| Step Index | Ladder Step | Day Past Due | Min Gap from Prior Step | Nature & Tone of Message | Legal / Compliance Assessment |
|---|---|---|---|---|---|
| **0** | `reminder_1` | **Day +1** | — | Polite courtesy inquiry, invoice reference, payment link, opt-out note. | **APPROVED**. Pure commercial notification; non-coercive. |
| **1** | `reminder_2` | **Day +4** | 3 days | Business inquiry asking whether missing PO or invoice dispute exists. | **APPROVED**. Encourages dialogue and dispute identification. |
| **2** | `final_notice` | **Day +8** | 4 days | Formal notice stating automated reminders will cease and account will hand over to manual recovery. | **APPROVED**. Specific, non-threatening, states accurate administrative next step. |
| **3** | `human_handoff` | **Day +15** | 7 days | Autonomous agent ceases all outbound outreach; transitions case to human Accounts Receivable team. | **APPROVED**. Mandatory stopping rule. Agent never sends infinite loops. |

* **Max Contacts Cap:** `max_contacts_per_invoice = 4` (inclusive of final notice). The agent is strictly barred from autonomous contacts beyond this cap.

---

## 5. Review of Template Copy (`app/services/executor/templates.py`)

Counsel has conducted a sentence-level review of the rendered message templates:

1. **`_BODIES["reminder_1"]`**:
   - *"Our records show invoice {invoice_id} for {amount} became due on {due_date} and is now {days} days past due. If it is already scheduled for payment, please ignore this note."*
   - **Verdict: COMPLIANT**. Professional, objective, avoids accusatory phrasing.
2. **`_BODIES["reminder_2"]`**:
   - *"We wrote to you about invoice {invoice_id} for {amount}, which was due on {due_date} and is now {days} days past due. We have not yet seen the payment. If something is holding it up -- a missing purchase order, a query on the amount -- please reply and tell us; we would rather resolve it than keep writing."*
   - **Verdict: COMPLIANT**. Explicitly solicits resolution of commercial disputes rather than demanding payment blindly.
3. **`_BODIES["final_notice"]`**:
   - *"Invoice {invoice_id} for {amount} is now {days} days past due, and our previous reminders have gone unanswered. Unless the amount is paid or you contact us to arrange terms, this account will be passed to our team for manual recovery."*
   - **Verdict: COMPLIANT**. Avoids threats of litigation, police action, or credit defamation; accurately states administrative escalation.
4. **`_SETTLEMENT_BODY`**:
   - *"Invoice {invoice_id} for {amount} is {days} days past due. To close this without further escalation, we can accept {settlement_amount} -- a {discount_pct:g}% reduction. This offer applies to this invoice only."*
   - **Verdict: COMPLIANT**. Transparent discount terms; does not introduce compound penalties.
5. **Sign-off and Opt-out**:
   - *"If you believe this invoice is incorrect, reply to this email and we will put collection on hold while we look into it."*
   - *'To stop receiving reminders about this account, reply with "unsubscribe".'*
   - **Verdict: COMPLIANT**. Meets the standard for clear dispute routing and unhindered consent revocation.

---

## 6. Approved WhatsApp Template Language & DLT Specifications

Under Meta WhatsApp Business Messaging Policy and TRAI DLT guidelines, WhatsApp outreach must use pre-registered HSM (Highly Structured Message) utility templates. Plain freeform promotional text is prohibited.

### 6.1 Template 1: `b2b_payment_reminder_v1` (Utility Category)
```text
Hello {{1}},

This is a reminder regarding invoice {{2}} for {{3}} from {{4}}, which became due on {{5}}. 

You can view details and settle the invoice securely here: {{6}}

If payment is already in progress, or if you have queries regarding this invoice, please reply to this message.

To unsubscribe from WhatsApp reminders, reply "STOP".
```
* **Variables:** `{{1}}` Customer Name, `{{2}}` Invoice ID, `{{3}}` Amount in INR, `{{4}}` Supplier Name, `{{5}}` Due Date, `{{6}}` Payment Link URL.
* **Category:** UTILITY.

### 6.2 Template 2: `b2b_payment_final_notice_v1` (Utility Category)
```text
Hello {{1}},

Invoice {{2}} for {{3}} from {{4}} is now {{5}} days overdue. Our previous reminders remain unanswered.

Please settle the outstanding balance here: {{6}}

Unless payment or payment terms are arranged, this invoice will be transferred to our team for direct follow-up. Reply to this message to raise any dispute.

Reply "STOP" to opt out of WhatsApp communications.
```
* **Category:** UTILITY.

### 6.3 SMS TRAI DLT Requirements
1. **Header Registration:** 6-character Alpha Header registered with telecom operators (e.g., `RECOUP`, `SUPPLR`).
2. **Template ID:** Every SMS template must carry an active 19-digit DLT Template ID approved on the telecom DLT portal (Vilpower / PingConnect / JIO DLT).
3. **Opt-out Marker:** SMS must include standard trailer: `"To opt out SMS STOP to <shortcode>"`.

---

## 7. Catalogue of Forbidden Phrasing & Prohibited Tactics

Under Indian Penal Code (IPC) Sections 503/506 (Criminal Intimidation), Section 499 (Defamation), and RBI Fair Practice Directives, the autonomous agent is strictly forbidden from using or synthesizing the following phrasing:

| Category | Prohibited Phrasing / Tactic | Statutory / Legal Risk |
|---|---|---|
| **Legal Threats** | *"We will initiate criminal proceedings against you / FIR under Section 420."* | IPC Section 506 (Criminal Intimidation); false claim of criminal liability for commercial breach. |
| **Insolvency Threats** | *"We will file an IBC / NCLT petition to bankrupt your company."* | Coercive threat of legal process without statutory notice under Section 8 IBC. |
| **Defamation & Shaming** | *"You are marked as a chronic defaulter / fraudulent business in our public ledger."* | IPC Section 499/500 (Criminal & Civil Defamation); tortious interference. |
| **Third-Party Disclosure** | *"We will contact your board, clients, suppliers, or family members."* | SPDI Rules / DPDP Act Section 6; RBI Recovery Agent Guidelines (Section 3.1). |
| **Physical Intimidation** | *"Our field representatives will visit your office premises immediately."* | RBI Fair Practices Code; unlawful trespass / intimidation. |
| **Unlawful Interest / Penalties** | *"A daily late fee of 5% will be levied unless paid today."* | Indian Contract Act Section 74 (Unreasonable penalty clauses); Usurious Loans Act. |
| **Dispute Denial** | *"Disputes will only be entertained after full payment is received."* | Fair trade practice violation; Consumer Protection Act / MSMED Act Section 18. |

---

## 8. Policy Freeze Order

The following operational configuration parameters are subject to a **Strict Legal Freeze**:

```python
DEFAULT_MIN_CONTACT_GAP_DAYS = 3       # Frozen Default
LEGAL_MIN_CONTACT_GAP_DAYS = 2         # Statutory Floor (Hard Constraint)
DEFAULT_MAX_CONTACTS_PER_INVOICE = 4   # Hard Cap
DEFAULT_OPTOUT_DAYS = 30               # Minimum Opt-out Window
DEFAULT_ESCALATION_LADDER = (
    "reminder_1",
    "reminder_2",
    "final_notice",
    "human_handoff",
)
```

### Governance Rule
Any proposed modification to:
1. Reduce `min_contact_gap_days` below 3 days (or below the legal floor of 2 days);
2. Increase `max_contacts_per_invoice` above 4;
3. Alter or bypass the immediate opt-out mechanism;
4. Modify message copy in `templates.py` or WhatsApp templates;

**REQUIRES FORMAL RE-SUBMISSION TO AND WRITTEN APPROVAL FROM LEGAL COUNSEL PRIOR TO DEPLOYMENT.**

---
*Signed,*  
**Counsel for Regulatory Compliance & Commercial Technology**
