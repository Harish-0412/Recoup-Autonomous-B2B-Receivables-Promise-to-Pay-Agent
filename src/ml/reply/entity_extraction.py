"""Deterministic entity extraction from customer replies.

No model, no training data, no network call -- and therefore just as auditable
as any other rule in the policy engine. When a promise is auto-created from a
reply, a human can point at the exact regex that produced the amount.

The extractors are conservative by design: returning ``None`` sends the reply
down the LLM or human-review path, whereas a confidently wrong amount could
create a bogus promise-to-pay record.
"""

from datetime import date, datetime, timedelta
from typing import Iterable

import re

import dateparser

from src.ml.schemas import ExtractedEntities

# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

DEFAULT_CURRENCY = "INR"

#: Ordered so that a more specific token wins: "US$" before "$".
_CURRENCY_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"₹|\brs\.?\b|\binr\b|\brupees?\b", "INR"),
    (r"\busd\b|\bus\$|\bdollars?\b", "USD"),
    (r"€|\beur\b|\beuros?\b", "EUR"),
    (r"£|\bgbp\b|\bpounds?\b", "GBP"),
    (r"\baed\b|\bdirhams?\b", "AED"),
    (r"\bsgd\b", "SGD"),
    (r"\$", "USD"),
)

_CURRENCY_REGEXES = tuple(
    (re.compile(pattern, re.IGNORECASE), code) for pattern, code in _CURRENCY_PATTERNS
)


def normalize_currency(text: str) -> str:
    """Return the ISO currency code mentioned in ``text``.

    Defaults to ``INR`` -- Recoup's market -- unless another currency token is
    explicitly present.
    """

    if not text:
        return DEFAULT_CURRENCY

    best: tuple[int, str] | None = None
    for regex, code in _CURRENCY_REGEXES:
        match = regex.search(text)
        if match and (best is None or match.start() < best[0]):
            best = (match.start(), code)

    return best[1] if best else DEFAULT_CURRENCY


# ---------------------------------------------------------------------------
# Masking: strip spans that would otherwise be misread as amounts or dates
# ---------------------------------------------------------------------------

#: INV-2026-00027, PO/4471-A, GSTIN-style references.
_REFERENCE_RE = re.compile(r"\b[A-Za-z]{2,}[-/][A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)*\b")

_MONTHS = (
    "jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?"
    r"|aug(?:ust)?|sep(?:t)?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"
)

_ORDINAL = r"(?:st|nd|rd|th)?"

_DATE_PATTERNS: tuple[str, ...] = (
    r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    r"\b\d{4}[/-]\d{1,2}[/-]\d{1,2}\b",
    rf"\b\d{{1,2}}{_ORDINAL}\s+(?:of\s+)?(?:{_MONTHS})\.?(?:,?\s+\d{{2,4}})?\b",
    rf"\b(?:{_MONTHS})\.?\s+\d{{1,2}}{_ORDINAL}(?:,?\s+\d{{2,4}})?\b",
)

_DATE_REGEXES = tuple(re.compile(pattern, re.IGNORECASE) for pattern in _DATE_PATTERNS)

#: A bare four-digit year is a year, not a rupee amount, in this domain.
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


def _blank(match: re.Match[str]) -> str:
    """Replace a span with spaces so every other offset stays put."""

    return " " * (match.end() - match.start())


def _mask_for_amounts(text: str) -> str:
    """Blank out references, dates and years before hunting for money."""

    masked = _REFERENCE_RE.sub(_blank, text)
    for regex in _DATE_REGEXES:
        masked = regex.sub(_blank, masked)
    return _YEAR_RE.sub(_blank, masked)


def _mask_for_dates(text: str) -> str:
    """Blank out document references so their digits are not read as dates."""

    return _REFERENCE_RE.sub(_blank, text)


# ---------------------------------------------------------------------------
# Amounts
# ---------------------------------------------------------------------------

#: Indian grouping (1,80,000), western grouping (180,000) and plain numbers.
_NUMBER = r"\d{1,3}(?:(?:,\d{2})+,\d{3}|(?:,\d{3})+)(?:\.\d+)?|\d+(?:\.\d+)?"

_MULTIPLIERS: dict[str, float] = {
    "k": 1e3,
    "thousand": 1e3,
    "l": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "lakh": 1e5,
    "lakhs": 1e5,
    "lakhs.": 1e5,
    "cr": 1e7,
    "crore": 1e7,
    "crores": 1e7,
    "mn": 1e6,
    "million": 1e6,
}

_UNIT = r"k|thousand|lakhs?|lacs?|l|crores?|cr|million|mn"

_CURRENCY_PREFIX = r"₹|rs\.?|inr|rupees?|\$|usd|€|eur|£|gbp|aed"

#: Tier 1: an explicit currency marker. Tier 2: a magnitude unit. Tier 3: digit
#: grouping. Tier 4: a bare number. Lower tier wins.
_AMOUNT_TIER_REGEXES: tuple[tuple[int, re.Pattern[str]], ...] = (
    (
        1,
        re.compile(
            rf"(?:{_CURRENCY_PREFIX})\s*(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT})?\b",
            re.IGNORECASE,
        ),
    ),
    (
        2,
        re.compile(rf"(?P<number>{_NUMBER})\s*(?P<unit>{_UNIT})\b", re.IGNORECASE),
    ),
    (
        3,
        re.compile(rf"(?P<number>\d{{1,3}}(?:(?:,\d{{2}})+,\d{{3}}|(?:,\d{{3}})+)(?:\.\d+)?)"),
    ),
    (
        4,
        re.compile(r"(?P<number>\d+(?:\.\d+)?)"),
    ),
)

#: A number followed by one of these counts something that is not money.
_NON_MONETARY_SUFFIXES: frozenset[str] = frozenset(
    {
        "day", "days", "week", "weeks", "month", "months", "year", "years",
        "percent", "pc", "pcs", "units", "unit", "nos", "no", "kg", "kgs",
        "tons", "boxes", "cartons", "invoices", "invoice", "installments",
        "instalments", "emis", "emi", "hours", "hrs", "am", "pm",
    }
)

#: A number preceded by one of these identifies a document, not an amount.
_REFERENCE_PREFIXES: frozenset[str] = frozenset(
    {"invoice", "inv", "po", "gst", "gstin", "ref", "reference", "utr", "order", "bill", "no"}
)

_WORD_BEFORE_RE = re.compile(r"([A-Za-z]+)[\s.:#-]*$")
_WORD_AFTER_RE = re.compile(r"^[\s]*([A-Za-z%]+)")


def _is_percentage(text: str, end: int) -> bool:
    rest = text[end:]
    stripped = rest.lstrip()
    return stripped.startswith("%") or stripped.lower().startswith("percent")


def _word_before(text: str, start: int) -> str | None:
    match = _WORD_BEFORE_RE.search(text[:start])
    return match.group(1).lower() if match else None


def _word_after(text: str, end: int) -> str | None:
    match = _WORD_AFTER_RE.match(text[end:])
    return match.group(1).lower() if match else None


def _parse_number(raw: str) -> float | None:
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _amount_candidates(text: str) -> list[tuple[int, int, float]]:
    """Return ``(tier, position, value)`` for every plausible money mention."""

    masked = _mask_for_amounts(text)
    candidates: list[tuple[int, int, float]] = []

    for tier, regex in _AMOUNT_TIER_REGEXES:
        for match in regex.finditer(masked):
            number = _parse_number(match.group("number"))
            if number is None:
                continue

            unit = (match.groupdict().get("unit") or "").lower().rstrip(".")
            multiplier = _MULTIPLIERS.get(unit, 1.0) if unit else 1.0

            if _is_percentage(masked, match.end()):
                continue
            if _word_before(masked, match.start()) in _REFERENCE_PREFIXES:
                continue
            if not unit and _word_after(masked, match.end()) in _NON_MONETARY_SUFFIXES:
                continue

            value = number * multiplier
            if value <= 0:
                continue
            # A bare, ungrouped, unmarked number below 1000 is far more likely
            # to be a quantity than a payment.
            if tier == 4 and value < 1000:
                continue

            candidates.append((tier, match.start(), value))

    return candidates


def extract_amount(text: str) -> float | None:
    """Extract a monetary amount, or ``None`` when nothing is confidently money.

    Handles rupee symbols and words, Indian and western digit grouping, and the
    shorthand Indian businesses actually type: ``1.8L``, ``45k``, ``2 crore``.
    Percentages, day counts, quantities and document references are rejected.
    """

    if not text:
        return None

    candidates = _amount_candidates(text)
    if not candidates:
        return None

    best_tier = min(tier for tier, _, _ in candidates)
    same_tier = [c for c in candidates if c[0] == best_tier]
    return round(min(same_tier, key=lambda c: c[1])[2], 2)


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_WEEKDAYS = "monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun"

_DATE_CANDIDATE_PATTERNS: tuple[str, ...] = _DATE_PATTERNS + (
    r"\bday after tomorrow\b",
    r"\btomorrow\b",
    r"\btoday\b",
    r"\btonight\b",
    r"\b(?:in|within|after)\s+\d{1,3}\s*(?:days?|weeks?|months?)\b",
    r"\b\d{1,3}\s*(?:days?|weeks?|months?)\s+(?:from now|from today|time)\b",
    r"\b(?:next|this|coming|by)\s+(?:" + _WEEKDAYS + r")\b",
    r"\b(?:" + _WEEKDAYS + r")\b",
    r"\bnext\s+(?:week|month)\b",
    r"\bend\s+of\s+(?:the\s+|this\s+|next\s+)?(?:week|month)\b",
    r"\b(?:month|week)[\s-]?end\b",
)

_DATE_CANDIDATE_REGEXES = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in _DATE_CANDIDATE_PATTERNS
)

_LEADING_CONNECTORS_RE = re.compile(
    r"^(?:by|on|before|till|until|upto|up to|latest by|positively by|the)\s+",
    re.IGNORECASE,
)

#: dateparser resolves "Friday" but not "next Friday"; with a future
#: preference the two mean the same thing, so the qualifier is dropped.
_WEEKDAY_QUALIFIER_RE = re.compile(
    r"^(?:next|this|coming)\s+(?=(?:" + _WEEKDAYS + r")\b)", re.IGNORECASE
)

#: dateparser resolves "in 2 weeks" but not "within 2 weeks".
_WITHIN_RE = re.compile(r"^within\s+", re.IGNORECASE)

_MONTH_END_RE = re.compile(
    r"\bend\s+of\s+(?:the\s+|this\s+)?month\b|\bmonth[\s-]?end\b", re.IGNORECASE
)
_NEXT_MONTH_END_RE = re.compile(r"\bend\s+of\s+next\s+month\b", re.IGNORECASE)

_DATEPARSER_SETTINGS: dict[str, object] = {
    "DATE_ORDER": "DMY",
    "PREFER_DATES_FROM": "future",
    "PREFER_DAY_OF_MONTH": "first",
    "RETURN_AS_TIMEZONE_AWARE": False,
}


def _normalize_date_fragment(fragment: str) -> str:
    fragment = fragment.strip()
    fragment = _LEADING_CONNECTORS_RE.sub("", fragment)
    fragment = _WEEKDAY_QUALIFIER_RE.sub("", fragment)
    fragment = _WITHIN_RE.sub("in ", fragment)
    return fragment.strip()


def _last_day_of_month(value: date) -> date:
    if value.month == 12:
        return value.replace(day=31)
    return value.replace(month=value.month + 1, day=1) - timedelta(days=1)


def _first_day_of_next_month(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return value.replace(month=value.month + 1, day=1)


def date_candidates(text: str) -> list[str]:
    """Every date-like fragment in ``text``, in priority order.

    Absolute forms are listed before relative ones: ``"22 August 2026"`` is a
    stronger signal than ``"Friday"`` when both appear.
    """

    masked = _mask_for_dates(text)
    found: list[str] = []
    seen_spans: set[tuple[int, int]] = set()

    for regex in _DATE_CANDIDATE_REGEXES:
        for match in regex.finditer(masked):
            span = (match.start(), match.end())
            if any(start <= span[0] < end for start, end in seen_spans):
                continue
            seen_spans.add(span)
            found.append(match.group(0))

    return found


def _resolve_fragment(fragment: str, reference_dt: datetime) -> date | None:
    normalized = _normalize_date_fragment(fragment)
    if not normalized:
        return None

    reference_date = reference_dt.date()

    # dateparser has no notion of "month end"; these are common enough in
    # collections correspondence to be worth handling explicitly.
    if _NEXT_MONTH_END_RE.search(normalized):
        return _last_day_of_month(_first_day_of_next_month(reference_date))
    if _MONTH_END_RE.search(normalized):
        return _last_day_of_month(reference_date)

    settings = dict(_DATEPARSER_SETTINGS)
    settings["RELATIVE_BASE"] = reference_dt

    parsed = dateparser.parse(normalized, settings=settings)  # type: ignore[arg-type]
    return parsed.date() if parsed else None


def extract_date(text: str, reference_dt: datetime) -> date | None:
    """Resolve the date a reply refers to, relative to ``reference_dt``.

    Candidate fragments are located with explicit patterns first and only then
    handed to ``dateparser`` -- it resolves a short fragment far more reliably
    than a whole free-form sentence.
    """

    if not text:
        return None

    for fragment in date_candidates(text):
        resolved = _resolve_fragment(fragment, reference_dt)
        if resolved is not None:
            return resolved

    return None


# ---------------------------------------------------------------------------
# Dispute reasons
# ---------------------------------------------------------------------------

_DISPUTE_REASONS: tuple[tuple[str, str], ...] = (
    (r"\bshort\s*(?:supply|shipped|delivered)\b|\bquantit(?:y|ies)\b|\bshort\s+aaya\b", "quantity_mismatch"),
    (r"\bgst\b|\btax\b|\bvat\b|\btds\b", "tax_discrepancy"),
    (
        r"\brate\b|\bprice\b|\bpricing\b|\boverchar\w+\b|\bduplicate\b"
        r"|\bcharg\w*\b(?:\s+\w+){0,3}\s+twice\b|\bbilled\s+twice\b",
        "pricing_dispute",
    ),
    (r"\bdamag\w+\b|\bdefect\w+\b|\bqualit\w+\b|\brejected\b", "quality_issue"),
    (r"\bnot\s+(?:received|delivered)\b|\bdelivery\b|\bdelay\w*\s+delivery\b", "delivery_issue"),
    (r"\bpo\b|\bpurchase\s+order\b|\bcontract\b|\bagreement\b", "contract_mismatch"),
    (r"\bgoods\s+receipt\b|\bgrn\b", "grn_mismatch"),
)

_DISPUTE_REGEXES = tuple(
    (re.compile(pattern, re.IGNORECASE), reason) for pattern, reason in _DISPUTE_REASONS
)


def extract_dispute_reason(text: str) -> str | None:
    """Classify *why* a reply disputes an invoice, when a known reason is stated."""

    if not text:
        return None

    for regex, reason in _DISPUTE_REGEXES:
        if regex.search(text):
            return reason
    return None


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------


def extract_entities(
    text: str,
    reference_dt: datetime,
    *,
    include_dispute_reason: bool = True,
) -> ExtractedEntities:
    """Run every deterministic extractor and return a populated schema object."""

    return ExtractedEntities(
        promised_amount=extract_amount(text),
        promised_date=extract_date(text, reference_dt),
        currency=normalize_currency(text),
        dispute_reason=extract_dispute_reason(text) if include_dispute_reason else None,
    )


def merge_entities(
    primary: ExtractedEntities,
    fallback: ExtractedEntities,
    *,
    fields: Iterable[str] = ("promised_amount", "promised_date", "dispute_reason"),
) -> ExtractedEntities:
    """Fill gaps in ``primary`` from ``fallback`` without overwriting anything set."""

    merged = primary.model_copy(deep=True)
    for field in fields:
        if getattr(merged, field, None) is None:
            setattr(merged, field, getattr(fallback, field, None))
    return merged
