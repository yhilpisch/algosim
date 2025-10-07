from __future__ import annotations

import json
from pathlib import Path

from rt_sim.recorder import RunRecorder, prepare_run_directory, load_run_summary


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line]


def test_prepare_and_record(tmp_path):
    cfg = {
        "run": {"seed": 123},
        "transport": {"endpoints": {"ticks_pub": "tcp://127.0.0.1:5555"}},
    }
    run_dir = prepare_run_directory(tmp_path, "RID-123", cfg)
    assert run_dir.exists()
    assert (run_dir / "meta.json").exists()
    assert (run_dir / "config_used.yaml").exists()

    recorder = RunRecorder(run_dir, enable_ticks=True, enable_orders=True, enable_fills=True)
    tick = {
        "seq": 1,
        "ts_sim": 0.1,
        "ts_wall": 100.0,
        "price": 101.0,
        "asset_id": "X",
        "run_id": "RID-123",
    }
    recorder.log_tick(tick)
    order = {
        "ts_wall_in": 100.2,
        "strategy_id": "ctx",
        "side": "BUY",
        "qty": 2,
        "tag": "test",
        "run_id": "RID-123",
    }
    recorder.log_order(order)
    fill = {
        "ts_wall": 100.25,
        "ts_sim": 0.2,
        "strategy_id": "ctx",
        "side": "BUY",
        "qty": 2,
        "fill_price": 101.1,
        "commission": 0.1,
        "slippage_bps": 1.0,
        "pos_after": 2,
        "cash_after": -202.2,
        "equity_after": 0.0,
        "run_id": "RID-123",
    }
    recorder.log_fill(fill)
    recorder.close()

    ticks_json = _read_lines(run_dir / "ticks.jsonl")
    assert len(ticks_json) == 1
    assert json.loads(ticks_json[0])["price"] == tick["price"]
    ticks_csv = _read_lines(run_dir / "ticks.csv")
    assert ticks_csv[0].startswith("seq,ts_sim")
    assert "101.0" in ticks_csv[1]

    orders_json = _read_lines(run_dir / "orders.jsonl")
    assert len(orders_json) == 1
    assert json.loads(orders_json[0])["strategy_id"] == order["strategy_id"]

    fills_csv = _read_lines(run_dir / "fills.csv")
    assert fills_csv[0].startswith("ts_wall,ts_sim")
    assert "ctx" in fills_csv[1]

    summary = load_run_summary(run_dir)
    assert summary["ticks_count"] == 1
    assert summary["orders_count"] == 1
    assert summary["fills_count"] == 1
    assert summary["run_dir"] == str(run_dir)
