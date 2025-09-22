from __future__ import annotations

import math
from typing import Iterable, List, Sequence, Tuple


def compute_drawdown(equity: Sequence[float]) -> Tuple[float, int, int]:
    """Compute max drawdown from an equity curve.

    Returns (max_drawdown, peak_index, trough_index) where drawdown is expressed as a fraction (0.1 = 10%).
    If equity has <2 points, returns (0.0, 0, 0).
    """
    if not equity or len(equity) < 2:
        return 0.0, 0, 0
    peak = equity[0]
    peak_i = 0
    max_dd = 0.0
    max_i = 0
    for i, v in enumerate(equity):
        if v > peak:
            peak = v
            peak_i = i
        dd = 0.0 if peak <= 0 else (peak - v) / peak
        if dd > max_dd:
            max_dd = dd
            max_i = i
    return max_dd, peak_i, max_i


def compute_sharpe_from_equity(equity: Sequence[float], rf_per_period: float = 0.0, annualization_factor: float = 252.0) -> float:
    """Compute a basic Sharpe ratio from an equity curve by using simple per-step returns.

    annualization_factor scales from per-step to annualized (e.g., 252 for daily, or seconds_in_year / sample_period_seconds).
    """
    if not equity or len(equity) < 3:
        return 0.0
    rets: List[float] = []
    for i in range(1, len(equity)):
        if equity[i - 1] != 0:
            rets.append((equity[i] - equity[i - 1]) / equity[i - 1])
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    # subtract risk-free per period
    mean -= rf_per_period
    var = sum((r - (sum(rets) / len(rets))) ** 2 for r in rets) / max(1, (len(rets) - 1))
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(annualization_factor)


def compute_time_weighted_exposure(times: Sequence[float], positions: Sequence[float]) -> float:
    """Compute time-weighted exposure: fraction of time with non-zero position.

    times must be monotonically increasing and align with positions.
    Returns a value in [0,1]. If insufficient data, returns 0.
    """
    if not times or len(times) < 2 or len(times) != len(positions):
        return 0.0
    total = 0.0
    active = 0.0
    for i in range(1, len(times)):
        dt = max(0.0, times[i] - times[i - 1])
        total += dt
        if positions[i - 1] != 0.0:
            active += dt
    if total == 0:
        return 0.0
    return active / total


def compute_trade_stats(fills: Sequence[dict]) -> dict:
    """Compute simple trade statistics from a sequence of fills.

    Uses FIFO lot matching to compute realized P&L segments and hold times.
    Returns dict with keys: trade_count, win_rate, avg_trade_pl, avg_hold_s.
    """
    if not fills:
        return {"trade_count": 0, "win_rate": 0.0, "avg_trade_pl": 0.0, "avg_hold_s": 0.0}

    # Ensure chronological order by timestamp if present
    fills_sorted = sorted(
        fills,
        key=lambda f: (float(f.get("ts_wall", 0.0)), float(f.get("ts_sim", 0.0))),
    )

    # Inventory lots for long and short
    long_lots: List[Tuple[float, float, float, float]] = []  # (qty, price, ts, comm_per_unit)
    short_lots: List[Tuple[float, float, float, float]] = []

    trade_pnls: List[float] = []
    trade_holds: List[float] = []

    for f in fills_sorted:
        side = str(f.get("side", "")).upper()
        qty = float(f.get("qty", 0.0))
        price = float(f.get("fill_price", 0.0))
        ts = float(f.get("ts_wall", 0.0))
        comm_per_unit = float(f.get("commission", 0.0)) / qty if qty else 0.0

        if side == "BUY":
            # First close shorts
            remain = qty
            while remain > 0 and short_lots:
                sqty, sprice, sts, scomm = short_lots[0]
                matched = min(remain, sqty)
                # Short P&L = entry_price - exit_price (minus commissions)
                pnl = (sprice - price) * matched - (scomm + comm_per_unit) * matched
                hold = max(0.0, ts - sts)
                trade_pnls.append(pnl)
                trade_holds.append(hold)
                sqty -= matched
                remain -= matched
                if sqty <= 1e-12:
                    short_lots.pop(0)
                else:
                    short_lots[0] = (sqty, sprice, sts, scomm)
            # Remaining creates/extends long
            if remain > 1e-12:
                long_lots.append((remain, price, ts, comm_per_unit))

        elif side == "SELL":
            # First close longs
            remain = qty
            while remain > 0 and long_lots:
                lqty, lprice, lts, lcomm = long_lots[0]
                matched = min(remain, lqty)
                # Long P&L = exit_price - entry_price (minus commissions)
                pnl = (price - lprice) * matched - (lcomm + comm_per_unit) * matched
                hold = max(0.0, ts - lts)
                trade_pnls.append(pnl)
                trade_holds.append(hold)
                lqty -= matched
                remain -= matched
                if lqty <= 1e-12:
                    long_lots.pop(0)
                else:
                    long_lots[0] = (lqty, lprice, lts, lcomm)
            # Remaining creates/extends short
            if remain > 1e-12:
                short_lots.append((remain, price, ts, comm_per_unit))

    n = len(trade_pnls)
    if n == 0:
        return {"trade_count": 0, "win_rate": 0.0, "avg_trade_pl": 0.0, "avg_hold_s": 0.0}
    wins = sum(1 for p in trade_pnls if p > 0)
    return {
        "trade_count": n,
        "win_rate": wins / n,
        "avg_trade_pl": sum(trade_pnls) / n,
        "avg_hold_s": sum(trade_holds) / n if trade_holds else 0.0,
    }


def compute_time_weighted_dollar_exposure(
    tick_times: Sequence[float],
    tick_prices: Sequence[float],
    pos_series: Sequence[Tuple[float, float]],
    normalizer: float,
) -> float:
    """Compute time-weighted average dollar exposure relative to `normalizer`.

    - Integrates |pos| * price over tick intervals using the position at the start of each interval.
    - Returns avg exposure as a fraction of `normalizer` (e.g., initial cash). Can exceed 1.0 if leveraged.
    """
    n = len(tick_times)
    if n < 2 or n != len(tick_prices) or normalizer <= 0:
        return 0.0
    # Ensure pos_series is sorted by time
    ps = sorted(list(pos_series), key=lambda x: x[0])
    if not ps:
        return 0.0
    pos_idx = 0
    total = 0.0
    dollar = 0.0
    for i in range(1, n):
        t0, t1 = float(tick_times[i - 1]), float(tick_times[i])
        dt = max(0.0, t1 - t0)
        if dt == 0.0:
            continue
        # Advance position index up to t0
        while pos_idx + 1 < len(ps) and ps[pos_idx + 1][0] <= t0:
            pos_idx += 1
        pos_t0 = float(ps[pos_idx][1])
        px_t0 = float(tick_prices[i - 1])
        dollar += abs(pos_t0) * px_t0 * dt
        total += dt
    if total == 0.0:
        return 0.0
    return dollar / (total * normalizer)
