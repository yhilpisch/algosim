from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class Tick(BaseModel):
    ts_sim: float = Field(..., description="Simulated timestamp in seconds")
    ts_wall: float = Field(..., description="Wall-clock time (epoch seconds)")
    seq: int
    price: float
    model_state: Dict[str, Any]
    asset_id: str = "X"
    run_id: str


class Order(BaseModel):
    ts_sim: float
    ts_wall: float
    strategy_id: str
    side: str  # "BUY" | "SELL"
    qty: float
    tag: Optional[str] = None
    run_id: str


class Fill(BaseModel):
    ts_sim: float
    ts_wall: float
    strategy_id: str
    side: str
    qty: float
    fill_price: float
    slippage_bps: float
    commission: float
    latency_ms: int
    order_tag: Optional[str] = None
    run_id: str


class PositionSnapshot(BaseModel):
    ts_sim: float
    ts_wall: float
    pos: float
    cash: float
    last_price: float
    unrealized: float
    realized: float
    equity: float
    run_id: str


def now_epoch() -> float:
    return datetime.utcnow().timestamp()

