import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("founder-radar")
app = Flask(__name__)
DB = os.getenv("DATABASE_PATH", "state.db")
POLL_HOURS = float(os.getenv("POLL_HOURS", "8"))
YC_URL = os.getenv("YC_DIRECTORY_URL", "https://www.ycombinator.com/companies")
SPEEDRUN_URL = os.getenv("SPEEDRUN_URL", "https://speedrun.a16z.com/companies")
SERPER_KEY = os.getenv("SERPER_API_KEY", "")
SLACK_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL_ID", "")
ALERT_TOKEN = os.getenv("ALERT_TOKEN", "")
BASELINE = os.getenv("BASELINE_ON_FIRST_RUN", "true").lower() == "true"

YC_RE = re.compile(r"\bYC\s*[SWPFX]\d{2}\b", re.I)
SPEED_RE = re.compile(r"\b(?:cohort\s*)?(\d{3})\b", re.I)
YC_SIGNAL = [r"YC\s*[SWPFX]\d{2}", r"Y Combinator", r"got into YC", r"accepted into Y Combinator", r"joining Y Combinator"]
SPEED_SIGNAL = [r"a16z\s+Speedrun", r"Speedrun\s+(?:batch|cohort)"]


def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.execute("CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, source TEXT, url TEXT, seen_at TEXT, delivered_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS companies(identity TEXT PRIMARY KEY, name TEXT, program TEXT, batch TEXT, url TEXT, description TEXT, founder TEXT, first_seen_at TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    c.commit()
    return c


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def page(url):
    r = requests.get(url, timeout=30, headers={"User-Agent": "FounderRadarSignal/1.1"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def parse_yc():
    soup = page(YC_URL); out = {}
    for a in soup.select('a[href^="/companies/"]'):
        slug = a.get("href", "").rstrip("/").split("/")[-1]
        if not slug or slug in {"companies", "industry"}: continue
        text = clean(a.get_text(" ", strip=True)); context = clean(a.parent.get_text(" ", strip=True) if a.parent else text)[:1500]
        m = YC_RE.search(context)
        out[slug] = {"identity": "yc:" + slug, "name": text or slug.replace("-", " ").title(), "program": "YC", "batch": m.group(0).upper().replace(" ", "") if m else "Unspecified", "url": "https://www.ycombinator.com" + a["href"], "description": context, "founder": ""}
    return list(out.values())


def parse_speedrun():
    soup = page(SPEEDRUN_URL); out = {}
    for a in soup.select('a[href^="/companies/"]'):
        slug = a.get("href", "").rstrip("/").split("/")[-1]
        if not slug: continue
        text = clean(a.get_text(" ", strip=True)); context = clean(a.parent.get_text(" ", strip=True) if a.parent else text)[:1800]
        m = SPEED_RE.search(context)
        out[slug] = {"identity": "speedrun:" + slug, "name": text or slug.replace("-", " ").title(), "program": "Speedrun", "batch": "SR" + m.group(1) if m else "Unspecified", "url": "https://speedrun.a16z.com" + a["href"], "description": context, "founder": ""}
    return list(out.values())


def upsert_company(row):
    c = conn(); exists = c.execute("SELECT 1 FROM companies WHERE identity=?", (row["identity"],)).fetchone()
    c.execute("INSERT INTO companies VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(identity) DO UPDATE SET name=excluded.name,batch=excluded.batch,url=excluded.url,description=excluded.description", (row["identity"], row["name"], row["program"], row.get("batch"), row["url"], row.get("description", ""), row.get("founder", ""), now()))
    c.commit(); c.close(); return exists is not None


def official_names():
    c = conn(); rows = c.execute("SELECT name FROM companies").fetchall(); c.close(); return {clean(x[0]).lower() for x in rows}


def serper(q, n=20):
    if not SERPER_KEY: return []
    r = requests.post("https://google.serper.dev/search", headers={"X-API-KEY": SERPER_KEY, "Content-Type": "application/json"}, json={"q": q, "num": n}, timeout=30)
    r.raise_for_status(); return r.json().get("organic", [])


def extract_company(text, url):
    patterns = [r"(?:building|launching|launched|introducing|meet)\s+([A-Z][A-Za-z0-9&.' -]{1,60}?)(?:\s+\(YC|\s+is\s+|\s+—|[.!?,]|$)", r"\b([A-Z][A-Za-z0-9&.' -]{1,60})\s*\(YC\s*[SWPFX]\d{2}\)"]
    for p in patterns:
        m = re.search(p, text, re.I)
        if m and 1 < len(clean(m.group(1))) < 70: return clean(m.group(1)).strip(" .,-–")
    path = urlparse(url).path.strip("/").split("/")
    if len(path) > 1 and path[0] == "company": return path[1].replace("-", " ").title()
    return "Unknown company"


def social_signals(source):
    if source == "X":
        qs = ['site:x.com ("YC S26" OR "YC W26" OR "got into YC" OR "accepted into Y Combinator" OR "joining Y Combinator")', 'site:x.com ("a16z Speedrun" OR "Speedrun batch" OR "Speedrun cohort")']
        hosts = ("x.com", "twitter.com")
    else:
        qs = ['site:linkedin.com/posts ("YC S26" OR "YC W26" OR "got into YC" OR "accepted into Y Combinator" OR "joining Y Combinator")', 'site:linkedin.com/posts ("a16z Speedrun" OR "Speedrun batch" OR "Speedrun cohort")', 'site:linkedin.com/company ("YC S26" OR "Y Combinator" OR "a16z Speedrun")']
        hosts = ("linkedin.com",)
    out = {}
    for q in qs:
        for r in serper(q):
            url = r.get("link", ""); host = urlparse(url).netloc.lower()
            if not any(h in host for h in hosts): continue
            text = clean((r.get("title", "") + " " + r.get("snippet", "")))
            yc = any(re.search(p, text, re.I) for p in YC_SIGNAL); speed = any(re.search(p, text, re.I) for p in SPEED_SIGNAL)
            if not (yc or speed): continue
            batch = YC_RE.search(text)
            founder = clean(r.get("title", "").split(" - ")[0]) or "Unknown founder"
            out[url] = {"source": source, "program": "Speedrun" if speed and not yc else "YC", "company": extract_company(text, url), "founder": founder, "batch": batch.group(0).upper().replace(" ", "") if batch else "Unspecified", "description": r.get("snippet", "")[:900], "url": url, "text": text}
    return list(out.values())


def official_social_hit(signal):
    company = clean(signal.get("company", ""))
    if company == "Unknown company": return False
    if signal["program"] == "YC": q = f'site:x.com/ycombinator "{company}" OR site:linkedin.com/company/y-combinator "{company}"'
    else: q = f'site:x.com/a16z "{company}" OR site:linkedin.com/company/a16z "{company}"'
    hits = serper(q, 10)
    return any(company.lower() in clean(h.get("title", "") + " " + h.get("snippet", "")).lower() for h in hits)


def seen(key):
    c = conn(); x = c.execute("SELECT 1 FROM seen WHERE key=?", (key,)).fetchone(); c.close(); return bool(x)


def claim(key, source, url):
    c = conn(); cur = c.execute("INSERT OR IGNORE INTO seen(key,source,url,seen_at) VALUES(?,?,?,?)", (key, source, url, now())); c.commit(); c.close(); return cur.rowcount == 1


def slack(item):
    if not SLACK_TOKEN or not SLACK_CHANNEL: return {"sent": False, "reason": "Slack credentials not configured"}
    if item["type"] == "early": head = "🔥 *EARLY YC SIGNAL — Founder Announced Before YC*"
    elif item["program"] == "Speedrun": head = "🚀 *NEW SPEEDRUN COMPANY*"
    else: head = "🟢 *NEW YC COMPANY*"
    text = f"{head}\n\n*Company:* {item['company']}\n*Founder:* {item.get('founder','Not listed')}\n*Batch:* {item.get('batch','Unspecified')}\n*Source:* {item['source']}\n*Status:* {item['status']}\n\n*Description:* {item.get('description','Not available')[:900]}\n*Original post / profile:* {item['url']}\n*Detected:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    r = requests.post("https://slack.com/api/chat.postMessage", headers={"Authorization": f"Bearer {SLACK_TOKEN}", "Content-Type": "application/json; charset=utf-8"}, json={"channel": SLACK_CHANNEL, "text": text}, timeout=20); r.raise_for_status(); data = r.json()
    if not data.get("ok"): raise RuntimeError(data.get("error", "Slack API error"))
    return {"sent": True, "ts": data.get("ts", "")}


def emit(item):
    key = hashlib.sha256((item["source"] + "|" + item["url"]).encode()).hexdigest()
    if not claim(key, item["source"], item["url"]): return {"sent": False, "duplicate": True}
    try:
        result = slack(item)
        c = conn(); c.execute("UPDATE seen SET delivered_at=? WHERE key=?", (now() if result.get("sent") else "", key)); c.commit(); c.close(); return result
    except Exception:
        log.exception("Slack delivery failed")
        return {"sent": False, "delivery_error": True}


def scan():
    first = not bool(official_names()); official_alerts = []
    for row in parse_yc() + parse_speedrun():
        new = not upsert_company(row)
        if new and (not first or not BASELINE):
            official_alerts.append(emit({"type":"official","program":row["program"],"company":row["name"],"founder":row.get("founder","Not listed"),"batch":row.get("batch","Unspecified"),"source":"YC Directory" if row["program"] == "YC" else "a16z Speedrun Directory","status":"Confirmed by YC" if row["program"] == "YC" else "Confirmed by Speedrun","description":row.get("description",""),"url":row["url"]}))
    social_alerts = []
    if SERPER_KEY:
        names = official_names()
        for s in social_signals("X") + social_signals("LinkedIn"):
            if s["company"].lower() in names or official_social_hit(s): continue
            social_alerts.append(emit({"type":"early","program":s["program"],"company":s["company"],"founder":s["founder"],"batch":s["batch"],"source":s["source"],"status":"Founder announced / not yet officially announced by program","description":s["description"],"url":s["url"]}))
    result = {"checked_at":now(),"official_alerts":official_alerts,"social_alerts":social_alerts,"new_alert_count":sum(x.get("sent",False) for x in official_alerts + social_alerts),"sources":["YC Directory","a16z Speedrun Directory","X","LinkedIn"]}
    c=conn(); c.execute("INSERT INTO meta VALUES ('last_scan',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(result),)); c.commit(); c.close(); return result


def scheduler():
    while True:
        try: log.info("scan=%s", scan())
        except Exception: log.exception("scheduled scan failed")
        time.sleep(max(60, int(POLL_HOURS*3600)))


def auth(): return not ALERT_TOKEN or request.headers.get("Authorization") == f"Bearer {ALERT_TOKEN}"

@app.get("/")
def index(): return jsonify({"service":"Founder Radar Signal","status":"ok","poll_hours":POLL_HOURS,"sources":["YC Directory","a16z Speedrun Directory","X","LinkedIn"]})

@app.get("/health")
def health(): return jsonify({"status":"healthy","slack_configured":bool(SLACK_TOKEN and SLACK_CHANNEL),"social_search_configured":bool(SERPER_KEY),"database":DB})

@app.post("/scan")
def manual_scan():
    if not auth(): return jsonify({"error":"unauthorized"}),401
    return jsonify(scan())

@app.get("/manifest")
def manifest():
    return jsonify({"protocol":"marketplace-agent","protocol_version":"1.0","agent_version":"1.1.0","metadata":{"name":"Founder Radar Signal","short_description":"Early YC and a16z Speedrun founder-launch monitor with Slack alerts.","description":"Monitors YC, a16z Speedrun, X and LinkedIn. Founder-first signals can alert before official accelerator announcements; directories provide confirmation.","category":"sales"},"actions":[{"id":"scan_now","name":"Scan now","description":"Run an incremental scan of YC, a16z Speedrun, X and LinkedIn.","input_schema":{"type":"object","properties":{},"required":[],"additionalProperties":False}}],"capabilities":{"sync":True,"streaming":False,"async_tasks":False,"cancellation":False,"attachments":False,"feedback":False},"input_modes":["text/plain"],"output_modes":["text/markdown"],"limits":{"max_request_bytes":1048576,"max_attachment_bytes":0,"max_run_seconds":120}})

@app.post("/runs")
def runs():
    if not auth(): return jsonify({"error":{"code":"unauthorized","message":"The Access Key is missing or invalid."}}),401
    if request.headers.get("X-Agent-Protocol-Version") != "1.0": return jsonify({"error":{"code":"unsupported_protocol_version","message":"Only Pond Protocol V1 (1.0) is supported."}}),400
    body=request.get_json(silent=True) or {}
    if body.get("action_id") != "scan_now": return jsonify({"error":{"code":"unsupported_operation","message":"The scan_now action is required."}}),400
    result=scan(); return jsonify({"run_id":body.get("run_id","local-run"),"output":[{"type":"text","text":f"Scan completed. {result['new_alert_count']} new Slack alerts were sent."}],"usage":{"input_tokens":0,"output_tokens":0}})

if __name__ == "__main__":
    threading.Thread(target=scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT","10000")))
