"""SQLite-only, local supervisor heartbeat and command queue. No guild data stored."""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_NAME = 'supervisor.db'

def db_path(root):
    return Path(root) / 'runtime' / DB_NAME

@contextmanager
def connect(root):
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    con.execute('PRAGMA busy_timeout=5000')
    con.execute('CREATE TABLE IF NOT EXISTS status (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)')
    con.execute('CREATE TABLE IF NOT EXISTS requests (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL, created REAL NOT NULL)')
    try:
        with con:
            yield con
    finally:
        con.close()

def put_status(root, payload):
    with connect(root) as con:
        con.execute('INSERT INTO status(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload', (json.dumps(payload),))

def get_status(root):
    # Reading before the first supervisor start must not create runtime files.
    if not db_path(root).is_file():
        return {}
    try:
        with connect(root) as con:
            row = con.execute('SELECT payload FROM status WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}
    except (sqlite3.Error, ValueError, TypeError):
        return {}

def send_command(root, action):
    if action not in ('stop', 'restart', 'shutdown'):
        raise ValueError('Unsupported action')
    with connect(root) as con:
        con.execute('INSERT INTO requests(action,created) VALUES(?,?)', (action, time.time()))

def take_command(root):
    with connect(root) as con:
        row = con.execute('SELECT id,action,created FROM requests ORDER BY id LIMIT 1').fetchone()
        if row:
            con.execute('DELETE FROM requests WHERE id=?', (row[0],))
    return {'action': row[1], 'timestamp': row[2]} if row else {}

def clear_commands(root):
    with connect(root) as con:
        con.execute('DELETE FROM requests')
