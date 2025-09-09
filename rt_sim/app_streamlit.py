from __future__ import annotations

import json
import threading
import time
from collections import deque

import plotly.graph_objs as go
import streamlit as st
import zmq
from queue import Queue, Full, Empty

# Background listener writes into a Queue object passed at thread start.
# Keep the reference in session_state so reruns use the same queue.
_LAST_THREAD_ERROR: str | None = None

try:
    from rt_sim.transport import Transport
    from rt_sim.utils import load_config
except ModuleNotFoundError:
    # Fallback: add project root to sys.path when running via `streamlit run`
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from rt_sim.transport import Transport
    from rt_sim.utils import load_config


st.set_page_config(page_title="algosim — Ticks", layout="wide")


def ensure_state():
    if "ticks" not in st.session_state:
        st.session_state.ticks = deque(maxlen=2000)
    if "listener_thread" not in st.session_state:
        st.session_state.listener_thread = None
    if "listener_event" not in st.session_state:
        st.session_state.listener_event = threading.Event()
    if "conflate" not in st.session_state:
        st.session_state.conflate = True
    if "queue" not in st.session_state:
        st.session_state.queue = Queue(maxsize=10000)


def start_listener(cfg):
    event: threading.Event = st.session_state.listener_event
    if event.is_set():
        return

    event.set()
    q: Queue = st.session_state.queue

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
                        q_out.put_nowait((payload["ts_wall"], payload["price"]))
                    except Full:
                        # Drop some backlog
                        try:
                            for _ in range(q_out.qsize() // 2):
                                q_out.get_nowait()
                        except Empty:
                            pass
            sub.close(0)
        except Exception as e:
            _LAST_THREAD_ERROR = f"Listener thread error: {e}"

    th = threading.Thread(target=_loop, args=(event, st.session_state.conflate, q), daemon=True)
    th.start()
    st.session_state.listener_thread = th


def stop_listener():
    event: threading.Event = st.session_state.listener_event
    event.clear()
    th = st.session_state.listener_thread
    if th and th.is_alive():
        th.join(timeout=0.5)
    st.session_state.listener_thread = None


def main():
    ensure_state()
    st.title("algosim — Real-Time Ticks (MVP)")

    with st.sidebar:
        st.header("Connection")
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
        cfg = st.session_state.get("cfg", load_config(None))
        st.checkbox("Conflate latest only", key="conflate")
        st.caption(f"Using config: {resolved}")
        st.code(json.dumps(cfg["transport"], indent=2))
        cols = st.columns(3)
        if cols[0].button("Start SUB"):
            start_listener(cfg)
        if cols[1].button("Stop SUB"):
            stop_listener()
        if cols[2].button("Test receive (1s)"):
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
                if cnt > 0:
                    st.success(f"Received {cnt} messages in ~1s. Last price={last.get('price'):.5f} seq={last.get('seq')}")
                else:
                    st.warning("Received 0 messages in ~1s. Check simulator and endpoint/topic.")
            except Exception as e:
                st.error(f"Test receive failed: {e}")
        # Status block
        st.divider()
        st.subheader("Status")
        ep = cfg["transport"]["endpoints"]["ticks_pub"]
        st.write(f"Endpoint: `{ep}` | Topic: `X` | Conflate: {st.session_state.conflate}")
        st.write(f"Queue size: {st.session_state.queue.qsize()}")
        th = st.session_state.listener_thread
        st.write(f"Listener alive: {bool(th and th.is_alive())}")
        last = st.session_state.get("last_recv_ts", None)
        if last:
            st.write(f"Last received at: {last}")
        else:
            st.write("Last received: none yet")
        if _LAST_THREAD_ERROR:
            st.error(_LAST_THREAD_ERROR)

    st.caption("Subscribe to latest tick (CONFLATE=1); printing recent ticks below.")
    placeholder = st.empty()

    # Simple autorefresh loop
    chart_refresh_ms = int(1000 / max(1, int(load_config(None)["ui"]["throttle_fps"])) )
    # Drain incoming queue into ticks before drawing
    drained = 0
    try:
        while True:
            ts, price = st.session_state.queue.get_nowait()
            st.session_state.ticks.append((ts, price))
            drained += 1
    except Empty:
        pass
    if drained:
        import datetime as _dt

        st.session_state.last_recv_ts = _dt.datetime.now().isoformat(timespec="seconds")

    with placeholder.container():
        data = list(st.session_state.ticks)
        if data:
            # Render as plain text lines (ts_wall ISO-ish, price)
            import datetime as _dt

            def _fmt(ts: float, px: float) -> str:
                ts_str = _dt.datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
                return f"{ts_str}  price={px:.5f}"

            lines = [_fmt(ts, px) for ts, px in data[-500:]]  # show last 500 lines
            st.text("\n".join(lines))
            st.caption(f"Tick count: {len(data)} (showing last {min(len(data), 500)})")
        else:
            st.info("No ticks yet. Start the simulator and then Start SUB.")

    st_autorefresh = st.empty()
    st_autorefresh.caption("Auto-refresh active")
    time.sleep(chart_refresh_ms / 1000)
    # Streamlit >=1.32 uses st.rerun(); experimental_rerun deprecated
    try:
        st.rerun()
    except Exception:
        # Older versions fallback
        if hasattr(st, "experimental_rerun"):
            st.experimental_rerun()  # type: ignore[attr-defined]


if __name__ == "__main__":
    main()
