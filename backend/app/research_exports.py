"""Research-focused, per-run CSV exports.

The primary CSV contains six research outcomes. Supporting tables preserve
the records needed to audit each outcome and reconstruct the run.
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .evaluation import run_metrics
from .models import (
    Artifact, ErrorAnnotation, EvaluationRecord, Event, Project,
    ReferenceAnnotation, ReviewRecord, Run,
)

PRIMARY_HEADERS = [
    "run_id", "pair_id", "condition", "model", "corpus_paper_count",
    "corpus_fingerprint_sha256", "extraction_accuracy_mean_1_to_5",
    "extraction_completeness_score_mean_1_to_5",
    "evidence_traceability_mean_1_to_5",
    "organization_quality_mean_1_to_5",
    "literature_synthesis_quality_mean_1_to_5",
    "human_review_time_minutes",
]


def _csv_text(rows: list[dict], headers: list[str]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        safe = {}
        for key, value in row.items():
            safe[key] = (
                "'" + value
                if isinstance(value, str)
                and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                else value
            )
        writer.writerow(safe)
    return output.getvalue()


def _condition(value: str | None) -> str:
    return {
        "without_intermediate_human_supervision": "A_AUTONOMOUS",
        "with_human_supervision_all_stages": "B_HUMAN_SUPERVISED",
        "search_discovery": "SEARCH_ONLY_NOT_EXPERIMENT",
    }.get(value or "", value or "")


def _mean_score(evaluations, stage: str, dimensions: tuple[str, ...]) -> float | None:
    accepted = {name.casefold() for name in dimensions}
    values = []
    for evaluation in evaluations:
        if evaluation.stage != stage:
            continue
        for dimension, score in (evaluation.scores or {}).items():
            if (
                dimension.casefold() in accepted
                and isinstance(score, (int, float))
                and not isinstance(score, bool)
                and 1 <= float(score) <= 5
            ):
                values.append(float(score))
    return round(sum(values) / len(values), 4) if values else None


def _single_model(run: Run, artifacts: list[Artifact]) -> str:
    model_ids = {
        str(artifact.model).strip()
        for artifact in artifacts
        if artifact.model and artifact.model.casefold() != "human correction"
    }
    if not model_ids:
        model_ids = {
            str(value).strip()
            for value in (run.settings.get("model_ids") or {}).values()
            if value
        }
    if len(model_ids) == 1:
        return next(iter(model_ids))
    if not model_ids:
        return ""
    return "MISMATCH:" + " | ".join(sorted(model_ids))


def build_run_summary(db: Session, run: Run) -> dict:
    metrics = run_metrics(db, run)
    evaluations = db.scalars(
        select(EvaluationRecord)
        .where(EvaluationRecord.run_id == run.id)
        .order_by(EvaluationRecord.created_at, EvaluationRecord.id)
    ).all()
    reviews = db.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.run_id == run.id)
        .order_by(ReviewRecord.created_at, ReviewRecord.id)
    ).all()
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.run_id == run.id)
        .order_by(Artifact.created_at, Artifact.version, Artifact.id)
    ).all()

    paper_ids = run.settings.get("source_paper_ids") or []
    extraction = metrics["stage_observations"]["extraction"]
    field_presence = extraction.get("field_presence_rate")
    measured_review_ms = [
        review.review_duration_ms
        for review in reviews
        if review.review_duration_ms is not None
    ]
    review_ms = sum(measured_review_ms) if measured_review_ms else (
        0 if run.settings.get("condition") == "without_intermediate_human_supervision" else None
    )

    return {
        "run_id": run.id,
        "pair_id": run.settings.get("replicate", ""),
        "condition": _condition(run.settings.get("condition")),
        "model": _single_model(run, artifacts),
        "corpus_paper_count": len(paper_ids),
        "corpus_fingerprint_sha256": run.settings.get("source_snapshot_hash", ""),
        "extraction_accuracy_mean_1_to_5": _mean_score(
            evaluations, "extraction", ("factual_accuracy",)
        ),
        "extraction_completeness_score_mean_1_to_5": _mean_score(
            evaluations, "extraction", ("extraction_completeness",)
        ),
        "evidence_traceability_mean_1_to_5": _mean_score(
            evaluations, "extraction", ("evidence_support",)
        ),
        "organization_quality_mean_1_to_5": _mean_score(
            evaluations, "organization", ("organization_quality",)
        ),
        "literature_synthesis_quality_mean_1_to_5": _mean_score(
            evaluations, "synthesis", ("synthesis_quality",)
        ),
        "human_review_time_minutes": (
            round(review_ms / 60000, 3) if review_ms is not None else None
        ),
    }


def run_summary_csv(db: Session, run: Run) -> str:
    summary = build_run_summary(db, run)
    return _csv_text(
        [{key: summary.get(key) for key in PRIMARY_HEADERS}],
        PRIMARY_HEADERS,
    )


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def build_five_tables(db: Session, run: Run) -> dict[str, tuple[list[dict], list[str]]]:
    project = db.get(Project, run.project_id)
    summary = build_run_summary(db, run)
    metrics = run_metrics(db, run)
    evaluations = db.scalars(
        select(EvaluationRecord)
        .where(EvaluationRecord.run_id == run.id)
        .order_by(EvaluationRecord.created_at, EvaluationRecord.id)
    ).all()
    reviews = db.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.run_id == run.id)
        .order_by(ReviewRecord.created_at, ReviewRecord.id)
    ).all()
    errors = db.scalars(
        select(ErrorAnnotation)
        .where(ErrorAnnotation.run_id == run.id)
        .order_by(ErrorAnnotation.created_at, ErrorAnnotation.id)
    ).all()
    events = db.scalars(
        select(Event).where(Event.run_id == run.id)
        .order_by(Event.created_at, Event.id)
    ).all()
    artifacts = db.scalars(
        select(Artifact).where(Artifact.run_id == run.id)
        .order_by(Artifact.created_at, Artifact.version, Artifact.id)
    ).all()

    run_row = {
        **summary,
        "project_title": project.title if project else "",
        "research_question": project.question if project else "",
        "run_status": run.status,
        "protocol_version": run.settings.get("protocol_version"),
        "temperature": run.settings.get("temperature"),
        "random_seed": run.settings.get("random_seed"),
        "source_mode": run.settings.get("source_mode"),
        "extraction_field_presence_rate_percent": (
            round(float(metrics["stage_observations"]["extraction"].get("field_presence_rate")) * 100, 2)
            if metrics["stage_observations"]["extraction"].get("field_presence_rate") is not None else None
        ),
        "created_at_utc": run.created_at.isoformat() if run.created_at else "",
        "updated_at_utc": run.updated_at.isoformat() if run.updated_at else "",
        "input_tokens": metrics["system"].get("input_tokens"),
        "output_tokens": metrics["system"].get("output_tokens"),
        "total_tokens": metrics["system"].get("total_tokens"),
        "provider_reported_cost": metrics["system"].get("provider_reported_cost"),
        "cost_currency": metrics["system"].get("cost_currency"),
        "machine_execution_seconds": (
            metrics["system"].get("total_agent_execution_duration_ms") or 0
        ) / 1000,
        "wall_clock_seconds": (
            metrics["system"].get("total_elapsed_duration_ms") or 0
        ) / 1000,
        "retry_count": metrics["system"].get("retry_or_return_count"),
        "failure_count": metrics["system"].get("stage_failure_count"),
    }
    run_headers = list(run_row.keys())

    quality_rows = [
        {
            "evaluation_id": evaluation.id,
            "run_id": evaluation.run_id,
            "artifact_id": evaluation.artifact_id,
            "stage": evaluation.stage,
            "evaluator_id": evaluation.evaluator_id,
            "rubric_version": evaluation.rubric_version,
            "dimension": dimension,
            "score_1_to_5": score,
            "comments": evaluation.comments,
            "evidence_references_json": _json(evaluation.evidence_references or []),
            "reference_paper_ids_json": _json(evaluation.reference_paper_ids or []),
            "created_at_utc": evaluation.created_at.isoformat() if evaluation.created_at else "",
        }
        for evaluation in evaluations
        for dimension, score in (evaluation.scores or {}).items()
    ]
    quality_headers = [
        "evaluation_id", "run_id", "artifact_id", "stage", "evaluator_id",
        "rubric_version", "dimension", "score_1_to_5", "comments",
        "evidence_references_json", "reference_paper_ids_json", "created_at_utc",
    ]

    claim_rows = []
    for evaluation in evaluations:
        for index, evidence in enumerate(evaluation.evidence_references or []):
            claim_rows.append({
                "record_type": "evaluation_evidence_reference",
                "run_id": run.id,
                "artifact_id": evaluation.artifact_id,
                "stage": evaluation.stage,
                "evaluator_id": evaluation.evaluator_id,
                "reference_index": index,
                "evidence_status": evidence.get("status", "") if isinstance(evidence, dict) else "",
                "claim_text": evidence.get("claim", evidence.get("claim_text", "")) if isinstance(evidence, dict) else "",
                "source_reference": evidence.get("source", evidence.get("source_reference", "")) if isinstance(evidence, dict) else "",
                "evidence_record_json": _json(evidence),
                "error_category": "",
                "severity": "",
                "corrected": "",
                "propagated": "",
            })
    for error in errors:
        claim_rows.append({
            "record_type": "annotated_error",
            "run_id": run.id,
            "artifact_id": error.artifact_id,
            "stage": error.stage,
            "evaluator_id": error.evaluator_id,
            "reference_index": "",
            "evidence_status": "error_annotated",
            "claim_text": error.comments,
            "source_reference": "",
            "evidence_record_json": _json(error.source_evidence or []),
            "error_category": error.category,
            "severity": error.severity,
            "corrected": error.corrected,
            "propagated": error.propagated,
        })
    claim_headers = [
        "record_type", "run_id", "artifact_id", "stage", "evaluator_id",
        "reference_index", "evidence_status", "claim_text", "source_reference",
        "evidence_record_json", "error_category", "severity", "corrected", "propagated",
    ]

    intervention_rows = [
        {
            "review_id": review.id,
            "run_id": review.run_id,
            "artifact_id": review.artifact_id,
            "stage": review.stage,
            "revision_number": review.revision_number,
            "decision": review.decision,
            "reviewer_comments": review.comments,
            "correction_json": _json(review.correction),
            "review_duration_seconds": (
                review.review_duration_ms / 1000
                if review.review_duration_ms is not None else None
            ),
            "created_at_utc": review.created_at.isoformat() if review.created_at else "",
        }
        for review in reviews
    ]
    intervention_headers = [
        "review_id", "run_id", "artifact_id", "stage", "revision_number",
        "decision", "reviewer_comments", "correction_json",
        "review_duration_seconds", "created_at_utc",
    ]

    performance_rows = [{
        "run_id": run.id,
        "input_tokens": metrics["system"].get("input_tokens"),
        "output_tokens": metrics["system"].get("output_tokens"),
        "total_tokens": metrics["system"].get("total_tokens"),
        "provider_reported_cost": metrics["system"].get("provider_reported_cost"),
        "cost_currency": metrics["system"].get("cost_currency"),
        "machine_execution_seconds": run_row["machine_execution_seconds"],
        "wall_clock_seconds": run_row["wall_clock_seconds"],
        "human_review_minutes": summary.get("human_review_time_minutes"),
        "retry_count": metrics["system"].get("retry_or_return_count"),
        "failure_count": metrics["system"].get("stage_failure_count"),
        "artifact_count": len(artifacts),
        "event_count": len(events),
    }]
    performance_headers = [
        "run_id", "input_tokens", "output_tokens", "total_tokens",
        "provider_reported_cost", "cost_currency", "machine_execution_seconds",
        "wall_clock_seconds", "human_review_minutes", "retry_count",
        "failure_count", "artifact_count", "event_count",
    ]

    return {
        "experiment_runs.csv": ([run_row], run_headers),
        "quality_evaluation.csv": (quality_rows, quality_headers),
        "claim_evidence.csv": (claim_rows, claim_headers),
        "human_interventions.csv": (intervention_rows, intervention_headers),
        "system_performance.csv": (performance_rows, performance_headers),
    }


def run_research_tables_zip(db: Session, run: Run) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (rows, headers) in build_five_tables(db, run).items():
            archive.writestr(name, _csv_text(rows, headers))
        archive.writestr(
            "README.txt",
            "Five linked research CSV tables. Join using run_id. Blank quality scores mean no independent evaluator score was recorded. Human review decisions are not quality scores. Extraction completeness is an independent 1-5 evaluation score; experiment_runs.csv separately reports extraction_field_presence_rate_percent as an automatically observed field-presence proxy, not semantic accuracy. claim_evidence.csv contains only evaluator-provided evidence references and structured error annotations actually recorded; it does not invent claim-level labels.",
        )
    return buffer.getvalue()
