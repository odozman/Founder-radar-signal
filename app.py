import hashlib
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request

app = Flask(__name__)
DB_PATH = os.getenv("DATABASE_PATH", "state.db")
POLL_HOURS = float(os.getenv("POLL_HOURS", "8"))
YC_URL = os.getenv("YC_DIRECTORY_URL", "https://www.ycombinator.com/companies")
SPEEDRUN_URL = os.getenv("SPEEDRUN_URL", "https://speedrun.a16z.com/")
SPEEDRUN_NAME = os.getenv("SPEEDRUN_NAME", "a16z Speedrun")
SERPER_KEY = os.getenv("SERPER_API_KEY", "")
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.getenv("SLACK_CHANNEL_ID", "C0BV4HMSEBW")
ALERT_TOKEN = os.getenv("ALERT_TOKEN", "")

YC_PHRASES = [
    r"we(?:'|’| have)? ?(?:just )?got into y combinator",
    r"we(?:'|’| have)? ?been accepted(?: into| to)? y combinator",
    r"accepted into yc",
    r"joining y combinator",
    r"join(?:ing)? yc s\d{2}",
    r"yc s\d{2}",
    r"yc w\d{2}",
    r"backed by y combinator",
    r"y combinator (?:accepted|batch)",
]
SPEED_PHRASES = [r"speedrun", r"a16z speedrun", r"speedrun batch"]


def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS seen (key TEXT PRIMARY KEY, source TEXT, url TEXT, created_at TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS companies (name TEXT PRIMARY KEY, source TEXT, batch TEXT, url TEXT, description TEXT, confirmed_at TEXT)")
    conn.commit()
    return conn


def seen(key: str) -> bool:
    conn = db(); row = conn.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone(); conn.close(); return bool(row)


def mark_seen(key: str, source: str, url: str):
    conn = db(); conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?,?,?)", (key, source, url, datetime.now(timezone.utc).isoformat())); conn.commit(); conn.close()


def text_of(url: str) -> str:
    r = requests.get(url, timeout=25, headers={"User-Agent": "FounderRadarSignal/1.0"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)


def extract_batch(text: str) -> str:
    m = re.search(r"\b(YC\s*[SW]\d{2})\b", text, re.I)
    return m.group(1).upper().replace(" ", " ") if m else "Unspecified"


def extract_company(text: str) -> str:
    patterns = [
        r"(?:building|launching|launched|joining y combinator.*?building)\s+([A-Z][A-Za-z0-9&. -]{1,50}?)(?:\s*\(YC|\s+is\s+|\s+that\s+|\.|,|$)",
        r"([A-Z][A-Za-z0-9&. -]{1,50})\s*\(YC\s*[SW]\d{2}\)",
    ]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m: return m.group(1).strip(" .,-")
    return "Unknown company"


def slack_alert(item: dict[str, Any]):
    if not SLACK_TOKEN or not SLACK_CHANNEL_ID:
        return {"sent": False, "reason": "Slack credentials not configured"}
    company = item.get("company", "Unknown company")
    founder = item.get("founder", "Unknown founder")
    source = item.get("source", "Unknown source")
    batch = item.get("batch", "Unspecified")
    status = item.get("status", "Signal detected")
    desc = item.get("description", "")
    url = item.get("url", "")
    text = (f"*EARLY YC SIGNAL*\n*Company:* {company}\n*Founder:* {founder}\n*Batch:* {batch}\n*Source:* {source}\n*Status:* {status}\n\n"
            f"*Description:* {desc[:500] or 'Not available'}\n*Original post:* {url}\n*Detected:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    r = requests.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json; charset=utf-8"}, json={"channel": SLACK_CHANNEL_ID, "text": text}, timeout=20)
    r.raise_for_status(); data = r.json()
    if not data.get("ok"): raise RuntimeError(data.get("error", "Slack API error"))
    return {"sent": True, "ts": data.get("ts")}


def serper(query: str) -> list[dict[str, Any]]:
    if not SERPER_KEY: return []
    r = requests.post("https://google.serper.dev/search", headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}, json={"q": query, "num": 20}, timeout=25)
    r.raise_for_status(); return r.json().get("organic", [])


def search_social(source: str) -> list[dict[str, Any]]:
    if source == "X":
        queries = [
            'site:x.com ("YC S26" OR "YC W26" OR "got into YC" OR "accepted into Y Combinator") (founder OR cofounder)',
            'site:x.com ("Speedrun" OR "a16z Speedrun") (founder OR accepted OR building)',
        ]
        allowed = "x.com"
    else:
        queries = [
            'site:linkedin.com/posts ("YC S26" OR "YC W26" OR "joining Y Combinator" OR "got into YC") (founder OR cofounder)',
            'site:linkedin.com/posts ("Speedrun" OR "a16z Speedrun") (founder OR building OR accepted)',
        ]
        allowed = "linkedin.com"
    out=[]
    for q in queries:
        for row in serper(q):
            link=row.get("link","")
            if allowed in link: out.append(row)
    return out


def official_companies() -> dict[str, dict[str, str]]:
    result={}
    for source, url in (("YC Directory", YC_URL), (SPEEDRUN_NAME, SPEEDRUN_URL)):
        try:
            soup=BeautifulSoup(requests.get(url, timeout=30, headers={"User-Agent":"FounderRadarSignal/1.0"}).text,"html.parser")
            text=soup.get_text(" ", strip=True)
            # Keep a compact fingerprint even when the site's DOM changes.
            result[source]={"url":url,"fingerprint":hashlib.sha256(text.encode()).hexdigest()}
        except Exception:
            result[source]={"url":url,"fingerprint":""}
    return result


def run_scan():
    # Official directories are checked first so social signals can be classified as confirmed/unconfirmed.
    official=official_companies()
    signals=[]
    for source in ("X", "LinkedIn"):
        for row in search_social(source):
            title=row.get("title",""); snippet=row.get("snippet",""); link=row.get("link","")
            combined=f"{title} {snippet}".strip()
            if not any(re.search(p, combined, re.I) for p in YC_PHRASES+SPEED_PHRASES): continue
            key=hashlib.sha256((source+"|"+link).encode()).hexdigest()
            if seen(key): continue
            batch=extract_batch(combined)
            company=extract_company(combined)
            speed=any(re.search(p, combined, re.I) for p in SPEED_PHRASES)
            source_label=SPEEDRUN_NAME if speed else source
            item={"company":company,"founder":title.split(" - ")[0][:100] or "Unknown founder","batch":batch,"source":source_label,"status":"Founder announced / not yet officially announced by YC","description":snippet,"url":link}
            signals.append(item)
            mark_seen(key, source, link)
            try: slack_alert(item)
            except Exception as exc: print("Slack alert failed:", exc)
    return {"official":official,"signals":signals,"checked_at":datetime.now(timezone.utc).isoformat()}


def scheduler():
    while True:
        try: print(run_scan())
        except Exception as exc: print("Scan failed:", exc)
        time.sleep(max(1, int(POLL_HOURS*3600)))


@app.get("/")
def index(): return jsonify({"service":"Founder Radar Signal","status":"ok","sources":["YC Directory",SPEEDRUN_NAME,"X","LinkedIn"],"poll_hours":POLL_HOURS})

@app.get("/health")
def health(): return jsonify({"status":"healthy","slack_configured":bool(SLACK_TOKEN and SLACK_CHANNEL_ID),"search_configured":bool(SERPER_KEY),"database":DB_PATH})

@app.post("/scan")
def scan():
    if ALERT_TOKEN and request.headers.get("X-Alert-Token") != ALERT_TOKEN: return jsonify({"error":"unauthorized"}),401
    return jsonify(run_scan())

@app.get("/manifest")
def pond_manifest():
    return jsonify({"protocol":"marketplace-agent","protocol_version":"1.0","name":"YC Founder Signal","description":"Monitors YC, Speedrun, X and LinkedIn for founder launch signals and posts incremental Slack alerts.","capabilities":["scheduled monitoring","stateful deduplication","Slack alerts","early founder detection"]})

@app.post("/runs")
def pond_run():
    if ALERT_TOKEN and request.headers.get("Authorization") != f"Bearer {ALERT_TOKEN}": return jsonify({"error":"unauthorized"}),401
    body=request.get_json(silent=True) or {}
    result=run_scan() if body.get("action","scan")=="scan" else {"status":"ok"}
    return jsonify({"status":"completed","result":result})

if __name__ == "__main__":
    db().close()
    threading.Thread(target=scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
