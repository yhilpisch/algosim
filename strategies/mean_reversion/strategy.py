NAME = "Mean Reversion Fade"
PARAMS = {
    "fast_window": 10,
    "slow_window": 40,
    "entry_threshold_bps": 6.0,
    "exit_threshold_bps": 1.5,
    "qty": 25,
    "cooldown_s": 3.0,
}


def init(ctx):
    ctx.fast = ctx.indicator.SMA(int(ctx.get_param("fast_window", 10)))
    ctx.slow = ctx.indicator.SMA(int(ctx.get_param("slow_window", 40)))
    ctx.set_state("entry_threshold_bps", float(ctx.get_param("entry_threshold_bps", 6.0)))
    ctx.set_state("exit_threshold_bps", float(ctx.get_param("exit_threshold_bps", 1.5)))
    ctx.set_state("qty", float(ctx.get_param("qty", 25)))
    ctx.set_state("cooldown_s", float(ctx.get_param("cooldown_s", 3.0)))
    ctx.set_state("target_pos", 0.0)
    ctx.set_state("last_trade_ts", 0.0)


def on_tick(ctx, tick):
    price = float(tick["price"])
    fast = ctx.fast.update(price)
    slow = ctx.slow.update(price)
    if fast is None or slow is None or slow <= 0.0:
        return

    deviation_bps = (price - slow) / slow * 10000.0
    entry = float(ctx.get_state("entry_threshold_bps", 6.0))
    exit_band = float(ctx.get_state("exit_threshold_bps", entry / 2.0))

    target = float(ctx.get_state("target_pos", 0.0))
    base_qty = float(ctx.get_state("qty", 25.0))

    if deviation_bps <= -entry:
        target = base_qty
    elif deviation_bps >= entry:
        target = -base_qty
    elif abs(deviation_bps) <= exit_band:
        target = 0.0

    trend_bps = (fast - slow) / slow * 10000.0
    if target > 0 and trend_bps > entry:
        target = 0.0
    elif target < 0 and trend_bps < -entry:
        target = 0.0

    ctx.set_state("target_pos", target)

    pos = float(ctx.position())
    diff = target - pos
    now = float(tick.get("ts_wall", 0.0))
    last_trade_ts = float(ctx.get_state("last_trade_ts", 0.0))
    cooldown = float(ctx.get_state("cooldown_s", 3.0))
    if abs(diff) < 1e-9 or (now - last_trade_ts) < cooldown:
        return

    side = "BUY" if diff > 0 else "SELL"
    ctx.place_market_order(side, abs(diff), tag=f"mr_target_{target:+.1f}_{deviation_bps:.2f}bps")
    ctx.set_state("last_trade_ts", now)


def on_stop(ctx):
    pass
