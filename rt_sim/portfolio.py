from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

from .models import PositionSnapshot


@dataclass
class Portfolio:
    """Track cash, position, and P&L for a single-asset account."""

    initial_cash: float
    cash: float = field(init=False)
    pos: float = field(default=0.0, init=False)
    avg_price: float = field(default=0.0, init=False)
    realized: float = field(default=0.0, init=False)
    last_price: Optional[float] = field(default=None, init=False)
    last_ts_sim: float = field(default=0.0, init=False)
    last_ts_wall: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self.cash = float(self.initial_cash)

    def update_market_price(self, price: float, ts_sim: Optional[float] = None, ts_wall: Optional[float] = None) -> None:
        """Record the latest mark price (from ticks)."""
        self.last_price = float(price)
        if ts_sim is not None:
            self.last_ts_sim = float(ts_sim)
        if ts_wall is not None:
            self.last_ts_wall = float(ts_wall)

    def apply_fill(
        self,
        side: str,
        qty: float,
        price: float,
        commission: float,
        ts_sim: float,
        ts_wall: float,
        run_id: str,
    ) -> Tuple[float, PositionSnapshot]:
        """Apply a fill, update internal state, and return (realized_delta, snapshot)."""
        side_u = side.upper()
        qty_f = float(qty)
        price_f = float(price)
        commission_f = float(commission)

        realized_delta = 0.0
        remaining = qty_f

        if side_u == "BUY":
            self.cash -= price_f * qty_f + commission_f
            if self.pos < 0.0 and remaining > 0.0:
                cover = min(remaining, abs(self.pos))
                realized_delta += (self.avg_price - price_f) * cover
                self.pos += cover
                remaining -= cover
                if abs(self.pos) < 1e-12:
                    self.pos = 0.0
                    self.avg_price = 0.0
            if remaining > 1e-12:
                if self.pos <= 0.0:
                    # new or flipped to long
                    self.pos = remaining
                    self.avg_price = price_f
                else:
                    new_pos = self.pos + remaining
                    self.avg_price = (self.avg_price * self.pos + price_f * remaining) / new_pos
                    self.pos = new_pos

        elif side_u == "SELL":
            self.cash += price_f * qty_f - commission_f
            if self.pos > 0.0 and remaining > 0.0:
                close = min(remaining, self.pos)
                realized_delta += (price_f - self.avg_price) * close
                self.pos -= close
                remaining -= close
                if self.pos < 1e-12:
                    self.pos = 0.0
                    self.avg_price = 0.0
            if remaining > 1e-12:
                if self.pos >= 0.0:
                    # new or flipped to short
                    self.pos = -remaining
                    self.avg_price = price_f
                else:
                    new_pos = self.pos - remaining
                    # pos is negative, keep avg price as positive entry
                    self.avg_price = (self.avg_price * abs(self.pos) + price_f * remaining) / abs(new_pos)
                    self.pos = new_pos
        else:
            raise ValueError(f"Unsupported side '{side}' in fill")

        self.realized += realized_delta
        self.last_price = price_f
        self.last_ts_sim = float(ts_sim)
        self.last_ts_wall = float(ts_wall)

        snapshot = self.snapshot(ts_sim=ts_sim, ts_wall=ts_wall, run_id=run_id)
        return realized_delta, snapshot

    def snapshot(self, ts_sim: Optional[float] = None, ts_wall: Optional[float] = None, run_id: str = "") -> PositionSnapshot:
        """Build a PositionSnapshot using current state."""
        last_px = self.last_price if self.last_price is not None else 0.0
        ts_sim_val = float(ts_sim if ts_sim is not None else self.last_ts_sim)
        ts_wall_val = float(ts_wall if ts_wall is not None else self.last_ts_wall)
        unrealized = 0.0
        if self.last_price is not None and self.pos != 0.0:
            unrealized = (last_px - self.avg_price) * self.pos
        equity = self.cash + self.pos * last_px
        return PositionSnapshot(
            ts_sim=ts_sim_val,
            ts_wall=ts_wall_val,
            pos=self.pos,
            cash=self.cash,
            last_price=last_px,
            unrealized=unrealized,
            realized=self.realized,
            equity=equity,
            run_id=run_id,
        )
