"""Readable one-row-per-run CSV exports backed by the experiment database."""

import csv
import io
import re
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .evaluation import run_metrics
from .models import Artifact, Project, ReviewRecord, Run

SUMMARY_CSV_HEADERS = [
    "Run ID",
    "Project",
    "Research question",
    "Experiment label",
    "Experimental condition",
    "Human review between agents",
    "Condition code (internal)",
    "Replicate ID",
    "Protocol version",
    "Run status",
    "Current stage",
    "Created (UTC)",
    "Last updated (UTC)",
    "Model used by each agent",
    "Temperature",
    "Random seed",
    "Search query",
    "Corpus papers",
    "Corpus fingerprint (SHA-256)",
    "Search candidates retrieved",
    "Duplicate records removed",
    "Papers included by reviewer",
    "Papers excluded by reviewer",
    "Search Precision@10 (mean)",
    "Search Recall@10 (mean)",
    "Search reference evaluations",
    "Extraction papers processed",
    "Extraction fields present",
    "Extraction fields possible",
    "Extraction completeness (%) - not accuracy",
    "Evidence quotes captured",
    "Organization themes",
    "Organization agreements",
    "Organization disagreements",
    "Organization research gaps",
    "Synthesis words",
    "Verified citations",
    "Rejected citations",
    "Input tokens",
    "Output tokens",
    "Total tokens",
    "Provider-reported cost",
    "Cost currency",
    "Agent time by stage (mean seconds)",
    "Total agent execution seconds",
    "Total elapsed seconds",
    "Human review count",
    "Measured human review seconds",
    "Reviews without measured time",
    "Review decisions",
    "Saved-corpus restarts",
    "Independent evaluation scores (mean; stage and dimension)",
    "Error count",
    "Corrected errors",
    "Propagated errors",
    "Error counts by type",
    "Failure count",
    "Evidence package URL",
]


def _utc(value):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _number(value, divisor=1):
    return "" if value is None else round(value / divisor, 4)


def _mean(metric):
    return metric.get("mean") if metric else None


def _condition_label(condition):
    return {
        "without_intermediate_human_supervision": "Condition A - Automatic",
        "with_human_supervision_all_stages": "Condition B - Human review",
        "search_discovery": "Search only - corpus preparation (not an experiment run)",
    }.get(condition, condition or "")


def _score_summary(quality):
    entries = []
    for stage, dimensions in sorted(quality.items()):
        for dimension, stats in sorted(dimensions.items()):
            mean = stats.get("mean")
            count = stats.get("n", 0)
            if mean is not None:
                entries.append(f"{stage}: {dimension} = {mean:.2f}/5 (n={count})")
    return "; ".join(entries)


def _safe_model_id(value):
    candidate = str(value or "").strip()
    if re.search(r"(?i)(sk-[a-z0-9_-]{8,}|api[_-]?key|secret|token)", candidate):
        return "Invalid model ID (credential redacted)"
    return candidate


def _latest_model_ids(db, run):
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.run_id == run.id)
        .order_by(Artifact.created_at, Artifact.version, Artifact.id)
    ).all()
    model_ids = {}
    for artifact in artifacts:
        if artifact.model and artifact.model.casefold() != "human correction":
            model_ids[artifact.stage] = artifact.model
    for stage, model_id in (run.settings.get("model_ids") or {}).items():
        model_ids.setdefault(stage, _safe_model_id(model_id))
    return model_ids


def build_experiment_log_rows(db: Session, project: Project):
    runs = db.scalars(
        select(Run).where(Run.project_id == project.id).order_by(Run.created_at, Run.id)
    ).all()
    rows = []
    for run in runs:
        metrics = run_metrics(db, run)
        observations = metrics["stage_observations"]
        system = metrics["system"]
        human = metrics["human"]
        search_metrics = metrics["search_reference_metrics"]
        search = observations["search"]
        extraction = observations["extraction"]
        organization = observations["organization"]
        generation = observations["generation"]
        models = _latest_model_ids(db, run)
        reviews = db.scalars(
            select(ReviewRecord)
            .where(ReviewRecord.run_id == run.id)
            .order_by(ReviewRecord.created_at, ReviewRecord.id)
        ).all()
        review_decisions = "; ".join(
            f"{review.stage}={review.decision}" for review in reviews
        )
        measured_reviews = [
            review.review_duration_ms
            for review in reviews
            if review.review_duration_ms is not None
        ]
        review_seconds = (
            round(sum(measured_reviews) / 1000, 3)
            if measured_reviews
            else 0
            if run.settings.get("condition") == "without_intermediate_human_supervision"
            else ""
        )
        stage_seconds = system.get("stage_duration_ms", {})
        model_summary = "; ".join(
            f"{label}={run.models.get(stage, '')}:{models.get(stage, '')}".rstrip(":")
            for stage, label in (
                ("search", "Search"),
                ("extraction", "Extraction"),
                ("organization", "Organization"),
                ("synthesis", "Generation"),
            )
        )
        stage_time_summary = "; ".join(
            f"{label}={_number(_mean(stage_seconds.get(stage)), 1000)}"
            for stage, label in (
                ("search", "Search"),
                ("extraction", "Extraction"),
                ("organization", "Organization"),
                ("synthesis", "Generation"),
            )
            if _mean(stage_seconds.get(stage)) is not None
        )
        rows.append(
            {
                "Run ID": run.id,
                "Project": project.title,
                "Research question": project.question,
                "Experiment label": run.settings.get("experiment_label", ""),
                "Experimental condition": _condition_label(
                    run.settings.get("condition")
                ),
                "Human review between agents": (
                    "No - automatic progression"
                    if run.settings.get("condition") == "without_intermediate_human_supervision"
                    else "Yes - reviewer approves each stage"
                    if run.settings.get("condition") == "with_human_supervision_all_stages"
                    else "Not applicable - search only"
                    if run.settings.get("condition") == "search_discovery"
                    else ""
                ),
                "Condition code (internal)": run.settings.get("condition", ""),
                "Replicate ID": run.settings.get("replicate", ""),
                "Protocol version": run.settings.get("protocol_version", ""),
                "Run status": run.status,
                "Current stage": run.current_stage,
                "Created (UTC)": _utc(run.created_at),
                "Last updated (UTC)": _utc(run.updated_at),
                "Model used by each agent": model_summary,
                "Temperature": run.settings.get("temperature", ""),
                "Random seed": run.settings.get("random_seed", ""),
                "Search query": run.settings.get("search_query", project.question),
                "Corpus papers": len(run.settings.get("source_paper_ids") or []),
                "Corpus fingerprint (SHA-256)": run.settings.get(
                    "source_snapshot_hash", ""
                ),
                "Search candidates retrieved": search.get("retrieved_count"),
                "Duplicate records removed": search.get("duplicate_count"),
                "Papers included by reviewer": search.get("included_by_reviewer_count"),
                "Papers excluded by reviewer": search.get("excluded_by_reviewer_count"),
                "Search Precision@10 (mean)": _mean(search_metrics["precision_at_10"]),
                "Search Recall@10 (mean)": _mean(search_metrics["recall_at_10"]),
                "Search reference evaluations": search_metrics.get(
                    "reference_set_observations", 0
                ),
                "Extraction papers processed": extraction.get("papers_processed"),
                "Extraction fields present": extraction.get("fields_reported"),
                "Extraction fields possible": extraction.get("fields_possible"),
                "Extraction completeness (%) - not accuracy": (
                    round(extraction["field_presence_rate"] * 100, 1)
                    if extraction.get("field_presence_rate") is not None
                    else ""
                ),
                "Evidence quotes captured": extraction.get(
                    "source_verified_evidence_quotes"
                ),
                "Organization themes": organization.get("themes_identified"),
                "Organization agreements": organization.get("agreement_entries"),
                "Organization disagreements": organization.get("disagreement_entries"),
                "Organization research gaps": organization.get("gap_entries"),
                "Synthesis words": generation.get("word_count"),
                "Verified citations": generation.get("verified_source_citation_count"),
                "Rejected citations": generation.get("rejected_citation_count"),
                "Input tokens": system.get("input_tokens", 0),
                "Output tokens": system.get("output_tokens", 0),
                "Total tokens": system.get("total_tokens", 0),
                "Provider-reported cost": system.get("provider_reported_cost"),
                "Cost currency": system.get("cost_currency") or "",
                "Agent time by stage (mean seconds)": stage_time_summary,
                "Total agent execution seconds": _number(
                    system.get("total_agent_execution_duration_ms"), 1000
                ),
                "Total elapsed seconds": _number(
                    system.get("total_elapsed_duration_ms"), 1000
                ),
                "Human review count": human.get("review_count", 0),
                "Measured human review seconds": review_seconds,
                "Reviews without measured time": human.get(
                    "unmeasured_review_count", 0
                ),
                "Review decisions": review_decisions,
                "Saved-corpus restarts": run.settings.get(
                    "saved_corpus_restart_count", 0
                ),
                "Independent evaluation scores (mean; stage and dimension)": _score_summary(
                    metrics["quality_evaluations"]
                ),
                "Error count": metrics["errors"].get("total", 0),
                "Corrected errors": metrics["errors"].get("corrected", 0),
                "Propagated errors": metrics["errors"].get("propagated", 0),
                "Error counts by type": "; ".join(
                    f"{kind}={count}"
                    for kind, count in sorted(
                        metrics["errors"].get("categories", {}).items()
                    )
                ),
                "Failure count": system.get("stage_failure_count", 0),
                "Evidence package URL": f"/api/v1/runs/{run.id}/evidence-package",
            }
        )
    return rows


def experiment_log_csv(db: Session, project: Project) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=SUMMARY_CSV_HEADERS, extrasaction="ignore"
    )
    writer.writeheader()
    rows = build_experiment_log_rows(db, project)
    writer.writerows(
        {
            key: "'" + value
            if isinstance(value, str)
            and value.startswith(("=", "+", "-", "@", "\t", "\r"))
            else value
            for key, value in row.items()
        }
        for row in rows
    )
    return output.getvalue()
