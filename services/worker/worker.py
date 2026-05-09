from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "api"))

from peacepulse_core import init_db, triage_pending


def main() -> None:
    init_db()
    once = "--once" in sys.argv
    while True:
        processed = triage_pending()
        print(f"worker processed {processed} queued reports")
        if once:
            return
        time.sleep(10)


if __name__ == "__main__":
    main()
