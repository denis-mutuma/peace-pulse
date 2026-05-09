from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "api"))

from peacepulse_core import init_db, run_sync


def main() -> None:
    init_db()
    print(run_sync())


if __name__ == "__main__":
    main()
