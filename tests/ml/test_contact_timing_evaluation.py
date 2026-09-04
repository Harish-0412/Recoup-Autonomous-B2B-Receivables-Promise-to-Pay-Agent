"""Unit tests for the grouped split and the replay estimator."""

import numpy as np
import pandas as pd

from src.ml.contact_timing.dataset import build_engagement_log
from src.ml.contact_timing.evaluation import (
    replay_evaluate,
    split_by_customer,
    train_bandit,
)


def test_split_is_grouped_by_customer():
    log = build_engagement_log(customer_count=60, touches_per_customer=4, seed=3)
    train, heldout = split_by_customer(log.frame, eval_fraction=0.3, seed=3)
    assert set(train["customer_id"]).isdisjoint(set(heldout["customer_id"]))
    assert len(train) + len(heldout) == len(log.frame)
    assert len(heldout) > 0 and len(train) > 0


def test_replay_report_is_internally_consistent():
    log = build_engagement_log(customer_count=120, touches_per_customer=6, seed=5)
    train, heldout = split_by_customer(log.frame, eval_fraction=0.3, seed=5)
    bandit = train_bandit(train)
    report = replay_evaluate(bandit, heldout, seed=5)

    assert report.train_rows == len(train)
    assert report.eval_rows == len(heldout)
    assert 0.0 <= report.policy_reward <= 1.0
    assert 0.0 <= report.logging_reward <= 1.0
    assert report.matched_rows > 0
    assert report.matched_rows <= report.eval_rows
    assert report.lift_over_logging == report.policy_reward - report.logging_reward
    assert 0.0 <= report.truth_mae <= 1.0


def test_replay_beats_random_on_separable_signal():
    # Strong planted gap: morning always works, late never does.
    rows = []
    for i in range(200):
        rows.append({"customer_id": f"C-{i % 40}", "segment": "reliable_prompt_clean",
                     "arm": "tue_morning", "reward": 1})
        rows.append({"customer_id": f"C-{i % 40}", "segment": "reliable_prompt_clean",
                     "arm": "fri_late", "reward": 0})
    frame = pd.DataFrame(rows)
    train = frame.iloc[:300]
    heldout = frame.iloc[300:].copy()
    # Held-out logged uniformly so replay can agree with the policy.
    rng = np.random.default_rng(0)
    heldout["arm"] = rng.choice(["tue_morning", "fri_late"], size=len(heldout))
    heldout["reward"] = (heldout["arm"] == "tue_morning").astype(int)

    bandit = train_bandit(train)
    report = replay_evaluate(bandit, heldout, seed=0)
    assert report.policy_reward >= report.logging_reward
