import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "").strip()
ALERT_TOKEN = os.getenv("ALERT_TOKEN", "").strip()


def score_signal(payload: dict[str, Any]) -> tuple[int, list[str]]:
    text = " ".join(
        str(payload.get(key, ""))
        for key in ("text", "company", "founder", "bio")
    ).lower()

    score = 0
    reasons: list[str] = []
    keywords = {
        "yc": 3,
        "y combinator": 3,
        "yc s26": 5,
        "yc w26": 5,
        "speedrun": 3,
        "backed by y combinator": 5,
        "accepted into yc": 5,
        "we got into yc": 5,
        "moving to sf": 1,
        "san francisco": 1,
        "founder": 1,
    }
    for keyword, points in keywords.items():
        if keyword in text:
            score += points
            reasons.append(keyword)

    return score, reasons


def post_to_slack(payload: dict[str, Any], score: int, reasons: list[str]) -> None:
    if not SLACK_WEBHOOK_URL:
        raise RuntimeError("SLACK_WEBHOOK_URL is not configured")

    company = payload.get("company") or "Unknown company"
    founder = payload.get("founder") or "Unknown founder"
    batch = payload.get("batch") or "Unspecified"
    source = payload.get("source") or "Unknown source"
    status = payload.get("status") or "Founder announcement detected"
    original_post = payload.get("original_post") or payload.get("url") or ""
    company_url = payload.get("company_url") or ""

    text = (
        "🚨 *FOUNDER RADAR — YC SIGNAL*\n"
        f"*Company:* {company}\n"
        f"*Founder:* {founder}\n"
        f"*Batch:* {batch}\n"
        f"*Source:* {source}\n"
        f"*Status:* ⚡ {status}\n\n"
        f"*Signal score:* {score}/10\n"
        f"*Detected keywords:* {', '.join(reasons) if reasons else 'none'}\n\n"
        f"*Original post:* {original_post}\n"
        f"*Company:* {company_url}\n"
        f"*Detected:* {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    response = requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=15)
    response.raise_for_status()


def authorized() -> bool:
    if not ALERT_TOKEN:
        return True
    supplied = request.headers.get("X-Alert-Token", "")
    return supplied == ALERT_TOKEN


@app.get("/")
def index():
    return jsonify({
        "service": "Founder Radar Signal",
        "status": "ok",
        "endpoints": ["/health", "/ingest", "/demo"],
    })


@app.get("/health")
def health():
    return jsonify({"status": "healthy", "slack_configured": bool(SLACK_WEBHOOK_URL)})


@app.post("/ingest")
def ingest():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    if not payload.get("text") and not payload.get("company"):
        return jsonify({"error": "Send at least text or company"}), 400

    score, reasons = score_signal(payload)
    threshold = int(os.getenv("SIGNAL_THRESHOLD", "5"))

    if score < threshold:
        return jsonify({"alerted": False, "score": score, "threshold": threshold, "reasons": reasons})

    post_to_slack(payload, score, reasons)
    return jsonify({"alerted": True, "score": score, "reasons": reasons})


@app.post("/demo")
def demo():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401

    payload = {
        "company": "Demo Labs",
        "founder": "Jane Demo (@demo_founder)",
        "batch": "YC S26",
        "source": "X",
        "status": "Founder announcement detected before YC confirmation",
        "original_post": "https://x.com/example/status/123456",
        "company_url": "https://example.com",
        "text": "We got into YC S26! Excited to move to SF and start building.",
    }
    score, reasons = score_signal(payload)
    post_to_slack(payload, score, reasons)
    return jsonify({"alerted": True, "demo": True, "score": score, "reasons": reasons})


@app.post("/dedupe-key")
def dedupe_key():
    payload = request.get_json(silent=True) or {}
    raw = "|".join(str(payload.get(k, "")) for k in ("company", "founder", "url", "text"))
    return jsonify({"key": hashlib.sha256(raw.encode()).hexdigest()[:16]})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
