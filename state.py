import json
import sqlite3
import threading
from datetime import datetime, timezone

class State:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.init()
    def db(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c
    def init(self):
        with self.lock, self.db() as c:
            c.execute('CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, source TEXT, url TEXT, seen_at TEXT, delivered_at TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS companies(identity TEXT PRIMARY KEY, name TEXT, program TEXT, batch TEXT, url TEXT, description TEXT, founder TEXT, first_seen_at TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)')
            c.commit()
    def claim(self, key, source, url):
        with self.lock, self.db() as c:
            cur=c.execute('INSERT OR IGNORE INTO seen VALUES (?,?,?,?,NULL)',(key,source,url,datetime.now(timezone.utc).isoformat())); c.commit(); return cur.rowcount==1
    def delivered(self,key):
        with self.lock, self.db() as c:
            c.execute('UPDATE seen SET delivered_at=? WHERE key=?',(datetime.now(timezone.utc).isoformat(),key)); c.commit()
    def upsert_company(self,row):
        with self.lock, self.db() as c:
            exists=c.execute('SELECT 1 FROM companies WHERE identity=?',(row['identity'],)).fetchone() is not None
            c.execute('INSERT INTO companies VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET name=excluded.name,batch=excluded.batch,url=excluded.url,description=excluded.description',(row['identity'],row['name'],row['program'],row.get('batch',''),row['url'],row.get('description',''),row.get('founder',''),datetime.now(timezone.utc).isoformat())); c.commit(); return exists
    def company_names(self):
        with self.lock, self.db() as c: return {r['name'].strip().lower() for r in c.execute('SELECT name FROM companies')}
    def has_companies(self):
        with self.lock, self.db() as c: return c.execute('SELECT 1 FROM companies LIMIT 1').fetchone() is not None
    def set_last_scan(self,result):
        with self.lock, self.db() as c:
            c.execute("INSERT INTO meta VALUES ('last_scan',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps(result),)); c.commit()
    def last_scan(self):
        with self.lock, self.db() as c:
            r=c.execute("SELECT value FROM meta WHERE key='last_scan'").fetchone(); return json.loads(r['value']) if r else None
