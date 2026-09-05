# Founder Radar Signal

Founder Radar Signal monitors startup-founder launch signals and sends incremental alerts to Slack.

## What it monitors

1. Y Combinator company directory
2. a16z Speedrun directory
3. X posts indexed by Google
4. LinkedIn posts indexed by Google

The monitor runs every 8 hours by default and stores previously seen source URLs in SQLite so the same signal is not repeatedly alerted.

### Important note about Speedrun

There is not currently an official YC program directory called Speedrun in this implementation. The configured Speedrun source is **a16z Speedrun**. Change `SPEEDRUN_URL` and `SPEEDRUN_NAME` if the intended Speedrun directory is different.

## Architecture

- Python + Flask
- SQLite state store
- Requests + BeautifulSoup for public-page retrieval
- Serper for Google-indexed X/LinkedIn discovery
- Slack Bot API for channel alerts
- Background 8-hour polling loop
- Pond-compatible `/manifest` and authenticated `/runs` endpoints

## Slack alert format

Each new signal includes:

- Company
- Founder
- Batch, when detected
- Source
- Status
- Description/snippet
- Original post URL
- Detection timestamp

## Environment variables

Required for full operation:

- `SLACK_BOT_TOKEN`: Slack bot token with permission to post in the target channel
- `SLACK_CHANNEL_ID`: destination channel ID, e.g. `C0BV4HMSEBW`
- `SERPER_API_KEY`: API key for Google-indexed social discovery
- `ALERT_TOKEN`: shared secret for protected scan/Pond endpoints

Optional:

- `POLL_HOURS` default `8`
- `DATABASE_PATH` default `state.db`
- `YC_DIRECTORY_URL` default `https://www.ycombinator.com/companies`
- `SPEEDRUN_URL` default `https://speedrun.a16z.com/`
- `SPEEDRUN_NAME` default `a16z Speedrun`

## Local setup

```bash
git clone https://github.com/odozman/Founder-radar-signal.git
cd Founder-radar-signal
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set the environment variables, then run:

```bash
python app.py
```

Health check:

```text
GET /health
```

Manual scan:

```text
POST /scan
```

## Slack setup

1. Create a Slack app in the Slack API dashboard.
2. Add a bot user.
3. Give the bot permission to send messages (`chat:write`).
4. Install the app into the workspace.
5. Invite the bot to the destination channel.
6. Put the bot token in `SLACK_BOT_TOKEN`.
7. Set `SLACK_CHANNEL_ID` to the destination channel ID.
8. Deploy the service and call `/health` to confirm Slack configuration is detected.

## Render deployment

The included `render.yaml` is configured for a Python web service with one Gunicorn worker. One worker is intentional because the polling loop is stateful and should not run concurrently in multiple web workers.

For production persistence, use a Render persistent disk or move the `seen` and `companies` tables to PostgreSQL. Without persistent storage, a restart/redeploy can reset deduplication state.

## Pond

The application exposes:

- `GET /manifest`
- `POST /runs`

`POST /runs` accepts `Authorization: Bearer <ALERT_TOKEN>` and can execute a scan using `{\"action\":\"scan\"}`.

These endpoints are intended to make the monitor easy to connect to an agent infrastructure such as Pond. Pond registration and health verification are not claimed as complete until the deployed public HTTPS endpoint is registered and tested with a real Pond access key.

## Early-signal logic

The social discovery layer searches for common founder language such as YC batch references, joining Y Combinator, acceptance announcements, and Speedrun references. New source URLs are deduplicated before Slack delivery.

The current implementation is an MVP. For higher-confidence production detection, add direct X/LinkedIn APIs or a compliant social-data provider, then reconcile each social company against structured YC company records instead of relying only on page-level fingerprints.

## Example signal

A public LinkedIn announcement from Vestris founders Aahil Valliani and Joshua Tang referenced joining Y Combinator S26 and building Vestris. This is the type of founder-first signal the bot is designed to surface before relying solely on an official directory update.
