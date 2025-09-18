from __future__ import annotations

import math
import time
from typing import Dict

import numpy as np

from .models import Tick
from .transport import Transport
from .utils import new_run_id


def _next_dt_seconds(cfg: Dict) -> float:
    sched = cfg["schedule"]
    speed = max(1e-6, float(sched.get("speed", 1.0)))
    mode = sched.get("mode", "poisson")
    if mode == "fixed":
        return float(sched.get("dt_fixed_ms", 200)) / 1000.0 / speed
    # Poisson arrivals
    mean_ms = float(sched.get("dt_mean_ms", 150)) / speed
    # exponential in seconds
    return np.random.exponential(mean_ms / 1000.0)


def _ou_exact_step(x_t: float, kappa: float, theta: float, sigma: float, dt: float, mu: float = 0.0) -> float:
    if kappa <= 0:
        # fall back to pure diffusion (approx)
        return x_t + sigma * math.sqrt(max(dt, 0.0)) * np.random.normal()
    exp_term = math.exp(-kappa * dt)
    mean = theta + (x_t - theta) * exp_term
    var = (sigma * sigma) * (1 - math.exp(-2 * kappa * dt)) / (2 * kappa)
    std = math.sqrt(max(var, 0.0))
    return mean + std * np.random.normal() + mu * dt


def _x_to_price(x: float, P0: float) -> float:
    return float(P0 * math.exp(x))


def run(config: Dict, transport: Transport, run_id: str | None = None) -> None:
    """Run the market simulator publishing ticks over ZMQ PUB.

    Config expects keys under `model`, `schedule`, and `transport.endpoints.ticks_pub`.
    """
    run_id = run_id or new_run_id()
    m = config["model"]
    kappa, theta, sigma = float(m["kappa"]), float(m["theta"]), float(m["sigma"])
    mu = float(m.get("mu", 0.0))
    P0, x = float(m["P0"]), float(m.get("x0", 0.0))

    ep = config["transport"]["endpoints"]
    ticks_pub_addr = ep["ticks_pub"]
    asset_id = "X"
    print(
        f"[sim] starting OU simulator | ticks_pub={ticks_pub_addr} | asset_id={asset_id} | "
        f"kappa={kappa} theta={theta} sigma={sigma} P0={P0}",
        flush=True,
    )
    pub = transport.bind_pub(ticks_pub_addr, kind="ticks")

    seq = 0
    t_sim = 0.0
    t_start_wall = time.time()
    # asset_id already set above

    duration_s = float(config.get("run", {}).get("duration_s", 0))

    try:
        while True:
            dt = _next_dt_seconds(config)
            time.sleep(dt)
            t_sim += dt
            x = _ou_exact_step(x, kappa, theta, sigma, dt, mu)
            price = _x_to_price(x, P0)
            seq += 1
            tick = Tick(
                ts_sim=t_sim,
                ts_wall=time.time(),
                seq=seq,
                price=price,
                model_state={"x": x, "dt": dt, "kappa": kappa, "theta": theta, "sigma": sigma},
                asset_id=asset_id,
                run_id=run_id,
            )
            Transport.send_json(pub, asset_id, tick.model_dump())
            # Print each tick to stdout for visibility
            print(
                f"[tick] run_id={run_id} seq={seq} ts_sim={t_sim:.3f}s price={price:.5f} dt_ms={dt*1000:.0f}",
                flush=True,
            )

            # optional stop
            if duration_s and (t_sim >= duration_s or (time.time() - t_start_wall) >= duration_s * 2):
                break
    except KeyboardInterrupt:
        pass
