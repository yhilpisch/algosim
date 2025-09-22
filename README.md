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
    - Ticks: Chart with ISO timestamps or plain Text; message rate; seq gaps
    - Fills / Orders: manual BUY/SELL on orders_push; live fills list
    - P&L: compact grid of KPIs (Position qty/value, Cash, Equity, MaxDD, Sharpe, Exposure, Win Rate, Avg Trade P/L, Avg Hold, Dollar Exposure) + equity chart
    - Strategy: inline code editor for `strategy.py`, Start/Stop strategy host, PARAMS override (JSON), tick topic, conflation toggle, and live logs
  - Status: endpoint info, ticks/sec, seq gaps, listener health; counts for received ticks/fills
  - Test buttons: “Test receive (ticks)” and “Test fills” (1‑second checks)
  - Refresh controls: auto‑refresh toggle + rate; manual refresh
  - Local Processes: start/stop local simulator and broker from UI
  - Strategy Hosts management: track PIDs and “Stop ALL strategy hosts” (cleans up after hard reloads)
- Strategy Runner: SMA crossover (`strategies/sma_crossover/run_sma.py`) with hysteresis and min-interval
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

- Strategy path: defaults to `strategies/sma_crossover/strategy.py` (resolved relative to project root)
- Load file / Save file: edit the file inline
- PARAMS override (JSON): e.g. `{ "window": 30, "qty": 1, "threshold_bps": 5, "min_interval_s": 3 }`
- Strategy ID: topic used for fills (e.g., `sma1`)
- Tick topic: `X` (default asset) or empty to subscribe to all
- Start strategy host: launches a subprocess and shows Live Logs (written to `runs/strategy_host_*.log`)
- Stop strategy host: terminates the subprocess
- Stop ALL strategy hosts: sends SIGTERM to all tracked strategy host PIDs (`runs/strategy_hosts.json`)

The example strategy template implements price‑vs‑SMA crossover with a no‑trade band (threshold_bps) and a cooldown (min_interval_s) to limit churn.

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

The app will display resulting fills and live P&L.

## Configuration Notes

- Edit `configs/default.yaml` for model params (e.g., `sigma`, `mu`), schedule, execution, endpoints, and `portfolio.initial_cash`.
- The app’s Config section (bottom of sidebar) resets Position/Cash/P&L to reflect the loaded config.
 - In the P&L tab, you can set the Sharpe annualization factor (e.g., trading seconds per year) in the sidebar under “Metrics Settings”.

## Troubleshooting

- No fills: start broker and simulator first; verify “Fills listener alive: True” and try “Test fills (1s)”.
- Orders deferred: broker prints “defer fill: no price yet” until it receives the first tick.
- Strategy runner sees 0 ticks: subscribe to all topics (`--topic ""`), avoid conflation, and wait for the runner’s “first tick …” message.
 - Strategy host via UI shows no logs: use the Strategy tab’s “Stop ALL strategy hosts”, start local simulator/broker, then Start strategy host again; logs are tailed from `runs/strategy_host_*.log`.

## Status (MVP)

- Implemented: simulator, ZMQ transport, Streamlit subscriber, CLI, strategy template
- Next: broker + portfolio + fills, strategy host, recorder, metrics, tests
