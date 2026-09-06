"""
generate_pdf.py  -  Build Recoup_Technical_Documentation.pdf
                    directly via ReportLab (no Word/LibreOffice needed).
Run from the project root:
    python scripts/generate_pdf.py
"""
import pathlib
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image,
    Table, TableStyle, HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

# ── paths ──────────────────────────────────────────────────────────────────
ROOT    = pathlib.Path(__file__).resolve().parents[1]
ART_DIR = pathlib.Path(
    r"C:\Users\haris\.gemini\antigravity\brain\0bd0b9d8-0f57-4110-9f5d-a093dcdc4608"
)
IMG_ARCH = ART_DIR / "architecture_diagram_1788679647586.jpg"
IMG_ESM  = ART_DIR / "escalation_state_machine_1788679702289.jpg"
IMG_DB   = ART_DIR / "database_schema_1788679722671.jpg"
IMG_SEC  = ART_DIR / "security_model_1788679741868.jpg"
OUT      = ROOT / "Recoup_Technical_Documentation.pdf"

# ── colours ─────────────────────────────────────────────────────────────────
NAVY  = colors.HexColor("#003366")
TEAL  = colors.HexColor("#007A87")
DARK  = colors.HexColor("#1A1A2E")
GREY  = colors.HexColor("#555555")
LGREY = colors.HexColor("#F4F4F8")
WHITE = colors.white

W, H = A4

# ── styles ───────────────────────────────────────────────────────────────────
base = getSampleStyleSheet()

def style(name, parent="Normal", **kw):
    s = ParagraphStyle(name, parent=base[parent], **kw)
    return s

S_COVER_TITLE = style("CoverTitle", fontSize=52, textColor=NAVY,
                      alignment=TA_CENTER, spaceAfter=8, leading=58, fontName="Helvetica-Bold")
S_COVER_SUB   = style("CoverSub",   fontSize=18, textColor=TEAL,
                      alignment=TA_CENTER, spaceAfter=6, leading=22)
S_COVER_DESC  = style("CoverDesc",  fontSize=14, textColor=GREY,
                      alignment=TA_CENTER, spaceAfter=4, leading=18, fontName="Helvetica-Oblique")
S_COVER_VER   = style("CoverVer",   fontSize=10, textColor=GREY, alignment=TA_CENTER)

S_H1   = style("H1",   fontSize=18, textColor=NAVY, spaceBefore=18, spaceAfter=8,
               fontName="Helvetica-Bold", leading=22)
S_H2   = style("H2",   fontSize=14, textColor=TEAL, spaceBefore=14, spaceAfter=6,
               fontName="Helvetica-Bold", leading=18)
S_H3   = style("H3",   fontSize=12, textColor=DARK, spaceBefore=10, spaceAfter=4,
               fontName="Helvetica-Bold", leading=16)
S_BODY = style("Body", fontSize=10, textColor=DARK, spaceBefore=3, spaceAfter=5,
               leading=15, alignment=TA_JUSTIFY)
S_NOTE = style("Note", fontSize=9,  textColor=GREY, leftIndent=18,
               spaceBefore=3, spaceAfter=3, leading=13, fontName="Helvetica-Oblique")
S_CODE = style("Code", fontSize=8.5, textColor=DARK, fontName="Courier",
               backColor=LGREY, leftIndent=12, rightIndent=12,
               spaceBefore=4, spaceAfter=4, leading=13)
S_CAP  = style("Cap",  fontSize=9,  textColor=GREY, alignment=TA_CENTER,
               fontName="Helvetica-Oblique", spaceAfter=8)
S_TOC  = style("TOC",  fontSize=11, textColor=NAVY, leading=18)

# ── helpers ──────────────────────────────────────────────────────────────────
def h1(txt):   return Paragraph(txt, S_H1)
def h2(txt):   return Paragraph(txt, S_H2)
def h3(txt):   return Paragraph(txt, S_H3)
def body(txt): return Paragraph(txt.replace("\n", "<br/>"), S_BODY)
def note(txt): return Paragraph(f"<i>{txt}</i>", S_NOTE)
def code(txt): return Paragraph(txt.replace(" ", "&nbsp;").replace("\n","<br/>"), S_CODE)
def cap(txt):  return Paragraph(txt, S_CAP)
def sp(n=6):   return Spacer(1, n)
def hr():      return HRFlowable(width="100%", thickness=0.5, color=TEAL, spaceAfter=4)
def pb():      return PageBreak()

def img(path: pathlib.Path, caption: str, max_w=14*cm):
    elems = []
    if path.exists():
        im = Image(str(path))
        aspect = im.imageWidth / im.imageHeight
        w = min(max_w, im.imageWidth)
        h = w / aspect
        if h > 12*cm:
            h = 12*cm
            w = h * aspect
        im.drawWidth  = w
        im.drawHeight = h
        elems.append(im)
    else:
        elems.append(body(f"[image not found: {path.name}]"))
    elems.append(cap(caption))
    return elems

def two_col_table(header, rows, col_widths=(6*cm, 11*cm)):
    data = [list(header)] + [list(r) for r in rows]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0),  NAVY),
        ("TEXTCOLOR",    (0,0), (-1,0),  WHITE),
        ("FONTNAME",     (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,0),  9),
        ("BACKGROUND",   (0,1), (-1,-1), LGREY),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LGREY]),
        ("FONTSIZE",     (0,1), (-1,-1), 9),
        ("GRID",         (0,0), (-1,-1), 0.3, colors.HexColor("#CCCCCC")),
        ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING",   (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",(0,0), (-1,-1), 4),
        ("WORDWRAP",     (0,0), (-1,-1), "CJK"),
    ]))
    return t

def three_col_table(header, rows, col_widths=(3.5*cm, 6.5*cm, 7*cm)):
    data = [list(header)] + [list(r) for r in rows]
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0,0), (-1,0),  NAVY),
        ("TEXTCOLOR",    (0,0), (-1,0),  WHITE),
        ("FONTNAME",     (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0,0), (-1,0),  9),
        ("ROWBACKGROUNDS",(0,1),(-1,-1), [WHITE, LGREY]),
        ("FONTSIZE",     (0,1), (-1,-1), 8.5),
        ("GRID",         (0,0), (-1,-1), 0.3, colors.HexColor("#CCCCCC")),
        ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0), (-1,-1), 5),
        ("RIGHTPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING",   (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0), (-1,-1), 3),
        ("WORDWRAP",     (0,0), (-1,-1), "CJK"),
    ]))
    return t

def kv_table(rows, col_widths=(6*cm, 11*cm)):
    return two_col_table(("Parameter", "Value"), rows, col_widths)

# ── page numbering & header ───────────────────────────────────────────────────
class RecoupDocTemplate(SimpleDocTemplate):
    def __init__(self, filename, **kw):
        super().__init__(filename, **kw)
        self.on_cover = True

    def afterFlowable(self, flowable):
        pass

def _add_page_num(canvas, doc):
    canvas.saveState()
    page = canvas.getPageNumber()
    if page > 2:   # skip cover + TOC page
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(GREY)
        canvas.drawRightString(W - 2*cm, 1.2*cm, f"Page {page}")
        canvas.drawString(2*cm, 1.2*cm, "Recoup — Technical Documentation")
        canvas.setLineWidth(0.3)
        canvas.setStrokeColor(TEAL)
        canvas.line(2*cm, 1.4*cm, W - 2*cm, 1.4*cm)
    canvas.restoreState()

# ════════════════════════════════════════════════════════════════════════════
#  BUILD STORY
# ════════════════════════════════════════════════════════════════════════════
import datetime
story = []

# ── COVER ────────────────────────────────────────────────────────────────────
story += [
    sp(130),
    Paragraph("Recoup", S_COVER_TITLE),
    sp(4),
    Paragraph("Autonomous B2B Receivables &amp; Promise-to-Pay Agent", S_COVER_SUB),
    sp(8),
    Paragraph("Complete Backend Technical Documentation", S_COVER_DESC),
    sp(12),
    Paragraph(f"Generated: {datetime.date.today().strftime('%d %B %Y')}", S_COVER_VER),
    sp(6),
    Paragraph("Version 1.0  |  Track 03: AI Revenue Recovery  |  Razorpay AI Buildathon 2026", S_COVER_VER),
    pb(),
]

# ── TOC PAGE ─────────────────────────────────────────────────────────────────
story += [h1("Table of Contents"), sp(6)]
toc_items = [
    ("1. Executive Overview", 3),
    ("2. High-Level Architecture", 4),
    ("3. Component Deep-Dive", 5),
    ("   3.1  Prioritization Scorer", 5),
    ("   3.2  Policy / Gate Engine", 5),
    ("   3.3  Escalation State Machine", 6),
    ("   3.4  Promise-to-Pay Tracker", 6),
    ("   3.5  Reply Understanding", 7),
    ("   3.6  Decision Trace / Audit Ledger", 7),
    ("   3.7  Action Executor", 7),
    ("   3.8  Webhook Receiver", 8),
    ("4. Database Schema", 9),
    ("5. The Agentic Decision Loop", 10),
    ("6. API Surface", 11),
    ("7. Technology Stack", 12),
    ("8. External Service Integrations", 13),
    ("9. Machine Learning Models", 14),
    ("10. Security Model", 16),
    ("11. Observability", 17),
    ("12. Scaling Considerations", 18),
    ("13. Deployment", 19),
    ("14. Project Structure", 20),
    ("15. Roadmap to Production", 21),
    ("Appendix A — Honest Metrics", 22),
]
toc_data = [[item, str(pg)] for item, pg in toc_items]
toc_t = Table(toc_data, colWidths=[14*cm, 2*cm])
toc_t.setStyle(TableStyle([
    ("FONTSIZE",     (0,0), (-1,-1), 10),
    ("TEXTCOLOR",    (0,0), (-1,-1), DARK),
    ("TEXTCOLOR",    (1,0), (1,-1),  NAVY),
    ("FONTNAME",     (1,0), (1,-1),  "Helvetica-Bold"),
    ("TOPPADDING",   (0,0), (-1,-1), 3),
    ("BOTTOMPADDING",(0,0), (-1,-1), 3),
    ("LINEBELOW",    (0,0), (-1,-1), 0.2, colors.HexColor("#DDDDDD")),
]))
story += [toc_t, pb()]

# ════════════════════════════════════════════════════════════════════════════
#  1. EXECUTIVE OVERVIEW
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("1. Executive Overview"), hr(),
    body("India's Economic Survey (Budget 2026) put \u20b98.1 lakh crore currently stuck in delayed "
         "payments owed to MSMEs nationally. A 2026 industry report from Recordent found the average "
         "Indian SME is carrying \u20b93.83 crore in overdue receivables at any given time. "
         "This is one of the most pervasive working-capital problems a B2B business in India faces."),
    sp(),
    body("Recoup is an autonomous AI agent that decides who to chase for overdue B2B invoices, how hard, "
         "and when to stop \u2014 then proves exactly how much money it recovered. Built on FastAPI (Python 3.11+), "
         "PostgreSQL, Razorpay Payment Links, and a provider-agnostic LLM client, Recoup removes the "
         "judgment call of accounts-receivable follow-up from a person\u2019s plate without removing their control."),
    sp(10),
    h2("1.1 What Makes Recoup Different"),
    two_col_table(
        ("Generic Dunning Script", "Recoup"),
        [
            ("Same email to every overdue customer",   "Ranks by expected recovery value; low-risk invoices left alone"),
            ("Reminds until someone stops it",          "Hard escalation ladder with automatic stop \u2014 no indefinite nagging"),
            ("Any discount the LLM feels like offering","Policy-enforced discount ceiling the LLM cannot exceed"),
            ("'Reminder sent' is the whole log",        "Every action carries a reason; full decision trail queryable"),
            ("Success = emails sent",                   "Success = batch-level \u20b9 recovered, measured not claimed"),
            ("No memory of what was promised",          "Promises recorded, watched against deadline, drive next action"),
        ]
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  2. HIGH-LEVEL ARCHITECTURE
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("2. High-Level Architecture"), hr(),
    body("The diagram below shows the complete end-to-end data flow of Recoup, from the moment an "
         "overdue invoice enters the system through to the final batch evaluation report."),
    sp(8),
] + img(IMG_ARCH, "Figure 1 \u2013 Recoup Backend Data Flow") + [
    sp(10),
    h2("2.1 Core Design Principle: Score \u2192 Propose \u2192 GATE \u2192 Transition \u2192 Execute"),
    body("Every action the agent takes passes through exactly five ordered steps. "
         "The order is the safety property, not a style choice. Execution receives a PolicyDecision, "
         "never a raw ProposedAction, so there is no code path that can send a message without a gate verdict attached."),
    sp(6),
    three_col_table(
        ("Step", "Module", "Decides"),
        [
            ("score",      "app/core/scorer.py",     "What is this invoice worth acting on?"),
            ("propose",    "app/core/agent.py",       "What is the next rung, and how hard do we push?"),
            ("gate",       "app/core/policy.py",      "Is that action permitted by policy?"),
            ("transition", "app/core/escalation.py",  "May this case move to the next state?"),
            ("execute",    "app/services/",           "Send it, and log that we did"),
        ]
    ),
    note("Both approved AND blocked actions are recorded by app/core/audit.py. A blocked action leaves as much evidence as a sent one."),
    sp(8),
    h2("2.2 Layering: Why the Core Has No Database"),
    body("app/core/ operates on plain Pydantic snapshots (CaseSnapshot), not ORM rows. "
         "Two adapters feed it: one from the synthetic generator for batch demos, one from the database for live API operation. "
         "This achieves three goals:\n"
         "1. The demo runs on a clean checkout with no Postgres, no API keys, no network.\n"
         "2. Safety tests are unit-testable in milliseconds against in-memory objects.\n"
         "3. One scorer, one gate, two data sources \u2014 the API and batch paths converge on the same CaseSnapshot."),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  3. COMPONENT DEEP-DIVE
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("3. Component Deep-Dive"), hr(),
    h2("3.1 Prioritization Scorer  (app/core/scorer.py)"),
    body("The scorer ranks every open invoice by expected recovery value:"),
    sp(4),
    code("EV = P(recovery) x outstanding x urgency_weight - intervention_cost"),
    sp(4),
    body("This mirrors credit-risk practice (Expected Loss = PD x EAD x LGD) run in reverse. "
         "The scorer returns one of three tiers: WAIT (negative EV), REMIND (gentle contact), ESCALATE (high value-at-risk).\n\n"
         "The rules-based scorer declares fallback_used=True on every prediction. An optional trained model "
         "(XGBoost + Logistic Regression + MLP, isotonically calibrated) is available with USE_MODEL_SCORER=true.\n\n"
         "Trained model vs rules-based (batch of 600, seed 42):\n"
         "  AUC: 0.709 vs 0.667  |  Brier: 0.1944 vs 0.2433  |  Top-120 value-at-risk: +\u20b95.9L (+7.5%)"),
    sp(8),
    h2("3.2 Policy / Gate Engine  (app/core/policy.py)"),
    body("The policy engine is the single choke-point every outbound action must clear. "
         "Built on venmo/business-rules (Wave 4 adds regopy, in-process OPA). Rules are data: "
         "GET /api/v1/policy serves the compiled rule set verbatim.\n\n"
         "Two invariants:\n"
         "  \u2022 Blocks are absolute; adjustments only reduce. A rule can refuse or lower a ceiling, never approve or raise.\n"
         "  \u2022 Every violation is reported, not just the first. stop_on_first_trigger is off."),
    sp(4),
    kv_table([
        ("MAX_DISCOUNT_PERCENT",       "Hard ceiling on any discount offer (e.g. 10%)"),
        ("MAX_CONTACT_FREQUENCY_DAYS", "Minimum gap between contacts per invoice (e.g. 3 days)"),
        ("ESCALATION_LADDER_DAYS",     "Day offsets for each rung, e.g. 1,3,7,14"),
    ]),
    sp(8),
    h2("3.3 Escalation State Machine  (app/core/escalation.py)"),
    body("Built on pytransitions/transitions with auto_transitions=False. "
         "The only way a case can move is via the four declared triggers."),
    sp(6),
] + img(IMG_ESM, "Figure 2 \u2013 Escalation State Machine") + [
    sp(4),
    code("MONITORING  ->  send_reminder  ->  REMINDED\n"
         "REMINDED    ->  escalate       ->  ESCALATED\n"
         "ESCALATED   ->  handoff        ->  HUMAN_HANDOFF\n"
         "Any state   ->  close          ->  CLOSED"),
    sp(4),
    body("Guards (contact_allowed, ladder_step_due) are conditions on transitions, not post-hoc checks. "
         "A blocked transition simply does not happen \u2014 the trigger returns False and state is unchanged."),
    sp(8),
    h2("3.4 Promise-to-Pay Tracker  (app/core/promise_tracker.py)"),
    body("A promise is evidence of intent, never evidence of payment. Rules:\n"
         "  \u2022 Only a signature-verified Razorpay webhook sets PAID. Never a promise alone.\n"
         "  \u2022 A broken promise escalates exactly one rung per the ladder.\n"
         "  \u2022 Superseded promises are kept, not deleted ('rescheduled three times' is a pattern reviewers need).\n"
         "  \u2022 An opt-out reply permanently blocks all future automated contacts for that invoice."),
    sp(8),
    h2("3.5 Reply Understanding  (src/ml/reply/)"),
    body("Two-stage cascade classifier:\n\n"
         "Stage A: TF-IDF/SVM (joblib artifact) \u2014 handles 35.6% of replies at 0.917 accuracy. Grouped macro-F1: 0.764.\n\n"
         "Stage B: LLM via instructor (Groq/Gemini/Anthropic) \u2014 handles ambiguous cases with validation-aware retry.\n\n"
         "If both stages fail: classified as OTHER, routed to GET /api/v1/replies/review for human review. Never guesses.\n\n"
         "Intent labels: PROMISE_TO_PAY, DISPUTE, OPT_OUT, ACKNOWLEDGEMENT, OTHER"),
    sp(8),
    h2("3.6 Decision Trace / Audit Ledger  (app/core/audit.py)"),
    body("Append-only, hash-chained ledger. Each entry\u2019s entry_hash is SHA-256 over its own content "
         "plus its predecessor\u2019s hash. Any edit, reorder, or deletion breaks all subsequent hashes. "
         "DecisionLedger.verify() reports the first discrepancy.\n\n"
         "Wave 4 adds event sourcing (eventsourcing library): every Decision Trace append is dual-written "
         "as a semantic domain event, reconstructible at any version via get_case(invoice_id, version=N)."),
    sp(8),
    h2("3.7 Action Executor  (app/services/)"),
    body("Razorpay (razorpay_client.py): Creates test-mode Payment Links. DRY_RUN=true (default) logs "
         "without creating, consuming contact caps so a dry run behaves like live.\n\n"
         "Resend (resend_client.py): Email delivery. Signed Reply-To encodes invoice_id for reply routing.\n\n"
         "LLM (llm_client.py): Drafts explanation/reminder text ONLY. Cannot decide amounts or bypass the gate."),
    sp(8),
    h2("3.8 Webhook Receiver  (app/api/webhooks.py)"),
    body("The only writer of PAID status. Three ordered properties:\n"
         "  1. HMAC-SHA256 verification before any payload parsing.\n"
         "  2. Idempotency on event_id \u2014 duplicate returns 200 without re-processing.\n"
         "  3. Acknowledge what was stored: unparseable payload returns 400, not 500."),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  4. DATABASE SCHEMA
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("4. Database Schema"), hr(),
    body("PostgreSQL via SQLAlchemy 2.0 (async, psycopg3). Schema owned by Alembic \u2014 "
         "the application never creates tables at startup."),
    sp(8),
] + img(IMG_DB, "Figure 3 \u2013 Entity-Relationship Diagram") + [
    sp(6),
    h2("4.1 Tables"),
    two_col_table(
        ("Table", "Purpose"),
        [
            ("customers",      "Customer profiles: payment behaviour archetype, opt-out flag"),
            ("invoices",       "Core invoice record: amount, due_date, status, escalation_state"),
            ("promises",       "Promise-to-pay commitments: promised_amount, promised_date, kept flag"),
            ("decision_trace", "Immutable hash-chained audit log of every decision"),
            ("payments",       "Webhook-confirmed Razorpay payment events"),
            ("integrations",   "Credentials references for Razorpay, Resend, LLM"),
            ("run_summaries",  "Aggregate metrics from each batch run cycle"),
        ]
    ),
    h2("4.2 Migration Strategy"),
    body("All schema changes go through Alembic: alembic upgrade head runs before every deploy. "
         "The application performs a SELECT 1 connectivity check at startup and refuses to serve if the DB is unreachable."),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  5. AGENTIC DECISION LOOP
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("5. The Agentic Decision Loop"), hr(),
    body("The agent runs in batch mode (POST /api/v1/tasks/run-batch, advisory-locked) or per-invoice "
         "on demand (POST /api/v1/invoices/{id}/run-cycle). The decision loop is identical in both modes."),
    sp(8),
    h2("5.1 Step-by-Step Walkthrough"),
    body("Given three invoices:"),
    sp(4),
    code("INV-1044  Rs 75,000   2 days overdue   Customer: strong payment history\n"
         "INV-1042  Rs 50,000   5 days overdue   Customer: reliably pays late but pays\n"
         "INV-1043  Rs 2,00,000 12 days overdue  Customer: high-value, new risk signals"),
    sp(6),
    body("Step 1 - Score:\n"
         "  INV-1044 P(recovery)=0.84 -> REMIND  |  INV-1042 -> REMIND  |  INV-1043 -> ESCALATE\n\n"
         "Step 2 - Propose:\n"
         "  INV-1043: 'Pay Rs 1,80,000 by Friday; Rs 20,000 late fee waived'  (Rs 20,000 = configured ceiling)\n\n"
         "Step 3 - Gate:\n"
         "  All three pass contact-frequency cap. INV-1043 discount 10% <= 10% ceiling -> APPROVED.\n\n"
         "Step 4 - Transition:\n"
         "  Each invoice advances one rung on the state machine.\n\n"
         "Step 5 - Execute:\n"
         "  Razorpay Payment Link generated. Resend email dispatched. All appended to Decision Trace.\n\n"
         "Step 6 - Promise Tracking:\n"
         "  Customer replies 'I'll pay Friday' -> recorded as promise.\n"
         "  Friday arrives, no webhook -> promise broken -> INV-1043 escalates one rung."),
    sp(10),
    h2("5.2 Batch Evaluation Results (600 invoices, 5 cycles, seed 42)"),
    kv_table([
        ("Invoices processed",               "600"),
        ("Total overdue value",              "Rs 6.96 Cr"),
        ("Cases flagged for intervention",   "574"),
        ("Cases left alone",                 "26"),
        ("Interventions executed",           "580"),
        ("Actions blocked by policy",        "699"),
        ("Recovered (of flagged)",           "385  (67.1%)"),
        ("Recovered value",                  "Rs 4.68 Cr"),
        ("False / unnecessary interventions","316"),
        ("Compliance violations",            "0"),
        ("Ledger hash chain verified",       "Yes"),
        ("Decision trace entries",           "5,807"),
    ]),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  6. API SURFACE
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("6. API Surface"), hr(),
    body("Versioned REST API at /api/v1/. Interactive docs at /docs only when DEBUG=true (off by default)."),
    sp(8),
    three_col_table(
        ("Method", "Endpoint", "Purpose"),
        [
            ("GET",  "/api/v1/health",               "Liveness check"),
            ("GET",  "/api/v1/health/db",            "Database connectivity check"),
            ("POST", "/api/v1/invoices/batch",        "Ingest a batch of invoices and customers"),
            ("GET",  "/api/v1/invoices/{id}",         "View one invoice's current state"),
            ("GET",  "/api/v1/invoices/{id}/audit",   "Full decision trail for one invoice"),
            ("POST", "/api/v1/invoices/{id}/run-cycle","Manually trigger one agent decision cycle"),
            ("POST", "/api/v1/webhooks/razorpay",     "Razorpay webhook receiver (HMAC-verified)"),
            ("POST", "/api/v1/replies",               "Inbound email reply from Resend webhook"),
            ("GET",  "/api/v1/replies/review",        "Human review queue for ambiguous replies"),
            ("GET",  "/api/v1/reports/batch",         "Batch evaluation report"),
            ("GET",  "/api/v1/forecast/cash",         "Probabilistic 7/30-day cash forecast"),
            ("GET",  "/api/v1/forecast/cash/card",    "Validation metrics for the cash forecast"),
            ("GET",  "/api/v1/policy",                "Currently configured policy"),
            ("POST", "/api/v1/tasks/run-batch",       "Trigger autonomous batch run (advisory-locked)"),
            ("GET",  "/api/v1/secrets/status",        "Secret provider health and last-refresh time"),
            ("POST", "/api/v1/secrets/refresh",       "Manually trigger secret refresh from provider"),
        ],
        col_widths=(2*cm, 6.5*cm, 8.5*cm)
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  7. TECH STACK
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("7. Technology Stack"), hr(),
    three_col_table(
        ("Layer", "Choice", "Why"),
        [
            ("API framework",  "FastAPI + Uvicorn (Python 3.11+)",         "Typed contracts, async-native, Pydantic v2"),
            ("Database",       "PostgreSQL / SQLAlchemy 2.0 async / psycopg3","Relational integrity; Alembic migrations"),
            ("Local dev DB",   "SQLite via aiosqlite",                     "Zero-infrastructure demo and CI"),
            ("Payments",       "Razorpay Payment Links + Webhooks",        "Real verifiable lifecycle; zero financial risk"),
            ("Email",          "Resend",                                   "Fast setup; signed Reply-To routing"),
            ("LLM",            "Groq / Gemini / Anthropic (agnostic)",     "Zero-cost demo; reasoning never decides amounts"),
            ("Policy engine",  "business-rules + regopy (in-process OPA)","Rules as data; Rego < 20ms p99"),
            ("Escalation FSM", "pytransitions/transitions",                "auto_transitions=False enforces ladder"),
            ("Structured LLM", "instructor",                               "Schema-bound output with validation-aware retry"),
            ("Event sourcing",  "eventsourcing",                           "Aggregate stream; time-travel replay"),
            ("ML training",    "XGBoost, scikit-learn, SHAP",              "Recovery scorer; calibrated; explainable"),
            ("Rate limiting",  "Redis (hiredis) sliding window",           "Shared state across replicas"),
            ("Observability",  "OpenTelemetry SDK + OTLP",                 "Traces + metrics; console fallback locally"),
            ("Logging",        "structlog + python-json-logger",           "Structured JSON logs; request-ID correlation"),
            ("Hosting",        "Render (backend) + Vercel (frontend)",     "Single public URL; no infra overhead"),
        ],
        col_widths=(3.5*cm, 5.5*cm, 8*cm)
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  8. EXTERNAL SERVICES
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("8. External Service Integrations"), hr(),
    h2("8.1 Razorpay"),
    body("app/services/razorpay_client.py wraps the razorpay SDK (>=2.0).\n"
         "Payment Link creation: accepts amount, description, customer details, expiry. "
         "DRY_RUN=true logs the link body without creating it.\n"
         "Webhook: HMAC-SHA256 before any parsing. Idempotent on event_id. "
         "Handles payment.captured (-> PAID) and payment_link.expired (-> re-score)."),
    sp(8),
    h2("8.2 Resend Email"),
    body("app/services/resend_client.py:\n"
         "Sends HTML/text emails. Signed Reply-To encodes invoice_id (HMAC): "
         "reply+{invoice_id}@{RESEND_DOMAIN}. "
         "Inbound replies arrive at POST /api/v1/replies and are routed without any DB lookup."),
    sp(8),
    h2("8.3 LLM Client (Provider-Agnostic)"),
    body("Selects provider from LLM_PROVIDER env var (groq | gemini | anthropic).\n"
         "Two function types:\n"
         "  generate() -> free-text: reminder copy, escalation narration.\n"
         "  generate_structured() -> instructor-wrapped schema-bound calls (reply classification, promise extraction)."),
    sp(8),
    h2("8.4 Secrets Management"),
    body("Priority order: Render env -> Doppler -> AWS Secrets Manager.\n"
         "inject_secrets_into_env() runs first in lifespan startup, before any other service. "
         "Cached with TTL (SECRET_CACHE_TTL_HOURS, default 1h). "
         "Background refresh every SECRET_REFRESH_INTERVAL_SECONDS (default 300s).\n"
         "Secret values are scrubbed before any structlog emission."),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  9. ML MODELS
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("9. Machine Learning Models"), hr(),
    h2("9.1 Recovery Probability Model  (src/ml/recovery/)"),
    body("Three candidates trained on the same temporal split: Logistic Regression (baseline), "
         "XGBoost, small MLP. Each isotonically calibrated on a held-out validation split."),
    sp(4),
    two_col_table(
        ("Metric", "Result"),
        [
            ("AUC",            "0.709 (trained) vs 0.667 (rules)"),
            ("Brier score",    "0.1944 (trained) vs 0.2433 (rules)"),
            ("ECE",            "0.065 (trained) vs 0.180 (rules)"),
            ("Top-120 at risk","Rs 85.5L (trained) vs Rs 79.5L (rules)  (+7.5%)"),
        ]
    ),
    h3("Top SHAP features:"),
    two_col_table(
        ("Feature", "Mean |SHAP|"),
        [
            ("recency_weighted_on_time_score",  "0.469"),
            ("customer_on_time_ratio_all_time", "0.275"),
            ("customer_invoice_count",          "0.271"),
            ("prior_promise_kept",              "0.218"),
            ("customer_avg_days_late",          "0.214"),
            ("current_escalation_tier",         "0.153"),
        ]
    ),
    sp(8),
    h2("9.2 Reply Intent Classifier  (src/ml/reply/)"),
    body("Two-stage cascade:\n"
         "Stage A: TF-IDF + Linear SVM (sklearn Pipeline). "
         "Handles 35.6% of replies at 0.917 accuracy. Grouped macro-F1: 0.764.\n\n"
         "Stage B: LLM via instructor for ambiguous cases. Retries with the specific validation error.\n\n"
         "Intent labels: PROMISE_TO_PAY, DISPUTE, OPT_OUT, ACKNOWLEDGEMENT, OTHER."),
    sp(8),
    h2("9.3 Cash Forecast  (src/ml/cash_forecast/)"),
    body("Monte Carlo simulation over the at-risk book. "
         "Samples from calibrated P(recovery) distributions to produce P10/P50/P90 cash-in "
         "forecasts over 7-day and 30-day horizons. "
         "Validation card at GET /api/v1/forecast/cash/card."),
    sp(8),
    h2("9.4 Contact Timing  (src/ml/contact_timing/)"),
    body("Segmented Thompson Sampling bandit. "
         "Customers segmented by payment-behaviour archetype. "
         "Within each segment the bandit learns which day/time contact slots yield highest "
         "promise-to-pay conversion, updating Beta posteriors on each observed outcome."),
    sp(8),
    h2("9.5 Drift Detection  (src/ml/drift/)"),
    body("Detects customers whose payment behaviour is shifting from their historical archetype. "
         "Classifier trained on rolling payment ratio, days-late trend, dispute velocity. "
         "A drifting customer receives a higher urgency weight in the scorer."),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  10. SECURITY MODEL
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("10. Security Model"), hr(),
] + img(IMG_SEC, "Figure 4 \u2013 Layered Security Architecture") + [
    sp(8),
    h2("10.1 Request Layer"),
    body("CORS Middleware: explicit origin list (never wildcard). Allow-credentials only when specific origins named.\n\n"
         "Request ID Middleware: every request receives a UUID correlation ID injected into structlog context "
         "and propagated through all trace spans."),
    sp(6),
    h2("10.2 Webhook Security"),
    body("HMAC-SHA256 verified against RAZORPAY_WEBHOOK_SECRET before any payload parsing. "
         "Invalid signature returns 400 immediately. Verification uses the raw request body to prevent "
         "encoding normalisation from invalidating the signature."),
    sp(6),
    h2("10.3 Policy Gate as Security Boundary"),
    body("The gate is not just a business-rule checker \u2014 it is a hard security boundary:\n"
         "  \u2022 Discount ceiling: LLM cannot offer more than MAX_DISCOUNT_PERCENT regardless of text generated.\n"
         "  \u2022 Opt-out registry: once set, blocks all automated contacts permanently. No bypass.\n"
         "  \u2022 Contact frequency cap: prevents customer harassment and regulatory exposure.\n\n"
         "Wave 4 replaced business-rules with regopy (rego-cpp). Config values are a per-tenant data "
         "document the request cannot modify."),
    sp(6),
    h2("10.4 Audit Trail Integrity"),
    body("Hash-chained append-only ledger. Any edit, reorder, or deletion breaks every subsequent hash. "
         "Verified in the batch evaluation report and in unit tests."),
    sp(6),
    h2("10.5 Secrets Security"),
    two_col_table(
        ("Control", "Implementation"),
        [
            ("No secret logging",     "Values scrubbed before any structlog emission"),
            ("Provider auth",         "IAM roles / service accounts where supported"),
            ("Rotation validation",   "POST /api/v1/secrets/refresh + GET /api/v1/secrets/status"),
            ("PII in traces",         "Span attributes sanitised; customer names/emails excluded"),
            ("OTLP auth",             "Configurable auth headers on OTLP exporter endpoint"),
        ]
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  11. OBSERVABILITY
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("11. Observability"), hr(),
    h2("11.1 OpenTelemetry Tracing"),
    body("Auto-instrumented: FastAPI (request spans), SQLAlchemy (query spans), httpx (outbound HTTP).\n\n"
         "Manual span hierarchy:\n"
         "  batch.cycle -> batch.scoring_phase -> invoice.score (per invoice)\n"
         "  batch.cycle -> batch.decision_phase -> invoice.decision\n"
         "  batch.cycle -> batch.execution_phase -> invoice.execute\n\n"
         "Export: OTLP to configured endpoint. Falls back to ConsoleSpanExporter (stdout JSON) "
         "when OTEL_EXPORTER_OTLP_ENDPOINT is not set."),
    sp(8),
    h2("11.2 Structured Logging"),
    body("structlog emits JSON logs with: request_id, invoice_id, customer_id, action, "
         "level, timestamp, module, function.\n"
         "LOG_LEVEL env var controls verbosity (default INFO in production, DEBUG locally)."),
    sp(8),
    h2("11.3 Health Endpoints"),
    two_col_table(
        ("Endpoint", "Checks"),
        [
            ("GET /api/v1/health",         "Application liveness"),
            ("GET /api/v1/health/db",      "Database connectivity (SELECT 1)"),
            ("GET /api/v1/ready/enhanced", "DB + secret provider + OTLP exporter health"),
        ]
    ),
    h2("11.4 Key SLOs"),
    two_col_table(
        ("Metric", "Target"),
        [
            ("Batch cycle p95 latency",    "< 30 seconds"),
            ("Health check response time", "< 500 ms"),
            ("Secret refresh time",        "< 100 ms"),
            ("Policy gate decision",       "< 20 ms p99 (in-process Rego)"),
            ("Webhook verification + ack", "< 200 ms"),
        ]
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  12. SCALING CONSIDERATIONS
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("12. Scaling Considerations"), hr(),
    h2("12.1 Stateless API Design"),
    body("All state lives in PostgreSQL. Horizontal scaling is a config change (increase Render instance count). "
         "The batch advisory lock (pg_try_advisory_xact_lock) prevents concurrent batch cycles across replicas "
         "without any additional coordination."),
    sp(6),
    h2("12.2 Rate Limiter"),
    body("Redis sliding-window (redis[hiredis]>=5.0) when RATE_LIMIT_REDIS_URL is set. "
         "Falls back to in-process deque for a single replica. "
         "Adding Redis is the only change needed to make the frequency cap correct across replicas."),
    sp(6),
    h2("12.3 Database Connection Pooling"),
    body("DB_POOL_MODE controls SQLAlchemy pool mode:\n"
         "  session     \u2014 default; one connection per request.\n"
         "  transaction \u2014 connection returned to pool between transactions (PgBouncer-compatible)."),
    sp(6),
    h2("12.4 LLM Concurrency"),
    body("LLM calls are async (httpx-backed). The batch loop runs invoice decisions sequentially "
         "within a cycle to preserve causal ordering in the Decision Trace. "
         "Scoring (CPU-local, no writes) is safe to parallelise with asyncio.gather."),
    sp(6),
    h2("12.5 Multi-Tenant Roadmap"),
    body("1. OIDC/JWT authentication (Auth0, Clerk).\n"
         "2. Per-business row-level data isolation in Postgres (RLS or schema-per-tenant).\n"
         "3. Per-tenant PolicyConfig document in the Rego data store.\n"
         "4. Per-tenant ESCALATION_LADDER_DAYS, MAX_DISCOUNT_PERCENT, etc.\n\n"
         "The policy gate is already designed for this: Wave 4's Rego evaluation takes a "
         "business_id-keyed data document, so per-tenant configuration is a data change, not a code change."),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  13. DEPLOYMENT
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("13. Deployment"), hr(),
    h2("13.1 Infrastructure"),
    two_col_table(
        ("Component", "Hosting"),
        [
            ("Backend API",          "Render free web service (Python, uvicorn)"),
            ("Frontend dashboard",   "Vercel (Next.js)"),
            ("Database",             "Neon or Supabase free tier (PostgreSQL)"),
            ("Email inbound worker", "Cloudflare Email Worker"),
            ("Redis (optional)",     "Render Redis or Upstash"),
        ]
    ),
    h2("13.2 Release Process"),
    code("1. alembic upgrade head    <- migrate schema before new code serves\n"
         "2. uvicorn app.main:app    <- start application\n"
         "3. Health check at /api/v1/health/db confirms DB reachable\n"
         "4. install_cascade_classifier() binds the reply classifier"),
    sp(8),
    h2("13.3 Environment Variables"),
    two_col_table(
        ("Variable", "Purpose"),
        [
            ("DATABASE_URL",                  "Postgres connection string"),
            ("RAZORPAY_KEY_ID",               "Razorpay test-mode key ID"),
            ("RAZORPAY_KEY_SECRET",           "Razorpay test-mode key secret"),
            ("RAZORPAY_WEBHOOK_SECRET",       "HMAC secret for webhook signature verification"),
            ("RESEND_API_KEY",                "Resend email delivery API key"),
            ("LLM_PROVIDER",                  "groq | gemini | anthropic"),
            ("GROQ_API_KEY / GEMINI_API_KEY", "Set only the one matching LLM_PROVIDER"),
            ("MAX_DISCOUNT_PERCENT",          "Hard ceiling on any offer"),
            ("MAX_CONTACT_FREQUENCY_DAYS",    "Minimum gap between contacts per invoice"),
            ("ESCALATION_LADDER_DAYS",        "Day offsets, e.g. 1,3,7,14"),
            ("DRY_RUN",                       "true (default) = log actions, don't send"),
            ("USE_MODEL_SCORER",              "true = XGBoost scorer; false (default) = rules"),
            ("APP_ENV",                       "development | demo | production"),
            ("DEBUG",                         "true = /docs enabled; false = disabled"),
            ("RATE_LIMIT_REDIS_URL",          "Redis URL for distributed rate limiting"),
            ("OTEL_EXPORTER_OTLP_ENDPOINT",  "OTLP export target (console fallback if absent)"),
        ]
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  14. PROJECT STRUCTURE
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("14. Project Structure"), hr(),
    code(
        "app/\n"
        "  main.py                FastAPI app entry, lifespan, middleware\n"
        "  api/                   Route handlers (15 routers)\n"
        "  core/\n"
        "    domain.py            CaseSnapshot read model\n"
        "    scorer.py            Expected-value prioritisation\n"
        "    policy.py            Gate (business-rules + OPA/Rego)\n"
        "    escalation.py        State machine (pytransitions)\n"
        "    promise_tracker.py   Promise-to-pay logic\n"
        "    audit.py             Hash-chained Decision Trace\n"
        "    agent.py             Decision cycle, end to end\n"
        "    evaluation.py        Batch report computation\n"
        "    eventsourcing/       Event store integration\n"
        "  models/                SQLAlchemy ORM tables\n"
        "  schemas/               Pydantic request/response contracts\n"
        "  db/session.py          Async engine + session factory\n"
        "  services/\n"
        "    llm_client.py        Provider-agnostic LLM wrapper\n"
        "    razorpay_client.py   Payment Links + webhook verification\n"
        "    resend_client.py     Email delivery\n"
        "    repository.py        All DB access (single module)\n"
        "    batch_runner.py      Batch cycle orchestration\n"
        "\n"
        "src/\n"
        "  data/synthetic_generator.py   Seeded invoice+customer batch\n"
        "  ml/\n"
        "    recovery/            Recovery probability model\n"
        "    reply/               Reply intent cascade classifier\n"
        "    cash_forecast/       Monte Carlo cash forecast\n"
        "    contact_timing/      Thompson Sampling bandit\n"
        "    drift/               Payment behaviour drift classifier\n"
        "\n"
        "alembic/                 Schema migrations\n"
        "scripts/                 Batch demo, training, seeding, this script\n"
        "tests/                   384 tests (no DB or network required)\n"
        "frontend/                Next.js dashboard (Vercel)\n"
        "cloudflare-email-worker/ Inbound email routing to API\n"
        "docs/                    Architecture, model cards, evaluation report"
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  15. ROADMAP
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("15. Roadmap to Production"), hr(),
    body("The current MVP is production-shaped but not production-hardened:"),
    sp(6),
    two_col_table(
        ("Item", "Detail"),
        [
            ("Multi-tenant auth",         "OIDC/JWT + per-business row-level data isolation"),
            ("WhatsApp delivery",         "Meta Business verification; email stands in for demo"),
            ("Webhook idempotency",       "Harden double-counting protection for production payment infra"),
            ("Online model retraining",   "Re-train recovery scorer from webhook-confirmed outcomes"),
            ("Policy Simulation Engine",  "POST /policy/simulate: replay last quarter under proposed policy change"),
            ("SetFit reply classifier",   "Fine-tune from 8-16 labelled examples once real reply data exists"),
            ("Observability stack",       "Self-hosted Grafana + Tempo or Honeycomb for trace storage"),
            ("Secrets + backup drills",   "Automated backup drill, rotation runbooks, alerting"),
            ("Compliance review",         "Legal review of escalation cadence before any live customer contact"),
            ("Kubernetes / Terraform",    "Infrastructure-as-code for multi-region production deploy"),
        ]
    ),
    pb(),
]

# ════════════════════════════════════════════════════════════════════════════
#  APPENDIX A
# ════════════════════════════════════════════════════════════════════════════
story += [
    h1("Appendix A \u2014 How to Read the Evaluation Metrics"), hr(),
    body("Regenerate with:\n"
         "  python scripts/run_batch_demo.py --batch-size 600 --seed 42 --cycles 5 --use-model\n\n"
         "Read all numbers with these four caveats, which the report itself repeats:"),
    sp(6),
    h2("A.1 The agent did not cause these recoveries"),
    body("The synthetic generator samples each invoice's outcome independently of what the agent does. "
         "The recovery rate is a targeting measure \u2014 did the agent act on the invoices that were going to be paid? "
         "\u2014 not a causal one. Measuring uplift requires a holdout arm this simulation does not have."),
    sp(6),
    h2("A.2 The scorer is rules-based by default"),
    body("Probabilities from the rules-based scorer are not calibrated. "
         "Add --use-model to score with the trained, calibrated model."),
    sp(6),
    h2("A.3 False interventions are counted as a cost"),
    body("A contacted customer whose invoice would have been recovered anyway appears in the "
         "false interventions row \u2014 not quietly dropped. That number going up is a real regression."),
    sp(6),
    h2("A.4 Compliance violations are counted from the ledger, not from intent"),
    body("The detector walks the Decision Trace in sequence. A bug in the orchestrator cannot "
         "suppress the violation count because the count reads the audit record, not the "
         "orchestrator's own return values."),
]

# ════════════════════════════════════════════════════════════════════════════
#  BUILD PDF
# ════════════════════════════════════════════════════════════════════════════
doc = SimpleDocTemplate(
    str(OUT),
    pagesize=A4,
    rightMargin=2*cm, leftMargin=2*cm,
    topMargin=2.2*cm, bottomMargin=2.2*cm,
    title="Recoup Technical Documentation",
    author="Recoup",
    subject="Backend Readbook",
)
doc.build(story, onFirstPage=_add_page_num, onLaterPages=_add_page_num)
size = OUT.stat().st_size
print(f"\n[DONE] PDF written to:\n    {OUT}\n    Size: {size/1024:.0f} KB")
