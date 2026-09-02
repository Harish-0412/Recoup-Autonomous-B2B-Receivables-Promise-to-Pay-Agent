"""ML-facing views over the synthetic generator.

This package holds *adapters*, not a second generator. All records originate in
:mod:`src.data.synthetic_generator`; the code here only reshapes them into
leakage-safe frames for training and evaluation.
"""

from src.ml.data.export import (
    CUSTOMER_EXPORT_COLUMNS,
    INVOICE_EXPORT_COLUMNS,
    LABEL_COLUMNS,
    TRAINING_FRAME_COLUMNS,
    export_batch,
    to_customers_frame,
    to_invoices_frame,
    to_labels_frame,
    to_training_frame,
)

__all__ = [
    "CUSTOMER_EXPORT_COLUMNS",
    "INVOICE_EXPORT_COLUMNS",
    "LABEL_COLUMNS",
    "TRAINING_FRAME_COLUMNS",
    "export_batch",
    "to_customers_frame",
    "to_invoices_frame",
    "to_labels_frame",
    "to_training_frame",
]
