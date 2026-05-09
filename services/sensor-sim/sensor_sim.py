from __future__ import annotations

import random
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "api"))

from peacepulse_core import create_resource_event, init_db


def emit() -> None:
    queue_length = random.randint(8, 65)
    flow_rate = round(random.random() * 9, 1)
    uptime = 0 if flow_rate < 1.0 else 1
    event = create_resource_event(
        {
            "resource_id": "water-point-north",
            "queue_length": queue_length,
            "flow_rate": flow_rate,
            "uptime": uptime,
        }
    )
    print(f"sensor event {event['id']}: {event['anomaly']}")


def main() -> None:
    init_db()
    once = "--once" in sys.argv
    while True:
        emit()
        if once:
            return
        time.sleep(15)


if __name__ == "__main__":
    main()
