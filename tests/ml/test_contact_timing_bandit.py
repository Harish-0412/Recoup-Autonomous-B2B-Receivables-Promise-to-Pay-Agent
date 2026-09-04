"""Unit tests for the segmented Thompson Sampling bandit."""

import numpy as np
import pytest

from src.ml.contact_timing.bandit import TimingBandit
from src.ml.contact_timing.segments import ARMS


def test_cold_start_backs_off_to_global():
    bandit = TimingBandit()
    suggestion = bandit.suggest("reliable_prompt_clean", rng=np.random.default_rng(0))
    assert suggestion.arm in ARMS
    assert suggestion.backed_off_to_global is True
    assert suggestion.observations == 0
    assert suggestion.expected_response_rate == pytest.approx(0.5)


def test_update_moves_posterior_toward_observations():
    bandit = TimingBandit()
    for _ in range(8):
        bandit.update("mixed_slow_clean", "tue_midday", reward=1)
    for _ in range(2):
        bandit.update("mixed_slow_clean", "tue_midday", reward=0)

    assert bandit.expected_rate("mixed_slow_clean", "tue_midday") == pytest.approx(10 / 14)
    assert bandit.observations("mixed_slow_clean", "tue_midday") == 10
    assert bandit.backed_off("mixed_slow_clean", "tue_midday") is False


def test_global_posterior_learns_from_every_segment():
    bandit = TimingBandit()
    bandit.update("strained_verylate_flagged", "fri_late", reward=1)
    # A never-seen segment still benefits via the population prior.
    assert bandit.expected_rate("reliable_prompt_clean", "fri_late") > 0.5


def test_suggest_prefers_the_learned_best_arm():
    bandit = TimingBandit()
    bandit.fit_rows([("mixed_slow_clean", "wed_midday", 1)] * 30)
    bandit.fit_rows([("mixed_slow_clean", "mon_morning", 0)] * 30)

    rng = np.random.default_rng(7)
    picks = [bandit.suggest("mixed_slow_clean", rng=rng).arm for _ in range(50)]
    assert picks.count("wed_midday") > picks.count("mon_morning")


def test_exclude_removes_arms():
    bandit = TimingBandit()
    suggestion = bandit.suggest(
        "reliable_prompt_clean",
        rng=np.random.default_rng(1),
        exclude=tuple(a for a in ARMS if a != "fri_late"),
    )
    assert suggestion.arm == "fri_late"


def test_invalid_arm_and_reward_rejected():
    bandit = TimingBandit()
    with pytest.raises(ValueError):
        bandit.update("reliable_prompt_clean", "sunday_midnight", reward=1)
    with pytest.raises(ValueError):
        bandit.update("reliable_prompt_clean", "tue_midday", reward=2)
    with pytest.raises(ValueError):
        bandit.suggest("reliable_prompt_clean", exclude=tuple(ARMS))


def test_fit_rows_counts_and_validates():
    bandit = TimingBandit()
    bandit.fit_rows([("s", "mon_morning", 1), ("s", "mon_morning", 0)])
    assert bandit.train_rows == 2
    with pytest.raises(ValueError):
        bandit.fit_rows([("s", "nope", 1)])
