from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque

import plotly.graph_objs as go
import streamlit as st
import zmq
from queue import Queue, Full, Empty
from pathlib import Path
import os
import json as _json
import signal as _signal
import subprocess
import multiprocessing as mp

# Background listener writes into a Queue object passed at thread start.
# Keep the reference in session_state so reruns use the same queue.
_LAST_THREAD_ERROR: str | None = None
# Global log buffer for strategy host (avoid touching st.session_state from threads)
STRAT_LOGS: deque[str] = deque(maxlen=2000)
STRAT_LOG_LOCK = threading.Lock()
PID_REG_PATH: Path = Path(__file__).resolve().parents[1] / "runs/strategy_hosts.json"


def read_pid_registry() -> list[int]:
    try:
        data = _json.loads(PID_REG_PATH.read_text())
        if isinstance(data, list):
            return [int(x) for x in data]
    except Exception:
        pass
    return []


def write_pid_registry(pids: list[int]) -> None:
    try:
        PID_REG_PATH.parent.mkdir(parents=True, exist_ok=True)
        PID_REG_PATH.write_text(_json.dumps([int(x) for x in pids]))
    except Exception:
        pass


def register_pid(pid: int) -> None:
    pids = read_pid_registry()
    if pid not in pids:
        pids.append(pid)
        write_pid_registry(pids)


def unregister_pid(pid: int) -> None:
    pids = read_pid_registry()
    if pid in pids:
        pids.remove(pid)
        write_pid_registry(pids)

try:
    from rt_sim.transport import Transport
    from rt_sim.utils import load_config, new_run_id
except ModuleNotFoundError:
    # Fallback: add project root to sys.path when running via `streamlit run`
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from rt_sim.transport import Transport
    from rt_sim.utils import load_config, new_run_id

# Top-level process entry helpers for local demo controls
def _proc_broker_entry(cfg: dict, run_id: str) -> None:
    from rt_sim.transport import Transport
    from rt_sim.broker import run as run_broker

    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_broker(cfg, t, run_id)


def _proc_sim_entry(cfg: dict, run_id: str) -> None:
    from rt_sim.transport import Transport
    from rt_sim.simulator import run as run_sim

    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_sim(cfg, t, run_id)


def _proc_strategy_entry(
    cfg: dict,
    run_id: str,
    strategy_path: str,
    strategy_id: str | None,
    params_json: str | None,
    topic: str,
    conflate: bool,
) -> None:
    from rt_sim.transport import Transport
    from rt_sim.strategy_host import run as run_host
    import json as _json

    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    params = None
    if params_json:
        try:
            params = _json.loads(params_json)
        except Exception:
            params = None
    run_host(cfg, t, run_id, strategy_path, strategy_id=strategy_id, params_override=params, topic=topic, conflate=conflate)


st.set_page_config(page_title="algosim — Ticks", layout="wide")


def ensure_state():
    if "ticks" not in st.session_state:
        st.session_state.ticks = deque(maxlen=2000)
    if "fills" not in st.session_state:
        st.session_state.fills = deque(maxlen=500)
    if "pnl" not in st.session_state:
        st.session_state.pnl = deque(maxlen=2000)  # (ts_wall, equity)
    if "pos_series" not in st.session_state:
        st.session_state.pos_series = deque(maxlen=2000)  # (ts_wall, pos)
    if "initial_cash" not in st.session_state:
        cfg0 = st.session_state.get("cfg", load_config(None))
        try:
            st.session_state.initial_cash = float(cfg0.get("portfolio", {}).get("initial_cash", 100000.0))
        except Exception:
            st.session_state.initial_cash = 100000.0
    if "listener_thread" not in st.session_state:
        st.session_state.listener_thread = None
    if "listener_event" not in st.session_state:
        st.session_state.listener_event = threading.Event()
    if "fills_thread" not in st.session_state:
        st.session_state.fills_thread = None
    if "fills_event" not in st.session_state:
        st.session_state.fills_event = threading.Event()
    if "conflate" not in st.session_state:
        st.session_state.conflate = False
    if "queue" not in st.session_state:
        # size 1 when conflating, large otherwise
        st.session_state.queue = Queue(maxsize=(1 if st.session_state.conflate else 10000))
    if "test_recv_stats" not in st.session_state:
        st.session_state.test_recv_stats = None
    if "test_fills_stats" not in st.session_state:
        st.session_state.test_fills_stats = None
    if "fills_queue" not in st.session_state:
        st.session_state.fills_queue = Queue(maxsize=10000)
    if "auto_refresh" not in st.session_state:
        st.session_state.auto_refresh = True
    if "refresh_hz" not in st.session_state:
        st.session_state.refresh_hz = 1
    if "pos" not in st.session_state:
        st.session_state.pos = 0.0
    if "cash" not in st.session_state:
        # Initialize cash from config portfolio.initial_cash if available
        cfg0 = st.session_state.get("cfg", load_config(None))
        try:
            st.session_state.cash = float(cfg0.get("portfolio", {}).get("initial_cash", 100000.0))
        except Exception:
            st.session_state.cash = 100000.0
    if "last_price" not in st.session_state:
        st.session_state.last_price = None
    if "last_portfolio" not in st.session_state:
        st.session_state.last_portfolio = None
    if "last_run_id" not in st.session_state:
        st.session_state.last_run_id = None
    if "proc_broker" not in st.session_state:
        st.session_state.proc_broker = None
    if "proc_sim" not in st.session_state:
        st.session_state.proc_sim = None
    if "proc_strategy" not in st.session_state:
        st.session_state.proc_strategy = None
    if "strategy_path" not in st.session_state:
        st.session_state.strategy_path = "strategies/mean_reversion/strategy.py"
    if "strategy_code" not in st.session_state:
        try:
            st.session_state.strategy_code = Path(st.session_state.strategy_path).read_text()
        except Exception:
            st.session_state.strategy_code = ""
    if "strategy_log_thread" not in st.session_state:
        st.session_state.strategy_log_thread = None
    if "strategy_log_event" not in st.session_state:
        st.session_state.strategy_log_event = threading.Event()
    if "tick_chart" not in st.session_state:
        base_fig = go.Figure(
            data=[
                go.Scattergl(name="Price", mode="lines", x=[], y=[], line=dict(color="#3498db", width=2)),
                go.Scattergl(
                    name="Buys",
                    mode="markers",
                    x=[],
                    y=[],
                    marker=dict(symbol="triangle-up", color="#2ecc71", size=12, line=dict(color="white", width=1)),
                    hoverinfo="text",
                ),
                go.Scattergl(
                    name="Sells",
                    mode="markers",
                    x=[],
                    y=[],
                    marker=dict(symbol="triangle-down", color="#e74c3c", size=12, line=dict(color="white", width=1)),
                    hoverinfo="text",
                ),
            ],
            layout=go.Layout(uirevision="price_stream", height=500, margin=dict(l=10, r=10, t=10, b=10)),
        )
        st.session_state.tick_chart = {"fig": base_fig, "datarevision": 0}
    # PID registry helpers are provided at module scope


def start_listener(cfg):
    event: threading.Event = st.session_state.listener_event
    # If an old listener is running, stop it to recreate with current conflate setting/queue size.
    if event.is_set():
        stop_listener()
        time.sleep(0.05)

    # Recreate queue respecting current conflate setting
    st.session_state.queue = Queue(maxsize=(1 if st.session_state.conflate else 10000))
    q: Queue = st.session_state.queue
    event.set()

    def _loop(ev: threading.Event, conflate: bool, q_out: Queue):
        global _LAST_THREAD_ERROR
        try:
            t = Transport(hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]))
            sub = t.connect_sub(
                cfg["transport"]["endpoints"]["ticks_pub"], topic="X", conflate=conflate
            )
            poller = zmq.Poller()
            poller.register(sub, zmq.POLLIN)
            while ev.is_set():
                socks = dict(poller.poll(timeout=100))
                if sub in socks and socks[sub] == zmq.POLLIN:
                    _, payload = t.recv_json(sub)
                    # Push to module queue; main thread will drain
                    try:
                        q_out.put_nowait((payload.get("ts_wall", time.time()), payload.get("price"), payload.get("seq")))
                    except Full:
                        # Keep only the latest: drop one and insert
                        try:
                            q_out.get_nowait()
                        except Empty:
                            pass
                        try:
                            q_out.put_nowait((payload.get("ts_wall", time.time()), payload.get("price"), payload.get("seq")))
                        except Full:
                            pass
            sub.close(0)
        except Exception as e:
            _LAST_THREAD_ERROR = f"Listener thread error: {e}"

    th = threading.Thread(target=_loop, args=(event, st.session_state.conflate, q), daemon=True)
    th.start()
    st.session_state.listener_thread = th

    # Start fills listener (subscribe to all topics)
    fev: threading.Event = st.session_state.fills_event
    if fev.is_set():
        stop_fills()
        time.sleep(0.05)
    st.session_state.fills_queue = Queue(maxsize=10000)
    fq: Queue = st.session_state.fills_queue
    fev.set()

    def _fills_loop(ev: threading.Event, fq_out: Queue):
        global _LAST_THREAD_ERROR
        try:
            t2 = Transport(hwm_ticks=int(cfg["transport"]["hwm"]["fills_pub"]))
            sub2 = t2.connect_sub(cfg["transport"]["endpoints"]["fills_pub"], topic="", conflate=False)
            poller2 = zmq.Poller(); poller2.register(sub2, zmq.POLLIN)
            while ev.is_set():
                socks2 = dict(poller2.poll(timeout=100))
                if sub2 in socks2 and socks2[sub2] == zmq.POLLIN:
                    topic, payload = t2.recv_json(sub2)
                    try:
                        fq_out.put_nowait((topic, payload))
                    except Full:
                        try:
                            fq_out.get_nowait()
                            fq_out.put_nowait((topic, payload))
                        except Exception:
                            pass
            sub2.close(0)
        except Exception as e:
            _LAST_THREAD_ERROR = f"Fills listener error: {e}"

    fth = threading.Thread(target=_fills_loop, args=(fev, fq), daemon=True)
    fth.start()
    st.session_state.fills_thread = fth


def stop_listener():
    event: threading.Event = st.session_state.listener_event
    event.clear()
    th = st.session_state.listener_thread
    if th and th.is_alive():
        th.join(timeout=0.5)
    st.session_state.listener_thread = None

    stop_fills()


def stop_fills():
    fev: threading.Event = st.session_state.fills_event
    fev.clear()
    fth = st.session_state.fills_thread
    if fth and fth.is_alive():
        fth.join(timeout=0.5)
    st.session_state.fills_thread = None


def main():
    ensure_state()
    st.title("algosim — Real-Time Ticks (MVP)")

    cfg = st.session_state.get("cfg", load_config(None))

    with st.sidebar:
        st.header("Connection")
        cols = st.columns(2)
        if cols[0].button("Start SUB"):
            start_listener(cfg)
        if cols[1].button("Stop SUB"):
            stop_listener()
        st.caption("Use the Admin tab for diagnostics and advanced controls.")

    tab_ticks, tab_fills, tab_pnl, tab_strategy, tab_admin = st.tabs(
        ["Ticks", "Fills / Orders", "P&L", "Strategy", "Admin"]
    )

    with tab_ticks:
        # Render mode selector on top, default to Chart
        render_mode = st.radio("Render mode", ["Chart", "Text"], index=0, horizontal=True)
        st.caption("Subscribe to ticks; choose text or chart rendering below.")
        placeholder = st.empty()

    # Simple autorefresh loop
    chart_refresh_ms = int(1000 / max(1, int(st.session_state.get("refresh_hz", 5))))
    # Drain incoming queue into ticks before drawing
    drained = 0
    try:
        while True:
            ts, price, seq = st.session_state.queue.get_nowait()
            st.session_state.ticks.append((ts, price))
            st.session_state.last_price = price
            # Update arrival times and gap metrics
            now = time.time()
            if "arrival_times" not in st.session_state:
                st.session_state.arrival_times = deque(maxlen=500)
            st.session_state.arrival_times.append(now)
            last_seq = st.session_state.get("last_seq")
            if seq is not None:
                if last_seq is not None and seq != last_seq + 1:
                    st.session_state["gap_count"] = st.session_state.get("gap_count", 0) + 1
                st.session_state["last_seq"] = seq
            drained += 1
    except Empty:
        pass
    if drained:
        import datetime as _dt

        st.session_state.last_recv_ts = _dt.datetime.now().isoformat(timespec="seconds")

    # Drain fills queue
    fdrained = 0
    try:
        while True:
            topic, payload = st.session_state.fills_queue.get_nowait()
            if topic == "portfolio" or payload.get("type") == "portfolio":
                st.session_state.last_portfolio = payload
                st.session_state.pos = float(payload.get("pos", st.session_state.pos))
                st.session_state.cash = float(payload.get("cash", st.session_state.cash))
                last_px = payload.get("last_price", st.session_state.last_price)
                if last_px is not None:
                    st.session_state.last_price = float(last_px)
                tsw = float(payload.get("ts_wall", time.time()))
                eq = float(payload.get("equity", st.session_state.cash + st.session_state.pos * float(st.session_state.last_price or 0.0)))
                st.session_state.pnl.append((tsw, eq))
                st.session_state.pos_series.append((tsw, st.session_state.pos))
                run_id = payload.get("run_id")
                if run_id:
                    st.session_state.last_run_id = run_id
            else:
                tsf = float(payload.get("ts_wall", time.time()))
                st.session_state.fills.append((tsf, payload))
                pos_after = payload.get("pos_after")
                cash_after = payload.get("cash_after")
                equity_after = payload.get("equity_after")
                if pos_after is not None:
                    st.session_state.pos = float(pos_after)
                else:
                    side = str(payload.get("side", "")).upper()
                    qty = float(payload.get("qty", 0.0))
                    if side == "BUY":
                        st.session_state.pos += qty
                    elif side == "SELL":
                        st.session_state.pos -= qty
                if cash_after is not None:
                    st.session_state.cash = float(cash_after)
                else:
                    qty = float(payload.get("qty", 0.0))
                    price = float(payload.get("fill_price", 0.0))
                    commission = float(payload.get("commission", 0.0))
                    side = str(payload.get("side", "")).upper()
                    if side == "BUY":
                        st.session_state.cash -= price * qty + commission
                    elif side == "SELL":
                        st.session_state.cash += price * qty - commission
                last_px = payload.get("fill_price", st.session_state.last_price)
                if last_px is not None:
                    st.session_state.last_price = float(last_px)
                if equity_after is not None:
                    eq_val = float(equity_after)
                else:
                    px = st.session_state.last_price if st.session_state.last_price is not None else 0.0
                    eq_val = st.session_state.cash + st.session_state.pos * float(px)
                st.session_state.pnl.append((tsf, eq_val))
                st.session_state.pos_series.append((tsf, st.session_state.pos))
                fdrained += 1
                run_id = payload.get("run_id")
                if run_id:
                    st.session_state.last_run_id = run_id
    except Empty:
        pass

        with placeholder.container():
            data = list(st.session_state.ticks)
            if not data:
                st.info("No ticks yet. Start the simulator and then Start SUB.")
            else:
                if render_mode == "Text":
                    # Render as plain text lines (ts_wall ISO-ish, price)
                    import datetime as _dt

                    def _fmt(ts: float, px: float) -> str:
                        ts_str = _dt.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
                        return f"{ts_str}  price={px:.5f}"

                    lines = [_fmt(ts, px) for ts, px in data[-500:]]  # show last 500 lines
                    st.text("\n".join(lines))
                    st.caption(f"Tick count: {len(data)} (showing last {min(len(data), 500)})")
                else:
                    # Convert epoch seconds to ISO strings for display on x-axis
                    import datetime as _dt

                    x_raw, y_vals = zip(*data)
                    x = [_dt.datetime.fromtimestamp(ts) for ts in x_raw]
                    chart_state = st.session_state.tick_chart
                    fig = chart_state["fig"]
                    fig.data[0].x = list(x)
                    fig.data[0].y = list(y_vals)

                    buys = [pt for pt in st.session_state.fills if str(pt[1].get("side", "")).upper() == "BUY"]
                    sells = [pt for pt in st.session_state.fills if str(pt[1].get("side", "")).upper() == "SELL"]

                    def _prep_points(points):
                        xs, ys, texts = [], [], []
                        for ts_fill, fill_payload in points:
                            px_fill = fill_payload.get("fill_price")
                            if px_fill is None:
                                continue
                            xs.append(_dt.datetime.fromtimestamp(ts_fill))
                            ys.append(px_fill)
                            qty_fill = float(fill_payload.get("qty", 0.0))
                            pos_after = float(fill_payload.get("pos_after", st.session_state.pos))
                            side_txt = str(fill_payload.get("side", "")).upper()
                            texts.append(
                                f"{side_txt} {qty_fill:g} @ {float(px_fill):.5f}<br>Position: {pos_after:,.2f}"
                            )
                        return xs, ys, texts

                    buy_x, buy_y, buy_text = _prep_points(buys)
                    sell_x, sell_y, sell_text = _prep_points(sells)
                    fig.data[1].x = list(buy_x)
                    fig.data[1].y = list(buy_y)
                    fig.data[1].hovertext = list(buy_text)
                    fig.data[2].x = list(sell_x)
                    fig.data[2].y = list(sell_y)
                    fig.data[2].hovertext = list(sell_text)

                    chart_state["datarevision"] += 1
                    fig.layout.datarevision = chart_state["datarevision"]

                    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": True})
                    st.caption(f"Tick count: {len(data)}")

    with tab_fills:
        # Manual orders on top
        st.subheader("Manual Orders")
        col1, col2, col3 = st.columns([1,1,2])
        qty = col1.number_input("Qty", min_value=0.0, value=1.0, step=1.0, format="%f")
        tag = col2.text_input("Tag", value="manual")
        waiting_for_price = st.session_state.last_price is None
        if waiting_for_price:
            st.info("Waiting for first tick price — start simulator and ensure ticks flow before sending orders.")
        def _send_order(side: str):
            try:
                cfg_loc = st.session_state.get("cfg", load_config(None))
                t3 = Transport(
                    hwm_ticks=int(cfg_loc["transport"]["hwm"]["ticks_pub"]),
                    hwm_orders=int(cfg_loc["transport"]["hwm"]["orders"]),
                    hwm_fills=int(cfg_loc["transport"]["hwm"]["fills_pub"]),
                )
                push = t3.connect_push(cfg_loc["transport"]["endpoints"]["orders_push"])
                # Ensure message flushes before close
                import zmq as _zmq
                push.setsockopt(_zmq.LINGER, 500)
                payload = {"strategy_id": "ui", "side": side, "qty": float(qty), "tag": tag}
                Transport.send_json_push(push, payload)
                # tiny delay helps handshake
                time.sleep(0.01)
                push.close()
                st.success(f"Sent {side} {qty:g}")
            except Exception as e:
                st.error(f"Failed to send order: {e}")
        cbu, cse = st.columns(2)
        if cbu.button("BUY", disabled=waiting_for_price):
            _send_order("BUY")
        if cse.button("SELL", disabled=waiting_for_price):
            _send_order("SELL")

        # Fills list below
        st.subheader("Fills (latest)")
        if st.session_state.fills:
            lines = []
            pos = 0.0
            for _, f in list(st.session_state.fills)[-200:]:
                side = f.get("side"); qty = float(f.get("qty", 0))
                pos += qty if side == "BUY" else -qty
                ts_str = __import__("datetime").datetime.fromtimestamp(f.get("ts_wall", time.time())).isoformat(timespec="seconds")
                lines.append(f"{ts_str}  {side} {qty:g} @ {float(f.get('fill_price', 0.0)):.5f}  pos≈{pos:,.2f}")
            st.text_area(
                "Recent fills",
                value="\n".join(reversed(lines)),
                height=220,
                key="fills_latest_view",
                help="Latest fills with running position.",
                disabled=True,
            )
        else:
            st.caption("No fills yet.")

    with tab_pnl:
        st.subheader("Live Position & P&L")
        pos = st.session_state.pos
        cash = st.session_state.cash
        last_px = st.session_state.last_price
        pos_value = (pos * float(last_px)) if last_px is not None else 0.0
        eq = cash + pos_value
        # Chart equity over time
        if st.session_state.pnl:
            import datetime as _dt
            from rt_sim.metrics import (
                compute_drawdown,
                compute_sharpe_from_equity,
                compute_time_weighted_exposure,
                compute_trade_stats,
                compute_time_weighted_dollar_exposure,
            )
            t_raw, eq_vals = zip(*list(st.session_state.pnl))
            # Defer plotting until after KPIs so metrics appear above the chart

            # Compute rolling metrics on the visible equity curve
            dd, _, _ = compute_drawdown(list(eq_vals))
            # Approx annualization: assume 1 Hz samples => 31,536,000 seconds/year
            # Scale per-step Sharpe with factor chosen conservatively (3600*24*252 ~ trading seconds)
            ann = float(st.session_state.get("ann_factor", 3600.0 * 24.0 * 252.0))
            sharpe = compute_sharpe_from_equity(list(eq_vals), annualization_factor=ann)
            # Exposure based on time-weighted pos_series
            if st.session_state.pos_series:
                pt, pv = zip(*list(st.session_state.pos_series))
                exposure = compute_time_weighted_exposure(list(pt), list(pv))
            else:
                exposure = 0.0
            # Trade stats from fills
            fills_payloads = [f for _, f in list(st.session_state.fills)]
            tstats = compute_trade_stats(fills_payloads)
            win_rate = tstats.get("win_rate", 0.0)
            avg_pl = tstats.get("avg_trade_pl", 0.0)
            avg_hold = tstats.get("avg_hold_s", 0.0)

            metric_items = [
                ("Position (qty)", f"{pos:,.2f}"),
                ("Position Value", f"${pos_value:,.2f}"),
                ("Cash", f"${cash:,.2f}"),
                ("Equity", f"${eq:,.2f}"),
                ("Max Drawdown", f"{dd*100:,.2f}%"),
                ("Sharpe (approx)", f"{sharpe:,.2f}"),
                ("Exposure", f"{exposure*100:,.1f}%"),
                ("Win Rate", f"{win_rate*100:,.1f}%"),
                ("Avg Trade P/L", f"${avg_pl:,.2f}"),
                ("Avg Hold (s)", f"{avg_hold:,.1f}"),
            ]
            # Dollar exposure (avg as % of initial cash)
            rel_dexp = None
            if st.session_state.ticks and st.session_state.pos_series:
                tt, tp = zip(*list(st.session_state.ticks))
                pt, pv = zip(*list(st.session_state.pos_series))
                try:
                    rel_dexp = compute_time_weighted_dollar_exposure(
                        list(tt), list(tp), list(zip(pt, pv)), float(st.session_state.initial_cash)
                    )
                except Exception:
                    rel_dexp = 0.0
            if rel_dexp is not None:
                metric_items.append(("Dollar Exposure (avg)", f"{rel_dexp*100:,.1f}%"))

            per_row = 6
            card_style = "font-size:0.8rem; color:#a0a0a0; margin-bottom:0.15rem;"
            value_style = "font-size:1.05rem; font-weight:600; margin:0;"
            for i in range(0, len(metric_items), per_row):
                cols = st.columns(per_row)
                for col, (label, value) in zip(cols, metric_items[i : i + per_row]):
                    col.markdown(
                        f"<div style='{card_style}'>{label}</div><div style='{value_style}'>{value}</div>",
                        unsafe_allow_html=True,
                    )

            # Now render equity chart below the KPIs
            x = [_dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") for ts in t_raw]
            figp = go.Figure(data=[go.Scatter(x=x, y=list(eq_vals), mode="lines", name="Equity")])
            figp.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(figp, use_container_width=True)
        else:
            st.caption("No P&L data yet. Send an order to create fills or wait for ticks.")

    with tab_strategy:
        st.subheader("Strategy Host")
        spath_in = st.text_input("Strategy path", value=st.session_state.strategy_path)
        base_dir = Path(__file__).resolve().parents[1]
        spath = Path(spath_in)
        if not spath.is_absolute():
            spath = base_dir / spath
        st.session_state.strategy_path = str(spath)
        colsS = st.columns(2)
        if colsS[0].button("Load file"):
            try:
                st.session_state.strategy_code = Path(st.session_state.strategy_path).read_text()
                st.success("Loaded strategy file.")
            except Exception as e:
                st.error(f"Failed to load: {e}")
        if colsS[1].button("Save file"):
            try:
                Path(st.session_state.strategy_path).write_text(st.session_state.strategy_code)
                st.success("Saved strategy file.")
            except Exception as e:
                st.error(f"Failed to save: {e}")

        st.text_area("strategy.py", height=260, key="strategy_code")
        st.divider()
        st.subheader("Run Controls")
        sid = st.text_input("Strategy ID", value="sma1")
        topic_str = st.text_input("Tick topic (empty=all)", value="X")
        conflate = st.checkbox("Conflate latest only (ticks)", value=False, key="strategy_conflate")
        params_json = st.text_area("PARAMS override (JSON)", value="", placeholder='{"fast": 20, "slow": 50, "qty": 1, "threshold_bps": 15, "min_interval_s": 10}')
        stop_mode = st.radio(
            "Stop action",
            ("Flatten position", "Terminate only"),
            index=0,
            key="strategy_stop_mode",
            horizontal=True,
            help="Choose whether to send a flattening market order before shutting down the host.",
        )
        flatten_on_stop = stop_mode == "Flatten position"
        sbtn = st.columns(2)
        if sbtn[0].button("Start strategy host"):
            try:
                # if already running
                proc = st.session_state.proc_strategy
                if proc and getattr(proc, "poll", lambda: None)() is None:
                    st.warning("Strategy host already running")
                else:
                    # Build subprocess command to capture stdout
                    cfg_path = st.session_state.get("cfg_path", "configs/default.yaml")
                    # Resolve cfg path relative to project root if needed
                    cfg_p = Path(cfg_path)
                    if not cfg_p.is_absolute():
                        cfg_p = base_dir / cfg_p
                    conflate_flag = "--conflate" if conflate else "--no-conflate"
                    cmd = [
                        sys.executable,
                        "-u",
                        "-m",
                        "rt_sim.cli",
                        "run-strategy",
                        "--config",
                        str(cfg_p),
                        "--path",
                        st.session_state.strategy_path,
                        "--id",
                        sid,
                        "--topic",
                        topic_str,
                        conflate_flag,
                    ]
                    if params_json.strip():
                        cmd += ["--params", params_json]
                    env = dict(os.environ)
                    env["PYTHONUNBUFFERED"] = "1"
                    # set log file for host to write into
                    log_path = Path(base_dir / f"runs/strategy_host_{int(time.time())}.log")
                    env["STRAT_LOG_FILE"] = str(log_path)
                    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env)
                    st.session_state.strategy_log_path = str(log_path)
                    st.session_state.proc_strategy = p
                    register_pid(p.pid)
                    st.success(f"Strategy host started (pid={p.pid})")
            except Exception as e:
                st.error(f"Failed to start strategy host: {e}")
        if sbtn[1].button("Stop strategy host"):
            p = st.session_state.proc_strategy
            if p and getattr(p, "poll", lambda: 1)() is None:
                try:
                    st.session_state.strategy_log_event.clear()
                    p.terminate()
                    try:
                        p.wait(timeout=1)
                    except Exception:
                        p.kill()
                    unregister_pid(p.pid)
                    st.success("Strategy host stopped")
                    if flatten_on_stop and sid:
                        def _strategy_net_position(strategy_id: str) -> float:
                            total = 0.0
                            for _, f in st.session_state.fills:
                                if f.get("strategy_id") != strategy_id:
                                    continue
                                qty = float(f.get("qty", 0.0))
                                side_txt = str(f.get("side", "")).upper()
                                total += qty if side_txt == "BUY" else -qty
                            return total

                        net_pos = _strategy_net_position(sid)
                        if abs(net_pos) > 1e-9:
                            close_side = "SELL" if net_pos > 0 else "BUY"
                            close_qty = abs(net_pos)

                            def _send_flatten_order(side: str, qty: float) -> None:
                                cfg_loc = st.session_state.get("cfg", load_config(None))
                                t_local = Transport(
                                    hwm_ticks=int(cfg_loc["transport"]["hwm"]["ticks_pub"]),
                                    hwm_orders=int(cfg_loc["transport"]["hwm"]["orders"]),
                                    hwm_fills=int(cfg_loc["transport"]["hwm"]["fills_pub"]),
                                )
                                push_socket = t_local.connect_push(cfg_loc["transport"]["endpoints"]["orders_push"])
                                import zmq as _zmq

                                push_socket.setsockopt(_zmq.LINGER, 500)
                                payload = {
                                    "strategy_id": sid,
                                    "side": side,
                                    "qty": float(qty),
                                    "tag": "auto_flatten",
                                }
                                Transport.send_json_push(push_socket, payload)
                                time.sleep(0.01)
                                push_socket.close()

                            try:
                                _send_flatten_order(close_side, close_qty)
                                st.info(
                                    f"Requested {close_side} {close_qty:g} to flatten strategy '{sid}'. Check fills to confirm."
                                )
                            except Exception as err:
                                st.error(f"Failed to send flatten order: {err}")
                        else:
                            st.info("Strategy position already flat.")
                except Exception as e:
                    st.error(f"Failed to stop strategy host: {e}")
            else:
                st.info("Strategy host not running")
        alive = bool(st.session_state.proc_strategy and getattr(st.session_state.proc_strategy, "poll", lambda: 1)() is None)
        st.caption(f"Strategy host alive: {alive}")

        st.subheader("Live Logs")
        log_path = Path(st.session_state.get("strategy_log_path", ""))
        if log_path.exists():
            try:
                content = log_path.read_text()
                lines = content.strip().splitlines()
            except Exception:
                lines = []
        else:
            lines = []
        if not lines:
            st.caption("No logs yet.")
        st.text_area(
            "Log output",
            value="\n".join(reversed(lines[-400:])) if lines else "",
            height=240,
            key="strategy_logs_view",
            help="Most recent strategy host log lines.",
            disabled=True,
        )

        st.divider()
        st.subheader("Manage Strategy Hosts")
        pid_list = read_pid_registry()
        st.caption(f"Tracked strategy host PIDs: {pid_list if pid_list else '[]'}")
        if st.button("Stop ALL strategy hosts"):
            stopped = []
            still = []
            for pid in pid_list:
                try:
                    os.kill(pid, _signal.SIGTERM)
                    stopped.append(pid)
                except Exception:
                    still.append(pid)
            # Quick cleanup of registry
            # Rebuild registry based on processes that are still alive
            remaining = []
            for pid in pid_list:
                try:
                    os.kill(pid, 0)
                except Exception:
                    continue
                else:
                    remaining.append(pid)
            write_pid_registry(remaining)
            st.success(f"Sent SIGTERM to: {stopped}. Remaining tracked: {remaining}")

    with tab_admin:
        st.subheader("Listener Settings")
        listener_cols = st.columns(3)
        with listener_cols[0]:
            st.checkbox("Conflate latest only", key="conflate")
        with listener_cols[1]:
            st.checkbox("Auto-refresh", key="auto_refresh")
        with listener_cols[2]:
            st.button("Refresh now", on_click=lambda: None)
        st.slider("Refresh rate (Hz)", 1, 20, key="refresh_hz")

        run_id = st.session_state.get("last_run_id")
        if run_id:
            export_base = Path(cfg.get("run", {}).get("export_dir", "runs/last"))
            st.caption(f"Latest run artifacts: {export_base / run_id}")

        st.divider()
        st.subheader("Diagnostics")
        diag_col1, diag_col2 = st.columns(2)
        tick_feedback = diag_col1.empty()
        fill_feedback = diag_col2.empty()
        test_window_s = 3.0
        if diag_col1.button(f"Test receive ({int(test_window_s)}s)"):
            try:
                t = Transport(hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]))
                sub = t.connect_sub(cfg["transport"]["endpoints"]["ticks_pub"], topic="X", conflate=False)
                time.sleep(0.1)  # allow subscription handshake
                poller = zmq.Poller(); poller.register(sub, zmq.POLLIN)
                import time as _time

                start = _time.time(); cnt = 0; last = None
                while _time.time() - start < test_window_s:
                    socks = dict(poller.poll(timeout=100))
                    if sub in socks and socks[sub] == zmq.POLLIN:
                        _, payload = t.recv_json(sub)
                        cnt += 1; last = payload
                sub.close(0)
                st.session_state.test_recv_stats = {
                    "count": cnt,
                    "last_price": (last or {}).get("price"),
                    "last_seq": (last or {}).get("seq"),
                    "window_s": test_window_s,
                    "ts": _time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                if cnt > 0:
                    price_val = st.session_state.test_recv_stats.get("last_price")
                    price_str = f"{float(price_val):.5f}" if price_val not in (None, "") else "n/a"
                    tick_feedback.success(
                        f"{cnt} messages in ~{test_window_s:.0f}s | last price={price_str} seq={st.session_state.test_recv_stats.get('last_seq', '-') }"
                    )
                else:
                    tick_feedback.info(f"No ticks observed in ~{test_window_s:.0f}s window.")
            except Exception as e:
                tick_feedback.error(f"Test receive failed: {e}")
        if diag_col2.button(f"Test fills ({int(test_window_s)}s)"):
            try:
                t = Transport(hwm_ticks=int(cfg["transport"]["hwm"]["fills_pub"]))
                sub = t.connect_sub(cfg["transport"]["endpoints"]["fills_pub"], topic="", conflate=False)
                time.sleep(0.1)  # allow subscription handshake
                poller = zmq.Poller(); poller.register(sub, zmq.POLLIN)
                import time as _time

                start = _time.time(); cnt = 0; last = None
                while _time.time() - start < test_window_s:
                    socks = dict(poller.poll(timeout=100))
                    if sub in socks and socks[sub] == zmq.POLLIN:
                        _, payload = t.recv_json(sub)
                        cnt += 1; last = payload
                sub.close(0)
                st.session_state.test_fills_stats = {
                    "count": cnt,
                    "last_price": (last or {}).get("fill_price"),
                    "last_qty": (last or {}).get("qty"),
                    "last_side": (last or {}).get("side"),
                    "window_s": test_window_s,
                    "ts": _time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                if cnt > 0:
                    price_val = st.session_state.test_fills_stats["last_price"]
                    price_str = f"{price_val:.5f}" if price_val is not None else "n/a"
                    fill_feedback.success(
                        f"{cnt} fills in ~{test_window_s:.0f}s | last {st.session_state.test_fills_stats['last_side']} {st.session_state.test_fills_stats['last_qty']} @ {price_str}"
                    )
                else:
                    fill_feedback.info(f"No fills observed in ~{test_window_s:.0f}s window.")
            except Exception as e:
                fill_feedback.error(f"Test fills failed: {e}")
        tr = st.session_state.get("test_recv_stats")
        if tr:
            price_val = tr.get("last_price")
            price_str = f"{float(price_val):.5f}" if price_val not in (None, "") else "n/a"
            st.caption(
                f"Last receive test @ {tr['ts']}: count={tr['count']} over ~{tr['window_s']:.0f}s | last_price={price_str} | last_seq={tr.get('last_seq', '-') }"
            )
        trf = st.session_state.get("test_fills_stats")
        if trf:
            price_val = trf.get("last_price")
            price_str = f"{float(price_val):.5f}" if price_val not in (None, "") else "n/a"
            side = trf.get("last_side", "-")
            qty = trf.get("last_qty", "-")
            st.caption(
                f"Last fills test @ {trf['ts']}: count={trf['count']} over ~{trf['window_s']:.0f}s | last {side} {qty} @ {price_str}"
            )

        st.divider()
        st.subheader("Local Processes")
        broker_cols = st.columns(2)
        if broker_cols[0].button("Start local broker"):
            try:
                if st.session_state.proc_broker and st.session_state.proc_broker.is_alive():
                    broker_cols[0].warning("Broker already running")
                else:
                    run_id = new_run_id()
                    p = mp.Process(target=_proc_broker_entry, args=(cfg, run_id), daemon=True)
                    p.start(); st.session_state.proc_broker = p
                    broker_cols[0].success(f"Broker started (pid={p.pid})")
            except Exception as e:
                broker_cols[0].error(f"Failed to start broker: {e}")
        if broker_cols[1].button("Stop local broker"):
            p = st.session_state.proc_broker
            if p and p.is_alive():
                p.terminate(); p.join(timeout=1)
                broker_cols[1].success("Broker stopped")
            else:
                broker_cols[1].info("Broker not running")
        sim_cols = st.columns(2)
        if sim_cols[0].button("Start local simulator"):
            try:
                if st.session_state.proc_sim and st.session_state.proc_sim.is_alive():
                    sim_cols[0].warning("Simulator already running")
                else:
                    run_id = new_run_id()
                    p = mp.Process(target=_proc_sim_entry, args=(cfg, run_id), daemon=True)
                    p.start(); st.session_state.proc_sim = p
                    sim_cols[0].success(f"Simulator started (pid={p.pid})")
            except Exception as e:
                sim_cols[0].error(f"Failed to start simulator: {e}")
        if sim_cols[1].button("Stop local simulator"):
            p = st.session_state.proc_sim
            if p and p.is_alive():
                p.terminate(); p.join(timeout=1)
                sim_cols[1].success("Simulator stopped")
            else:
                sim_cols[1].info("Simulator not running")

        st.divider()
        st.subheader("Metrics Settings")
        default_ann = int(3600 * 24 * 252)
        st.number_input(
            "Sharpe annualization factor",
            min_value=1,
            value=st.session_state.get("ann_factor", default_ann),
            key="ann_factor",
            help="Scale per-step Sharpe to annualized (e.g., trading-seconds-per-year)",
        )

        st.divider()
        st.subheader("Status")
        ep = cfg["transport"]["endpoints"]["ticks_pub"]
        ep_f = cfg["transport"]["endpoints"]["fills_pub"]
        status_cols = st.columns(2)
        with status_cols[0]:
            st.write(f"Ticks endpoint: `{ep}`")
            st.write(f"Fills endpoint: `{ep_f}`")
            st.write(f"Queue size: {st.session_state.queue.qsize()}")
        with status_cols[1]:
            th = st.session_state.listener_thread
            fth = st.session_state.fills_thread
            st.write(f"Listener alive: {bool(th and th.is_alive())}")
            st.write(f"Fills listener alive: {bool(fth and fth.is_alive())}")
            last = st.session_state.get("last_recv_ts")
            st.write(f"Last received: {last if last else 'none yet'}")
        if _LAST_THREAD_ERROR:
            st.error(_LAST_THREAD_ERROR)
        atimes = st.session_state.get("arrival_times", deque())
        rate = 0.0
        if atimes:
            cutoff = time.time() - 5.0
            recent = [t for t in atimes if t >= cutoff]
            if len(recent) >= 2:
                dur = max(1e-6, (recent[-1] - recent[0]))
                rate = len(recent) / dur
        metric_cols = st.columns(3)
        metric_cols[0].metric("Approx ticks/sec", f"{rate:.1f}")
        metric_cols[1].metric("Seq gaps", st.session_state.get("gap_count", 0))
        metric_cols[2].metric("UI fills captured", len(st.session_state.fills))
        if not (st.session_state.listener_thread and st.session_state.listener_thread.is_alive()):
            st.caption("UI fills update only while the main listener (Start SUB) is running.")

        st.divider()
        st.subheader("Config")
        cfg_path = st.text_input("Config path", value="configs/default.yaml")
        base_dir = Path(__file__).resolve().parents[1]
        resolved = Path(cfg_path)
        if not resolved.is_absolute():
            resolved = base_dir / resolved
        if st.button("Load config"):
            try:
                st.session_state.cfg = load_config(str(resolved))
                st.session_state.cfg_path = str(resolved)
                cfg = st.session_state.cfg
                try:
                    init_cash = float(cfg.get("portfolio", {}).get("initial_cash", 100000.0))
                except Exception:
                    init_cash = 100000.0
                st.session_state.initial_cash = init_cash
                st.session_state.pos = 0.0
                st.session_state.cash = init_cash
                st.session_state.last_price = None
                st.session_state.pnl = deque(maxlen=2000)
                st.session_state.fills = deque(maxlen=500)
                st.session_state.pos_series = deque(maxlen=2000)
                st.success(f"Loaded config: {resolved} (initial cash set to ${init_cash:,.2f})")
            except Exception as e:
                st.error(f"Failed to load config: {e}")
        st.caption(f"Using config: {resolved}")
        st.code(json.dumps(cfg["transport"], indent=2))

    if st.session_state.get("auto_refresh", True):
        st_autorefresh = st.empty()
        st_autorefresh.caption("Auto-refresh active")
        time.sleep(chart_refresh_ms / 1000)
        try:
            st.rerun()
        except Exception:
            if hasattr(st, "experimental_rerun"):
                st.experimental_rerun()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
