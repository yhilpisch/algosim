from __future__ import annotations

import multiprocessing as mp
from pathlib import Path
from typing import Optional

import click

from .simulator import run as run_simulator
from .transport import Transport
from .utils import load_config, new_run_id, seed_everything


def _sim_entry(cfg: dict, run_id: str) -> None:
    """Top-level simulator process entry (must be picklable for multiprocessing)."""
    t = Transport(
        hwm_ticks=int(cfg["transport"]["hwm"]["ticks_pub"]),
        hwm_orders=int(cfg["transport"]["hwm"]["orders"]),
        hwm_fills=int(cfg["transport"]["hwm"]["fills_pub"]),
    )
    run_simulator(cfg, t, run_id)


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
        click.echo(f"Simulator starting inline (run_id={run_id}). Press Ctrl-C to stop.")
        try:
            _sim_entry(cfg, run_id)
        except KeyboardInterrupt:
            click.echo("Stopping...")
    else:
        p = mp.Process(target=_sim_entry, args=(cfg, run_id), daemon=True)
        p.start()
        click.echo(f"Simulator started (run_id={run_id}, pid={p.pid}). Press Ctrl-C to stop.")
        try:
            p.join()
        except KeyboardInterrupt:
            click.echo("Stopping...")
        finally:
            if p.is_alive():
                p.terminate()
                p.join(timeout=1)


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


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
