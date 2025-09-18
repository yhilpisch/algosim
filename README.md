# algosim — Real‑Time Algorithmic Trading Simulator (MVP, ZMQ)

<img src="https://hilpisch.com/tpq_logo.png" alt="The Python Quants — TPQ Logo" width="30%" />

algosim is a small, local-first teaching tool that simulates real‑time prices, lets you plug in tiny Python strategies, and streams events over ZeroMQ. A Streamlit UI provides controls and live views; a CLI supports headless runs and reproducible replays.

Author: Dr. Yves J. Hilpisch — The Python Quants GmbH

## Features (MVP)

- Simulator: OU/Vasicek price process (small moves + gentle upward drift), Poisson/fixed tick schedule
- Broker: MARKET fills with latency, slippage, commissions; publishes fills; simple position/cash tracking
- Transport: ZMQ PUB/SUB for ticks and fills; PUSH/PULL for orders (JSON)
- Streamlit App:
  - Tabs: Ticks (Chart/Text), Fills / Orders (manual BUY/SELL), P&L (Position, Cash, Equity + chart)
  - Status: endpoint info, ticks/sec, seq gaps, listener health
  - Test buttons: “Test receive (ticks)” and “Test fills” (1‑second checks)
  - Refresh controls: auto‑refresh toggle + rate; manual refresh
  - Local Processes: start/stop simulator and broker from UI
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

## Troubleshooting

- No fills: start broker and simulator first; verify “Fills listener alive: True” and try “Test fills (1s)”.
- Orders deferred: broker prints “defer fill: no price yet” until it receives the first tick.
- Strategy runner sees 0 ticks: subscribe to all topics (`--topic ""`), avoid conflation, and wait for the runner’s “first tick …” message.

## Status (MVP)

- Implemented: simulator, ZMQ transport, Streamlit subscriber, CLI, strategy template
- Next: broker + portfolio + fills, strategy host, recorder, metrics, tests
