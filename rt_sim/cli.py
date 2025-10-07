from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from typing import Optional

import click
import json
import time
import yaml

from .simulator import run as run_simulator
from .transport import Transport
from .broker import run as run_broker
from .strategy_host import run as run_strategy_host
from .utils import load_config, new_run_id, seed_everything
from .recorder import prepare_run_directory, load_run_summary


def _sim_entry(cfg: dict, run_id: str, export_dir: str | None) -> None:
    """Top-level simulator process entry (must be picklable for multiprocessing)."""
    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_simulator(cfg, t, run_id, export_dir=export_dir)


def _broker_entry(cfg: dict, run_id: str, export_dir: str | None) -> None:
    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_broker(cfg, t, run_id, export_dir=export_dir)


@click.group(name="sim")
def cli() -> None:
    """algosim command-line tools"""


@cli.command("run")
@click.option("--config", "config_path", type=click.Path(exists=True), default="configs/default.yaml")
@click.option("--headless/--no-headless", default=True, help="Run without UI")
@click.option("--inline/--no-inline", default=False, help="Run in current process for debugging")
def cmd_run(config_path: str, headless: bool, inline: bool) -> None:
    """Run the simulator (and optionally headless only). UI is launched separately via streamlit."""
    cfg = load_config(config_path)
    seed_everything(int(cfg["run"]["seed"]))
    run_id = new_run_id()
    export_base = Path(cfg.get("run", {}).get("export_dir", "runs/last"))
    run_dir = prepare_run_directory(export_base, run_id, cfg)

    if inline:
        click.echo(f"Simulator+Broker starting inline (run_id={run_id}). Press Ctrl-C to stop.")
        click.echo(f"Recording artifacts to {run_dir}")
        try:
            # Run both in current process: simulator on a child process, broker here
            sp = mp.Process(target=_sim_entry, args=(cfg, run_id, str(run_dir)), daemon=True)
            sp.start()
            _broker_entry(cfg, run_id, str(run_dir))
        except KeyboardInterrupt:
            click.echo("Stopping...")
    else:
        ps = mp.Process(target=_sim_entry, args=(cfg, run_id, str(run_dir)), daemon=True)
        pb = mp.Process(target=_broker_entry, args=(cfg, run_id, str(run_dir)), daemon=True)
        ps.start(); pb.start()
        click.echo(f"Simulator started (pid={ps.pid}); Broker started (pid={pb.pid}). Ctrl-C to stop.")
        click.echo(f"Recording artifacts to {run_dir}")
        try:
            ps.join(); pb.join()
        except KeyboardInterrupt:
            click.echo("Stopping...")
        finally:
            for proc in (ps, pb):
                if proc.is_alive():
                    proc.terminate(); proc.join(timeout=1)


@cli.command("new-strategy")
@click.argument("name")
def cmd_new_strategy(name: str) -> None:
    """Scaffold a new strategy folder with template."""
    base = Path("strategies") / name
    base.mkdir(parents=True, exist_ok=True)
    strat = base / "strategy.py"
    if strat.exists():
        click.echo(f"Strategy already exists at {strat}")
        return
    strat.write_text(
        (
            "NAME = \"SMA Crossover\"\n"
            "PARAMS = {\"fast\": 20, \"slow\": 50, \"qty\": 1}\n\n"
            "def init(ctx):\n    ctx.fast = ctx.indicator.SMA(ctx.get_param('fast', 20))\n    ctx.slow = ctx.indicator.SMA(ctx.get_param('slow', 50))\n    ctx.set_state('qty', ctx.get_param('qty', 1))\n\n"
            "def on_tick(ctx, tick):\n    p = tick['price']\n    f = ctx.fast.update(p)\n    s = ctx.slow.update(p)\n    if f is None or s is None:\n        return\n    pos = ctx.position()\n    if f > s and pos <= 0:\n        ctx.place_market_order('BUY', abs(pos) + ctx.get_state('qty', 1), tag='bullish')\n    elif f < s and pos >= 0:\n        ctx.place_market_order('SELL', abs(pos) + ctx.get_state('qty', 1), tag='bearish')\n\n"
            "def on_stop(ctx):\n    pass\n"
        )
    )
    click.echo(f"Created {strat}")


@cli.command("run-strategy")
@click.option("--config", "config_path", type=click.Path(exists=True), default="configs/default.yaml")
@click.option("--path", "strategy_path", type=click.Path(exists=True), required=True, help="Path to strategy.py")
@click.option("--id", "strategy_id", default=None, help="Override strategy_id/topic for fills")
@click.option("--params", "params_json", default=None, help="JSON object to override strategy PARAMS")
@click.option("--topic", default="", help="Tick topic to subscribe (empty=all)")
@click.option("--conflate/--no-conflate", default=False)
def cmd_run_strategy(config_path: str, strategy_path: str, strategy_id: str | None, params_json: str | None, topic: str, conflate: bool) -> None:
    """Run a strategy module via the built-in host with ctx API."""
    cfg = load_config(config_path)
    run_id = new_run_id()
    try:
        params = json.loads(params_json) if params_json else None
    except Exception as e:
        raise click.ClickException(f"Invalid --params JSON: {e}")

    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    click.echo(
        f"Starting strategy host (id={strategy_id or 'auto'}) using {strategy_path} | topic='{topic or '*'}' conflate={conflate}"
    )
    try:
        run_strategy_host(cfg, t, run_id, strategy_path, strategy_id=strategy_id, params_override=params, topic=topic, conflate=conflate)
    except KeyboardInterrupt:
        click.echo("Stopping strategy host...")


@cli.command("report")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
def cmd_report(run_dir: str) -> None:
    """Summarize a recorded run (counts, metadata)."""
    summary = load_run_summary(run_dir)
    click.echo(f"Run directory: {summary.get('run_dir', run_dir)}")
    if summary.get("run_id"):
        click.echo(f"Run ID: {summary['run_id']}")
    if summary.get("created_utc"):
        click.echo(f"Created (UTC): {summary['created_utc']}")
    if summary.get("seed") is not None:
        click.echo(f"Seed: {summary['seed']}")
    if summary.get("meta_error"):
        click.echo(f"Meta warning: {summary['meta_error']}")
    click.echo(
        f"Events → ticks: {summary.get('ticks_count', 0)}, orders: {summary.get('orders_count', 0)}, fills: {summary.get('fills_count', 0)}"
    )


@cli.command("replay")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False))
@click.option("--speed", default=1.0, show_default=True, help="Playback speed multiplier (1.0 = real-time)")
@click.option("--publish/--no-publish", default=True, show_default=True, help="Publish ticks over ZMQ during replay")
@click.option("--echo/--no-echo", default=False, show_default=True, help="Print each tick to stdout during replay")
def cmd_replay(run_dir: str, speed: float, publish: bool, echo: bool) -> None:
    """Replay recorded ticks (optionally rebroadcast over ZMQ)."""
    run_path = Path(run_dir)
    ticks_path = run_path / "ticks.jsonl"
    if not ticks_path.exists():
        raise click.ClickException(f"No ticks.jsonl found in {run_dir}")

    cfg_path = run_path / "config_used.yaml"
    cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    transport_cfg = cfg.get("transport", {})
    endpoints = transport_cfg.get("endpoints", {})
    ticks_ep = endpoints.get("ticks_pub", "tcp://127.0.0.1:5555")

    pub = None
    if publish:
        t = Transport(
            hwm_ticks=int(transport_cfg.get("hwm", {}).get("ticks_pub", 20000) or 20000),
            hwm_orders=int(transport_cfg.get("hwm", {}).get("orders", 20000) or 20000),
            hwm_fills=int(transport_cfg.get("hwm", {}).get("fills_pub", 20000) or 20000),
        )
        pub = t.bind_pub(ticks_ep, kind="ticks")
        click.echo(f"Publishing replayed ticks to {ticks_ep}")

    prev_ts: Optional[float] = None
    count = 0
    with ticks_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            tick = json.loads(line)
            ts_sim = float(tick.get("ts_sim", 0.0))
            if prev_ts is not None and speed > 0:
                delay = max(0.0, (ts_sim - prev_ts) / max(speed, 1e-6))
                if delay:
                    time.sleep(delay)
            if publish and pub is not None:
                topic = tick.get("asset_id", "")
                Transport.send_json(pub, topic or "", tick)
            if echo:
                price = tick.get("price")
                click.echo(f"[{tick.get('seq')}] ts_sim={ts_sim:.3f} price={price}")
            prev_ts = ts_sim
            count += 1

    click.echo(f"Replayed {count} ticks from {ticks_path}")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
