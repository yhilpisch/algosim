from __future__ import annotations

from strategies.mean_reversion import strategy as mr


class DummySMA:
    def __init__(self, window: int):
        self.window = max(1, int(window))
        self.values: list[float] = []

    def update(self, value: float) -> float | None:
        self.values.append(float(value))
        if len(self.values) < self.window:
            return None
        if len(self.values) > self.window:
            self.values.pop(0)
        return sum(self.values) / len(self.values)


class DummyIndicator:
    def SMA(self, window: int) -> DummySMA:
        return DummySMA(window)


class DummyCtx:
    def __init__(self, params: dict[str, float]):
        self.params = params
        self.state: dict[str, float | str | None] = {}
        self._pos = 0.0
        self.orders: list[dict[str, float | str]] = []
        self.indicator = DummyIndicator()

    def get_param(self, name: str, default=None):
        return self.params.get(name, default)

    def set_state(self, key: str, value) -> None:
        self.state[key] = value

    def get_state(self, key: str, default=None):
        return self.state.get(key, default)

    def position(self) -> float:
        return self._pos

    def place_market_order(self, side: str, qty: float, tag: str | None = None) -> None:
        self.orders.append({"side": side.upper(), "qty": qty, "tag": tag})
        if side.upper() == "BUY":
            self._pos += qty
        else:
            self._pos -= qty


def _tick(price: float, ts_wall: float) -> dict[str, float]:
    return {"price": price, "ts_wall": ts_wall}


def test_mean_reversion_targets_and_exits():
    params = {
        "fast_window": 2,
        "slow_window": 4,
        "entry_threshold_bps": 1.0,
        "exit_threshold_bps": 0.5,
        "qty": 10.0,
        "cooldown_s": 0.0,
    }
    ctx = DummyCtx(params)
    mr.init(ctx)
    # Warm up
    prices = [100.0, 100.0, 100.0, 100.0]
    ts = 0.0
    for price in prices:
        mr.on_tick(ctx, _tick(price, ts))
        ts += 1.0
    # Strategy may already have entered short on the rally; clear orders and reset position for guard test
    ctx.orders.clear()
    ctx._pos = 0.0
    ctx.set_state("target_pos", 0.0)

    # Price drops -> expect BUY to reach +qty
    mr.on_tick(ctx, _tick(98.0, ts))
    assert ctx.orders[-1]["side"] == "BUY"
    assert ctx.orders[-1]["qty"] == 10.0
    assert ctx.position() == 10.0

    # Revert near slow -> expect SELL to flatten
    ts += 1.0
    mr.on_tick(ctx, _tick(100.0, ts))
    assert ctx.orders[-1]["side"] == "SELL"
    assert ctx.orders[-1]["qty"] == 10.0
    assert ctx.position() == 0.0


def test_mean_reversion_trend_guard_blocks_entry():
    params = {
        "fast_window": 2,
        "slow_window": 4,
        "entry_threshold_bps": 2.0,
        "exit_threshold_bps": 1.0,
        "qty": 5.0,
        "cooldown_s": 0.0,
    }
    ctx = DummyCtx(params)
    mr.init(ctx)
    # Warm-up sequence that creates slow < fast
    prices = [100.0, 100.0, 100.0, 120.0]
    ts = 0.0
    for price in prices:
        mr.on_tick(ctx, _tick(price, ts))
        ts += 1.0

    # Strategy may already be short from the rally; reset tracking for guard evaluation
    ctx.orders.clear()
    ctx._pos = 0.0
    ctx.set_state("target_pos", 0.0)

    # Sharp drop should suggest long entry, but fast remains elevated above slow -> guard triggers (no order)
    mr.on_tick(ctx, _tick(101.0, ts))
    assert ctx.orders == []
    assert ctx.position() == 0.0
