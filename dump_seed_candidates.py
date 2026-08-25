from hanno.config import load_config
from hanno.ledger.repository import Ledger

config = load_config()
ledger = Ledger.connect(config.ledger.database_path)
rows = ledger.list_candidates_by_seed("81f5273e-7e9b-4e19-b47b-2b9133a54cdd")
rows.sort(key=lambda c: (c["iteration"], c["generated_at"] or ""))
for c in rows:
    print("=" * 90)
    print(
        f"iter={c['iteration']} score={c['path_score']} status={c['parse_status']} "
        f"dup={c['duplicate_status']} novelty={c['self_rated_novelty']} "
        f"rigor={c['self_rated_rigor']} claim_type={c['claim_type']}"
    )
    print("core_claim:", c["core_claim"])
    print("formal_statement:", c["formal_statement"])
    print("intuition:", c["intuition"])
print("TOTAL:", len(rows))
ledger.close()