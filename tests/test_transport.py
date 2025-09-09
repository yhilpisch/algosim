from __future__ import annotations

import threading
import time

from rt_sim.transport import Transport


def test_pub_sub_smoke():
    t = Transport()
    addr = "tcp://127.0.0.1:5599"
    pub = t.bind_pub(addr)
    sub = t.connect_sub(addr, topic="X")
    received = {}

    def sub_loop():
        topic, payload = t.recv_json(sub)
        received["topic"] = topic
        received["payload"] = payload

    th = threading.Thread(target=sub_loop, daemon=True)
    th.start()
    time.sleep(0.05)
    Transport.send_json(pub, "X", {"hello": "world"})
    th.join(timeout=1.0)
    assert received.get("topic") == "X"
    assert received.get("payload") == {"hello": "world"}
    sub.close(0)
    pub.close(0)

