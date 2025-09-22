from __future__ import annotations

import importlib.util
import json
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Dict, Optional

import zmq

from .transport import Transport
import os

_LOG_FILE = None


def _log(msg: str) -> None:
    global _LOG_FILE
    try:
        print(msg, flush=True)
    except Exception:
        pass
    try:
        path = os.environ.get("STRAT_LOG_FILE")
        if path:
            if _LOG_FILE is None:
                _LOG_FILE = open(path, "a", buffering=1)
            _LOG_FILE.write(msg + "\n")
    except Exception:
        pass


class SMA:
    def __init__(self, window: int):
        self.w = max(1, int(window))
        self.buf: list[float] = []
        self.sum = 0.0

    def update(self, x: float) -> Optional[float]:
        if len(self.buf) == self.w:
            self.sum -= self.buf[0]
            self.buf = self.buf[1:]
        self.buf.append(float(x))
        self.sum += float(x)
        if len(self.buf) < self.w:
            return None
        return self.sum / self.w


class IndicatorsNS:
    def SMA(self, window: int) -> SMA:
        return SMA(window)


class StrategyContext:
    def __init__(self, strategy_id: str, params: Dict[str, Any], transport: Transport, endpoints: Dict[str, str]):
        self._sid = strategy_id
        self._params = params or {}
        self._state: Dict[str, Any] = {}
        self._t = transport
        self._endpoints = endpoints
        self.indicator = IndicatorsNS()
        # runtime/position tracking
        self._pos: float = 0.0
        # sockets
        self._push = self._t.connect_push(self._endpoints["orders_push"])
        self._push.setsockopt(zmq.LINGER, 500)

    # params/state
    def get_param(self, name: str, default: Any = None) -> Any:
        return self._params.get(name, default)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        return self._state.get(key, default)

    # trading helpers
    def position(self) -> float:
        return self._pos

    def place_market_order(self, side: str, qty: float, tag: Optional[str] = None) -> None:
        payload = {
            "strategy_id": self._sid,
            "side": str(side).upper(),
            "qty": float(qty),
            "tag": tag,
        }
        Transport.send_json_push(self._push, payload)
        _log(f"[host] order placed: {payload}")

    # internal update from fills
    def _on_fill(self, fill: Dict[str, Any]) -> None:
        side = str(fill.get("side", "")).upper()
        qty = float(fill.get("qty", 0.0))
        if side == "BUY":
            self._pos += qty
        elif side == "SELL":
            self._pos -= qty

    def close(self) -> None:
        try:
            self._push.close(0)
        except Exception:
            pass


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load strategy module from {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = mod
    spec.loader.exec_module(mod)  # type: ignore[assignment]
    return mod


def run(
    config: Dict[str, Any],
    transport: Transport,
    run_id: str,
    strategy_path: str,
    strategy_id: Optional[str] = None,
    params_override: Optional[Dict[str, Any]] = None,
    topic: str = "",
    conflate: bool = False,
) -> None:
    endpoints = config["transport"]["endpoints"]
    ticks_addr = endpoints["ticks_pub"]
    fills_addr = endpoints["fills_pub"]

    mod_path = Path(strategy_path).resolve()
    mod = _load_module(mod_path)
    sid = strategy_id or getattr(mod, "NAME", None) or mod_path.stem
    base_params = getattr(mod, "PARAMS", {}) or {}
    user_params = params_override or {}
    params = {**base_params, **user_params}

    ctx = StrategyContext(sid, params, transport, endpoints)
    if hasattr(mod, "init") and callable(mod.init):  # type: ignore[attr-defined]
        mod.init(ctx)  # type: ignore[attr-defined]

    sub_ticks = transport.connect_sub(ticks_addr, topic=topic, conflate=conflate)
    sub_fills = transport.connect_sub(fills_addr, topic=sid, conflate=False)

    poller = zmq.Poller()
    poller.register(sub_ticks, zmq.POLLIN)
    poller.register(sub_fills, zmq.POLLIN)

    _log(
        f"[host] strategy_id={sid} | file={mod_path} | ticks={ticks_addr} topic='{topic or '*'}' conflate={conflate} | fills={fills_addr}"
    )

    last_mtime = mod_path.stat().st_mtime
    running = True

    def _sigint(_sig, _frm):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _sigint)

    try:
        tick_count = 0
        t_last = time.time()
        saw_first_tick = False
        while running:
            socks = dict(poller.poll(timeout=200))
            # fills update position
            if sub_fills in socks and socks[sub_fills] == zmq.POLLIN:
                _, fill = transport.recv_json(sub_fills)
                ctx._on_fill(fill)

            # ticks -> on_tick
            if sub_ticks in socks and socks[sub_ticks] == zmq.POLLIN:
                _, tick = transport.recv_json(sub_ticks)
                tick_count += 1
                if not saw_first_tick:
                    saw_first_tick = True
                    _log(f"[host] first tick price={float(tick.get('price', 0.0)):.5f}")
                if hasattr(mod, "on_tick") and callable(mod.on_tick):  # type: ignore[attr-defined]
                    try:
                        mod.on_tick(ctx, tick)  # type: ignore[attr-defined]
                    except Exception as e:
                        _log(f"[host] on_tick error: {e}")

            now = time.time()
            if now - t_last >= 1.0:
                _log(f"[host] ticks last 1s: {tick_count}")
                tick_count = 0
                t_last = now

            # hot reload check
            try:
                mtime = mod_path.stat().st_mtime
                if mtime > last_mtime:
                    last_mtime = mtime
                    print(f"[host] change detected -> reload {mod_path}", flush=True)
                    # teardown
                    if hasattr(mod, "on_stop") and callable(mod.on_stop):  # type: ignore[attr-defined]
                        try:
                            mod.on_stop(ctx)  # type: ignore[attr-defined]
                        except Exception:
                            pass
                    # reload fresh state
                    mod = _load_module(mod_path)
                    ctx._state.clear()
                    ctx._pos = 0.0
                    if hasattr(mod, "init") and callable(mod.init):  # type: ignore[attr-defined]
                        mod.init(ctx)  # type: ignore[attr-defined]
            except Exception:
                pass
    finally:
        try:
            if hasattr(mod, "on_stop") and callable(mod.on_stop):  # type: ignore[attr-defined]
                mod.on_stop(ctx)  # type: ignore[attr-defined]
        except Exception:
            pass
        try:
            sub_ticks.close(0)
            sub_fills.close(0)
        except Exception:
            pass
        ctx.close()
