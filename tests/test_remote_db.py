import pytest
from backend.app.services.remote_db import Connection, remote_settings

def test_namespace_rejects_cross_scope_and_physical_names():
    account = Connection(None, 'alice')
    system = Connection(None)
    for sql in ['SELECT * FROM zerodha_sessions', 'SELECT * FROM tf_a_other_trades',
                'SELECT * FROM sqlite_master', 'ATTACH DATABASE ? AS other']:
        with pytest.raises(ValueError): account.scope_sql(sql)
    with pytest.raises(ValueError): system.scope_sql('SELECT * FROM trades')
    assert account.scope_sql("SELECT 'trades' FROM trades") == "SELECT 'trades' FROM " + account.prefix + 'trades'
    assert Connection(None, 'bob').prefix != account.prefix

def test_partial_remote_configuration_fails_closed(monkeypatch):
    monkeypatch.setenv('TURSO_DATABASE_URL', 'libsql://synthetic.invalid')
    monkeypatch.delenv('TURSO_AUTH_TOKEN', raising=False)
    with pytest.raises(ValueError): remote_settings()

def test_transaction_rollback_and_reopen(tmp_path):
    import libsql
    path = str(tmp_path / 'remote.db')
    conn = Connection(libsql.connect(path), 'alice')
    conn.execute('CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)')
    conn.commit()
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute('INSERT INTO trades VALUES (?, ?)', (1, 'AAA'))
            raise RuntimeError('abort')
    conn.close()
    conn = Connection(libsql.connect(path), 'alice')
    assert conn.execute('SELECT COUNT(*) FROM trades').fetchone()[0] == 0
    conn.close()
