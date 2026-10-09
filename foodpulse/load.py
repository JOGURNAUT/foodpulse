"""Load the raw CSVs into the warehouse.

    python -m foodpulse.load
    FOODPULSE_WAREHOUSE=snowflake://ACCOUNT/FOODPULSE/RAW python -m foodpulse.load
"""

from __future__ import annotations

import sys

from .warehouse import load_all


def main(argv=None) -> int:
    result = load_all()
    print(f"  {result['flavour']}  ->  {result['warehouse']}")
    for table, n in result["counts"].items():
        print(f"    raw_{table:<13} {n:>8,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
