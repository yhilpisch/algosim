from __future__ import annotations

import math

from rt_sim.metrics import compute_drawdown, compute_sharpe_from_equity, compute_time_weighted_exposure
from rt_sim.metrics import compute_time_weighted_dollar_exposure


def test_drawdown_simple():
    eq = [100, 110, 105, 120, 90, 95, 130]
    dd, i_peak, i_trough = compute_drawdown(eq)
    # Max drop from 120 to 90 => 25%
    assert abs(dd - 0.25) < 1e-9
    assert i_peak == 3
    assert i_trough == 4


def test_sharpe_nonzero():
    # Equity steadily increasing
    eq = [100 + i for i in range(1, 200)]
    s = compute_sharpe_from_equity(eq, annualization_factor=252.0)
    assert s > 0


def test_exposure_time_weighted():
    times = [0, 1, 2, 3, 4]
    # pos non-zero for half the intervals (2 out of 4 seconds)
    pos = [0, 1, 1, 0, 0]
    exp = compute_time_weighted_exposure(times, pos)
    assert abs(exp - 0.5) < 1e-9


def test_dollar_exposure_relative():
    times = [0, 1, 2, 3]
    prices = [100, 100, 100, 100]
    pos_series = [(0, 1), (3, 1)]  # long 1 for whole period
    rel = compute_time_weighted_dollar_exposure(times, prices, pos_series, normalizer=100)
    # avg dollar exposure = 100 over 3 seconds; normalized by 100 -> 1.0 (100%)
    assert abs(rel - 1.0) < 1e-9
