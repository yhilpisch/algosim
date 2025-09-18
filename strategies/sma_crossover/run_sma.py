from __future__ import annotations

import argparse
import signal
import sys
import time
from collections import deque
from typing import Deque, Optional

import zmq

try:
    from rt_sim.utils import load_config
    from rt_sim.transport import Transport
except ModuleNotFoundError:
    # Allow running directly from repo root
    import pathlib

    sys.path.append(str(pathlib.Path(__file__).resolve().parents[2]))
    from rt_sim.utils import load_config
    from rt_sim.transport import Transport


def run(
    strategy_id: str,
    config_path: str,
    window: int,
    qty: float,
    conflate: bool = False,
    topic: str = "",
    threshold_bps: float = 10.0,
    min_interval_s: float = 5.0,
    print_each: bool = False,
    report_sec: float = 1.0,
) -> None:
    cfg = load_config(config_path)
    ep = cfg["transport"]["endpoints"]
    ticks_addr = ep["ticks_pub"]
    fills_addr = ep["fills_pub"]
    orders_addr = ep["orders_push"]

    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )

    # Sockets
    # Subscribe to all topics by default to avoid topic mismatch issues
    sub_ticks = t.connect_sub(ticks_addr, topic=topic, conflate=conflate)
    sub_fills = t.connect_sub(fills_addr, topic=strategy_id, conflate=False)
    push_orders = t.connect_push(orders_addr)
    push_orders.setsockopt(zmq.LINGER, 500)

    print(
        f"[sma] strategy_id={strategy_id} window={window} qty={qty} | ticks={ticks_addr} topic='{topic or '*'}' conflate={conflate} | fills={fills_addr} | orders={orders_addr}",
        flush=True,
    )

    # SMA state
    buf: Deque[float] = deque(maxlen=window)
    ssum: float = 0.0
    last_side: Optional[str] = None  # 'LONG' or 'SHORT'
    pos: float = 0.0
    last_trade_ts: float = 0.0

    # Allow brief time for SUB subscriptions to propagate
    time.sleep(0.2)
    poller = zmq.Poller()
    poller.register(sub_ticks, zmq.POLLIN)
    poller.register(sub_fills, zmq.POLLIN)

    running = True
    tick_count_total = 0
    tick_count_window = 0
    t_last = time.time()

    def _sigint(_sig, _frm):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _sigint)

    try:
        # Initial wait for first tick (up to ~3s)
        t0 = time.time()
        while True:
            socks = dict(poller.poll(timeout=200))
            if sub_ticks in socks and socks[sub_ticks] == zmq.POLLIN:
                _, first_tick = t.recv_json(sub_ticks)
                px0 = float(first_tick.get("price"))
                buf.append(px0); ssum += px0
                tick_count_total += 1; tick_count_window += 1
                print(f"[sma] first tick px={px0:.5f}", flush=True)
                break
            if time.time() - t0 > 3.0:
                print("[sma] waiting for ticks... (no data yet)", flush=True)
                t0 = time.time()
                # continue waiting
        # Main loop
        while running:
            socks = dict(poller.poll(timeout=200))
            # Handle fills to track position
            if sub_fills in socks and socks[sub_fills] == zmq.POLLIN:
                _, fill = t.recv_json(sub_fills)
                side = str(fill.get("side", "")).upper()
                fqty = float(fill.get("qty", 0.0))
                pos += fqty if side == "BUY" else -fqty
                print(
                    f"[sma] fill side={side} qty={fqty} px={float(fill.get('fill_price', 0.0)):.5f} pos={pos}",
                    flush=True,
                )

            # Handle ticks
            if sub_ticks in socks and socks[sub_ticks] == zmq.POLLIN:
                _, tick = t.recv_json(sub_ticks)
                px = float(tick.get("price"))
                tick_count_total += 1
                tick_count_window += 1
                # Update SMA
                if len(buf) == window:
                    ssum -= buf[0]
                buf.append(px)
                ssum += px
                sma = ssum / len(buf)

                if print_each:
                    if len(buf) < window:
                        print(
                            f"[sma] tick #{tick_count_total} px={px:.5f} (warm-up {len(buf)}/{window})",
                            flush=True,
                        )
                    else:
                        print(
                            f"[sma] tick #{tick_count_total} px={px:.5f} sma={sma:.5f}",
                            flush=True,
                        )

                if len(buf) >= window:
                    # Crossover with hysteresis and min interval
                    diff_bps = (px - sma) / max(1e-12, sma) * 10000.0
                    if abs(diff_bps) < threshold_bps:
                        # Inside no-trade band; do nothing
                        pass
                    else:
                        want_side = "LONG" if diff_bps > 0 else "SHORT"
                        if last_side is None:
                            last_side = want_side
                        elif want_side != last_side and (time.time() - last_trade_ts) >= min_interval_s:
                            if want_side == "LONG" and pos <= 0:
                                order_qty = abs(pos) + qty
                                payload = {"strategy_id": strategy_id, "side": "BUY", "qty": float(order_qty), "tag": f"sma_up_{threshold_bps:.1f}bps"}
                                Transport.send_json_push(push_orders, payload)
                                print(f"[sma] BUY {order_qty} (px={px:.5f}, sma={sma:.5f}, diff={diff_bps:.2f}bps)", flush=True)
                                last_trade_ts = time.time()
                            elif want_side == "SHORT" and pos >= 0 and qty > 0:
                                order_qty = abs(pos) + qty
                                payload = {"strategy_id": strategy_id, "side": "SELL", "qty": float(order_qty), "tag": f"sma_dn_{threshold_bps:.1f}bps"}
                                Transport.send_json_push(push_orders, payload)
                                print(f"[sma] SELL {order_qty} (px={px:.5f}, sma={sma:.5f}, diff={diff_bps:.2f}bps)", flush=True)
                                last_trade_ts = time.time()
                            last_side = want_side
            # periodic tick count report
            now = time.time()
            if not print_each and (now - t_last) >= report_sec:
                rate = tick_count_window / max(1e-6, (now - t_last))
                print(f"[sma] ticks last {report_sec:.0f}s: {tick_count_window} (rate={rate:.1f}/s) total={tick_count_total}", flush=True)
                tick_count_window = 0
                t_last = now
    finally:
        try:
            sub_ticks.close(0)
            sub_fills.close(0)
            push_orders.close(0)
        except Exception:
            pass


def main() -> None:
    p = argparse.ArgumentParser(description="Run a simple SMA crossover strategy over ZMQ")
    p.add_argument("--config", default="configs/default.yaml")
    p.add_argument("--strategy-id", default="sma1")
    p.add_argument("--window", type=int, default=50)
    p.add_argument("--qty", type=float, default=100.0)
    p.add_argument("--no-conflate", action="store_true")
    p.add_argument("--topic", default="", help="Tick topic to subscribe to (empty = all)")
    p.add_argument("--print-each", action="store_true", help="Print a line for every tick")
    p.add_argument("--report-sec", type=float, default=1.0, help="When not printing each tick, report tick counts every N seconds")
    p.add_argument("--threshold-bps", type=float, default=10.0, help="No-trade band around SMA in basis points (default 10 bps)")
    p.add_argument("--min-interval-s", type=float, default=5.0, help="Minimum seconds between trades (default 5s)")
    args = p.parse_args()

    run(
        strategy_id=args.strategy_id,
        config_path=args.config,
        window=args.window,
        qty=args.qty,
        conflate=not args.no_conflate,
        topic=args.topic,
        print_each=args.print_each,
        report_sec=args.report_sec,
        threshold_bps=args.threshold_bps,
        min_interval_s=args.min_interval_s,
    )


if __name__ == "__main__":
    main()
