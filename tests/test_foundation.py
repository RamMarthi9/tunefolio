import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

os.environ['TUNEFOLIO_DISABLE_SCHEDULER'] = '1'
os.environ['ENVIRONMENT'] = 'test'

import pytest
from fastapi.testclient import TestClient
from backend.app.services import db
from backend.app.services import zerodha_holdings as broker
from backend.app.services.trade_sync import _insert_trades
from backend.app.main import app


@pytest.fixture(autouse=True, params=['sqlite', 'libsql'])
def isolated_storage(tmp_path, monkeypatch, request):
    monkeypatch.setattr(db, 'DATA_ROOT', tmp_path)
    monkeypatch.delenv('VERCEL', raising=False)
    monkeypatch.setenv('ENVIRONMENT', 'test')
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    if request.param == 'libsql':
        import libsql
        from backend.app.services.remote_db import Connection
        monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://synthetic.invalid')
        monkeypatch.setenv('TURSO_AUTH_TOKEN', 'synthetic')
        monkeypatch.setattr(db, 'remote_connect', lambda account_id=None:
            Connection(libsql.connect(str(tmp_path / 'shared.db'), timeout=15), account_id))
    broker._holdings_cache.clear()
    broker._margins_cache.clear()
    db.init_db()


def seed(user, symbol):
    with db.account_scope(user):
        db.init_account()
        _insert_trades([{'tradingsymbol': symbol, 'exchange': 'NSE', 'transaction_type': 'BUY',
                         'quantity': 2, 'average_price': 100, 'trade_id': 'same-id',
                         'order_id': '1', 'fill_timestamp': '2026-04-01T12:00:00'}])
    return db.save_zerodha_session(user, 'synthetic-token-'+user)


@pytest.mark.parametrize('path', ['/portfolio/trades?symbol=AAA', '/portfolio/realised-pnl',
    '/portfolio/historical-holdings', '/portfolio/sector-allocation', '/portfolio/holdings',
    '/portfolio/margins', '/portfolio/daily-pnl', '/portfolio/comparative',
    '/portfolio/changes', '/portfolio/performance', '/portfolio/trade-sync/status', '/holdings'])
def test_private_routes_require_session(path):
    with TestClient(app) as client:
        response = client.get(path)
        assert response.status_code == 401
        assert response.headers['cache-control'] == 'no-store'


def test_two_accounts_and_restart():
    a, b = seed('alice', 'AAA'), seed('bob', 'BBB')
    for _ in range(2):  # New app/client connections reuse durable files, never a shared seed.
        with TestClient(app) as client:
            client.cookies.set('tf_session', a)
            assert client.get('/portfolio/trades?symbol=AAA').json()['count'] == 1
            assert client.get('/portfolio/trades?symbol=BBB').json()['count'] == 0
            client.cookies.set('tf_session', b)
            assert client.get('/portfolio/trades?symbol=AAA').json()['count'] == 0
            assert client.get('/portfolio/trades?symbol=BBB').json()['count'] == 1


def test_parallel_account_contexts():
    seed('alice', 'AAA'); seed('bob', 'BBB')
    def symbols(user):
        with db.account_scope(user):
            conn = db.get_connection()
            rows = conn.execute('SELECT symbol FROM trades').fetchall()
            conn.close()
            return [r['symbol'] for r in rows]
    with ThreadPoolExecutor() as pool:
        assert list(pool.map(symbols, ['alice', 'bob']*10)) == [['AAA'], ['BBB']]*10
    with pytest.raises(RuntimeError): db.get_connection()


def test_revocation_and_expiry_override_cache():
    sid = seed('alice', 'AAA')
    import time
    broker._holdings_cache[sid] = {'timestamp': time.time(), 'data': [{'secret': True}]}
    db.deactivate_session(sid)
    with pytest.raises(Exception) as error: broker.fetch_zerodha_holdings(sid)
    assert error.value.status_code == 401
    assert db.get_active_access_token(sid+':injected-token') is None
    sid = db.save_zerodha_session('alice', 'synthetic')
    conn = db.system_connection()
    conn.execute('UPDATE zerodha_sessions SET expires_at = ? WHERE id = ?',
                 ((datetime.utcnow()-timedelta(seconds=1)).isoformat(), sid))
    conn.commit(); conn.close()
    assert db.get_active_zerodha_session(sid) is None
    assert db.get_active_access_token(sid) is None


def test_serverless_storage_fails_closed(monkeypatch):
    monkeypatch.delenv('TURSO_DATABASE_URL', raising=False)
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    monkeypatch.setenv('VERCEL', '1')
    with TestClient(app) as client:
        assert client.get('/portfolio/trades?symbol=AAA').status_code == 503
        assert client.get('/auth/zerodha/login').status_code == 503


def test_no_shared_import_or_implicit_sync():
    from backend.app.services.trade_sync import sync_trades_from_kite
    assert sync_trades_from_kite()['reason'] == 'explicit_account_required'
    sid = seed('alice', 'AAA')
    with TestClient(app) as client:
        client.cookies.set('tf_session', sid)
        assert client.post('/portfolio/trades/import').status_code == 409
        assert client.post('/auth/zerodha/logout', headers={'Origin':'https://attacker.invalid'}).status_code == 403
        assert db.get_active_access_token(sid)
        assert client.post('/auth/zerodha/logout').status_code == 200
        assert client.get('/portfolio/realised-pnl').status_code == 401


def test_callback_stays_same_origin_and_cookie_is_opaque(monkeypatch):
    from backend.app.auth import zerodha
    class Reply:
        status_code = 200
        def json(self): return {'data': {'user_id': 'alice', 'access_token': 'broker-secret'}}
    monkeypatch.setattr(zerodha.requests, 'post', lambda *a, **k: Reply())
    from types import SimpleNamespace
    monkeypatch.setattr(zerodha, 'threading', SimpleNamespace(Thread=lambda **kw: SimpleNamespace(start=lambda: None)))
    with TestClient(app, base_url='https://tunefolio.in') as client:
        assert client.get('/auth/zerodha/callback?request_token=fake', follow_redirects=False).status_code == 400
        login = client.get('/auth/zerodha/login', follow_redirects=False)
        from urllib.parse import urlparse, parse_qs
        params = parse_qs(urlparse(login.headers['location']).query)
        state = parse_qs(params['redirect_params'][0])['state'][0]
        response = client.get('/auth/zerodha/callback?request_token=fake&state='+state, follow_redirects=False)
        assert response.headers['location'] == '/?status=connected'
        cookie = response.headers['set-cookie']
        assert 'broker-secret' not in cookie
        assert 'HttpOnly' in cookie and 'Secure' in cookie and 'SameSite=lax' in cookie
        assert client.get('/session/active').status_code == 200
        assert client.get('/auth/zerodha/callback?request_token=fake&state='+state, follow_redirects=False).status_code == 400


def test_observations_are_isolated_and_identical_fetches_do_not_replace_evidence():
    from backend.app.services.performance import observe_holdings, changes
    row = {'tradingsymbol':'AAA', 'exchange':'NSE', 'quantity':2, 'last_price':100}
    for user in ['alice', 'bob']:
        with db.account_scope(user): db.init_account()
    with db.account_scope('alice'):
        observe_holdings([row], '2026-04-01T10:00:00+00:00')
        observe_holdings([dict(row, last_price=110)], '2026-04-02T10:00:00+00:00')
        observe_holdings([dict(row, last_price=110)], '2026-04-02T10:00:01+00:00')
        result = changes()
        assert result['from'] == '2026-04-01T10:00:00+00:00'
        assert result['data'][0]['value_change'] == 20
    with db.account_scope('bob'):
        assert changes()['status'] == 'unavailable'


def test_fifo_missing_basis_and_empty_history_are_unavailable():
    from backend.app.services.trades import compute_realised_pnl
    with db.account_scope('alice'):
        db.init_account()
        assert compute_realised_pnl()['total_realised_pnl'] is None
        _insert_trades([{'tradingsymbol':'AAA', 'exchange':'NSE', 'transaction_type':'SELL',
                        'quantity':2, 'average_price':100, 'trade_id':'sell',
                        'fill_timestamp':'2026-04-01T10:00:00'}])
        result = compute_realised_pnl()
        assert result['status'] == 'incomplete'
        assert result['total_realised_pnl'] is None
        assert result['missing_cost_basis'][0]['quantity'] == 2


def test_missing_delivery_is_null_through_cache():
    from backend.app.services.delivery import _build_row
    from datetime import date
    with db.account_scope('alice'):
        db.init_account()
        row = _build_row(date.today().isoformat(), 1000, None, 100, 99, 99, 101, 98)
        db.save_delivery_cache('AAA', [row])
        saved = db.get_delivery_cache('AAA')[0]
        assert saved['delivered_qty'] is None
        assert saved['delivery_pct'] is None
        assert saved['not_delivered_qty'] is None


def test_broker_expiry_revokes_only_affected_account(monkeypatch):
    a, b = seed('alice', 'AAA'), seed('bob', 'BBB')
    class Rejected:
        status_code = 403
    monkeypatch.setattr(broker.requests, 'get', lambda *a, **kw: Rejected())
    with db.account_scope('alice'):
        with pytest.raises(Exception) as error:
            broker.fetch_zerodha_holdings(a)
        assert error.value.status_code == 401
    assert db.get_active_access_token(a) is None
    assert db.get_active_access_token(b) is not None


def test_partial_trade_sync_does_not_verify_fiscal_profit():
    from backend.app.services.trades import compute_realised_pnl
    seed('alice', 'AAA')
    with db.account_scope('alice'):
        result = compute_realised_pnl('2026-04-01', '2026-09-20')
        assert result['total_realised_pnl'] is None
        assert result['coverage_verified'] is False
        # A reviewed receipt permits a genuine zero for a period with no sales.
        with db.get_connection() as conn:
            conn.execute('INSERT INTO trade_reconciliations VALUES (?, ?, ?, ?, ?)',
                         ('2026-04-01', '2026-09-20', result['ledger_digest'], 'synthetic reconciliation', '2026-09-20'))
        conn.close()
        assert compute_realised_pnl('2026-04-01', '2026-09-20')['total_realised_pnl'] == 0
        assert compute_realised_pnl('2026-04-01', '2026-09-21')['total_realised_pnl'] is None
    with db.account_scope('bob'):
        db.init_account()
        assert compute_realised_pnl('2026-04-01', '2026-09-20')['total_realised_pnl'] is None


def test_cash_zero_and_missing_are_distinct(monkeypatch):
    from backend.app.routes import portfolio
    from starlette.requests import Request
    request = Request({'type': 'http', 'headers': []})
    monkeypatch.setattr(portfolio, 'fetch_zerodha_margins', lambda _: {'available': {'cash': 0, 'live_balance': 123, 'opening_balance': 456}, 'net': 789})
    result = portfolio.portfolio_margins(request)
    assert result['cash'] == 0
    assert result['live_balance'] == 123
    monkeypatch.setattr(portfolio, 'fetch_zerodha_margins', lambda _: {'available': {}})
    assert portfolio.portfolio_margins(request)['cash'] is None
