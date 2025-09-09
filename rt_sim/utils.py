from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

import yaml


DEFAULT_CONFIG: Dict[str, Any] = {
    "transport": {
        "endpoints": {
            "ticks_pub": "tcp://127.0.0.1:5555",
            "orders_push": "tcp://127.0.0.1:5556",
            "fills_pub": "tcp://127.0.0.1:5557",
        },
        "hwm": {"ticks_pub": 20000, "orders": 20000, "fills_pub": 20000},
        "conflate": {"ui_ticks_sub": True},
    },
    "model": {
        "type": "vasicek",
        "kappa": 0.8,
        "theta": 0.0,
        "sigma": 0.2,
        "P0": 100.0,
        "x0": 0.0,
    },
    "schedule": {"mode": "poisson", "dt_fixed_ms": 200, "dt_mean_ms": 150, "speed": 1.0},
    "execution": {
        "latency_ms": 50,
        "slippage_bps": 1.0,
        "commission_bps": 0.5,
        "commission_fixed": 0.0,
    },
    "strategy": {
        "path": "strategies/sma_crossover/strategy.py",
        "params": {"fast": 20, "slow": 50, "qty": 1},
    },
    "run": {"seed": 42, "duration_s": 0, "export_dir": "runs/last"},
    "ui": {"throttle_fps": 10},
}


def deep_update(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_update(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: str | os.PathLike | None) -> Dict[str, Any]:
    """Load YAML config and merge with defaults.

    If path is None, return defaults. If the file doesn't exist, raise FileNotFoundError.
    """
    if path is None:
        return DEFAULT_CONFIG.copy()
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {p}")
    cfg = yaml.safe_load(p.read_text()) or {}
    if not isinstance(cfg, dict):
        raise ValueError("Config YAML must be a mapping at top level")
    return deep_update(DEFAULT_CONFIG.copy(), cfg)


def new_run_id() -> str:
    # ISO-like, sortable
    return time.strftime("%Y-%m-%dT%H-%M-%S")


def seed_everything(seed: int) -> None:
    random.seed(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except Exception:
        pass
