"""Real-world training data for the drift model.

Source: "Default of Credit Card Clients" (Yeh, 2009), UCI ML Repository
(id 350, CC BY 4.0, DOI 10.24432/C55S3H) -- 30,000 credit-card customers
in Taiwan, April-September 2005, with six months of repayment status
(``PAY_0``..``PAY_6``), bill statements (``BILL_AMT1``..``6``) and payment
amounts (``PAY_AMT1``..``6``), plus a ``default payment next month`` flag.

Why this dataset: it is real repayment behavior with the exact signals a
drift detector needs -- delays that lengthen, coverage that thins, missed
months -- rather than a synthetic generator whose planted signal would only
prove the pipeline can recover its own assumptions. The monthly grain
differs from the backend's trailing-90-day window; the shared
``src.ml.drift.features`` schema is rate/ratio-based precisely so the same
columns mean the same thing under both grains (documented in the model
card as a limitation, not hidden).

``default payment next month`` is a *proxy*, not drift ground truth: nobody
labelled "drift" in this data. It is used only to sanity-check that flagged
customers default more often than unflagged ones -- a detector whose flags
carry no forward risk signal would be decorative.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from src.ml.drift.features import FEATURE_COLUMNS, PeriodSeries, from_period_series

#: Six monthly periods, oldest first. The source names them PAY_0, PAY_2..PAY_6
#: (PAY_1 does not exist upstream); BILL/PAY amounts are suffixed 1..6 in the
#: same chronological order.
PAY_STATUS_COLS = ["PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"]
BILL_COLS = [f"BILL_AMT{i}" for i in range(1, 7)]
PAY_AMT_COLS = [f"PAY_AMT{i}" for i in range(1, 7)]
TARGET_COL = "default payment next month"

SOURCE_FILENAME = "default of credit card clients.xls"
SOURCE_URL = "https://archive.ics.uci.edu/static/public/350/default+of+credit+card+clients.zip"
SOURCE_DOI = "10.24432/C55S3H"
SOURCE_LICENSE = "CC BY 4.0"
SOURCE_ROWS = 30000


def raw_path(data_dir: Path) -> Path:
    """Location of the cached source file (zip), downloading it if absent."""

    import urllib.request
    import zipfile

    data_dir.mkdir(parents=True, exist_ok=True)
    xls = data_dir / SOURCE_FILENAME
    if xls.exists():
        return xls
    archive = data_dir / "default-credit-card-clients.zip"
    if not archive.exists():
        urllib.request.urlretrieve(SOURCE_URL, archive)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(data_dir)
    if not xls.exists():
        raise FileNotFoundError(f"Expected {SOURCE_FILENAME} inside the UCI archive")
    return xls


def load_uci_frame(data_dir: Path) -> pd.DataFrame:
    """Read the source workbook into a validated frame."""

    frame = pd.read_excel(raw_path(data_dir), header=1)
    expected = {"ID", "LIMIT_BAL", *PAY_STATUS_COLS, *BILL_COLS, *PAY_AMT_COLS, TARGET_COL}
    missing = expected - set(frame.columns)
    if missing:
        raise ValueError(f"UCI source is missing columns: {sorted(missing)}")
    if len(frame) == 0:
        raise ValueError("UCI source loaded zero rows")
    return frame


def row_to_series(row: pd.Series) -> PeriodSeries:
    """Map one UCI customer row onto the shared period-series schema.

    ``PAY_*`` codes -2/-1 mean paid duly or no consumption (no delay);
    0 means revolving use (treated as no measured delay); 1..8 count
    months of delay and pass through as months directly.
    """

    delays = tuple(max(float(row[col]), 0.0) for col in PAY_STATUS_COLS)
    paid = tuple(max(float(row[col]), 0.0) for col in PAY_AMT_COLS)
    billed = tuple(max(float(row[col]), 0.0) for col in BILL_COLS)
    return PeriodSeries(
        delays_months=delays,
        paid=paid,
        billed=billed,
        limit=max(float(row["LIMIT_BAL"]), 1e-9),
    )


def build_feature_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """One feature row per customer, in ``FEATURE_COLUMNS`` order."""

    rows = [from_period_series(row_to_series(row)) for _, row in frame.iterrows()]
    return pd.DataFrame(rows, columns=list(FEATURE_COLUMNS)).astype("float64")


def degrade_series(series: PeriodSeries, *, seed: int = 42) -> PeriodSeries:
    """Synthetically degrade recent behavior to simulate payment drift.

    Adds two months of delay to the last two periods and halves their
    payments (floored at zero). Used only to measure whether the detector
    catches a customer sliding downhill -- the recall half of the
    evaluation, since real drift labels do not exist.
    """

    rng = np.random.default_rng(seed)
    delays = list(series.delays_months)
    paid = list(series.paid)
    jitter = rng.uniform(1.8, 2.2, size=min(2, len(delays)))
    for i, extra in zip(range(len(delays) - len(jitter), len(delays)), jitter, strict=False):
        delays[i] = delays[i] + float(extra)
        paid[i] = paid[i] * 0.5
    return PeriodSeries(
        delays_months=tuple(delays),
        paid=tuple(paid),
        billed=series.billed,
        limit=series.limit,
    )
