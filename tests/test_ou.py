from __future__ import annotations

import numpy as np

from rt_sim.simulator import _ou_exact_step


def test_ou_step_runs():
    # smoke test to ensure step returns a float and is finite
    x = 0.0
    for _ in range(10):
        x = _ou_exact_step(x, kappa=0.8, theta=0.0, sigma=0.2, dt=0.1)
        assert np.isfinite(x)

