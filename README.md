# Invoice Action Agent

FastAPI A2A API extracted from the supplied source. This is an API service, not a web UI.

## Deploy on Render

1. Upload these files to a GitHub, GitLab, or Bitbucket repository.
2. In Render choose New > Web Service and connect that repository.
3. Runtime: Python. Build command: `pip install -r requirements.txt`.
4. Start command: `uvicorn main:app_Q2 --host 0.0.0.0 --port $PORT --workers 1`.
5. Set health check path to `/healthz`.
6. In Environment add `GEMINI_API_KEY` with your actual Google AI Studio key, then save and redeploy. Do not include quotation marks in the Dashboard value.
7. Optionally set `GEMINI_MODEL` to another model available to your Google project. Default: `gemini-3.5-flash-lite` (retained from the supplied source).
8. Open `/.well-known/agent-card.json` and check the advertised `/a2a/` URL. Render's external URL is detected automatically; `PUBLIC_BASE_URL` can override it.

## Gemini key

Create a key at https://aistudio.google.com/api-keys . Keep it in server environment variables, never in Git. The original code sent the literal text GEMINI_API_KEY; main.py now reads `os.getenv("GEMINI_API_KEY", "")` and sends that value via the X-goog-api-key header.

## Local run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY='your-key'
uvicorn main:app_Q2 --host 0.0.0.0 --port 8000 --workers 1
```

`.env.example` documents variables; the app does not automatically load .env files.

## Storage and protocol

SQLite state and decision logs are in DATA_DIR (default current directory). For persistent hosted state, attach a persistent disk and set DATA_DIR to its mount path. Ephemeral hosting loses tasks/cache/logs on replacement. Use one instance and one worker with this implementation.

A2A requests require `Authorization: Bearer <caller-token>` and `A2A-Version: 1.0`; POSTs also require `Content-Type: application/a2a+json`. The original protocol accepts any nonempty caller token as an identity; it does not validate tokens against an issuer or allowlist. Retain it only in a controlled test environment until proper authentication is added.

The app records accepted action receipts; it contains no integration that transfers money or executes actions in an accounting system. Agent Card and health endpoint are public. Missing Gemini configuration returns 503 on new invoice batches. Gemini failures with a configured key retain the original open_exception fallback.

## Verification

Source syntax and deployment configuration were checked. Live Gemini requests and hosted deployment require your key and Git repository.
