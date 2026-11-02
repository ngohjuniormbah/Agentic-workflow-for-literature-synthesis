# Synthesis: human-supervised literature review

Synthesis is a local-first research platform for studying human review at Search → Extraction → Organization → Synthesis checkpoints. Runs persist as versioned artifacts, capture the source set and experimental settings, pause at scenario-specific review gates, and export as JSONL for analysis.

The stages have separate responsibilities: Search retrieves and deduplicates candidate records, then the selected LLM ranks all candidates without dropping any; Extraction identifies claims with verbatim evidence quotes and methods/limitations; Organization groups the reviewed claims and records agreements, disagreements, and gaps; Synthesis writes a cited draft from those claims and themes. The same selected LLM is called in all four stages. OpenAlex or the saved corpus supplies Search candidates. At a Search review gate, the researcher can include or exclude each candidate. The decision and screened source snapshot are saved before the next stage, and only included source IDs flow to Extraction. At review gates, the researcher can inspect the artifact, add feedback, and approve, retry, or return a stage. Review decisions record four 1–5 ratings (relevance, evidence accuracy, coverage, and traceability) in the event log.

## Start locally with OpenAI

Requirements: Python 3.11+, Node.js 20+, npm. OpenAI API requests require internet access and are billed separately from a ChatGPT subscription. You may run the application locally while calling OpenAI remotely.

1. Copy `backend/.env.example` to `backend/.env` and set `OPENAI_API_KEY` and `OPENAI_MODEL_ID` to your API key and exact supported model ID. Keep the key in the backend environment; never put it in the frontend.
2. Copy `frontend/.env.local.example` to `frontend/.env.local`.
3. In terminal 1: `cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && alembic upgrade head && uvicorn app.main:app --host 127.0.0.1 --port 8000`
4. In terminal 2: `cd backend && . .venv/bin/activate && python -m app.worker`
5. In terminal 3: `cd frontend && npm install && npm run dev`
6. Open http://localhost:3000. Use **Model connections → Test connection** before starting a run.

The database-backed worker claims queued runs independently of HTTP requests. It resumes committed workflow checkpoints after a worker restart; calls interrupted before artifact commit may be retried and billed again. SQLite is suitable for a single-machine pilot. Use PostgreSQL for concurrent workers or a shared deployment.

## Run the four-scenario experiment

Each run selects one of four conditions. All four workflow stages execute in every condition:

| Condition | Human review gates | Operational definition |
| --- | --- | --- |
| `full_audit` | Search, Extraction, Organization, Synthesis | Review and rate each stage before it proceeds. Search screening is saved; only included papers reach Extraction. |
| `targeted_retry` | Search, Synthesis | Review and screen the Search results, then review the final synthesis. A retry is requested by the human when needed. |
| `endpoint_oversight` | Synthesis | The workflow runs through Search, Extraction, and Organization without review gates; the human reviews the final synthesis. |
| `midpoint_gating` | Extraction | Search runs, then a human reviews the extracted claims before Organization and Synthesis continue. |

For a four-model comparison, the app assigns one provider to all four agents within each model condition. In Search, the provider ranks every retrieved candidate; OpenAlex or the fixed corpus handles retrieval. The `condition` field selects the actual gate behavior; it is not merely a free-text label. Each run stores the scenario, mapped review policy, model assignments, replicate ID, ranked and screened source snapshots, and protocol version.

The 4 × 4 design is **16 model-scenario configurations**. Replicates are additional runs: for example, four replicates per configuration require 64 complete runs. Keep replicate count, corpus, research question, model settings, and reviewer procedure in your written protocol before collecting results.

1. Create one project with the final research question and scope.
2. Build and freeze one corpus for comparison. Prefer text-based PDFs uploaded to **Saved papers**; scanned PDFs require OCR first. PDFs are extracted locally. Keep this corpus unchanged across model and scenario runs. Use **Saved corpus (local / fixed)** for every configuration so live search changes do not confound the comparison.
3. In each run, enter the study label, scenario, replicate ID, seed, and temperature. For each model condition, choose that same provider for all four agents. Test its connection first.
4. At the configured gates, use the same inclusion rules and review rubric. For Search gates, apply a prewritten screening rule and retain the same final source set in every condition. Record reviewer notes and do not treat technical pilots as confirmatory runs.
5. Export **Run records (JSONL)** after each run. It contains run settings, scenario, ranked and screened source snapshots and hashes, artifacts, prompt hashes, reviewer decisions/ratings, stage and provider-request durations, token usage, and provider-reported cost when returned. Provider cost is not guaranteed for every API; do not interpret a missing cost field as zero.

Separate runs do not share mutable artifact state. Temperature is sent to the model endpoint; the recorded replicate seed is metadata and is not sent because seed support varies by model/provider. Temperature zero does not guarantee identical results across models or provider revisions. Protocol version `1.2` records the scenario-gated workflow and LLM-ranked Search behavior; keep protocol versions consistent within a study.

## OpenRouter models

When ready, configure `OPENROUTER_API_KEY` once and set `OPENROUTER_QWEN_MODEL_ID`, `OPENROUTER_KIMI_MODEL_ID`, and `OPENROUTER_DEEPSEEK_MODEL_ID` to the exact model slugs from OpenRouter. OpenAI uses its separate `OPENAI_API_KEY` and `OPENAI_MODEL_ID`. All provider secrets remain on the backend. Each connection test sends a small billable request.

## Container deployment

Copy `.env.example` to `.env`, set strong unique secrets and model configuration, then run `docker compose up --build -d`. The web port binds to `127.0.0.1` by default. This setup is intended for one researcher on a trusted machine. Do not expose it to a network or multiple users without adding authenticated identity, per-user project authorization, TLS, backups, retention/deletion controls, and operational monitoring. Uploaded PDFs and extracted text are stored in the database; model providers receive paper text sent for analysis.

## Verification

Backend checks: `cd backend && . .venv/bin/activate && python -m unittest discover -s tests`. Frontend checks: `cd frontend && npm run build`. A successful build does not validate external credentials; use the in-app connection checks and a complete small-corpus run before collecting study data.
