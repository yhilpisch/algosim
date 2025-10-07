# algosim — Real‑Time Algorithmic Trading Simulator (MVP, ZMQ)

<img src="https://hilpisch.com/tpq_logo.png" alt="The Python Quants — TPQ Logo" width="30%" />

algosim is a small, local-first teaching tool that simulates real‑time prices, lets you plug in tiny Python strategies, and streams events over ZeroMQ. A Streamlit UI provides controls and live views; a CLI supports headless runs and reproducible replays.

Author: Dr. Yves J. Hilpisch — The Python Quants GmbH

## Features (MVP)

- Simulator: OU/Vasicek price process (small moves + gentle upward drift), Poisson/fixed tick schedule
- Broker: MARKET fills with latency, slippage, commissions; publishes fills; simple position/cash tracking
- Transport: ZMQ PUB/SUB for ticks and fills; PUSH/PULL for orders (JSON)
- Streamlit App:
  - Tabs
    - Ticks: real-time Plotly chart with persistent streaming updates, trade overlays (green up-triangles for buys, red down-triangles for sells), optional text mode, and live tick stats
    - Fills / Orders: manual BUY/SELL on orders_push, scrollable fill history, contextual warnings
    - P&L: dense 6-per-row KPI grid (Position, Value, Cash, Equity, MaxDD, Sharpe, Exposure, Win Rate, Avg Trade P/L, Avg Hold, Dollar Exposure) + equity chart
    - Strategy: inline code editor for `strategy.py`, Start/Stop strategy host, PARAMS override (JSON), tick topic, conflation toggle, auto-flatten option on stop, and timestamped live logs in a scrollable pane
    - Admin: listener controls, diagnostics (3s receive/fill probes), status dashboard, local process management, metrics settings, config loader, strategy host registry tools, latest recording path hint
  - Start/Stop SUB quick controls remain in the sidebar for convenience
  - Status/diagnostics distinguish between listener-derived metrics and test probes
- Built-in Strategies:
  - Mean-reversion fade (`strategies/mean_reversion/strategy.py`): targets ±qty positions when price deviates from a slow SMA beyond configurable entry/exit bands, with trend-aware guardrails and cooldown
  - SMA crossover (`strategies/sma_crossover/strategy.py` / `strategies/sma_crossover/run_sma.py`)
- Recorder: captures ticks, orders, fills, config, and seed to `runs/<export>/<run_id>/` (JSONL + CSV) for deterministic replays (`sim report`, `sim replay`)
- Config: YAML (`configs/default.yaml`) incl. `portfolio.initial_cash` (default 100,000)

See `outline.md` for the full specification and roadmap.

## Quickstart

- Create a virtual environment and install dependencies

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

- Start simulator and broker (inline to see logs)

```
python -m rt_sim.cli run --config configs/default.yaml --inline
```

- In another terminal, start the UI

```
streamlit run rt_sim/app_streamlit.py
```

In the UI sidebar:
- Local Processes: Start local simulator and broker (if not already running)
- Status: verify ticks are flowing (ticks/sec > 0)
- Ticks tab: Chart (ISO timestamps) or Text mode
- Fills / Orders tab: send manual BUY/SELL; fills appear below
- P&L tab: Position | Cash | Equity (single line), live equity chart

### Strategy Host (via UI)

Use the Strategy tab to edit and run a strategy with the built‑in host:

- Strategy path: defaults to `strategies/mean_reversion/strategy.py` (resolved relative to project root); SMA crossover remains available at `strategies/sma_crossover/strategy.py`
- Load file / Save file: edit the file inline
- PARAMS override (JSON): e.g. `{ "fast_window": 10, "slow_window": 40, "entry_threshold_bps": 6, "exit_threshold_bps": 2, "qty": 20, "cooldown_s": 4 }`
- Strategy ID: topic used for fills (e.g., `sma1`)
- Tick topic: `X` (default asset) or empty to subscribe to all
- Start strategy host: launches a subprocess and shows Live Logs (written to `runs/strategy_host_*.log`)
- Stop strategy host: terminates the subprocess (optionally auto-flattens the position first)
- Stop ALL strategy hosts: sends SIGTERM to all tracked strategy host PIDs (`runs/strategy_hosts.json`)

The example strategy template implements price‑vs‑SMA crossover with a no‑trade band (threshold_bps) and a cooldown (min_interval_s) to limit churn.

### Recording & Replay

- Every `sim run` stores artifacts under `run.export_dir/<run_id>/` (default `runs/last/<run_id>`)
- Inspect a run with `python -m rt_sim.cli report runs/last/<run_id>` — prints counts and metadata
- Re-broadcast ticks using `python -m rt_sim.cli replay runs/last/<run_id> --speed 2.0 --echo`
- Artifacts include JSONL + CSV for ticks/orders/fills plus `config_used.yaml` and `meta.json` (seed + timestamps)

### Testing

- Install deps: `pip install -r requirements.txt`
- Run the suite: `python -m pytest`
- Coverage includes metrics, OU stepper, transport smoke tests, recorder persistence, and mean-reversion signal logic

## Strategy Runner (SMA Crossover)

Run a simple price vs SMA crossover strategy that places orders automatically over ZMQ:

```
python strategies/sma_crossover/run_sma.py \
  --config configs/default.yaml \
  --strategy-id sma1 \
  --window 100 \
  --qty 1 \
  --threshold-bps 15 \
  --min-interval-s 10
```

Useful options:
- `--print-each` prints every tick (warm‑up and px/SMA)
- `--report-sec N` prints tick counts every N seconds (default 1s)
- `--topic X` subscribe to a specific tick topic (default subscribes to all)
- `--no-conflate` process all ticks (default behavior already avoids conflation)

The app will display resulting fills and live P&L. Trades also appear on the live tick chart.

## Configuration Notes

- Edit `configs/default.yaml` for model params (e.g., `sigma`, `mu`), schedule, execution, endpoints, and `portfolio.initial_cash`.
- The app’s Config section (bottom of sidebar) resets Position/Cash/P&L to reflect the loaded config.
 - In the P&L tab, you can set the Sharpe annualization factor (e.g., trading seconds per year) in the sidebar under “Metrics Settings”.

## Troubleshooting

- No fills: start broker and simulator first; verify “Fills listener alive: True” and try “Test fills (3s)”.
- Orders deferred: broker prints “defer fill: no price yet” until it receives the first tick.
- Strategy runner sees 0 ticks: subscribe to all topics (`--topic ""`), avoid conflation, and wait for the runner’s “first tick …” message.
 - Strategy host via UI shows no logs: use the Strategy tab’s “Stop ALL strategy hosts”, start local simulator/broker, then Start strategy host again; logs are tailed from `runs/strategy_host_*.log`.

## Status (MVP)

- Implemented: OU simulator, broker with execution costs + portfolio snapshots, ZeroMQ transport, Streamlit UI (ticks/fills/P&L/strategy/admin), CLI (`run`, `run-strategy`, `new-strategy`, `report`, `replay`), SMA strategy host, SMA & mean-reversion example strategies, recorder exports, metrics helpers, and unit/smoke tests (metrics, transport, recorder, strategies)
- Next: headless evaluation workflow (`sim eval`) for deterministic comparisons, strategy linting (`sim doctor`), richer analytics/metrics dashboards, CI integration for tests, and additional strategy templates
