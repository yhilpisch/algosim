from __future__ import annotations

import csv
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml


def prepare_run_directory(base_dir: Path | str, run_id: str, config: Dict[str, Any]) -> Path:
    """Create directory for run artifacts and persist configuration metadata."""
    base = Path(base_dir)
    run_path = base / run_id
    run_path.mkdir(parents=True, exist_ok=True)

    meta_path = run_path / "meta.json"
    meta = {
        "run_id": run_id,
        "created_utc": datetime.utcnow().isoformat(timespec="seconds"),
        "seed": config.get("run", {}).get("seed"),
    }
    meta_path.write_text(json.dumps(meta, indent=2))

    cfg_path = run_path / "config_used.yaml"
    cfg_path.write_text(yaml.safe_dump(config, sort_keys=False))

    return run_path


class RunRecorder:
    """Incrementally persist ticks, orders, and fills for deterministic replays."""

    def __init__(
        self,
        run_dir: Path | str,
        *,
        enable_ticks: bool = False,
        enable_orders: bool = False,
        enable_fills: bool = False,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

        self._tick_json = self._tick_csv = self._tick_writer = None
        self._order_json = self._order_csv = self._order_writer = None
        self._fill_json = self._fill_csv = self._fill_writer = None

        if enable_ticks:
            self._tick_json, self._tick_csv, self._tick_writer = self._open_pair(
                "ticks.jsonl",
                "ticks.csv",
                ["seq", "ts_sim", "ts_wall", "price", "asset_id", "run_id"],
            )
        if enable_orders:
            self._order_json, self._order_csv, self._order_writer = self._open_pair(
                "orders.jsonl",
                "orders.csv",
                ["ts_wall_in", "strategy_id", "side", "qty", "tag", "run_id"],
            )
        if enable_fills:
            self._fill_json, self._fill_csv, self._fill_writer = self._open_pair(
                "fills.jsonl",
                "fills.csv",
                [
                    "ts_wall",
                    "ts_sim",
                    "strategy_id",
                    "side",
                    "qty",
                    "fill_price",
                    "commission",
                    "slippage_bps",
                    "pos_after",
                    "cash_after",
                    "equity_after",
                    "run_id",
                ],
            )

    def _open_pair(self, json_name: str, csv_name: str, headers: Iterable[str]):
        json_path = self.run_dir / json_name
        csv_path = self.run_dir / csv_name
        json_file = json_path.open("a", encoding="utf-8")
        csv_exists = csv_path.exists() and csv_path.stat().st_size > 0
        csv_file = csv_path.open("a", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        if not csv_exists:
            writer.writerow(list(headers))
            csv_file.flush()
        return json_file, csv_file, writer

    def log_tick(self, payload: Dict[str, Any]) -> None:
        if self._tick_json is None or self._tick_writer is None:
            return
        self._write_json(self._tick_json, payload)
        row = [
            payload.get("seq"),
            payload.get("ts_sim"),
            payload.get("ts_wall"),
            payload.get("price"),
            payload.get("asset_id"),
            payload.get("run_id"),
        ]
        self._tick_writer.writerow(row)
        self._tick_json.flush()
        self._tick_csv.flush()

    def log_order(self, payload: Dict[str, Any]) -> None:
        if self._order_json is None or self._order_writer is None:
            return
        self._write_json(self._order_json, payload)
        row = [
            payload.get("ts_wall_in"),
            payload.get("strategy_id"),
            payload.get("side"),
            payload.get("qty"),
            payload.get("tag"),
            payload.get("run_id"),
        ]
        self._order_writer.writerow(row)
        self._order_json.flush()
        self._order_csv.flush()

    def log_fill(self, payload: Dict[str, Any]) -> None:
        if self._fill_json is None or self._fill_writer is None:
            return
        self._write_json(self._fill_json, payload)
        row = [
            payload.get("ts_wall"),
            payload.get("ts_sim"),
            payload.get("strategy_id"),
            payload.get("side"),
            payload.get("qty"),
            payload.get("fill_price"),
            payload.get("commission"),
            payload.get("slippage_bps"),
            payload.get("pos_after"),
            payload.get("cash_after"),
            payload.get("equity_after"),
            payload.get("run_id"),
        ]
        self._fill_writer.writerow(row)
        self._fill_json.flush()
        self._fill_csv.flush()

    def close(self) -> None:
        for handle in (
            self._tick_json,
            self._tick_csv,
            self._order_json,
            self._order_csv,
            self._fill_json,
            self._fill_csv,
        ):
            try:
                if handle:
                    handle.close()
            except Exception:
                pass

    def __enter__(self) -> "RunRecorder":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @staticmethod
    def _write_json(handle, payload: Dict[str, Any]) -> None:
        handle.write(json.dumps(payload, separators=(",", ":")) + "\n")
        handle.flush()


def load_run_summary(run_dir: Path | str) -> Dict[str, Any]:
    run_path = Path(run_dir)
    summary: Dict[str, Any] = {"run_dir": str(run_path)}

    meta_path = run_path / "meta.json"
    if meta_path.exists():
        try:
            summary.update(json.loads(meta_path.read_text()))
        except json.JSONDecodeError:
            summary["meta_error"] = "Invalid meta.json"

    for kind in ("ticks", "orders", "fills"):
        file_path = run_path / f"{kind}.jsonl"
        count = 0
        if file_path.exists():
            with file_path.open("r", encoding="utf-8") as fh:
                for _ in fh:
                    count += 1
        summary[f"{kind}_count"] = count

    return summary
