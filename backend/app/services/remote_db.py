"""Remote libSQL connection with server-selected table namespaces.

Only application-owned SQL is accepted. Values always remain bound parameters.
No user ID, namespace or raw SQL is accepted from HTTP clients.
"""
import hashlib
import os
import re
from collections.abc import Mapping
from urllib.parse import urlsplit

SYSTEM_TABLES = frozenset({'zerodha_sessions', 'login_states'})
ACCOUNT_TABLES = frozenset({'holdings_snapshots', 'instruments', 'trades',
    'delivery_cache', 'index_cache', 'portfolio_observations', 'performance_events', 'trade_reconciliations'})
# Match string literals and comments before identifiers, so their text is untouched.
_TOKENS = re.compile(r"'(?:''|[^'])*'|--[^\n]*|/\*.*?\*/|[A-Za-z_][A-Za-z0-9_]*", re.S)


def remote_settings():
    url = os.getenv('TURSO_DATABASE_URL', '')
    token = os.getenv('TURSO_AUTH_TOKEN', '')
    if bool(url) != bool(token):
        raise ValueError('Turso database configuration is incomplete')
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in ('libsql', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Turso requires a secure libsql:// or https:// database URL')
    return url, token


class Row(Mapping):
    """sqlite3.Row-compatible key and positional access over DB-API tuples."""
    def __init__(self, names, values):
        self._names, self._values = tuple(names), tuple(values)
    def __getitem__(self, key):
        return self._values[key] if isinstance(key, (int, slice)) else self._values[self._names.index(key)]
    def __iter__(self): return iter(self._names)
    def __len__(self): return len(self._values)


class Cursor:
    def __init__(self, connection, raw):
        self.connection, self.raw = connection, raw
    @property
    def rowcount(self): return self.raw.rowcount
    @property
    def lastrowid(self): return self.raw.lastrowid
    def execute(self, sql, parameters=()):
        self.raw.execute(self.connection.scope_sql(sql), parameters)
        return self
    def executemany(self, sql, parameters):
        self.raw.executemany(self.connection.scope_sql(sql), parameters)
        return self
    def _row(self, value):
        if value is None: return None
        return Row([col[0] for col in self.raw.description], value)
    def fetchone(self): return self._row(self.raw.fetchone())
    def fetchall(self): return [self._row(row) for row in self.raw.fetchall()]
    def close(self): self.raw.close()


class Connection:
    def __init__(self, raw, account_id=None):
        self.raw = raw
        self.tables = ACCOUNT_TABLES if account_id is not None else SYSTEM_TABLES
        self.prefix = 'tf_a_' + hashlib.sha256(account_id.encode()).hexdigest() + '_' if account_id is not None else 'tf_system_'

    def scope_sql(self, sql):
        # Every table reference in application SQL uses an unquoted logical name.
        # Reject explicit physical names, catalog access and attached databases.
        def replace(match):
            word = match.group(0)
            if word.startswith(("'", '--', '/*')): return word
            key = word.lower()
            if key.startswith(('tf_', 'sqlite_')) or key in ('attach', 'detach', 'pragma'):
                raise ValueError('Direct database or physical namespace access is forbidden')
            if key in SYSTEM_TABLES | ACCOUNT_TABLES:
                if key not in self.tables:
                    raise ValueError('Table is outside this connection scope')
                return self.prefix + key
            return word
        if any(c in sql for c in ('"', '`', '[')):
            raise ValueError('Quoted identifiers are not supported in application SQL')
        return _TOKENS.sub(replace, sql)

    def cursor(self): return Cursor(self, self.raw.cursor())
    def execute(self, sql, parameters=()): return self.cursor().execute(sql, parameters)
    def executemany(self, sql, parameters): return self.cursor().executemany(sql, parameters)
    def commit(self): self.raw.commit()
    def rollback(self): self.raw.rollback()
    def close(self): self.raw.close()
    def __enter__(self): return self
    def __exit__(self, kind, value, traceback):
        self.rollback() if kind else self.commit()
        return False


def connect(account_id=None):
    import libsql
    settings = remote_settings()
    if settings is None:
        raise RuntimeError('Turso database configuration is incomplete')
    url, token = settings
    # Direct remote connection only. No /tmp replica, sync_url or token fallback.
    raw = libsql.connect(database=url, auth_token=token, timeout=15)
    return Connection(raw, account_id)
