# algosim — Real‑Time Algorithmic Trading Simulator (MVP, ZMQ)

![The Python Quants — TPQ Logo](https://hilpisch.com/tpq_logo.png)

algosim is a small, local-first teaching tool that simulates real‑time prices, lets you plug in tiny Python strategies, and streams events over ZeroMQ. A Streamlit UI provides controls and live views; a CLI supports headless runs and reproducible replays.

Author: Dr. Yves J. Hilpisch — The Python Quants GmbH (with Codex & GPT-5)

## Overview

- OU/Vasicek price simulator with fixed or Poisson tick timings
- ZeroMQ transport: PUB/SUB for ticks and fills; PUSH/PULL for orders
- Streamlit UI: control + live visualization or plain text printing of ticks
- CLI: run the simulator and scaffold strategy templates
- Deterministic seeds and config in YAML

See `outline.md` for the full MVP specification and roadmap.

## Quickstart

- Create a virtual environment and install dependencies

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

- Start the simulator (inline to see tick prints)

```
python -m rt_sim.cli run --config configs/default.yaml --inline
```

- In another terminal, start the UI

```
streamlit run rt_sim/app_streamlit.py
```

In the UI sidebar: Load config → choose render mode (Text/Chart) → Start SUB.

## Status (MVP)

- Implemented: simulator, ZMQ transport, Streamlit subscriber, CLI, strategy template
- Next: broker + portfolio + fills, strategy host, recorder, metrics, tests

