"""Import a reconciled valuation/flow ledger into one explicitly named account.

Usage: python -m scripts.import_performance ledger.json --account BROKER_USER_ID
       Add --apply only after verifying ownership and complete external-flow coverage.
No account, source, or financial values are inferred. Dry-run is the default.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('--account', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / 'backend' / '.env')
    from backend.app.services.db import account_scope, init_account, system_connection
    from backend.app.services.performance import time_weighted_return, init_evidence_tables, get_connection
    payload = json.loads(args.ledger.read_text(encoding='utf-8'))
    if payload.get('account_id') != args.account or payload.get('complete_external_flow_coverage') is not True:
        parser.error('Ledger must declare matching account_id and verified complete_external_flow_coverage.')
    events = payload.get('events', [])
    result = time_weighted_return(events)
    if result['status'] != 'available':
        parser.error(result['reason'])
    # Require an account already observed through broker authentication.
    conn = system_connection()
    known = conn.execute('SELECT 1 FROM zerodha_sessions WHERE user_id = ? LIMIT 1', (args.account,)).fetchone()
    conn.close()
    if not known:
        parser.error('Account must first be verified through broker sign-in.')
    print(f'Validated {len(events)} events. ' + ('Importing.' if args.apply else 'Dry run only.'))
    if args.apply:
        with account_scope(args.account):
            init_account()
            init_evidence_tables()
            conn = get_connection()
            try:
                with conn:
                    # A partial append could omit flows at the join. Require a fresh
                    # series; replacement needs an explicit, separately reviewed migration.
                    if conn.execute('SELECT COUNT(*) FROM performance_events').fetchone()[0]:
                        raise ValueError('Existing series found; refusing to append or overwrite verified history.')
                    conn.executemany('INSERT INTO performance_events VALUES (?, ?, ?, ?, ?)', [
                        (datetime.fromisoformat(e['observed_at']).astimezone(timezone.utc).isoformat(),
                         e['value_before_flow'], e['external_flow'], e['source'], 1)
                        for e in events])
            finally:
                conn.close()


if __name__ == '__main__':
    main()
