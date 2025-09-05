# MCP Bridge (Railway)

This service keeps a WebSocket <-> stdio bridge alive so your agent can talk to an MCP tool.

## Deploy (Railway, free + simple)

1) Push this repo to GitHub.
2) Railway → **New Project** → **Deploy from Repo**.
3) Choose **Background Worker**.
4) **Start Command**: auto-detected from `Procfile` (`python -u echo.py`).
5) **Variables**:
   - `MCP_ENDPOINT` = wss://... (the agent's WS endpoint)
   - `API_TOKEN` = your secret token (if your upstream requires it)
6) Deploy. Railway will keep it running and restart on crash.

## Local test

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export MCP_ENDPOINT=wss://YOUR-AGENT-ENDPOINT
export API_TOKEN=yourtoken
python -u echo.py
