"""
generate_docx_final.py  –  Recoup Technical Documentation Generator
Run from the project root:
    python scripts/generate_docx_final.py
Produces: Recoup_Technical_Documentation.docx in the project root.
"""
import pathlib
import datetime
import re
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

# ── paths ──────────────────────────────────────────────────────────────────
ROOT    = pathlib.Path(__file__).resolve().parents[1]
ART_DIR = pathlib.Path(
    r"C:\Users\haris\.gemini\antigravity\brain\0bd0b9d8-0f57-4110-9f5d-a093dcdc4608"
)
IMG_ARCH = ART_DIR / "architecture_diagram_1788679647586.jpg"
IMG_ESM  = ART_DIR / "escalation_state_machine_1788679702289.jpg"
IMG_DB   = ART_DIR / "database_schema_1788679722671.jpg"
IMG_SEC  = ART_DIR / "security_model_1788679741868.jpg"

OUT = ROOT / "Recoup_Technical_Documentation.docx"

# ── colour palette ──────────────────────────────────────────────────────────
NAVY   = RGBColor(0x00, 0x33, 0x66)
TEAL   = RGBColor(0x00, 0x7A, 0x87)
DARK   = RGBColor(0x1A, 0x1A, 0x2E)
GREY   = RGBColor(0x55, 0x55, 0x55)
WHITE  = RGBColor(0xFF, 0xFF, 0xFF)

# ── helpers ─────────────────────────────────────────────────────────────────
def set_cell_bg(cell, hex_color: str):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tcPr.append(shd)


def heading1(doc, text):
    p = doc.add_heading(text, level=1)
    run = p.runs[0] if p.runs else p.add_run(text)
    run.font.color.rgb = NAVY
    run.font.size = Pt(18)
    return p


def heading2(doc, text):
    p = doc.add_heading(text, level=2)
    run = p.runs[0] if p.runs else p.add_run(text)
    run.font.color.rgb = TEAL
    run.font.size = Pt(14)
    return p


def heading3(doc, text):
    p = doc.add_heading(text, level=3)
    run = p.runs[0] if p.runs else p.add_run(text)
    run.font.color.rgb = DARK
    run.font.size = Pt(12)
    return p


def body(doc, text: str):
    p = doc.add_paragraph()
    run = p.add_run(text.strip())
    run.font.size = Pt(11)
    run.font.color.rgb = DARK
    return p


def note(doc, label: str, text: str):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Inches(0.4)
    r1 = p.add_run(f"【{label}】 ")
    r1.bold = True
    r1.font.color.rgb = TEAL
    r1.font.size = Pt(10)
    r2 = p.add_run(text.strip())
    r2.font.size = Pt(10)
    r2.font.color.rgb = GREY
    return p


def add_image(doc, path: pathlib.Path, caption: str, width: float = 6.2):
    if path.exists():
        doc.add_picture(str(path), width=Inches(width))
        last = doc.paragraphs[-1]
        last.alignment = WD_ALIGN_PARAGRAPH.CENTER
    else:
        body(doc, f"[diagram not found: {path.name}]")
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(caption)
    r.italic = True
    r.font.size = Pt(9.5)
    r.font.color.rgb = GREY
    doc.add_paragraph()


def add_kv_table(doc, rows: list[tuple[str, str]], col_w=(2.5, 4.0)):
    tbl = doc.add_table(rows=1, cols=2)
    tbl.style = "Light Grid Accent 1"
    hdr = tbl.rows[0].cells
    for i, h in enumerate(["Item", "Value"]):
        hdr[i].text = h
        hdr[i].paragraphs[0].runs[0].bold = True
    for k, v in rows:
        r = tbl.add_row().cells
        r[0].text = k
        r[1].text = v
    doc.add_paragraph()


def add_two_col_table(doc, header: tuple[str, str], rows: list[tuple[str, str]]):
    tbl = doc.add_table(rows=1, cols=2)
    tbl.style = "Light Grid Accent 1"
    hcells = tbl.rows[0].cells
    for i, h in enumerate(header):
        hcells[i].text = h
        hcells[i].paragraphs[0].runs[0].bold = True
    for a, b in rows:
        cells = tbl.add_row().cells
        cells[0].text = a
        cells[1].text = b
    doc.add_paragraph()


def add_three_col_table(doc, header: tuple, rows: list[tuple]):
    tbl = doc.add_table(rows=1, cols=3)
    tbl.style = "Light Grid Accent 1"
    for i, h in enumerate(header):
        hcells = tbl.rows[0].cells
        hcells[i].text = h
        hcells[i].paragraphs[0].runs[0].bold = True
    for row in rows:
        cells = tbl.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = v
    doc.add_paragraph()


def divider(doc):
    doc.add_paragraph("─" * 80).paragraph_format.space_after = Pt(2)


def toc_placeholder(doc):
    """Word-compatible TOC placeholder (manual update required)."""
    p = doc.add_paragraph()
    fld = OxmlElement("w:fldChar")
    fld.set(qn("w:fldCharType"), "begin")
    p._p.append(fld)
    instr = OxmlElement("w:instrText")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    p._p.append(instr)
    fld2 = OxmlElement("w:fldChar")
    fld2.set(qn("w:fldCharType"), "end")
    p._p.append(fld2)


# ══════════════════════════════════════════════════════════════════════════════
#  DOCUMENT BUILD
# ══════════════════════════════════════════════════════════════════════════════
doc = Document()

# ── normal margin ──────────────────────────────────────────────────────────
from docx.shared import Inches as I
for section in doc.sections:
    section.top_margin    = I(1)
    section.bottom_margin = I(1)
    section.left_margin   = I(1.1)
    section.right_margin  = I(1.1)

# ════════════════════════════════════════════════════════════════════════════
#  COVER PAGE
# ════════════════════════════════════════════════════════════════════════════
for _ in range(5):
    doc.add_paragraph()

p_title = doc.add_paragraph()
p_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p_title.add_run("Recoup")
r.bold = True
r.font.size  = Pt(52)
r.font.color.rgb = NAVY

p_sub = doc.add_paragraph()
p_sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
r2 = p_sub.add_run("Autonomous B2B Receivables & Promise-to-Pay Agent")
r2.font.size = Pt(18)
r2.font.color.rgb = TEAL

doc.add_paragraph()

p_desc = doc.add_paragraph()
p_desc.alignment = WD_ALIGN_PARAGRAPH.CENTER
r3 = p_desc.add_run("Complete Backend Technical Documentation")
r3.font.size = Pt(14)
r3.font.color.rgb = GREY
r3.italic = True

doc.add_paragraph()

p_date = doc.add_paragraph()
p_date.alignment = WD_ALIGN_PARAGRAPH.CENTER
r4 = p_date.add_run(f"Generated: {datetime.date.today().strftime('%d %B %Y')}")
r4.font.size = Pt(11)
r4.font.color.rgb = GREY

for _ in range(8):
    doc.add_paragraph()

p_version = doc.add_paragraph()
p_version.alignment = WD_ALIGN_PARAGRAPH.CENTER
rv = p_version.add_run("Version 1.0  |  Track 03: AI Revenue Recovery  |  Razorpay AI Buildathon 2026")
rv.font.size = Pt(10)
rv.font.color.rgb = GREY

doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  TABLE OF CONTENTS
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "Table of Contents")
toc_placeholder(doc)
note(doc, "Tip", "Right-click the TOC field in Word and choose 'Update Field' to populate all entries.")
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  1. EXECUTIVE OVERVIEW
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "1. Executive Overview")
body(doc, (
    "India's Economic Survey (Budget 2026) put ₹8.1 lakh crore currently stuck in delayed "
    "payments owed to MSMEs nationally. A 2026 industry report from Recordent found the average "
    "Indian SME is carrying ₹3.83 crore in overdue receivables at any given time. "
    "This is one of the most common working-capital problems a B2B or SaaS business in India will face."
))
doc.add_paragraph()
body(doc, (
    "Recoup is an autonomous AI agent that decides who to chase for overdue B2B invoices, how hard, "
    "and when to stop — then proves exactly how much money it recovered. Built on FastAPI (Python 3.11+), "
    "PostgreSQL, Razorpay Payment Links, and a provider-agnostic LLM client, Recoup takes the "
    "judgment call of accounts-receivable follow-up off a person's plate without removing their control."
))

heading2(doc, "1.1 What Makes Recoup Different")
add_two_col_table(doc,
    ("Generic Dunning Script", "Recoup"),
    [
        ("Same email to every overdue customer", "Ranks by expected recovery value; low-risk invoices deliberately left alone"),
        ("Reminds until someone stops it", "Hard escalation ladder with automatic stop — no indefinite nagging"),
        ("Any discount an LLM feels like offering", "Policy-enforced discount ceiling the LLM cannot exceed"),
        ("'Reminder sent' is the whole log", "Every action carries a reason; full decision trail queryable"),
        ("Success = emails sent", "Success = batch-level ₹ recovered, measured not claimed"),
        ("No memory of what was promised", "Promises recorded, watched against deadline, drive next action"),
    ]
)
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  2. HIGH-LEVEL ARCHITECTURE
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "2. High-Level Architecture")
body(doc, (
    "The diagram below shows the complete end-to-end data flow of Recoup, from the moment an "
    "overdue invoice enters the system to the final batch evaluation report."
))
doc.add_paragraph()
add_image(doc, IMG_ARCH, "Figure 1 – Recoup Backend Data Flow")

heading2(doc, "2.1 Core Design Principle: Score → Propose → GATE → Transition → Execute")
body(doc, (
    "Every action the agent takes passes through exactly five ordered steps. "
    "The order is the safety property, not a style choice. Execution receives a PolicyDecision, "
    "never a raw ProposedAction, so there is no code path that can send a message without a "
    "gate verdict attached."
))
doc.add_paragraph()
add_three_col_table(doc,
    ("Step", "Module", "Decides"),
    [
        ("score",      "app/core/scorer.py",     "What is this invoice worth acting on?"),
        ("propose",    "app/core/agent.py",       "What is the next rung, and how hard do we push?"),
        ("gate",       "app/core/policy.py",      "Is that action permitted by policy?"),
        ("transition", "app/core/escalation.py",  "May this case move to the next state?"),
        ("execute",    "app/services/",           "Send it, and log that we did"),
    ]
)
note(doc, "Key insight",
     "Both approved AND blocked actions are recorded by app/core/audit.py. "
     "A blocked action leaves as much evidence as a sent one.")

heading2(doc, "2.2 Layering: Why the Core Has No Database")
body(doc, (
    "app/core/ operates on plain Pydantic snapshots (CaseSnapshot from app/core/domain.py), "
    "not ORM rows. Two adapters feed it: one from the synthetic generator for batch demos, "
    "and one from the database for live API operation. This design achieves three goals: "
    "\n\n"
    "1. The demo runs on a clean checkout with no Postgres, no API keys, and no network.\n"
    "2. Safety tests are unit-testable in milliseconds against in-memory objects.\n"
    "3. One scorer, one gate, two data sources — the API and batch paths converge on the same "
    "CaseSnapshot so they cannot drift into enforcing different policies."
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  3. COMPONENT DEEP-DIVE
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "3. Component Deep-Dive")

heading2(doc, "3.1 Prioritization Scorer  (app/core/scorer.py)")
body(doc, (
    "The scorer ranks every open invoice by expected recovery value using the formula:\n\n"
    "    EV = P(recovery) × outstanding × urgency_weight − intervention_cost\n\n"
    "This mirrors standard credit-risk practice (Expected Loss = PD × EAD × LGD) but runs it "
    "in reverse to compute Expected Recovery. The scorer returns one of three tiers:\n\n"
    "  • WAIT   – contacting this customer has negative expected value\n"
    "  • REMIND – a single gentle reminder is warranted\n"
    "  • ESCALATE – high expected-value-at-risk; escalate immediately\n\n"
    "The rules-based scorer declares fallback_used=True on every prediction. An optional trained "
    "model (XGBoost + logistic regression + MLP, isotonically calibrated) can replace it by "
    "setting USE_MODEL_SCORER=true. The trained model achieves AUC 0.709 vs 0.667 for the "
    "rules-based incumbent, capturing ₹5.9L more in unpaid rupees within the top-120 work queue."
))

heading2(doc, "3.2 Policy / Gate Engine  (app/core/policy.py)")
body(doc, (
    "The policy engine is the single choke-point every outbound action must clear. "
    "It is built on the venmo/business-rules library (and Wave 4 added in-process OPA via regopy). "
    "Rules are data: GET /api/v1/policy serves the compiled rule set verbatim, so what the gate "
    "enforces and what the API reports it enforces are always identical.\n\n"
    "Two invariants:\n"
    "  • Blocks are absolute; adjustments only reduce. A rule's only powers are to refuse and "
    "to lower a ceiling. There is no action that approves or raises anything.\n"
    "  • Every violation is reported, not just the first. stop_on_first_trigger is off, so an "
    "audit record says every rule that fired — not just the first."
))
body(doc, "Configurable ceilings enforced by the gate:")
add_kv_table(doc, [
    ("MAX_DISCOUNT_PERCENT",        "Hard ceiling on any discount offer (e.g. 10%)"),
    ("MAX_CONTACT_FREQUENCY_DAYS",  "Minimum gap between contacts per invoice (e.g. 3 days)"),
    ("ESCALATION_LADDER_DAYS",      "Comma-separated day offsets for each rung (e.g. 1,3,7,14)"),
])

heading2(doc, "3.3 Escalation State Machine  (app/core/escalation.py)")
body(doc, (
    "Built on pytransitions/transitions with auto_transitions=False. "
    "This means the only way a case can move is via the four declared triggers — "
    "no code can jump directly to 'closed', skipping the ladder."
))
doc.add_paragraph()
add_image(doc, IMG_ESM, "Figure 2 – Escalation State Machine")

body(doc, (
    "States and transitions:\n\n"
    "  MONITORING  →  send_reminder  →  REMINDED\n"
    "  REMINDED    →  escalate       →  ESCALATED\n"
    "  ESCALATED   →  handoff        →  HUMAN_HANDOFF\n"
    "  Any state   →  close          →  CLOSED\n\n"
    "Guards (contact_allowed, ladder_step_due) are conditions on the transitions, not post-hoc "
    "checks. A blocked transition simply does not happen — the trigger returns False and state "
    "is unchanged. Both guards delegate to the policy engine, so the FSM and the outbound gate "
    "cannot disagree."
))

heading2(doc, "3.4 Promise-to-Pay Tracker  (app/core/promise_tracker.py)")
body(doc, (
    "A promise is evidence of intent, never evidence of payment. When a customer says "
    "'I'll pay Friday', that commitment is recorded and moves the invoice to PROMISED state, "
    "buying the customer quiet until their deadline.\n\n"
    "Key rules:\n"
    "  • Only a signature-verified Razorpay webhook sets PAID. Never a promise alone.\n"
    "  • A broken promise escalates exactly one rung per the ladder — not a retry loop.\n"
    "  • Superseded promises are kept, not deleted, because 'rescheduled three times' is a "
    "pattern a human reviewer needs to see.\n"
    "  • An opt-out reply ('stop contacting me') is recorded in the opt-out registry and the "
    "policy gate permanently blocks all future automated contacts for that invoice."
))

heading2(doc, "3.5 Reply Understanding  (src/ml/reply/)")
body(doc, (
    "Reply understanding uses a two-stage cascade classifier:\n\n"
    "  Stage A: TF-IDF/SVM classifier (trained) — handles the clear-cut cases locally "
    "in milliseconds. Grouped macro-F1: 0.764; the cascade keeps 35.6% of replies at "
    "0.917 accuracy.\n\n"
    "  Stage B: LLM via instructor (Groq/Gemini/Anthropic) — handles ambiguous cases "
    "escalated from Stage A. Uses schema-bound structured output with automatic retry on "
    "validation failure.\n\n"
    "If both stages fail, the reply is classified as IntentLabel.OTHER with fallback_used=True "
    "and routed to GET /api/v1/replies/review for human review. The agent never guesses."
))

heading2(doc, "3.6 Decision Trace / Audit Ledger  (app/core/audit.py)")
body(doc, (
    "Every decision — approved or blocked — is appended to an immutable, hash-chained ledger. "
    "Each entry's entry_hash is SHA-256 over its own content plus its predecessor's hash. "
    "Editing, reordering, or deleting any entry breaks every hash after it.\n\n"
    "DecisionLedger.verify() reports the first position that disagrees. "
    "Wave 4 added event sourcing (pyeventsourcing): every Decision Trace append is dual-written "
    "as semantic domain events, reconstructible at any version via get_case(invoice_id, version=N)."
))

heading2(doc, "3.7 Action Executor  (app/services/)")
body(doc, (
    "The action executor integrates with two external services:\n\n"
    "  Razorpay (app/services/razorpay_client.py)\n"
    "  Creates test-mode Payment Links with the exact amount and deadline. The link URL is "
    "included in the outbound message. DRY_RUN=true (the default) renders and logs the link "
    "without creating it, consuming contact caps so a dry run behaves identically to live.\n\n"
    "  Resend (app/services/resend_client.py)\n"
    "  Email delivery. The signed Reply-To address encodes the invoice ID so inbound replies "
    "are routed to the correct invoice without any database lookup on receipt.\n\n"
    "  LLM Client (app/services/llm_client.py)\n"
    "  Provider-agnostic (Groq / Gemini / Anthropic). Drafts explanation text and reminder copy "
    "ONLY. The LLM never decides amounts, never bypasses the policy engine."
))

heading2(doc, "3.8 Webhook Receiver  (app/api/webhooks.py)")
body(doc, (
    "The only writer of PAID status. Three ordered properties:\n\n"
    "  1. HMAC-SHA256 signature verification before any payload parsing.\n"
    "  2. Idempotency on Razorpay event_id — double-counting a payment corrupts every "
    "recovery number. The handler returns 200 if the event has already been processed.\n"
    "  3. Acknowledge what was stored, not what was understood. An unparseable payload "
    "returns 400 (not 500) so our bug does not become an infinite Razorpay retry storm."
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  4. DATABASE SCHEMA
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "4. Database Schema")
body(doc, (
    "Recoup uses PostgreSQL (Neon or Supabase free tier) with SQLAlchemy 2.0 (async) and "
    "Alembic for migrations. The schema is managed entirely by Alembic — the application "
    "never creates tables at startup."
))
doc.add_paragraph()
add_image(doc, IMG_DB, "Figure 3 – Entity-Relationship Diagram")

heading2(doc, "4.1 Tables")
add_two_col_table(doc,
    ("Table", "Purpose"),
    [
        ("customers",      "Stores customer profiles including payment behaviour archetype and opt-out flag"),
        ("invoices",       "Core invoice record: amount, due_date, status enum, current escalation_state"),
        ("promises",       "Promise-to-pay commitments linked to invoices, including kept/broken flag"),
        ("decision_trace", "Immutable hash-chained audit log of every decision taken"),
        ("payments",       "Webhook-confirmed payment events from Razorpay"),
        ("integrations",   "Credentials references for connected services (Razorpay, Resend, LLM)"),
        ("run_summaries",  "Aggregate metrics from each batch run cycle"),
    ]
)

heading2(doc, "4.2 Migration Strategy")
body(doc, (
    "All schema changes go through Alembic:\n\n"
    "    alembic upgrade head   ← run before every deploy\n\n"
    "The application boots only after this succeeds. A bad DATABASE_URL fails at startup "
    "(visible as a failed release) rather than as a 500 on the first request."
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  5. AGENTIC LOOP
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "5. The Agentic Decision Loop")
body(doc, (
    "The agent runs either in batch mode (POST /api/v1/tasks/run-batch, advisory-locked with "
    "a kill switch) or per-invoice on demand (POST /api/v1/invoices/{id}/run-cycle). "
    "The decision loop is identical in both modes."
))
heading2(doc, "5.1 Step-by-Step Walkthrough")
body(doc, (
    "Given three invoices:\n\n"
    "  INV-1044  ₹75,000   2 days overdue   Customer: strong payment history\n"
    "  INV-1042  ₹50,000   5 days overdue   Customer: reliably pays late but pays\n"
    "  INV-1043  ₹2,00,000 12 days overdue  Customer: high-value, new risk signals\n\n"
    "Step 1 — Score:\n"
    "  INV-1044 → P(recovery)=0.84 → EV is positive → tier: REMIND\n"
    "  INV-1042 → low urgency       → tier: REMIND (single friendly, no discount)\n"
    "  INV-1043 → high EV at risk   → tier: ESCALATE\n\n"
    "Step 2 — Propose:\n"
    "  INV-1043 → LLM drafts: 'Pay ₹1,80,000 by Friday; ₹20,000 late fee waived'\n"
    "  (₹20,000 = the business-configured ceiling. The LLM did not pick this number.)\n\n"
    "Step 3 — Gate:\n"
    "  All three pass contact-frequency cap (not contacted in last 3 days).\n"
    "  All three pass opt-out registry (no stop signal received).\n"
    "  INV-1043 discount offer checked: 10% ≤ 10% ceiling → APPROVED.\n\n"
    "Step 4 — Transition:\n"
    "  Each invoice advances one rung on the state machine.\n\n"
    "Step 5 — Execute:\n"
    "  Razorpay Payment Link generated (test mode). Resend email dispatched.\n"
    "  All three actions appended to the Decision Trace.\n\n"
    "Step 6 — Promise Tracking:\n"
    "  Customer on INV-1043 replies 'I'll pay Friday'. Recorded as promise.\n"
    "  Friday arrives, no webhook → promise broken → INV-1043 escalates one rung."
))

heading2(doc, "5.2 Batch Evaluation Results (600 invoices, 5 cycles, seed 42)")
add_kv_table(doc, [
    ("Invoices processed",           "600"),
    ("Total overdue value",          "₹6.96 Cr"),
    ("Cases flagged for intervention","574"),
    ("Cases left alone",             "26"),
    ("Interventions executed",       "580"),
    ("Actions blocked by policy",    "699"),
    ("Recovered (of flagged)",       "385  (67.1%)"),
    ("Recovered value",              "₹4.68 Cr"),
    ("False / unnecessary interventions", "316"),
    ("Compliance violations",        "0"),
    ("Ledger hash chain verified",   "Yes"),
    ("Decision trace entries",       "5,807"),
])
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  6. API SURFACE
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "6. API Surface")
body(doc, (
    "The FastAPI application exposes a versioned REST API at /api/v1/. "
    "Interactive docs are available at /docs only when DEBUG=true (off by default in every "
    "non-local environment)."
))
doc.add_paragraph()
add_three_col_table(doc,
    ("Method", "Endpoint", "Purpose"),
    [
        ("GET",  "/api/v1/health",               "Liveness check"),
        ("GET",  "/api/v1/health/db",            "Database connectivity check"),
        ("POST", "/api/v1/invoices/batch",        "Ingest a batch of invoices and customers"),
        ("GET",  "/api/v1/invoices/{id}",         "View one invoice's current state"),
        ("GET",  "/api/v1/invoices/{id}/audit",   "Full decision trail for one invoice"),
        ("POST", "/api/v1/invoices/{id}/run-cycle","Manually trigger one agent decision cycle"),
        ("POST", "/api/v1/webhooks/razorpay",     "Razorpay Payment Link webhook receiver (HMAC-verified)"),
        ("POST", "/api/v1/replies",               "Inbound email reply from Resend webhook"),
        ("GET",  "/api/v1/replies/review",        "Human review queue for ambiguous replies"),
        ("GET",  "/api/v1/reports/batch",         "Batch evaluation report"),
        ("GET",  "/api/v1/forecast/cash",         "Probabilistic 7/30-day cash forecast"),
        ("GET",  "/api/v1/forecast/cash/card",    "Validation metrics for the cash forecast"),
        ("GET",  "/api/v1/policy",                "Currently configured policy (ceilings, caps, ladder)"),
        ("POST", "/api/v1/tasks/run-batch",       "Trigger autonomous batch run (advisory-locked)"),
        ("GET",  "/api/v1/secrets/status",        "Secret provider health and last-refresh time"),
        ("POST", "/api/v1/secrets/refresh",       "Manually trigger secret refresh from provider"),
    ]
)

heading2(doc, "6.1 Router Structure")
body(doc, (
    "Each domain is a separate FastAPI APIRouter mounted in app/main.py:\n\n"
    "  broken_promise_router   →  broken promise detection endpoints\n"
    "  cash_forecast_router    →  probabilistic cash forecasting\n"
    "  contact_timing_router   →  ML-optimised contact timing\n"
    "  drift_router            →  customer payment behaviour drift detection\n"
    "  health_router           →  liveness and readiness probes\n"
    "  integrations_router     →  external service credential management\n"
    "  invoices_router         →  invoice ingestion, state, audit\n"
    "  models_router           →  ML model metadata and retraining triggers\n"
    "  payments_router         →  payment confirmation endpoints\n"
    "  policy_router           →  policy inspection\n"
    "  replies_router          →  inbound email reply handling\n"
    "  reports_router          →  batch evaluation reports\n"
    "  secrets_router          →  secret management operations\n"
    "  tasks_router            →  background batch task management\n"
    "  webhooks_router         →  Razorpay webhook receiver"
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  7. TECH STACK
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "7. Technology Stack")
add_three_col_table(doc,
    ("Layer", "Choice", "Why"),
    [
        ("API framework",     "FastAPI + Uvicorn (Python 3.11+)",  "Typed contracts, async-native, Pydantic v2 validation"),
        ("Database",          "PostgreSQL via SQLAlchemy 2.0 async + psycopg3", "Relational integrity; async driver; Alembic migrations"),
        ("Local dev DB",      "SQLite via aiosqlite",              "Zero-infrastructure demo and CI"),
        ("Payments",          "Razorpay Payment Links + Webhooks (test mode)", "Real verifiable payment lifecycle; zero financial risk"),
        ("Email delivery",    "Resend",                            "Fast setup; signed Reply-To routing for reply matching"),
        ("LLM",               "Groq / Gemini / Anthropic (provider-agnostic)", "Zero-cost demo; reasoning layer never decides amounts"),
        ("Policy engine",     "business-rules + regopy (in-process OPA)", "Rules as data; Rego evaluated at <20ms p99"),
        ("Escalation FSM",    "pytransitions/transitions",         "auto_transitions=False enforces ladder structurally"),
        ("Structured LLM",    "instructor",                        "Schema-bound output with validation-aware retry"),
        ("Event sourcing",    "eventsourcing",                     "Aggregate stream; time-travel replay; audit parity"),
        ("ML — training",     "XGBoost, scikit-learn, SHAP",       "Recovery scorer; calibrated probabilities; explainability"),
        ("Rate limiting",     "Redis (hiredis) sliding window",    "Shared state across replicas; falls back to in-process deque"),
        ("Observability",     "OpenTelemetry SDK + OTLP",          "Traces + metrics; console fallback for local dev"),
        ("Logging",           "structlog + python-json-logger",    "Structured JSON logs; request-ID correlation"),
        ("Hosting",           "Render (free web service)",         "One public URL; Alembic release step; no infrastructure"),
        ("Frontend",          "Next.js (Vercel)",                  "React dashboard for invoice management"),
        ("Email worker",      "Cloudflare Email Worker",           "Routes inbound email replies to the API"),
    ]
)
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  8. SERVICES DEEP DIVE
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "8. External Service Integrations")

heading2(doc, "8.1 Razorpay Integration")
body(doc, (
    "app/services/razorpay_client.py wraps the official razorpay Python SDK (>=2.0).\n\n"
    "Payment Link creation:\n"
    "  • Accepts amount (paise), description, customer details, expiry date.\n"
    "  • Returns the short URL included in the outbound message.\n"
    "  • DRY_RUN=true logs the link body without creating it.\n\n"
    "Webhook verification:\n"
    "  • HMAC-SHA256 using RAZORPAY_WEBHOOK_SECRET.\n"
    "  • Verification runs before any payload parsing — an invalid signature returns 400 immediately.\n"
    "  • Idempotency: duplicate event_id returns 200 without re-processing.\n"
    "  • Handled events: payment.captured (→ PAID), payment_link.expired (→ re-score)."
))

heading2(doc, "8.2 Resend Email Integration")
body(doc, (
    "app/services/resend_client.py:\n\n"
    "  • Sends HTML/text emails via Resend API.\n"
    "  • Reply-To address is signed with HMAC to encode the invoice_id:\n"
    "    reply+{invoice_id}@{RESEND_DOMAIN} — validated on receipt.\n"
    "  • Inbound replies arrive via Resend webhook at POST /api/v1/replies.\n"
    "  • The reply is routed to the correct invoice without any database lookup."
))

heading2(doc, "8.3 LLM Client (Provider-Agnostic)")
body(doc, (
    "app/services/llm_client.py selects the provider from LLM_PROVIDER env var:\n\n"
    "  groq      → groq>=1.7 (>=1.7 required; 0.6.x breaks with httpx>=0.28)\n"
    "  gemini    → google-generativeai>=0.8.3\n"
    "  anthropic → anthropic SDK\n\n"
    "Two function types:\n"
    "  generate()             → free-text: reminder copy, escalation narration\n"
    "  generate_structured()  → instructor-wrapped schema-bound: reply classification,\n"
    "                           promise extraction (retries on validation failure)\n\n"
    "The LLM is strictly subordinate to the policy engine. It cannot propose amounts, "
    "bypass ceilings, or make any decision that triggers an action. It drafts text only."
))

heading2(doc, "8.4 Secret Management")
body(doc, (
    "app/core/secrets.py implements a multi-provider loader:\n\n"
    "  Priority order: Render environment → Doppler → AWS Secrets Manager\n\n"
    "  • inject_secrets_into_env() runs first in the lifespan startup, before any other service.\n"
    "  • Secrets are cached in memory with a TTL (SECRET_CACHE_TTL_HOURS, default 1h).\n"
    "  • Background refresh every SECRET_REFRESH_INTERVAL_SECONDS (default 300s).\n"
    "  • GET /api/v1/secrets/status — returns provider health and last refresh time.\n"
    "  • POST /api/v1/secrets/refresh — manual trigger for zero-downtime rotation.\n"
    "  • Secret values never appear in logs or traces (scrubbed before structlog emission)."
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  9. ML MODELS
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "9. Machine Learning Models")

heading2(doc, "9.1 Recovery Probability Model")
body(doc, (
    "Location: src/ml/recovery/\n"
    "Training script: scripts/train_recovery_model.py\n\n"
    "Three candidates trained on the same temporal split:\n"
    "  • Logistic Regression (baseline)\n"
    "  • XGBoost (gradient-boosted trees)\n"
    "  • Small MLP\n\n"
    "Each is isotonically calibrated on a held-out validation split. "
    "The winner (highest AUC) is selected and saved as a joblib artifact.\n\n"
    "Key metrics (logreg-recovery-20260903-3f49c1 vs rules-based):\n"
))
add_two_col_table(doc,
    ("Metric", "Result"),
    [
        ("AUC",                  "0.709 (trained)  vs  0.667 (rules)"),
        ("Brier score",          "0.1944 (trained)  vs  0.2433 (rules)"),
        ("ECE",                  "0.065 (trained)  vs  0.180 (rules)"),
        ("Top-120 ₹ at risk",    "₹85.5L (trained)  vs  ₹79.5L (rules)  (+7.5%)"),
    ]
)
body(doc, "Top SHAP features:")
add_two_col_table(doc,
    ("Feature", "Mean |SHAP|"),
    [
        ("recency_weighted_on_time_score",   "0.469"),
        ("customer_on_time_ratio_all_time",  "0.275"),
        ("customer_invoice_count",           "0.271"),
        ("prior_promise_kept",               "0.218"),
        ("customer_avg_days_late",           "0.214"),
        ("current_escalation_tier",          "0.153"),
    ]
)

heading2(doc, "9.2 Reply Intent Classifier")
body(doc, (
    "Location: src/ml/reply/\n\n"
    "Two-stage cascade:\n"
    "  Stage A: TF-IDF vectoriser + Linear SVM (sklearn Pipeline, saved as joblib artifact)\n"
    "           Handles 35.6% of replies at 0.917 accuracy (confidence threshold)\n"
    "           Grouped macro-F1: 0.764\n\n"
    "  Stage B: LLM via instructor (for ambiguous cases)\n"
    "           instructor retries with the specific validation error when output is malformed\n\n"
    "Intent labels: PROMISE_TO_PAY, DISPUTE, OPT_OUT, ACKNOWLEDGEMENT, OTHER\n"
    "OPT_OUT is always routed to the policy engine opt-out registry immediately."
))

heading2(doc, "9.3 Cash Forecast Model")
body(doc, (
    "Location: src/ml/cash_forecast/\n"
    "API: GET /api/v1/forecast/cash, GET /api/v1/forecast/cash/card\n\n"
    "Monte Carlo simulation over the current at-risk book. For each invoice, "
    "samples from the calibrated recovery probability distribution to produce "
    "probabilistic P10/P50/P90 cash-in forecasts over 7-day and 30-day horizons. "
    "Validation card: GET /api/v1/forecast/cash/card returns coverage and bias metrics."
))

heading2(doc, "9.4 Contact Timing Model")
body(doc, (
    "Location: src/ml/contact_timing/\n"
    "API: GET /api/v1/contact-timing/{invoice_id}\n\n"
    "Segmented Thompson Sampling bandit. Customers are segmented by payment-behaviour archetype. "
    "Within each segment the bandit learns which day-of-week / time-of-day contact slots yield "
    "the highest promise-to-pay conversion, updating its Beta distribution posteriors on "
    "each observed outcome."
))

heading2(doc, "9.5 Drift Detection Model")
body(doc, (
    "Location: src/ml/drift/\n"
    "API: GET /api/v1/drift/{customer_id}\n\n"
    "Detects customers whose payment behaviour is shifting from their historical archetype. "
    "Uses a classifier trained on engineered features (rolling payment ratio, days-late trend, "
    "dispute velocity). A drifting customer receives a higher urgency weight in the scorer."
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  10. SECURITY MODEL
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "10. Security Model")
add_image(doc, IMG_SEC, "Figure 4 – Layered Security Architecture")

heading2(doc, "10.1 Request Layer Security")
body(doc, (
    "CORS Middleware:\n"
    "  • Allowed origins set via CORS_ORIGINS env var (explicit list, never wildcard).\n"
    "  • Allow-credentials is only enabled when specific origins are named — the combination "
    "the CORS spec actually permits.\n"
    "  • Allow-origin-regex additionally covers local dev addresses.\n\n"
    "Request ID Middleware:\n"
    "  • Every request receives a UUID correlation ID injected into structlog context.\n"
    "  • The ID propagates through all trace spans for end-to-end correlation."
))

heading2(doc, "10.2 Webhook Security")
body(doc, (
    "All inbound Razorpay webhook events are HMAC-SHA256 verified against "
    "RAZORPAY_WEBHOOK_SECRET before any payload parsing. An invalid signature returns 400 "
    "immediately. Verification uses the raw request body (before JSON parsing) to prevent "
    "any encoding normalisation from invalidating the signature."
))

heading2(doc, "10.3 Policy Gate as Security Boundary")
body(doc, (
    "The policy gate is not just a business-rule checker — it is a hard security boundary:\n\n"
    "  • Discount ceiling: the LLM cannot offer more than MAX_DISCOUNT_PERCENT regardless of "
    "what text it generates. The gate re-evaluates the offer amount independently.\n"
    "  • Opt-out registry: once a customer opts out, the gate blocks all further automated "
    "contacts permanently. There is no code path that bypasses this.\n"
    "  • Contact frequency cap: prevents customer harassment and regulatory exposure.\n\n"
    "Wave 4 replaced the business-rules backend with regopy (rego-cpp), the same policy "
    "language as Open Policy Agent but evaluated in-process at <20ms p99. "
    "Config values are a per-tenant data document the request cannot modify."
))

heading2(doc, "10.4 Audit Trail Integrity")
body(doc, (
    "The Decision Trace is a hash-chained append-only ledger. "
    "Any attempt to edit, reorder, or delete an entry breaks every subsequent hash. "
    "DecisionLedger.verify() is run as part of the batch evaluation report and is checked "
    "in tests. The 'Ledger hash chain verified: yes' line in the evaluation report is counted "
    "from the actual ledger, not from the orchestrator's own return values."
))

heading2(doc, "10.5 Secrets Security")
add_two_col_table(doc,
    ("Control", "Implementation"),
    [
        ("No secret logging",          "Secret values are scrubbed before any structlog emission"),
        ("Provider authentication",    "IAM roles/service accounts where the provider supports them"),
        ("Rotation validation",        "POST /api/v1/secrets/refresh + GET /api/v1/secrets/status"),
        ("PII in traces",              "Span attributes are sanitised; customer names/emails excluded"),
        ("OTLP export auth",           "Configurable auth headers on the OTLP exporter endpoint"),
    ]
)
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  11. OBSERVABILITY
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "11. Observability")

heading2(doc, "11.1 OpenTelemetry Tracing")
body(doc, (
    "The application is fully instrumented with OpenTelemetry SDK:\n\n"
    "  Auto-instrumented: FastAPI (request spans), SQLAlchemy (query spans), httpx (outbound HTTP)\n\n"
    "  Manual spans: Batch cycle hierarchy\n"
    "      batch.cycle → batch.scoring_phase → invoice.score (per invoice)\n"
    "      batch.cycle → batch.decision_phase → invoice.decision\n"
    "      batch.cycle → batch.execution_phase → invoice.execute\n\n"
    "  Export: OTLP to configured endpoint. Falls back to ConsoleSpanExporter (stdout JSON) "
    "when OTEL_EXPORTER_OTLP_ENDPOINT is not set, so local dev works with no external account."
))

heading2(doc, "11.2 Structured Logging")
body(doc, (
    "structlog is configured to emit JSON logs with:\n"
    "  • request_id (from the Request ID middleware)\n"
    "  • invoice_id, customer_id, action (when in agent context)\n"
    "  • level, timestamp, module, function\n\n"
    "Log level is controlled by LOG_LEVEL env var (default: INFO in production, DEBUG locally)."
))

heading2(doc, "11.3 Health Endpoints")
add_two_col_table(doc,
    ("Endpoint", "Checks"),
    [
        ("GET /api/v1/health",          "Application liveness (always 200 if the process is running)"),
        ("GET /api/v1/health/db",       "Database connectivity (SELECT 1)"),
        ("GET /api/v1/ready/enhanced",  "DB + secret provider + OTLP exporter health"),
    ]
)

heading2(doc, "11.4 Key SLOs")
add_two_col_table(doc,
    ("Metric", "Target"),
    [
        ("Batch cycle p95 latency",     "< 30 seconds"),
        ("Health check response time",  "< 500 ms"),
        ("Secret refresh time",         "< 100 ms"),
        ("Policy gate decision",        "< 20 ms p99 (in-process Rego)"),
        ("Webhook verification+ack",    "< 200 ms"),
    ]
)
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  12. SCALING CONSIDERATIONS
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "12. Scaling Considerations")

heading2(doc, "12.1 Current Architecture (Single-Tenant MVP)")
body(doc, (
    "The current deployment is a single Render web service backed by a single Postgres database "
    "(Neon/Supabase free tier). This is intentional for the buildathon MVP: the unit economics "
    "of a receivables agent are per-business, not per-user, so single-tenancy is the right "
    "starting shape."
))

heading2(doc, "12.2 Stateless API Design")
body(doc, (
    "The FastAPI application is stateless: all state lives in PostgreSQL. "
    "Horizontal scaling is a config change (increase Render instance count). "
    "The batch advisory lock (PostgreSQL pg_try_advisory_xact_lock) prevents two replicas "
    "from running the same batch cycle concurrently without any additional coordination layer."
))

heading2(doc, "12.3 Rate Limiter")
body(doc, (
    "The contact-frequency cap uses a Redis sliding-window rate limiter (redis[hiredis]>=5.0) "
    "when RATE_LIMIT_REDIS_URL is set. Without it, the limiter falls back to an in-process "
    "deque — correct for a single replica, wrong for multiple. Adding Redis is the only change "
    "needed to make the frequency cap correct across replicas."
))

heading2(doc, "12.4 Database Connection Pooling")
body(doc, (
    "SQLAlchemy async engine with psycopg3 (binary). Pool mode is controlled by DB_POOL_MODE:\n"
    "  session  — default; one connection per request\n"
    "  transaction — connection returned to pool between transactions (PgBouncer-compatible)\n\n"
    "For high-throughput deployments, PgBouncer in transaction mode sits in front of Postgres "
    "and the application sets DB_POOL_MODE=transaction."
))

heading2(doc, "12.5 LLM Concurrency")
body(doc, (
    "LLM calls are async (httpx-backed). The batch loop runs invoice decisions sequentially "
    "within a cycle to preserve causal ordering in the Decision Trace. "
    "Parallelising scoring (which is CPU-local and does not write) is the first safe "
    "optimisation: each invoice's score is independent and can be computed concurrently "
    "with asyncio.gather before the sequential decision loop begins."
))

heading2(doc, "12.6 Multi-Tenant Roadmap")
body(doc, (
    "The production path to multi-tenancy:\n\n"
    "  1. OIDC/JWT authentication (e.g. Auth0, Clerk)\n"
    "  2. Per-business row-level data isolation in Postgres (RLS or schema-per-tenant)\n"
    "  3. Per-tenant PolicyConfig document in the Rego data store\n"
    "  4. Per-tenant ESCALATION_LADDER_DAYS, MAX_DISCOUNT_PERCENT, etc. via the secrets/config layer\n\n"
    "The policy gate is already designed for this: Wave 4's Rego evaluation takes a "
    "business_id-keyed data document, so per-tenant configuration is a data change, not a code change."
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  13. DEPLOYMENT
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "13. Deployment")

heading2(doc, "13.1 Infrastructure Overview")
add_two_col_table(doc,
    ("Component", "Hosting"),
    [
        ("Backend API",          "Render free web service (Python, uvicorn)"),
        ("Frontend dashboard",   "Vercel (Next.js)"),
        ("Database",             "Neon or Supabase free tier (PostgreSQL)"),
        ("Email inbound worker", "Cloudflare Email Worker (routes replies to API)"),
        ("Redis (optional)",     "Render Redis or Upstash (for multi-replica rate limiting)"),
    ]
)

heading2(doc, "13.2 Release Process")
body(doc, (
    "Each deploy follows this sequence:\n\n"
    "  1. alembic upgrade head      — migrate schema before the new code starts serving\n"
    "  2. uvicorn app.main:app      — start the application\n"
    "  3. Health check at /api/v1/health/db confirms DB is reachable\n"
    "  4. install_cascade_classifier() binds the reply classifier\n\n"
    "The Dockerfile uses a two-stage build: dependencies installed in a builder image, "
    "only the virtual environment and source copied to the runtime image."
))

heading2(doc, "13.3 Environment Variables Reference")
add_two_col_table(doc,
    ("Variable", "Purpose"),
    [
        ("DATABASE_URL",                   "Postgres connection string (automatically normalised to postgresql+psycopg)"),
        ("RAZORPAY_KEY_ID",                "Razorpay test-mode key ID"),
        ("RAZORPAY_KEY_SECRET",            "Razorpay test-mode key secret"),
        ("RAZORPAY_WEBHOOK_SECRET",        "HMAC secret for webhook signature verification"),
        ("RESEND_API_KEY",                 "Resend email delivery API key"),
        ("LLM_PROVIDER",                   "groq | gemini | anthropic"),
        ("GROQ_API_KEY / GEMINI_API_KEY",  "Set only the one matching LLM_PROVIDER"),
        ("MAX_DISCOUNT_PERCENT",           "Hard ceiling the policy gate enforces on any offer"),
        ("MAX_CONTACT_FREQUENCY_DAYS",     "Minimum gap between outbound touches per invoice"),
        ("ESCALATION_LADDER_DAYS",         "Comma-separated day offsets, e.g. 1,3,7,14"),
        ("DRY_RUN",                        "true (default) = log actions, don't send; false = live"),
        ("USE_MODEL_SCORER",               "true = XGBoost scorer; false (default) = rules-based"),
        ("APP_ENV",                        "development | demo | production"),
        ("DEBUG",                          "true = /docs enabled; false (default) = disabled"),
        ("RATE_LIMIT_REDIS_URL",           "Redis URL for distributed rate limiting (optional)"),
        ("OTEL_EXPORTER_OTLP_ENDPOINT",   "OTLP export target (optional; console fallback if absent)"),
    ]
)
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  14. PROJECT STRUCTURE
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "14. Project Structure")
body(doc, (
    "app/\n"
    "├── main.py                   FastAPI application entry point, lifespan, middleware\n"
    "├── api/\n"
    "│   ├── health.py             Liveness + DB check\n"
    "│   ├── invoices.py           Ingest, read, audit trail, run-cycle\n"
    "│   ├── webhooks.py           Razorpay webhook receiver\n"
    "│   ├── reports.py            Batch evaluation report\n"
    "│   ├── policy.py             Active policy as served data\n"
    "│   ├── replies.py            Inbound email reply handling\n"
    "│   ├── tasks.py              Background batch task management\n"
    "│   ├── cash_forecast.py      Cash forecasting endpoints\n"
    "│   ├── contact_timing.py     Contact timing recommendations\n"
    "│   ├── drift.py              Payment behaviour drift detection\n"
    "│   ├── broken_promise.py     Broken promise detection\n"
    "│   ├── secrets.py            Secret management operations\n"
    "│   ├── integrations.py       External service credential management\n"
    "│   ├── models.py             ML model metadata\n"
    "│   └── payments.py           Payment confirmation\n"
    "├── core/\n"
    "│   ├── domain.py             CaseSnapshot: the read model for decisions\n"
    "│   ├── scorer.py             Expected-value prioritisation\n"
    "│   ├── policy.py             Gate (business-rules + OPA/Rego)\n"
    "│   ├── escalation.py         Escalation state machine\n"
    "│   ├── promise_tracker.py    Promise-to-pay logic\n"
    "│   ├── audit.py              Hash-chained Decision Trace\n"
    "│   ├── agent.py              Decision cycle, end to end\n"
    "│   ├── evaluation.py         Batch report computation\n"
    "│   ├── config.py             Pydantic Settings\n"
    "│   ├── logging.py            structlog setup\n"
    "│   ├── observability.py      OTel setup, request ID middleware\n"
    "│   └── eventsourcing/        Event store integration\n"
    "├── models/                   SQLAlchemy ORM tables + shared enums\n"
    "├── schemas/                  Pydantic request/response contracts\n"
    "├── db/session.py             Async engine + session factory\n"
    "└── services/\n"
    "    ├── llm_client.py         Provider-agnostic LLM wrapper\n"
    "    ├── razorpay_client.py    Payment Links + webhook verification\n"
    "    ├── resend_client.py      Email delivery\n"
    "    ├── repository.py         All database access (one module)\n"
    "    └── batch_runner.py       Batch cycle orchestration\n\n"
    "src/\n"
    "├── data/synthetic_generator.py   Seeded synthetic invoice+customer batch\n"
    "└── ml/\n"
    "    ├── recovery/             Recovery probability model (train + serve)\n"
    "    ├── reply/                Reply intent cascade classifier\n"
    "    ├── cash_forecast/        Monte Carlo cash forecast simulation\n"
    "    ├── contact_timing/       Thompson Sampling contact timing bandit\n"
    "    └── drift/                Payment behaviour drift classifier\n\n"
    "alembic/                      Migrations (0001_baseline + subsequent)\n"
    "scripts/\n"
    "├── run_batch_demo.py         Full agent loop + evaluation report\n"
    "├── train_recovery_model.py   Trains XGBoost/logreg/MLP recovery scorer\n"
    "├── seed_demo.py              Loads synthetic data into the database\n"
    "└── generate_docx_final.py    Generates this documentation\n\n"
    "tests/                        384 tests; no DB or network required\n"
    "frontend/                     Next.js dashboard (Vercel)\n"
    "cloudflare-email-worker/      Cloudflare Worker routes inbound email to API\n"
    "docs/                         Architecture, model cards, evaluation report\n"
))
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  15. ROADMAP TO PRODUCTION
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "15. Roadmap to Production")
body(doc, (
    "The current MVP is production-shaped but not production-hardened. "
    "Here is what hardening it means:"
))
add_two_col_table(doc,
    ("Item", "Detail"),
    [
        ("Multi-tenant auth",             "OIDC/JWT + per-business row-level data isolation in Postgres"),
        ("WhatsApp delivery",             "Meta Business verification; Resend email stands in for the demo"),
        ("Webhook idempotency hardening", "Payment infrastructure cannot afford to double-count a confirmation"),
        ("Online model retraining",       "Re-train recovery scorer from webhook-confirmed payment outcomes on a schedule"),
        ("Policy Simulation Engine",      "POST /policy/simulate: replay last quarter's book under a proposed policy change"),
        ("SetFit reply classifier",       "Fine-tune from 8–16 labelled examples once real reply data exists"),
        ("Observability stack",           "Self-hosted Grafana + Tempo or Honeycomb for production trace storage"),
        ("Secrets + backup procedures",   "Automated backup drill, rotation runbooks, alerting"),
        ("Compliance review",             "Legal review of escalation cadence against debt-collection guidance before any live customer"),
        ("Kubernetes / Terraform",        "Infrastructure-as-code for multi-region production deploy"),
    ]
)
doc.add_page_break()

# ════════════════════════════════════════════════════════════════════════════
#  16. APPENDIX — HONEST METRICS
# ════════════════════════════════════════════════════════════════════════════
heading1(doc, "Appendix A — How to Read the Evaluation Metrics")
body(doc, (
    "The batch evaluation report is generated by:\n"
    "    python scripts/run_batch_demo.py --batch-size 600 --seed 42 --cycles 5 --use-model\n\n"
    "Read all numbers with these four caveats, which the report itself repeats:\n\n"
    "1. The agent did not cause these recoveries.\n"
    "   The synthetic generator samples each invoice's outcome independently of what the "
    "agent does. The recovery rate is a targeting measure — did the agent act on the invoices "
    "that were going to be paid? — not a causal one. Measuring uplift requires a holdout arm "
    "this simulation does not have, so no uplift number is claimed.\n\n"
    "2. The scorer is rules-based by default.\n"
    "   Probabilities from the rules-based scorer are not calibrated. Add --use-model to score "
    "with the trained, calibrated model.\n\n"
    "3. False interventions are counted as a cost.\n"
    "   A contacted customer whose invoice would have been recovered anyway appears in the "
    "'false interventions' row — not quietly dropped. That number going up is a real regression.\n\n"
    "4. Compliance violations are counted from the ledger, not from intent.\n"
    "   The detector walks the Decision Trace in sequence. A bug in the orchestrator cannot "
    "suppress the violation count because the count reads the audit record, not the "
    "orchestrator's own return values."
))

# ════════════════════════════════════════════════════════════════════════════
#  SAVE
# ════════════════════════════════════════════════════════════════════════════
doc.save(str(OUT))
print(f"\n[DONE] Documentation written to:\n    {OUT}\n")

