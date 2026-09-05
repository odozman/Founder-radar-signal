# Founder Radar Signal

A personal Slack monitor for early YC and a16z Speedrun founder-launch signals.

## What it monitors

1. **Y Combinator company directory** — source of truth for confirmed YC companies.
2. **a16z Speedrun company directory** — source of truth for confirmed Speedrun companies. Important: public evidence indicates Speedrun is an a16z program, not a Y Combinator sub-program, so this implementation labels it separately rather than claiming it is YC-owned.
3. **X / Twitter** — founder posts and launch announcements mentioning YC/Speedrun.
4. **LinkedIn** — founder posts and company-page signals mentioning YC/Speedrun.

The monitor polls every 8 hours by default and uses SQLite to persist company and social-signal state, preventing repeat alerts.

## Early YC detection

The key rule is **founder-first detection**: a founder announcement can trigger before YC's own announcement. The social result is reconciled against the official YC directory and a search for official YC social mentions. If the company is not yet in the directory and no matching official social announcement is found, the Slack alert is marked:

`Founder announced / not yet officially announced by program`

This is deliberately different from simply watching YC's social account.

## Architecture

- Python + Flask
- SQLite persistent state
- BeautifulSoup + requests for public directories
- Serper for indexed X/LinkedIn discovery
- Slack `chat.postMessage`
- Background scheduler with configurable polling interval
- Pond-compatible Protocol V1 `/manifest` and `/runs` endpoints
- Modular source functions so additional social sources can be added later

## Files

- `app.py` — Flask service, scheduler, orchestration and Pond endpoints
- `sources.py` — YC, Speedrun, X and LinkedIn discovery/reconciliation
- `state.py` — persistent SQLite state and deduplication
- `slack_client.py` — Slack alert formatting and delivery
- `render.yaml` — Render deployment configuration
- `requirements.txt` — pinned Python dependencies

## Environment variables

Required for full monitoring:

- `SLACK_BOT_TOKEN` — Slack bot token (`xoxb-...`)
- `SLACK_CHANNEL_ID` — destination channel ID or DM conversation ID
- `SERPER_API_KEY` — Serper API key for indexed X/LinkedIn discovery
- `ALERT_TOKEN` — secret protecting manual/Pond scan endpoints

Optional:

- `POLL_HOURS` — default `8`
- `DATABASE_PATH` — default `state.db`
- `YC_DIRECTORY_URL` — default `https://www.ycombinator.com/companies`
- `SPEEDRUN_URL` — default `https://speedrun.a16z.com/companies`
- `BASELINE_ON_FIRST_RUN` — default `true`; first scan seeds the directory without flooding Slack with historical companies

## Local setup

```bash
git clone https://github.com/odozman/Founder-radar-signal.git
cd Founder-radar-signal
python -m venv .venv
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set the environment variables, then run:

```bash
python app.py
```

The service exposes:

- `GET /health` — health/config status
- `POST /scan` — manually trigger an incremental scan
- `GET /manifest` — Pond Protocol V1 manifest
- `POST /runs` — Pond Protocol V1 action execution

## Slack setup — single workspace

1. Open Slack's app management page and create a new app **From scratch**.
2. Give it a name such as `Founder Radar Signal` and select the single target workspace.
3. Under **OAuth & Permissions**, add the bot scope `chat:write`.
4. Install/reinstall the app to the workspace and copy the **Bot User OAuth Token**. Keep it secret.
5. Create or choose the destination channel.
6. Invite the bot to that channel.
7. Copy the channel ID from Slack and set `SLACK_CHANNEL_ID`.
8. Set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL_ID` in the runtime environment.
9. Run the service and call `/health`. It should report `slack_configured: true`.
10. Run `POST /scan` once. A newly discovered signal will be posted to the configured channel.

For a DM, use the Slack conversation/DM ID as `SLACK_CHANNEL_ID`.

## Render deployment

The included `render.yaml` is a single-worker Python web service. This matters because the scheduler is stateful. For production, attach persistent storage or migrate the SQLite state to PostgreSQL so redeploys do not erase deduplication state.

Set these Render environment variables:

- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_ID`
- `SERPER_API_KEY`
- `ALERT_TOKEN`
- `POLL_HOURS=8`
- `DATABASE_PATH=/var/data/state.db` if using a mounted persistent disk

## Pond integration

The service implements the Pond marketplace-agent Protocol V1 shape:

- public `GET /manifest`
- authenticated `POST /runs`
- `X-Agent-Protocol-Version: 1.0`
- `Authorization: Bearer <POND_ACCESS_KEY>` (the deployment can use the same value as `ALERT_TOKEN`)
- action ID: `scan_now`

The repository is **Pond-ready**, but Pond registration is an external account/deployment step and must not be claimed as completed until the deployed HTTPS endpoint is registered and successfully health-checked in Pond.

## Detection limitations

X and LinkedIn do not provide unrestricted public historical search APIs. This MVP uses Serper's indexed results, so detection depends on what search engines have indexed. For production-grade coverage, a compliant direct API or social-data provider can replace the discovery functions without changing the scheduler/state/Slack/Pond layers.

The implementation intentionally avoids claiming an early signal is confirmed by YC. Directory presence is the confirmation source of truth.

## Example Slack alert

```text
🔥 EARLY YC SIGNAL — Founder Announced Before YC

Company: Example AI
Founder: Jane Doe
Batch: YCS26
Source: X
Status: Founder announced / not yet officially announced by program

Description: We got into YC S26! Excited to start building...
Original post / profile: https://x.com/example/status/123
Detected: 2026-09-05 10:00 UTC
```

## License

MIT
