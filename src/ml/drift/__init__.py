"""Payment-behavior drift detection.

An Isolation Forest trained on real customer payment histories (UCI Default
of Credit Card Clients, 30k customers) learns what *normal* repayment
behavior looks like. In production the same feature function scores each
customer's trailing window: a score below the calibrated threshold means the
customer's behavior no longer resembles normal payers, and a drift flag is
recorded for a human to look at.

Disputes are deliberately *not* a model feature: a dispute already freezes
escalation and routes to human review through the deterministic reply path,
so feeding it to an anomaly score would only double-count it.
"""
