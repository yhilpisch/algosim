from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple
from collections import deque

from .transport import Transport


@dataclass
class PendingOrder:
    ts_wall_in: float
    strategy_id: str
    side: str  # BUY/SELL
    qty: float
    tag: Optional[str]


class Broker:
    def __init__(self, config: Dict, transport: Transport, run_id: str):
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

        # State
        self.last_price: Optional[float] = None
        self.last_ts_sim: float = 0.0
        self.pos: float = 0.0
        self.cash: float = 0.0
        self.realized: float = 0.0
        self.pending: Deque[PendingOrder] = deque()

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
                    # Debug tick reception
                    try:
                        print(f"[broker] tick seq={int(payload.get('seq', -1))} price={self.last_price:.5f}")
                    except Exception:
                        pass

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
                    # Update portfolio
                    self.pos += po.qty if po.side.upper() == "BUY" else -po.qty
                    self.cash += -notional - commission if po.side.upper() == "BUY" else notional - commission
                    # Realized P/L tracked implicitly; keep simple for MVP

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
                    }
                    Transport.send_json(pub, po.strategy_id, payload)
                    print(
                        f"[fill] strat={po.strategy_id} side={po.side} qty={po.qty} price={fill_price:.5f} pos={self.pos:.2f} cash={self.cash:.2f}",
                        flush=True,
                    )
        except KeyboardInterrupt:
            pass


def run(config: Dict, transport: Transport, run_id: str) -> None:
    Broker(config, transport, run_id).start()
