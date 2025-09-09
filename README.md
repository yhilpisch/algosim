# algosim (MVP)

Real-time algorithmic trading simulator using ZeroMQ for inter-process messaging. This MVP includes:

- OU/Vasicek price simulator publishing ticks over ZMQ PUB
- Streamlit UI subscribing to ticks (CONFLATE=1) and plotting live price
- CLI to run the simulator (`sim run`)
- Strategy template scaffold (placeholder)

## Quickstart

- Create venv and install deps

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

- Start simulator (publishes to tcp://127.0.0.1:5555)

```
sim run --config configs/default.yaml
```

- In another terminal, start UI

```
streamlit run rt_sim/app_streamlit.py
```

You should see a live-updating price chart. Stop with Ctrl-C.

## Next steps

- Add broker, portfolio, and fills PUB stream
- Add strategy host process and PUSH/PULL orders
- Add recorder and metrics

