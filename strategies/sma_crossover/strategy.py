NAME = "SMA Crossover"
PARAMS = {"fast": 20, "slow": 50, "qty": 1}


def init(ctx):
    ctx.fast = ctx.indicator.SMA(ctx.get_param("fast", 20))
    ctx.slow = ctx.indicator.SMA(ctx.get_param("slow", 50))
    ctx.set_state("qty", ctx.get_param("qty", 1))


def on_tick(ctx, tick):
    p = tick["price"]
    f = ctx.fast.update(p)
    s = ctx.slow.update(p)
    if f is None or s is None:
        return
    pos = ctx.position()
    if f > s and pos <= 0:
        ctx.place_market_order("BUY", abs(pos) + ctx.get_state("qty", 1), tag="bullish")
    elif f < s and pos >= 0:
        ctx.place_market_order("SELL", abs(pos) + ctx.get_state("qty", 1), tag="bearish")


def on_stop(ctx):
    pass

