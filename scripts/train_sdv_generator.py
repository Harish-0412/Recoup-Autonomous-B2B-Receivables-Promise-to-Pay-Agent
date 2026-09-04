"""Fit SDV GaussianCopulaSynthesizer, generate synthetic data, and score quality.

Usage:
    python scripts/train_sdv_generator.py --sample-size 500 --seed 42
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Enable relative import from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.data.sdv_generator import RecoupSDVSynthesizer, batch_to_dataframe
from src.data.synthetic_generator import generate_batch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train and evaluate SDV synthetic data model.")
    parser.add_argument("--batch-size", type=int, default=600, help="Seed batch size to fit on")
    parser.add_argument(
        "--sample-size", type=int, default=500, help="Number of synthetic rows to sample"
    )
    parser.add_argument("--seed", type=int, default=42, help="Seed for seed batch")
    parser.add_argument(
        "--out", type=Path, default=Path("data/sdv_model.pkl"), help="Path to save SDV model"
    )
    args = parser.parse_args(argv)

    print(f"Generating seed batch of {args.batch_size} invoices (seed={args.seed})...")
    seed_batch = generate_batch(batch_size=args.batch_size, seed=args.seed)
    seed_df = batch_to_dataframe(seed_batch)
    print(f"Seed dataframe shape: {seed_df.shape}")

    print("Fitting SDV GaussianCopulaSynthesizer...")
    synthesizer = RecoupSDVSynthesizer()
    synthesizer.fit(seed_df)

    print(f"Sampling {args.sample_size} synthetic receivable records...")
    synthetic_df = synthesizer.sample(args.sample_size)
    print(f"Sampled shape: {synthetic_df.shape}")

    print("\nEvaluating statistical fidelity with SDMetrics QualityReport...")
    quality = synthesizer.evaluate_quality(seed_df, synthetic_df)
    print(f"  Overall Quality Score:     {quality['quality_score'] * 100:.2f}%")
    print(f"  Column Shapes Fidelity:    {quality['column_shapes_score'] * 100:.2f}%")
    print(f"  Column Pair Trends:        {quality['column_pair_trends_score'] * 100:.2f}%")

    print(f"\nSaving fitted synthesizer to {args.out}...")
    synthesizer.save(args.out)
    print("Synthesizer saved successfully.")

    # Convert to typed GeneratedBatch demonstration
    typed_batch = synthesizer.sample_to_batch(num_rows=args.sample_size)
    print(
        f"Converted into GeneratedBatch with {len(typed_batch.invoices)} invoices and {len(typed_batch.customers)} customers."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
