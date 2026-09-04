"""Offline evaluation for the contact-timing bandit.

Protocol: split the engagement log *by customer* (a customer's touches never
appear on both sides -- the same grouped-split honesty as the reply
classifier), train on one side, and replay the other. Because the logging
policy was uniform, every arm had propensity 1/15, so restricting the eval to
rows where the learned policy's arm matches the logged arm is an unbiased
estimate of the policy's reward -- no propensity correction required. This is
the standard offline replay estimator, and the report states its matched
sample size alongside the number because a 40-row agreement proves nothing.

Three numbers, in order of importance:

1. **Policy vs logging policy.** Does Thompson Sampling beat sending at random
   times? If not, the bandit is decoration.
2. **Policy vs best fixed arm.** How much of the achievable gain comes from
   personalising by segment rather than picking one good global slot?
3. **Posterior vs simulator truth (MAE).** The simulator knows the latent
   response rates, so we can check the posteriors converge to them. Labelled
   as a simulator check, not a real-world claim.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from src.ml.contact_timing.bandit import TimingBandit
from src.ml.contact_timing.dataset import true_response_rate
from src.ml.contact_timing.segments import ARMS


class ReplayReport(BaseModel):
    """Everything measured about one bandit on one held-out customer set."""

    model_config = ConfigDict(protected_namespaces=())

    train_rows: int
    eval_rows: int
    eval_customers: int
    matched_rows: int
    policy_reward: float
    logging_reward: float
    best_fixed_arm: str
    best_fixed_arm_reward: float
    truth_mae: float

    @property
    def lift_over_logging(self) -> float:
        return self.policy_reward - self.logging_reward


def split_by_customer(
    frame: pd.DataFrame, *, eval_fraction: float = 0.3, seed: int = 42
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Grouped split: no customer on both sides."""

    rng = np.random.default_rng(seed)
    customers = np.array(sorted(frame["customer_id"].unique()))
    rng.shuffle(customers)
    n_eval = max(1, int(len(customers) * eval_fraction))
    eval_ids = set(customers[:n_eval].tolist())
    eval_mask = frame["customer_id"].isin(eval_ids)
    return frame[~eval_mask].copy(), frame[eval_mask].copy()


def train_bandit(train: pd.DataFrame) -> TimingBandit:
    """Fit posteriors from training rows (segment, arm, reward)."""

    bandit = TimingBandit()
    rows = [
        (str(segment), str(arm), int(reward))
        for segment, arm, reward in zip(
            train["segment"], train["arm"], train["reward"], strict=True
        )
    ]
    return bandit.fit_rows(rows)


def replay_evaluate(
    bandit: TimingBandit,
    eval_frame: pd.DataFrame,
    *,
    seed: int = 42,
) -> ReplayReport:
    """Replay held-out touches through the frozen bandit."""

    rng = np.random.default_rng(seed)
    matched_rewards: list[int] = []
    logging_rewards: list[int] = [int(r) for r in eval_frame["reward"]]
    arm_totals: dict[str, list[int]] = {arm: [0, 0] for arm in ARMS}

    for _, row in eval_frame.iterrows():
        arm_totals[str(row["arm"])][0] += int(row["reward"])
        arm_totals[str(row["arm"])][1] += 1
        suggestion = bandit.suggest(str(row["segment"]), rng=rng)
        if suggestion.arm == row["arm"]:
            matched_rewards.append(int(row["reward"]))

    arm_means = {
        arm: (total / count if count else 0.0) for arm, (total, count) in arm_totals.items()
    }
    best_arm = max(arm_means, key=lambda arm: arm_means[arm])

    # Simulator-truth check: posterior means vs latent rates over every
    # (segment, arm) pair observed in eval. Honest label: simulator check.
    errors: list[float] = []
    for (segment, arm), group in eval_frame.groupby(["segment", "arm"]):
        posterior = bandit.expected_rate(str(segment), str(arm))
        archetypes = group["archetype"].unique().tolist() if "archetype" in group else []
        for archetype in archetypes:
            from src.data.synthetic_generator import CustomerArchetype

            errors.append(
                abs(posterior - true_response_rate(CustomerArchetype(archetype), str(arm)))
            )
    truth_mae = float(np.mean(errors)) if errors else 0.0

    return ReplayReport(
        train_rows=int(bandit.train_rows),
        eval_rows=len(eval_frame),
        eval_customers=int(eval_frame["customer_id"].nunique()),
        matched_rows=len(matched_rewards),
        policy_reward=(
            float(np.mean(matched_rewards)) if matched_rewards else 0.0
        ),
        logging_reward=float(np.mean(logging_rewards)) if logging_rewards else 0.0,
        best_fixed_arm=best_arm,
        best_fixed_arm_reward=float(arm_means[best_arm]),
        truth_mae=round(truth_mae, 6),
    )


def format_replay_report(report: ReplayReport) -> str:
    """Aligned human-readable summary, printed by the training script."""

    lines = [
        f"eval rows={report.eval_rows} customers={report.eval_customers} "
        f"matched={report.matched_rows}",
        f"  policy reward:   {report.policy_reward:.3f}",
        f"  logging (random):{report.logging_reward:.3f}",
        f"  best fixed arm:  {report.best_fixed_arm} at {report.best_fixed_arm_reward:.3f}",
        f"  lift over random:{report.lift_over_logging:+.3f}",
        f"  simulator-truth MAE: {report.truth_mae:.4f}",
    ]
    return "\n".join(lines)
