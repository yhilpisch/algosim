# This is the SMA Strategy file.

NAME = "Price vs SMA Crossover"
PARAMS = {"window": 50, "qty": 100, "threshold_bps": 10.0, "min_interval_s": 5.0}


def init(ctx):
    w = int(ctx.get_param("window", 50))
    ctx.sma = ctx.indicator.SMA(w)
    ctx.set_state("qty", float(ctx.get_param("qty", 1)))
    ctx.set_state("threshold_bps", float(ctx.get_param("threshold_bps", 10.0)))
    ctx.set_state("min_interval_s", float(ctx.get_param("min_interval_s", 5.0)))
    ctx.set_state("last_side", None)
    ctx.set_state("last_trade_ts", 0.0)


def on_tick(ctx, tick):
    p = float(tick["price"])  # current price
    sma = ctx.sma.update(p)
    if sma is None:
        return  # warm-up
    diff_bps = (p - sma) / max(1e-12, sma) * 10000.0
    thr = float(ctx.get_state("threshold_bps", 10.0))
    if abs(diff_bps) < thr:
        return
    want = "LONG" if diff_bps > 0 else "SHORT"
    last = ctx.get_state("last_side")
    now = float(tick.get("ts_wall", 0.0))
    last_trade_ts = float(ctx.get_state("last_trade_ts", 0.0))
    min_gap = float(ctx.get_state("min_interval_s", 5.0))
    pos = float(ctx.position())
    qty = float(ctx.get_state("qty", 1))

    if last is None:
        ctx.set_state("last_side", want)
        return

    if want != last and (now - last_trade_ts) >= min_gap:
        if want == "LONG" and pos <= 0:
            order_qty = abs(pos) + qty
            ctx.place_market_order("BUY", order_qty, tag=f"sma_up_{thr:.1f}bps")
            ctx.set_state("last_trade_ts", now)
            ctx.set_state("last_side", want)
        elif want == "SHORT" and pos >= 0:
            order_qty = abs(pos) + qty
            ctx.place_market_order("SELL", order_qty, tag=f"sma_dn_{thr:.1f}bps")
            ctx.set_state("last_trade_ts", now)
            ctx.set_state("last_side", want)


def on_stop(ctx):
    pass
