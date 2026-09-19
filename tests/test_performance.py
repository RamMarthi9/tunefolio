from backend.app.services.performance import explain_changes, time_weighted_return


def holding(q, p, symbol='AAA'):
    return {'tradingsymbol': symbol, 'exchange': 'NSE', 'quantity': q, 'last_price': p}


def test_change_decomposition_includes_added_and_removed_positions():
    changes = explain_changes([holding(10, 100), holding(5, 20, 'OLD')],
                              [holding(12, 110), holding(3, 50, 'NEW')])
    by_symbol = {r['symbol']:r for r in changes}
    assert by_symbol['AAA']['price_effect'] == 100
    assert by_symbol['AAA']['quantity_effect'] == 220
    assert by_symbol['OLD']['value_change'] == -100
    assert by_symbol['NEW']['value_change'] == 150
    assert sum(r['value_change'] for r in changes) == (12*110+3*50)-(10*100+5*20)
    assert all(r['value_change'] == r['price_effect']+r['quantity_effect'] for r in changes)


def event(day, value, flow=0, verified=1):
    return {'observed_at': f'2026-04-{day:02}T10:00:00+00:00',
            'value_before_flow': value, 'external_flow': flow,
            'source':'synthetic reconciled ledger', 'coverage_verified':verified}


def test_twr_does_not_count_deposit_as_profit():
    result = time_weighted_return([event(1, 100), event(2, 110, 100), event(3, 231)])
    assert result['return_pct'] == 21
    assert result['status'] == 'available'


def test_withdrawal_and_loss():
    result = time_weighted_return([event(1, 100), event(2, 90, -40), event(3, 55)])
    assert result['return_pct'] == -1


def test_incomplete_or_invalid_history_is_never_zero_return():
    cases = [[], [event(1, 100)], [event(1, 100),event(2, 110, verified=0)],
             [event(2, 100), event(1, 110)], [event(1, 100),event(2, float('nan'))],
             [event(1, 0),event(2, 100)]]
    for events in cases:
        result = time_weighted_return(events)
        assert result['status'] != 'available'
        assert 'return_pct' not in result
