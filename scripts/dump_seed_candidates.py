"""Print candidates for one seed without changing the ledger."""

import argparse
import sqlite3
from pathlib import Path

from hanno.config import load_config
from hanno.ledger.repository import Ledger


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("seed_id", help="Seed UUID to inspect")
    parser.add_argument("--database", type=Path, help="Ledger path (defaults to configured ledger)")
    args = parser.parse_args()
    database = (args.database or Path(load_config().ledger.database_path)).resolve()
    if not database.is_file():
        parser.error(f"ledger not found: {database}")
    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    ledger = Ledger(connection)
    try:
        if ledger.get_seed(args.seed_id) is None:
            parser.error(f"seed not found: {args.seed_id}")
        rows = ledger.list_candidates_by_seed(args.seed_id)
        rows.sort(key=lambda c: (c["iteration"], c["generated_at"] or ""))
        for candidate in rows:
            print("=" * 90)
            print(
                f"iter={candidate['iteration']} score={candidate['path_score']} "
                f"status={candidate['parse_status']} dup={candidate['duplicate_status']} "
                f"novelty={candidate['self_rated_novelty']} rigor={candidate['self_rated_rigor']} "
                f"claim_type={candidate['claim_type']}"
            )
            for field in ("core_claim", "formal_statement", "intuition"):
                print(f"{field}:", candidate[field])
        print("TOTAL:", len(rows))
    finally:
        ledger.close()


if __name__ == "__main__":
    main()
