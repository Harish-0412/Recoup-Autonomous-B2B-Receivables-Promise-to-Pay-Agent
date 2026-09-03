"""Data generation for Recoup.

``synthetic_generator`` is the single source of truth for simulated customers,
invoices, payment outcomes and reply text. The rules-based demo and the ML
training pipeline both read from it, so the two can never silently drift apart.
"""

from src.data.synthetic_generator import (
    GENERATOR_VERSION,
    Customer,
    CustomerArchetype,
    Invoice,
    ReplySeedExample,
    SyntheticBatch,
    generate_batch,
    generate_customers,
    generate_invoices,
    generate_reply_seed_examples,
    make_rng,
    simulate_outcomes,
)

__all__ = [
    "Customer",
    "CustomerArchetype",
    "GENERATOR_VERSION",
    "Invoice",
    "ReplySeedExample",
    "SyntheticBatch",
    "generate_batch",
    "generate_customers",
    "generate_invoices",
    "generate_reply_seed_examples",
    "make_rng",
    "simulate_outcomes",
]
