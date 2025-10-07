from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Deque, Dict, Optional
from collections import deque
from pathlib import Path

from .transport import Transport
from .portfolio import Portfolio
from .recorder import RunRecorder


@dataclass
class PendingOrder:
    ts_wall_in: float
    strategy_id: str
    side: str  # BUY/SELL
    qty: float
    tag: Optional[str]


class Broker:
    def __init__(self, config: Dict, transport: Transport, run_id: str, export_dir: str | Path | None = None):
        self.cfg = config
        self.t = transport
        self.run_id = run_id
        ep = config["transport"]["endpoints"]
        self.addr_orders = ep["orders_push"]
        self.addr_ticks = ep["ticks_pub"]
        self.addr_fills = ep["fills_pub"]

        # Execution params
        ex = config["execution"]
        self.latency_ms = int(ex.get("latency_ms", 50))
        self.slippage_bps = float(ex.get("slippage_bps", 1.0))
        self.commission_bps = float(ex.get("commission_bps", 0.5))
        self.commission_fixed = float(ex.get("commission_fixed", 0.0))

        initial_cash = float(config.get("portfolio", {}).get("initial_cash", 100000.0))
        self.portfolio = Portfolio(initial_cash=initial_cash)

        self.last_price: Optional[float] = None
        self.last_ts_sim: float = 0.0
        self.pending: Deque[PendingOrder] = deque()
        self.recorder: Optional[RunRecorder] = (
            RunRecorder(export_dir, enable_orders=True, enable_fills=True) if export_dir is not None else None
        )

    def start(self) -> None:
        # Bind/Connect sockets
        pull = self.t.bind_pull(self.addr_orders)
        # Subscribe to all tick topics to avoid topic mismatches; avoid ZMQ conflation here
        sub = self.t.connect_sub(self.addr_ticks, topic="", conflate=False)
        pub = self.t.bind_pub(self.addr_fills, kind="fills")

        poller = __import__("zmq").Poller()
        poller.register(pull, __import__("zmq").POLLIN)
        poller.register(sub, __import__("zmq").POLLIN)
        print(
            f"[broker] listening orders@{self.addr_orders} | ticks@{self.addr_ticks} | fills@{self.addr_fills}",
            flush=True,
        )

        try:
            while True:
                socks = dict(poller.poll(timeout=50))
                # Ticks update last price and simulated time
                if sub in socks and socks[sub] == __import__("zmq").POLLIN:
                    _, payload = self.t.recv_json(sub)
                    self.last_price = float(payload.get("price"))
                    self.last_ts_sim = float(payload.get("ts_sim", self.last_ts_sim))
                    ts_wall = float(payload.get("ts_wall", time.time()))
                    self.portfolio.update_market_price(self.last_price, ts_sim=self.last_ts_sim, ts_wall=ts_wall)
                    # Debug tick reception
                    try:
                        print(f"[broker] tick seq={int(payload.get('seq', -1))} price={self.last_price:.5f}")
                    except Exception:
                        pass
                    snap_dict = self.portfolio.snapshot(ts_sim=self.last_ts_sim, ts_wall=ts_wall, run_id=self.run_id).model_dump()
                    Transport.send_json(pub, "portfolio", {"type": "portfolio", "source": "mark", **snap_dict})

                # Orders
                if pull in socks and socks[pull] == __import__("zmq").POLLIN:
                    order = self.t.recv_json_pull(pull)
                    print(f"[order] recv side={order.get('side')} qty={order.get('qty')} from={order.get('strategy_id')}", flush=True)
                    po = PendingOrder(
                        ts_wall_in=time.time(),
                        strategy_id=order.get("strategy_id", "unknown"),
                        side=order.get("side", "BUY"),
                        qty=float(order.get("qty", 0.0)),
                        tag=order.get("tag"),
                    )
                    self.pending.append(po)
                    if self.recorder:
                        self.recorder.log_order(
                            {
                                "ts_wall_in": po.ts_wall_in,
                                "strategy_id": po.strategy_id,
                                "side": po.side,
                                "qty": po.qty,
                                "tag": po.tag,
                                "run_id": self.run_id,
                            }
                        )

                # Attempt fills (wall-clock latency for MVP)
                now = time.time()
                while self.pending and (now - self.pending[0].ts_wall_in) * 1000.0 >= self.latency_ms:
                    po = self.pending.popleft()
                    if self.last_price is None:
                        # No price yet; defer
                        self.pending.appendleft(po)
                        print("[broker] defer fill: no price yet", flush=True)
                        break
                    price = float(self.last_price)
                    slip = self.slippage_bps / 10000.0
                    fill_price = price * (1.0 + slip) if po.side.upper() == "BUY" else price * (1.0 - slip)
                    notional = fill_price * po.qty
                    commission = self.commission_fixed + self.commission_bps / 10000.0 * notional
                    realized_delta, snapshot = self.portfolio.apply_fill(
                        po.side,
                        po.qty,
                        fill_price,
                        commission,
                        self.last_ts_sim,
                        now,
                        self.run_id,
                    )
                    snap_dict = snapshot.model_dump()

                    # Publish fill
                    payload = {
                        "ts_sim": self.last_ts_sim,
                        "ts_wall": now,
                        "strategy_id": po.strategy_id,
                        "side": po.side,
                        "qty": po.qty,
                        "fill_price": fill_price,
                        "slippage_bps": self.slippage_bps,
                        "commission": commission,
                        "latency_ms": self.latency_ms,
                        "order_tag": po.tag,
                        "run_id": self.run_id,
                        "pos_after": snap_dict["pos"],
                        "cash_after": snap_dict["cash"],
                        "equity_after": snap_dict["equity"],
                        "realized_pl": snap_dict["realized"],
                        "unrealized_pl": snap_dict["unrealized"],
                        "portfolio_ts_sim": snap_dict["ts_sim"],
                        "portfolio_ts_wall": snap_dict["ts_wall"],
                    }
                    Transport.send_json(pub, po.strategy_id, payload)
                    if self.recorder:
                        self.recorder.log_fill(payload)
                    print(
                        f"[fill] strat={po.strategy_id} side={po.side} qty={po.qty} price={fill_price:.5f} pos={self.portfolio.pos:.2f} cash={self.portfolio.cash:.2f} "
                        f"realized={self.portfolio.realized:.2f} delta={realized_delta:.2f}",
                        flush=True,
                    )
                    # Broadcast snapshot on dedicated topic for UI/consumers
                    snap_payload = {"type": "portfolio", "source": "fill", **snap_dict}
                    Transport.send_json(pub, "portfolio", snap_payload)
        except KeyboardInterrupt:
            pass
        finally:
            if self.recorder:
                self.recorder.close()


def run(config: Dict, transport: Transport, run_id: str, export_dir: str | Path | None = None) -> None:
    Broker(config, transport, run_id, export_dir=export_dir).start()
