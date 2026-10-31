# Synthesis: human-supervised literature review

Synthesis is a local-first research platform for studying human review at Search → Extraction → Organization → Synthesis checkpoints. Runs persist as versioned artifacts, capture the source set and experimental settings, pause for reviewer decisions, and export as JSONL for analysis.

## Start locally with OpenAI

Requirements: Python 3.11+, Node.js 20+, npm. OpenAI API requests require internet access and are billed separately from a ChatGPT subscription. You may run the application locally while calling OpenAI remotely.

1. Copy `backend/.env.example` to `backend/.env` and set `OPENAI_API_KEY` and `OPENAI_MODEL_ID` to your API key and exact supported model ID. Keep the key in the backend environment; never put it in the frontend.
2. Copy `frontend/.env.local.example` to `frontend/.env.local`.
3. In terminal 1: `cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && alembic upgrade head && uvicorn app.main:app --host 127.0.0.1 --port 8000`
4. In terminal 2: `cd backend && . .venv/bin/activate && python -m app.worker`
5. In terminal 3: `cd frontend && npm install && npm run dev`
6. Open http://localhost:3000. Use **Model connections → Test connection** before starting a run.

The database-backed worker claims queued runs independently of HTTP requests. It resumes committed workflow checkpoints after a worker restart; calls interrupted before artifact commit may be retried and billed again. SQLite is suitable for a single-machine pilot. Use PostgreSQL for concurrent workers or a shared deployment.

## Run a local-corpus experiment

1. Create a project and choose a human-review policy. For the treatment condition in which a person reviews every agent, choose **Review every stage**.
2. Open **Saved papers** and upload text-based PDFs to create the fixed corpus. Scanned PDFs require OCR first. PDFs are extracted locally and stored in the configured database.
3. In **Experiment run settings**, enter a study label, replicate ID, and random seed. Select **Saved corpus (local / fixed)**. This mode does not call OpenAlex. It records the frozen source IDs and metadata with the Search artifact; papers uploaded later are excluded from that run.
4. Select OpenAI for each AI stage. Search is deterministic corpus selection; extraction, organization, and synthesis call the configured model.
5. At each configured review gate, inspect the artifact and approve, request revision, or return to the previous checkpoint. Reviewer notes and decisions are appended to the event history.
6. Use **Export run records (JSONL)** to download project metadata, run configuration, source snapshot, artifacts, model identifiers, prompt hashes, and provider usage fields when returned by the provider.

For a live-search run, select **Search OpenAlex (internet)**. OpenAlex results can change over time, so use Saved corpus mode when matching a source set across experimental conditions. Separate runs do not share mutable artifact state. Temperature is sent to the model endpoint; the recorded replicate seed is metadata and is not sent because seed support varies by model/provider. Temperature zero does not guarantee identical results across models or provider revisions. The protocol marker is currently `1.0`; change it when prompts or workflow behavior change and keep protocol versions consistent within a study.

## OpenRouter models

When ready, configure `OPENROUTER_API_KEY` once and set `OPENROUTER_QWEN_MODEL_ID`, `OPENROUTER_KIMI_MODEL_ID`, and `OPENROUTER_DEEPSEEK_MODEL_ID` to the exact model slugs from OpenRouter. OpenAI uses its separate `OPENAI_API_KEY` and `OPENAI_MODEL_ID`. All provider secrets remain on the backend. Each connection test sends a small billable request.

## Container deployment

Copy `.env.example` to `.env`, set strong unique secrets and model configuration, then run `docker compose up --build -d`. The web port binds to `127.0.0.1` by default. This setup is intended for one researcher on a trusted machine. Do not expose it to a network or multiple users without adding authenticated identity, per-user project authorization, TLS, backups, retention/deletion controls, and operational monitoring. Uploaded PDFs and extracted text are stored in the database; model providers receive paper text sent for analysis.

## Verification

Backend checks: `cd backend && . .venv/bin/activate && python -m unittest discover -s tests`. Frontend checks: `cd frontend && npm run build`. A successful build does not validate external credentials; use the in-app connection checks and a complete small-corpus run before collecting study data.
