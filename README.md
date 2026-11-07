# Synthesis: human-supervised literature review

Synthesis is a local-first research platform for studying human review at Search → Extraction → Organization → Synthesis checkpoints. Runs persist as versioned artifacts, capture the source set and experimental settings, pause at scenario-specific review gates, and export as JSONL for analysis.

The stages have separate responsibilities: Search retrieves and deduplicates candidate records, then the selected LLM ranks all candidates without dropping any; Extraction identifies claims with verbatim evidence quotes and methods/limitations; Organization groups the reviewed claims and records agreements, disagreements, and gaps; Synthesis writes a cited draft from those claims and themes. The same selected LLM is called in all four stages. OpenAlex or the saved corpus supplies Search candidates. At a Search review gate, the researcher can include or exclude each candidate. The decision and screened source snapshot are saved before the next stage, and only included source IDs flow to Extraction. At review gates, the researcher can inspect the artifact, add feedback, and approve, retry, or return a stage. Review decisions record four 1–5 ratings (relevance, evidence accuracy, coverage, and traceability) in the event log.

## Start locally with OpenAI

Requirements: Python 3.11+, Node.js 20+, npm. OpenAI API requests require internet access and are billed separately from a ChatGPT subscription. You may run the application locally while calling OpenAI remotely.

1. Copy `backend/.env.example` to `backend/.env` and set `OPENAI_API_KEY` and `OPENAI_MODEL` to your API key and exact supported model ID. Keep the key in the backend environment; never put it in the frontend.
2. Copy `frontend/.env.local.example` to `frontend/.env.local`.
3. In terminal 1: `cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && alembic upgrade head && uvicorn app.main:app --host 127.0.0.1 --port 8000`
4. In terminal 2: `cd backend && . .venv/bin/activate && python -m app.worker`
5. In terminal 3: `cd frontend && npm install && npm run dev`
6. Open http://localhost:3000. Use **Model connections → Test connection** before starting a run.

The database-backed worker claims queued runs independently of HTTP requests. It resumes committed workflow checkpoints after a worker restart; calls interrupted before artifact commit may be retried and billed again. SQLite is suitable for a single-machine pilot. Use PostgreSQL for concurrent workers or a shared deployment.

## Run the controlled two-condition experiment

The initial paper protocol compares two conditions using the same four agents, one OpenAI model, one research question, one frozen source corpus, identical prompts/schemas/settings, and one independent evaluation rubric.

| Condition | Workflow | Operational definition |
| --- | --- | --- |
| `without_intermediate_human_supervision` | Search → Extraction → Organization → Synthesis | All four stages execute and their original outputs are retained. No review action or correction is allowed between stages. Quality evaluation happens separately after completion. |
| `with_human_supervision_all_stages` | Search → Review → Extraction → Review → Organization → Review → Synthesis → Review | The run pauses after every stage. A reviewer can approve, request revision, or approve a structured correction saved as a new artifact version. The model output is preserved. |

The experimental unit is a run. For a paired comparison, use the same **Saved corpus (local / fixed)**, question, scope, OpenAI model ID, temperature, prompts/schema version, and reviewer instructions for both conditions. OpenAlex search and ranking should be documented as a separate discovery/search-evaluation step; do not run OpenAlex independently for each paired condition because retrieval differences would confound downstream comparisons. A run freezes its selected source IDs and a content hash before extraction.

The app keeps workflow approval decisions separate from independent quality scores. Review comments, decisions, corrections, screening selections, and optional measured review duration are recorded in durable `review_records`; corrections create a new artifact version and never overwrite the original. In Condition B, a reviewer at Extraction, Organization, or Generation can restart at Extraction from the same approved Search snapshot. The restart reason, source IDs, source hash, and review duration are recorded, and previous artifacts remain in the run history. Independent reference annotations, evaluator scores, error labels, descriptive paired comparison, and a ZIP evidence package are available. Field presence is reported only as completeness, not semantic accuracy. The comparison reports descriptive statistics only; it does not calculate confidence intervals, significance tests, semantic field accuracy, or inter-rater agreement. PDF and DOCX rendering are not included. Do not describe these as measured results unless a human evaluator has entered the underlying records.

### Research procedure

1. Define the literature-search question, scope, inclusion/exclusion criteria, and a reference set/search log before looking at model outputs. Use **OpenAlex corpus discovery** to search by the project question; screen and freeze the candidate corpus. Keep the query, date, parameters, duplicate count, and reference set with the study. Search is retrieval plus LLM ranking of the retrieved candidates, not a claim of exhaustive database coverage.
2. Create a project with the research question and scope. Add the frozen corpus in **Saved papers** and use **Saved corpus (local / fixed)** for every formal run.
3. Test OpenAI and record the exact model ID. Create paired runs with matching replicate IDs (for example, `pair-01-A` and `pair-01-B`), temperature, source snapshot, and other settings; vary only the experimental condition. Run Condition A and B on the same frozen Saved corpus. A pilot with one pair checks the process; it is not enough by itself for a strong inferential claim.
4. In the supervised condition, follow the prewritten review protocol. Record reasons for approval/revision/correction, preserve original and corrected artifacts, and log review time. Do not alter workflow artifacts in the no-intermediate condition.
5. Evaluate completed outputs independently using the same versioned rubric. Do not use workflow approvals as quality scores or let the model be the only evaluator.
6. Export the raw run records and keep the protocol, corpus manifest, reference annotations, evaluator scores, and error log with the paper's research records.

Use descriptive statistics for the observations collected. Do not claim statistical significance unless the sample size and chosen test justify it. The recorded replicate seed is metadata and is not sent to the API; temperature zero does not make hosted model outputs deterministic. Machine time, human review time, token usage, and provider-reported cost are different measures; missing provider cost must not be treated as zero. Reference annotations are stored for traceability, but automated semantic agreement scores are not calculated. Search precision/recall require evaluator-entered relevant paper IDs; current Search metric is based on ranked IDs and may not fully capture retrieval recall without a well-defined reference set.

### Simple experiment log CSV

Use **Download simple experiment log (CSV)** in the project workspace to download a readable run register. The server builds it from the durable database records each time it is requested, so it includes discovery runs and every experiment run (queued, running, awaiting review, completed, or failed) without manual row entry. Download it again after runs or evaluations change to get the latest snapshot. Each row represents one run; pair Condition A and Condition B with the same replicate ID, corpus fingerprint, and model settings. The CSV is a summary, not the record of every artifact: use the detailed JSONL export and each run's evidence package for original outputs, review comments, event history, evaluator-level scores, and source manifests. Blank values mean unavailable or not measured; they are not zero. Provider-reported cost is blank when the provider did not return cost. Extraction completeness is a field-presence measure, not semantic accuracy. `experiment_run_log_template.csv` in the repository shows the same column headings for planning before the app is started.

Older runs may contain the previous four-scenario identifiers (`full_audit`, `targeted_retry`, `endpoint_oversight`, `midpoint_gating`). They remain readable for audit history but are not selectable for the initial two-condition protocol.
## OpenRouter models

When ready, configure `OPENROUTER_API_KEY` once and set `OPENROUTER_QWEN_MODEL_ID`, `OPENROUTER_KIMI_MODEL_ID`, and `OPENROUTER_DEEPSEEK_MODEL_ID` to the exact model slugs from OpenRouter. OpenAI uses its separate `OPENAI_API_KEY` and `OPENAI_MODEL`. All provider secrets remain on the backend. Each connection test sends a small billable request.

## Container deployment

Copy `.env.example` to `.env`, set strong unique secrets and model configuration, then run `docker compose up --build -d`. The web port binds to `127.0.0.1` by default. This setup is intended for one researcher on a trusted machine. Do not expose it to a network or multiple users without adding authenticated identity, per-user project authorization, TLS, backups, retention/deletion controls, and operational monitoring. Uploaded PDFs and extracted text are stored in the database; model providers receive paper text sent for analysis.

## Verification

Install validation tools with `cd backend && . .venv/bin/activate && pip install -r requirements-dev.txt`. Backend checks: `python -m pytest -q` and `ruff check app tests`. Frontend checks: `cd frontend && npm run build`. A successful build does not validate external credentials; use the in-app connection check and complete one small-corpus run under each condition before collecting formal study data.
