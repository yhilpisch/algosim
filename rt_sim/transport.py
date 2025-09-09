from __future__ import annotations

from typing import Any, Optional, Tuple

import json
import zmq


class Transport:
    def __init__(self, hwm_ticks: int = 20000, hwm_orders: int = 20000, hwm_fills: int = 20000):
        self.ctx = zmq.Context.instance()
        self.hwm_ticks = hwm_ticks
        self.hwm_orders = hwm_orders
        self.hwm_fills = hwm_fills

    # PUB/SUB for ticks and fills
    def bind_pub(self, addr: str, kind: str = "generic") -> zmq.Socket:
        sock = self.ctx.socket(zmq.PUB)
        if kind == "ticks":
            sock.set_hwm(self.hwm_ticks)
        elif kind == "fills":
            sock.set_hwm(self.hwm_fills)
        sock.bind(addr)
        return sock

    def connect_sub(self, addr: str, topic: str = "", conflate: bool = False) -> zmq.Socket:
        sock = self.ctx.socket(zmq.SUB)
        sock.set_hwm(self.hwm_ticks)
        if conflate:
            sock.setsockopt(zmq.CONFLATE, 1)
        sock.connect(addr)
        sock.setsockopt(zmq.SUBSCRIBE, topic.encode())
        return sock

    # PUSH/PULL for orders
    def bind_pull(self, addr: str) -> zmq.Socket:
        sock = self.ctx.socket(zmq.PULL)
        sock.set_hwm(self.hwm_orders)
        sock.bind(addr)
        return sock

    def connect_push(self, addr: str) -> zmq.Socket:
        sock = self.ctx.socket(zmq.PUSH)
        sock.set_hwm(self.hwm_orders)
        sock.connect(addr)
        return sock

    @staticmethod
    def send_json(sock: zmq.Socket, topic: str, payload: dict) -> None:
        data = json.dumps(payload, separators=(",", ":")).encode()
        sock.send_multipart([topic.encode(), data])

    @staticmethod
    def recv_json(sock: zmq.Socket) -> Tuple[str, dict]:
        topic, data = sock.recv_multipart()
        return topic.decode(), json.loads(data)

