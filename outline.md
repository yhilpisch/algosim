# Real-Time Algorithmic Trading Simulator — MVP Specification (ZMQ)

> **Purpose**: A teaching/illustration tool that simulates real-time pricing, lets users plug in tiny strategy scripts, executes orders, and visualizes P/L and live metrics in a web UI (Streamlit + Plotly). Runs locally first; designed to be CLI-and-LLM-friendly (Codex/GPT‑5). **This edition adopts ZeroMQ (pyzmq) for inter-process communication.**

The project/the app shall be called "algosim".

---

## 1) Goals & Non‑Goals

**Goals (MVP)**

* Real-time price stream from a configurable stochastic model (Vasicek/OU), with fixed or random inter-arrival times and a speed control.
* Streamlit web app for control + live visualization (price, indicators, trades, P/L, key metrics).
* Strategy plug-in via a tiny Python script following a clear, minimal API (hot-reload capability).
* Simple broker/execution with MARKET orders, configurable latency, slippage, and costs.
* Live portfolio & P/L tracking; rolling stats (drawdown, Sharpe, win rate, etc.).
* Deterministic replays via seeds; export run artifacts (events JSONL, trades CSV).
* CLI for headless runs and quick scaffolding.
* **ZeroMQ transport** for clean message flows (ticks, orders, fills), teachable and extensible.

**Non‑Goals (MVP)**

* Multi-asset universe, order book depth, L2 microstructure realism.
* Limit/stop orders, partial fills, margin models, corporate actions.
* Distributed deployments; external data feeds (can be added later thanks to ZMQ).

---

## 2) Primary User Stories

* **Educator**: “Start/stop simulations at different speeds, tweak model/fees, and show live how a strategy reacts.”
* **Learner**: “Write a 20–50 line strategy (e.g., SMA cross), hot-reload it, and watch P/L + stats update.”
* **Researcher**: “Run deterministic headless replays, export trades/metrics, and compare settings quickly.”

---

## 3) System Architecture (MVP, ZeroMQ)

**Process model**: Single OS process supervising separate **simulator**, **broker**, **UI**, and **strategy sandbox** components communicating over **ZeroMQ** sockets (pyzmq). Default endpoints use `tcp://127.0.0.1` for local development/teaching. The strategy runs in a **separate process** for isolation. A minimal in‑proc fallback may exist for unit tests, but the MVP runs on ZMQ.

**Core components**

1. **Market Simulator**: Generates price ticks using Vasicek/OU; schedules next tick on fixed Δt or Poisson arrivals.
2. **Transport (ZeroMQ)**: PUB/SUB and PUSH/PULL sockets for ticks, orders, and fills using `tcp://127.0.0.1` endpoints.
3. **Strategy Sandbox**: Separate Python process executing `strategy.py` (API below), consuming `Tick` events, emitting `Order` objects via ZMQ.
4. **Broker/Execution**: Applies latency/slippage/costs; returns fills; updates portfolio & cash; pushes PnL/metrics via ZMQ.
5. **Portfolio & Risk Engine**: Mark‑to‑market, realized/unrealized P/L, exposure, drawdown, rolling Sharpe, trade stats.
6. **UI (Streamlit)**: Controls + Plotly charts; status panels; live logs; file export buttons.
7. **Recorder**: Writes JSONL event log + CSVs for prices and trades; captures config/seed for reproducibility.

**Inter‑component IPC (MVP, ZeroMQ)**

* **Ticks**: `PUB` (simulator) → `SUB` (strategies, UI) at `tcp://127.0.0.1:5555` with **topic = asset\_id** (e.g., `"X"`). UI subscriber uses **`CONFLATE=1`** to render only the most recent tick.
* **Orders**: `PUSH` (strategy) → `PULL` (broker) at `tcp://127.0.0.1:5556`.
* **Fills/Positions**: `PUB` (broker) → `SUB` (strategies, UI) at `tcp://127.0.0.1:5557` with **topic = strategy\_id** (UI subscribes to all).
* **Serialization**: **JSON**. Each payload includes `seq`, `ts_sim`, `ts_wall`, `run_id`, and relevant IDs.
* **Admin/control**: **in‑proc** for MVP (no ZMQ REQ/REP). Health via optional PUB heartbeats later.

---

## 3a) Transport (ZeroMQ) — Defaults & Liveness

* **Endpoints (TCP local)**

  * `ticks_pub`:   `tcp://127.0.0.1:5555`
  * `orders_push`: `tcp://127.0.0.1:5556`
  * `fills_pub`:   `tcp://127.0.0.1:5557`
* **HWM**: `SNDHWM/RCVHWM = 20000` (teaching default). UI tick subscriber sets **`CONFLATE=1`** to always show the freshest price and avoid backpressure.
* **Determinism**: rely on included `seq` and `ts_sim` in every message to detect gaps and enable exact replays.
* **Reconnects**: ZMQ handles transient disconnects; the UI displays gap counts if `seq` jumps.

---

## 4) Data & Event Schemas (Pydantic models)

**Tick**

```json
{
  "ts": "ISO-8601 or ns epoch",
  "seq": 12345,
  "price": 101.2345,
  "model_state": {"x": 0.12, "dt": 0.2, "kappa": 0.8, "theta": 0.0, "sigma": 0.1},
  "asset_id": "X",
  "run_id": "2025-09-09T10:00:00Z"
}
```

**Order (MARKET only in MVP)**

```json
{
  "ts": "…",
  "strategy_id": "sma1",
  "side": "BUY" | "SELL",
  "qty": 1.0,
  "tag": "optional string from strategy",
  "run_id": "…"
}
```

**Fill**

```json
{
  "ts": "…",
  "strategy_id": "sma1",
  "side": "BUY" | "SELL",
  "qty": 1.0,
  "fill_price": 101.25,
  "slippage_bps": 1.0,
  "commission": 0.50,
  "latency_ms": 50,
  "order_tag": "…",
  "run_id": "…"
}
```

**Position/P\&L snapshot**

```json
{
  "ts": "…",
  "pos": 3.0,
  "cash": 9993.25,
  "last_price": 101.25,
  "unrealized": -12.50,
  "realized": 8.75,
  "equity": 9989.50,
  "run_id": "…"
}
```

---

## 5) Market Simulator (Vasicek/OU)

**Model**: $dX_t = \kappa(\theta - X_t)\,dt + \sigma\,dW_t$

**Discretization** (exact OU or Euler; MVP uses exact):

* Exact step for Δt: $X_{t+Δ} = \theta + (X_t - \theta) e^{-\kappa Δ} + \sigma \sqrt{\tfrac{1 - e^{-2\kappa Δ}}{2\kappa}}\, \varepsilon$, $\varepsilon\sim\mathcal{N}(0,1)$.

**Mapping to price**:

* Option A (default): $P_t = P_0 \cdot e^{X_t}$ for positivity.
* Option B: $P_t = P_0 + X_t$ with floor at $>0$ (teaching: mean reversion around $P_0$).

**Inter‑arrival times**:

* **Fixed**: constant Δt (e.g., 200 ms).
* **Random**: exponential with mean μ (Poisson arrivals). UI shows a **speed** slider that rescales μ.

**Controls (UI + YAML)**: `kappa, theta, sigma, P0, x0, dt_mode={fixed,poisson}, dt_fixed_ms, dt_mean_ms, seed`.

---

## 6) Strategy Plug‑in API (tiny, file‑based)

**File**: `strategies/<name>/strategy.py`

**Minimal API**

```python
# strategy.py (MVP API)

NAME = "SMA Crossover"
PARAMS = {"fast": 20, "slow": 50, "qty": 1}

def init(ctx):
    """Called once on strategy start. `ctx` exposes:
    - ctx.get_param(name, default)
    - ctx.set_state(key, value) / ctx.get_state(key, default)
    - ctx.indicator.SMA(window)
    - ctx.place_market_order(side: str, qty: float, tag: str | None)
    """

def on_tick(ctx, tick):
    """Called on each incoming Tick. Compute signals and place orders.
    `tick` is a dict-like (`tick["price"]`, `tick["ts"]`)."""

def on_stop(ctx):
    """Called on strategy shutdown (optional)."""
```

**Hot‑reload**: Strategy process restarts upon file change (debounced). State may be optionally rehydrated from last snapshot (MVP: fresh start).

**Safety**: Separate process; limited APIs via `ctx`; no network/file access in MVP beyond strategy folder (best‑effort; trust model is local teaching).

---

## 7) Broker/Execution (MVP)

* **Order type**: MARKET only.
* **Latency**: constant `latency_ms` before fill.
* **Slippage**: `slippage_bps` applied to last price toward worse side.
* **Costs**: `commission_fixed` + `commission_bps * notional`.
* **Fill rule**: All‑or‑nothing, immediate after latency.
* **Positioning**: Net quantity; short allowed; no margin model (equity can go negative in MVP).

---

## 8) Portfolio, P/L & Metrics

* **P/L**: Mark‑to‑market each tick; realized on fills; equity = cash + pos × price.
* **Rolling metrics** (configurable window):

  * Max drawdown & duration (from equity curve)
  * Sharpe (annualized from per‑tick/second returns with user‑set conversion)
  * Win rate (# profitable trades / total)
  * Avg trade P/L, avg hold time
  * Exposure (% time pos ≠ 0)
* **Snapshot frequency**: every N ticks and on events.

---

## 9) Streamlit UI (MVP)

**Layout**

* **Sidebar**: simulation controls (Start/Pause/Reset), seed, speed, Δt mode, Vasicek params, slippage/costs/latency, strategy picker, hot‑reload toggle, export.
* **Top KPIs**: Equity, P/L (realized/unrealized), Drawdown, Sharpe, Exposure, Win rate, Trades.
* **Main charts (Plotly)**:

  1. Price + indicators + trade markers (arrows for BUY/SELL)
  2. Equity/P\&L curve
  3. Histogram of returns (last N)
* **Tables**: Recent trades (time, side, qty, price, cost, tag); Config snapshot.
* **Status**: Event rate (ticks/sec), **ZMQ** socket status (connected, HWM), strategy health.

**Update cadence**: UI throttled to \~10 fps; **ticks SUB uses `CONFLATE=1`** so it renders the latest tick without back‑pressuring the simulator. Background loops process all fills/PNL updates at full speed.

---

## 10) CLI Tools (for local + LLM demos)

* `sim run --config config.yaml [--headless]` → run simulation; headless streams logs/metrics to stdout.
* `sim new-strategy my_sma` → scaffold `strategies/my_sma/` with template.
* `sim eval --config config.yaml --seed 42 --duration 60s --out out/` → deterministic replay; writes CSV/JSONL.
* `sim doctor strategies/my_sma/strategy.py` → static checks for API compliance.

---

## 11) Configuration (YAML)

```yaml
transport:
  type: zmq
  endpoints:
    ticks_pub:   tcp://127.0.0.1:5555
    orders_push: tcp://127.0.0.1:5556
    fills_pub:   tcp://127.0.0.1:5557
  hwm:
    ticks_pub: 20000
    orders:    20000
    fills_pub: 20000
  conflate:
    ui_ticks_sub: true

model:
  type: vasicek
  kappa: 0.8
  theta: 0.0
  sigma: 0.2
  P0: 100.0
  x0: 0.0
schedule:
  mode: poisson   # fixed | poisson
  dt_fixed_ms: 200
  dt_mean_ms: 150
  speed: 1.0      # multiplier
execution:
  latency_ms: 50
  slippage_bps: 1.0
  commission_bps: 0.5
  commission_fixed: 0.0
strategy:
  path: strategies/sma_crossover/strategy.py
  params: {fast: 20, slow: 50, qty: 1}
run:
  seed: 42
  duration_s: 0     # 0 means unlimited until Stop
  export_dir: runs/last
ui:
  throttle_fps: 10
```

---

## 12) Logging & Persistence

* **Events**: JSONL (`runs/<id>/events.jsonl`) with all Tick/Order/Fill/PnLUpdate.
* **Trades**: CSV (`runs/<id>/trades.csv`).
* **Config**: Store effective YAML + git commit (if repo) for provenance.
* **Repro**: `sim eval` replays with same seed and config.

---

## 13) Testing Strategy (PyTest)

* **Unit**: OU step exactness (mean/variance vs theory), SMA signals, P/L arithmetic, slippage/costs application.
* **Property-based**: No NaN equity; equity continuity; idempotent replay with fixed seed.
* **Integration**: End‑to‑end 5‑second run asserting trade count and metric bounds.
* **Transport smoke**: ZMQ sockets bind/connect; SUB receives ticks; PULL receives orders; PUB/PUB topics filter correctly.

---

## 14) Performance & Reliability

* Async event loop; **ZeroMQ** sockets sized with HWM; UI ticks use conflation to avoid rendering backlog.
* Bounded order queues (ZMQ PUSH/PULL) with metrics; warn on slow strategy (> latency budget).
* Clean shutdown on Ctrl‑C; flush logs; write final snapshot; ZMQ context terminated cleanly.

---

## 15) Security & Sandboxing (local teaching)

* Strategy executed in a separate process with a constrained API.
* MVP allows local imports; later: optional restricted mode (no network/files) via audit hooks or subprocess policy.
* **Local-only transport** via `127.0.0.1`; no external exposure by default.

---

## 16) Milestones & Acceptance Criteria

**M0 – Skeleton (Day 1)**

* Repo scaffold, env, CLI bootstrap; black/ruff/pytest configured; ZMQ context and sockets wired.

**M1 – Simulator + UI (Day 2–3)**

* OU price stream; fixed/poisson intervals; Streamlit chart updates; start/pause/reset; ticks published via ZMQ.

**M2 – Strategy Plug‑in (Day 4)**

* Strategy process, API (`init/on_tick/on_stop`), SMA example; hot‑reload; orders via PUSH/PULL.

**M3 – Broker & P/L (Day 5)**

* MARKET fills with latency/slippage/costs; live P/L; trades table; fills PUB to UI/strategy.

**M4 – Metrics & Export (Day 6)**

* Rolling stats; JSONL/CSV export; repro via seed; `sim eval` CLI.

**M5 – Polish (Day 7)**

* Error handling, UI KPIs (socket health/HWM), `sim doctor`, docs.

**Acceptance (MVP)**

* Run locally: start sim, load SMA strategy, observe trades & P/L updating in UI, export artifacts, replay deterministically via CLI and obtain identical trades.

---

## 17) Project Layout

```
rt-sim/
  README.md
  requirements.txt
  pyproject.toml
  rt_sim/
    __init__.py
    app_streamlit.py
    cli.py
    transport.py      # ZMQ transport abstraction
    models.py         # pydantic schemas
    simulator.py
    strategy_host.py  # separate process stub
    broker.py
    portfolio.py
    metrics.py
    recorder.py
    utils.py
  strategies/
    sma_crossover/
      strategy.py
  configs/
    default.yaml
  runs/
    …
  tests/
    test_ou.py
    test_broker.py
    test_strategy_api.py
    test_pnl.py
    test_transport.py
```

---

## 18) Dependencies & Environment

* Python 3.11+
* `streamlit`, `plotly`, `numpy`, `pandas`, `pydantic`, `scipy`, `pyyaml`, `click`, `pytest`, `ruff`, `black`, **`pyzmq`**.
* Optional: `uvloop` (non‑Windows).

**Setup**

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
streamlit run rt_sim/app_streamlit.py
```

---

## 19) Example Strategy Template (SMA Crossover)

```python
NAME = "SMA Crossover"
PARAMS = {"fast": 20, "slow": 50, "qty": 1}

def init(ctx):
    f, s = ctx.get_param("fast", 20), ctx.get_param("slow", 50)
    ctx.set_state("fast", f)
    ctx.set_state("slow", s)
    ctx.set_state("qty", ctx.get_param("qty", 1))
    ctx.fast = ctx.indicator.SMA(f)
    ctx.slow = ctx.indicator.SMA(s)

def on_tick(ctx, tick):
    p = tick["price"]
    f, s = ctx.fast.update(p), ctx.slow.update(p)
    if f is None or s is None:
        return
    pos = ctx.position()  # current signed qty
    if f > s and pos <= 0:
        ctx.place_market_order("BUY", abs(pos) + ctx.get_state("qty", 1), tag="bullish")
    elif f < s and pos >= 0:
        ctx.place_market_order("SELL", abs(pos) + ctx.get_state("qty", 1), tag="bearish")

def on_stop(ctx):
    pass
```

*(Template provided for API illustration; actual code generated in implementation stage.)*

---

## 20) Future Extensions (Post‑MVP)

* Alternative models (GBM, Heston-lite, jumps), regime switching.
* Multi‑asset, correlations; portfolio constraints; risk overlays.
* Limit/stop orders, partial fills, VWAP/TWAP execution.
* Latency distributions; network jitter; dropped ticks.
* Strategy timeouts, resource quotas; richer sandboxing.
* WebSocket event stream; Electron/desktop packaging.

---

## 21) LLM Demo Notes (Codex/GPT‑5 CLI)

* Prompts to generate: new strategies, metric modules, or model variants.
* Use `sim new-strategy` then ask LLM to implement `on_tick` logic per the API.
* Use `sim eval` with fixed seeds to compare LLM‑generated variants reproducibly.
* Showcase **ZMQ patterns** (PUB/SUB, PUSH/PULL) in prompts so codegen targets the correct transport calls.

---

## 22) Open Design Questions (tracked in repo issues)

1. Prefer exact OU step (scipy) or Euler for pedagogical transparency?
2. Default price mapping: exponential vs additive?
3. Hot-reload state rehydration: reset vs checkpoint restore?
4. Commission/slippage defaults for teaching (bps values)?
5. Rolling metrics windows & annualization factor (ticks→time conversion)?
6. Strategy sandbox restrictions (file/network) in MVP vs later?
7. Deterministic wall‑clock vs simulated clock semantics for latency?
8. First multi-asset extension (2 correlated OU) and multi-topic ZMQ layout?
