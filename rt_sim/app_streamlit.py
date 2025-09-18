from __future__ import annotations

import json
import threading
import time
from collections import deque

import plotly.graph_objs as go
import streamlit as st
import zmq
from queue import Queue, Full, Empty
import multiprocessing as mp

# Background listener writes into a Queue object passed at thread start.
# Keep the reference in session_state so reruns use the same queue.
_LAST_THREAD_ERROR: str | None = None

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


st.set_page_config(page_title="algosim — Ticks", layout="wide")


def ensure_state():
    if "ticks" not in st.session_state:
        st.session_state.ticks = deque(maxlen=2000)
    if "fills" not in st.session_state:
        st.session_state.fills = deque(maxlen=500)
    if "pnl" not in st.session_state:
        st.session_state.pnl = deque(maxlen=2000)  # (ts_wall, equity)
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
        st.session_state.refresh_hz = 5
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
    if "proc_broker" not in st.session_state:
        st.session_state.proc_broker = None
    if "proc_sim" not in st.session_state:
        st.session_state.proc_sim = None


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
                        fq_out.put_nowait((payload.get("ts_wall", time.time()), payload))
                    except Full:
                        try:
                            fq_out.get_nowait()
                            fq_out.put_nowait((payload.get("ts_wall", time.time()), payload))
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

    with st.sidebar:
        st.header("Connection")
        # Active config (loaded earlier or defaults)
        cfg = st.session_state.get("cfg", load_config(None))
        st.checkbox("Conflate latest only", key="conflate")
        cols = st.columns(2)
        if cols[0].button("Start SUB"):
            start_listener(cfg)
        if cols[1].button("Stop SUB"):
            stop_listener()
        # Test receive placed below the control row
        if st.button("Test receive (1s)"):
            try:
                # One-shot SUB test, no conflation, 1s window
                t = Transport(hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]))
                sub = t.connect_sub(cfg["transport"]["endpoints"]["ticks_pub"], topic="X", conflate=False)
                poller = zmq.Poller()
                poller.register(sub, zmq.POLLIN)
                import time as _time

                start = _time.time()
                cnt = 0
                last = None
                while _time.time() - start < 1.2:
                    socks = dict(poller.poll(timeout=100))
                    if sub in socks and socks[sub] == zmq.POLLIN:
                        _, payload = t.recv_json(sub)
                        cnt += 1
                        last = payload
                sub.close(0)
                # Persist results for display across reruns
                st.session_state.test_recv_stats = {
                    "count": cnt,
                    "last_price": (last or {}).get("price"),
                    "last_seq": (last or {}).get("seq"),
                    "window_s": 1.2,
                    "ts": _time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                if cnt > 0:
                    st.success(
                        f"Test receive: {cnt} messages in ~1s. Last price={st.session_state.test_recv_stats['last_price']:.5f} seq={st.session_state.test_recv_stats['last_seq']}"
                    )
                else:
                    st.warning("Test receive: 0 messages in ~1s. Check simulator and endpoint/topic.")
            except Exception as e:
                st.error(f"Test receive failed: {e}")
        if st.button("Test fills (1s)"):
            try:
                # One-shot SUB test for fills, subscribe to all topics
                t = Transport(hwm_ticks=int(cfg["transport"]["hwm"]["fills_pub"]))
                sub = t.connect_sub(cfg["transport"]["endpoints"]["fills_pub"], topic="", conflate=False)
                poller = zmq.Poller(); poller.register(sub, zmq.POLLIN)
                import time as _time
                start = _time.time(); cnt = 0; last = None
                while _time.time() - start < 1.2:
                    socks = dict(poller.poll(timeout=100))
                    if sub in socks and socks[sub] == zmq.POLLIN:
                        topic, payload = t.recv_json(sub)
                        cnt += 1; last = payload
                sub.close(0)
                st.session_state.test_fills_stats = {
                    "count": cnt,
                    "last_price": (last or {}).get("fill_price"),
                    "last_qty": (last or {}).get("qty"),
                    "last_side": (last or {}).get("side"),
                    "window_s": 1.2,
                    "ts": _time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                if cnt > 0:
                    st.success(
                        f"Test fills: {cnt} messages in ~1s. Last {st.session_state.test_fills_stats['last_side']} {st.session_state.test_fills_stats['last_qty']} @ {st.session_state.test_fills_stats['last_price']:.5f}"
                    )
                else:
                    st.warning("Test fills: 0 messages in ~1s. Check broker and fills endpoint.")
            except Exception as e:
                st.error(f"Test fills failed: {e}")
        # Local demo controls
        st.divider()
        st.subheader("Local Processes")
        lc = st.columns(2)
        if lc[0].button("Start local broker"):
            try:
                if st.session_state.proc_broker and st.session_state.proc_broker.is_alive():
                    st.warning("Broker already running")
                else:
                    run_id = new_run_id()
                    p = mp.Process(target=_proc_broker_entry, args=(cfg, run_id), daemon=True)
                    p.start(); st.session_state.proc_broker = p
                    st.success(f"Broker started (pid={p.pid})")
            except Exception as e:
                st.error(f"Failed to start broker: {e}")
        if lc[1].button("Stop local broker"):
            p = st.session_state.proc_broker
            if p and p.is_alive():
                p.terminate(); p.join(timeout=1)
                st.success("Broker stopped")
            else:
                st.info("Broker not running")
        lc2 = st.columns(2)
        if lc2[0].button("Start local simulator"):
            try:
                if st.session_state.proc_sim and st.session_state.proc_sim.is_alive():
                    st.warning("Simulator already running")
                else:
                    run_id = new_run_id()
                    p = mp.Process(target=_proc_sim_entry, args=(cfg, run_id), daemon=True)
                    p.start(); st.session_state.proc_sim = p
                    st.success(f"Simulator started (pid={p.pid})")
            except Exception as e:
                st.error(f"Failed to start simulator: {e}")
        if lc2[1].button("Stop local simulator"):
            p = st.session_state.proc_sim
            if p and p.is_alive():
                p.terminate(); p.join(timeout=1)
                st.success("Simulator stopped")
            else:
                st.info("Simulator not running")

        # Refresh controls
        st.divider()
        st.subheader("Refresh")
        st.checkbox("Auto-refresh", key="auto_refresh")
        st.slider("Refresh rate (Hz)", 1, 20, key="refresh_hz")
        st.button("Refresh now", on_click=lambda: None)

        # Status block
        st.divider()
        st.subheader("Status")
        ep = cfg["transport"]["endpoints"]["ticks_pub"]
        ep_f = cfg["transport"]["endpoints"]["fills_pub"]
        st.write(f"Ticks: `{ep}` | Topic: `X` | Conflate: {st.session_state.conflate}")
        st.write(f"Fills: `{ep_f}` (subscribe all topics)")
        st.write(f"Queue size: {st.session_state.queue.qsize()}")
        th = st.session_state.listener_thread
        st.write(f"Listener alive: {bool(th and th.is_alive())}")
        fth = st.session_state.fills_thread
        st.write(f"Fills listener alive: {bool(fth and fth.is_alive())}")
        last = st.session_state.get("last_recv_ts", None)
        if last:
            st.write(f"Last received at: {last}")
        else:
            st.write("Last received: none yet")
        if _LAST_THREAD_ERROR:
            st.error(_LAST_THREAD_ERROR)
        # Metrics: ticks/sec and sequenced gaps
        atimes = st.session_state.get("arrival_times", deque())
        rate = 0.0
        if atimes:
            cutoff = time.time() - 5.0
            recent = [t for t in atimes if t >= cutoff]
            if len(recent) >= 2:
                dur = max(1e-6, (recent[-1] - recent[0]))
                rate = len(recent) / dur
        st.write(f"Ticks/sec (approx): {rate:.1f}")
        st.write(f"Seq gaps detected: {st.session_state.get('gap_count', 0)}")
        # Persistent Test Receive results
        tr = st.session_state.get("test_recv_stats")
        if tr:
            st.info(
                f"Last Test receive @ {tr['ts']}: count={tr['count']} | last_price={tr['last_price']:.5f} | last_seq={tr['last_seq']} | window~{tr['window_s']}s"
            )
        # Persistent Test Fills results
        trf = st.session_state.get("test_fills_stats")
        if trf:
            if trf.get("count", 0) > 0 and trf.get("last_price") is not None:
                price_str = f"{float(trf['last_price']):.5f}"
            elif trf.get("count", 0) > 0:
                price_str = "n/a"
            else:
                price_str = "n/a"
            cnt = trf.get("count", 0)
            side = trf.get("last_side", "-")
            qty = trf.get("last_qty", "-")
            st.info(
                f"Last Test fills @ {trf['ts']}: count={cnt} | last {side} {qty} @ {price_str} | window~{trf['window_s']}s"
            )
        # Fills stats
        st.write(f"Fills received: {len(st.session_state.fills)}")

        # Config box at the end
        st.divider()
        st.subheader("Config")
        cfg_path = st.text_input("Config path", value="configs/default.yaml")
        base_dir = __import__("pathlib").Path(__file__).resolve().parents[1]
        resolved = __import__("pathlib").Path(cfg_path)
        if not resolved.is_absolute():
            resolved = base_dir / resolved
        if st.button("Load config"):
            try:
                st.session_state.cfg = load_config(str(resolved))
                st.success(f"Loaded config: {resolved}")
            except Exception as e:
                st.error(f"Failed to load config: {e}")
        st.caption(f"Using config: {resolved}")
        st.code(json.dumps(cfg["transport"], indent=2))

    tabs = st.tabs(["Ticks", "Fills / Orders", "P&L"]) 
    with tabs[0]:
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
        # Update equity on tick if we have price
        if st.session_state.last_price is not None:
            eq = st.session_state.cash + st.session_state.pos * float(st.session_state.last_price)
            st.session_state.pnl.append((time.time(), eq))

    # Drain fills queue
    fdrained = 0
    try:
        while True:
            tsf, payload = st.session_state.fills_queue.get_nowait()
            st.session_state.fills.append((tsf, payload))
            # Update position and cash
            side = str(payload.get("side", "")).upper()
            qty = float(payload.get("qty", 0.0))
            price = float(payload.get("fill_price", 0.0))
            commission = float(payload.get("commission", 0.0))
            if side == "BUY":
                st.session_state.pos += qty
                st.session_state.cash -= price * qty + commission
            elif side == "SELL":
                st.session_state.pos -= qty
                st.session_state.cash += price * qty - commission
            # Equity snapshot at fill
            last_px = st.session_state.last_price if st.session_state.last_price is not None else price
            eq = st.session_state.cash + st.session_state.pos * float(last_px)
            st.session_state.pnl.append((tsf, eq))
            fdrained += 1
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

                    x_raw, y = zip(*data)
                    x = [
                        _dt.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds") for ts in x_raw
                    ]
                    fig = go.Figure(data=[go.Scatter(x=x, y=list(y), mode="lines", name="Price")])
                    fig.update_layout(height=500, margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                    st.caption(f"Tick count: {len(data)}")

    with tabs[1]:
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
            st.text("\n".join(lines))
        else:
            st.caption("No fills yet.")

    with tabs[2]:
        st.subheader("Live Position & P&L")
        pos = st.session_state.pos
        cash = st.session_state.cash
        last_px = st.session_state.last_price
        eq = cash + (pos * float(last_px) if last_px is not None else 0.0)
        c1, c2, c3 = st.columns(3)
        c1.metric("Position (qty)", f"{pos:,.2f}")
        c2.metric("Cash", f"${cash:,.2f}")
        c3.metric("Equity", f"${eq:,.2f}")
        # Chart equity over time
        if st.session_state.pnl:
            import datetime as _dt
            t_raw, eq_vals = zip(*list(st.session_state.pnl))
            x = [_dt.datetime.fromtimestamp(ts).isoformat(timespec="seconds") for ts in t_raw]
            figp = go.Figure(data=[go.Scatter(x=x, y=list(eq_vals), mode="lines", name="Equity")])
            figp.update_layout(height=400, margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(figp, use_container_width=True)
        else:
            st.caption("No P&L data yet. Send an order to create fills or wait for ticks.")

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
