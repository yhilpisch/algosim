from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from typing import Optional

import click
import json

from .simulator import run as run_simulator
from .transport import Transport
from .broker import run as run_broker
from .strategy_host import run as run_strategy_host
from .utils import load_config, new_run_id, seed_everything


def _sim_entry(cfg: dict, run_id: str) -> None:
    """Top-level simulator process entry (must be picklable for multiprocessing)."""
    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_simulator(cfg, t, run_id)


def _broker_entry(cfg: dict, run_id: str) -> None:
    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_broker(cfg, t, run_id)


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

    if inline:
        click.echo(f"Simulator+Broker starting inline (run_id={run_id}). Press Ctrl-C to stop.")
        try:
            # Run both in current process: simulator on a child process, broker here
            sp = mp.Process(target=_sim_entry, args=(cfg, run_id), daemon=True)
            sp.start()
            _broker_entry(cfg, run_id)
        except KeyboardInterrupt:
            click.echo("Stopping...")
    else:
        ps = mp.Process(target=_sim_entry, args=(cfg, run_id), daemon=True)
        pb = mp.Process(target=_broker_entry, args=(cfg, run_id), daemon=True)
        ps.start(); pb.start()
        click.echo(f"Simulator started (pid={ps.pid}); Broker started (pid={pb.pid}). Ctrl-C to stop.")
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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
