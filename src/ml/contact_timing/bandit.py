"""Segmented Beta-Bernoulli Thompson Sampling for send-time selection.

One independent Beta posterior per (segment, arm). Thompson sampling draws one
sample per arm and pulls the argmax, which explores early and exploits late
with no epsilon to tune and no schedule to anneal. A global ("*") posterior
per arm backs every segment with too little data, so a new segment starts from
population behaviour rather than a coin flip.

The model object is deliberately plain data -- nested dicts of (alpha, beta)
floats plus metadata -- so the artifact is inspectable with any tool that
reads joblib, and the online update is an addition rather than a retrain.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.ml.contact_timing.segments import ARMS, GLOBAL_SEGMENT, PRIOR_ALPHA, PRIOR_BETA


@dataclass
class BanditSuggestion:
    """What the bandit recommends for one decision."""

    arm: str
    expected_response_rate: float
    observations: int
    segment: str
    backed_off_to_global: bool


@dataclass
class TimingBandit:
    """The fitted posterior plus the metadata needed to serve it."""

    counts: dict[str, dict[str, list[float]]] = field(default_factory=dict)
    prior_alpha: float = PRIOR_ALPHA
    prior_beta: float = PRIOR_BETA
    train_rows: int = 0
    feature_set_version: str = "contact-timing-v1"

    def _posterior(self, segment: str, arm: str) -> tuple[float, float]:
        cell = self.counts.get(segment, {}).get(arm)
        if cell is not None:
            return cell[0], cell[1]
        globe = self.counts.get(GLOBAL_SEGMENT, {}).get(arm)
        if globe is not None:
            return globe[0], globe[1]
        return self.prior_alpha, self.prior_beta

    def backed_off(self, segment: str, arm: str) -> bool:
        """Whether this (segment, arm) has no direct observations yet."""

        return arm not in self.counts.get(segment, {})

    def expected_rate(self, segment: str, arm: str) -> float:
        alpha, beta = self._posterior(segment, arm)
        return alpha / (alpha + beta)

    def observations(self, segment: str, arm: str) -> int:
        alpha, beta = self._posterior(segment, arm)
        return max(0, int(round(alpha + beta - self.prior_alpha - self.prior_beta)))

    def suggest(
        self,
        segment: str,
        *,
        rng: np.random.Generator | None = None,
        exclude: tuple[str, ...] = (),
    ) -> BanditSuggestion:
        """Thompson-sample every eligible arm and return the winner."""

        generator = rng if rng is not None else np.random.default_rng()
        eligible = [arm for arm in ARMS if arm not in exclude]
        if not eligible:
            raise ValueError("all arms excluded; nothing to suggest")

        best_arm = eligible[0]
        best_sample = -1.0
        for arm in eligible:
            alpha, beta = self._posterior(segment, arm)
            sample = float(generator.beta(alpha, beta))
            if sample > best_sample:
                best_sample = sample
                best_arm = arm

        backed_off = self.backed_off(segment, best_arm)
        effective = GLOBAL_SEGMENT if backed_off else segment
        return BanditSuggestion(
            arm=best_arm,
            expected_response_rate=self.expected_rate(effective, best_arm),
            observations=self.observations(effective, best_arm),
            segment=segment,
            backed_off_to_global=backed_off,
        )

    def update(self, segment: str, arm: str, *, reward: int) -> None:
        """Fold one observed outcome into the posterior. The online step."""

        if arm not in ARMS:
            raise ValueError(f"unknown send-time arm: {arm!r}")
        if reward not in (0, 1):
            raise ValueError(f"reward must be 0 or 1, got {reward!r}")
        cell = self.counts.setdefault(segment, {}).setdefault(
            arm, [self.prior_alpha, self.prior_beta]
        )
        # A global observation also teaches the population prior: a segment
        # that has never been seen still benefits from what every reply taught.
        globe = self.counts.setdefault(GLOBAL_SEGMENT, {}).setdefault(
            arm, [self.prior_alpha, self.prior_beta]
        )
        cell[0] += reward
        cell[1] += 1 - reward
        globe[0] += reward
        globe[1] += 1 - reward

    def fit_rows(
        self,
        rows: list[tuple[str, str, int]],
        *,
        update_global: bool = True,
    ) -> "TimingBandit":
        """Batch-train from (segment, arm, reward) rows. Returns self."""

        for segment, arm, reward in rows:
            if arm not in ARMS:
                raise ValueError(f"unknown send-time arm: {arm!r}")
            cell = self.counts.setdefault(segment, {}).setdefault(
                arm, [self.prior_alpha, self.prior_beta]
            )
            cell[0] += reward
            cell[1] += 1 - reward
            if update_global:
                globe = self.counts.setdefault(GLOBAL_SEGMENT, {}).setdefault(
                    arm, [self.prior_alpha, self.prior_beta]
                )
                globe[0] += reward
                globe[1] += 1 - reward
            self.train_rows += 1
        return self
