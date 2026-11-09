"""Research-focused, per-run CSV exports.

The primary CSV contains six explicitly defined research outcomes. Supporting
CSV tables preserve the observations needed to audit those outcomes.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from .evaluation import run_metrics
from .models import Artifact, ErrorAnnotation, EvaluationRecord, Event, Paper, Project, ReferenceAnnotation, ReviewRecord, Run

PRIMARY_HEADERS = [
    "run_id",
    "pair_id",
    "condition",
    "model",
    "corpus_paper_count",
    "corpus_fingerprint_sha256",
    "extraction_accuracy_mean_1_to_5",
    "extraction_completeness_percent",
    "evidence_traceability_mean_1_to_5",
    "organization_quality_mean_1_to_5",
    "literature_synthesis_quality_mean_1_to_5",
    "human_review_time_minutes",
]

def _csv_text(rows: list[dict], headers: list[str]) -> str:
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        # Reduce spreadsheet formula injection risk for user-authored text.
        safe = {}
        for key, value in row.items():
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                safe[key] = "'" + value
            else:
                safe[key] = value
        writer.writerow(safe)
    return out.getvalue()

def _condition(condition: str | None) -> str:
    return {
        "without_intermediate_human_supervision": "A_AUTONOMOUS",
        "with_human_supervision_all_stages": "B_HUMAN_SUPERVISED",
        "search_discovery": "SEARCH_ONLY_NOT_EXPERIMENT",
    }.get(condition or "", condition or "")

def _mean_stage_quality(evaluations: list[EvaluationRecord], stage: str, dimensions: tuple[str, ...]) -> float | None:
    values = []
    for row in evaluations:
        if row.stage != stage:
            continue
        scores = row.scores or {}
        for dimension in dimensions:
            value = scores.get(dimension)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.append(float(value))
    return round(sum(values) / len(values), 4) if values else None

def _extraction_accuracy_from_reference(
    evaluations: list[EvaluationRecord], references: list[ReferenceAnnotation]
) -> float | None:
    # Accuracy is only emitted when explicit human evaluation scores exist.
    # Field-presence/completeness is not treated as factual accuracy.
    score = _mean_stage_quality(
        evaluations,
        "extraction",
        ("factual_accuracy", "accuracy", "semantic_accuracy"),
    )
    if score is not None:
        return score
    return None

def build_run_summary(db: Session, run: Run) -> dict:
    project = db.get(Project, run.project_id)
    metrics = run_metrics(db, run)
    evaluations = db.scalars(
        select(EvaluationRecord).where(EvaluationRecord.run_id == run.id)
    ).all()
    reviews = db.scalars(
        select(ReviewRecord).where(ReviewRecord.run_id == run.id)
    ).all()
    paper_ids = run.settings.get("source_paper_ids") or []
    references = (
        db.scalars(
            select(ReferenceAnnotation).where(
                ReferenceAnnotation.project_id == run.project_id,
                ReferenceAnnotation.paper_id.in_(paper_ids),
            )
        ).all()
        if paper_ids else []
    )
    eval_by_stage = defaultdict(list)
    for row in evaluations:
        eval_by_stage[row.stage].append(row)

    extraction = metrics["stage_observations"]["extraction"]
    # Completeness is a presence proxy, named honestly rather than accuracy.
    completeness = extraction.get("field_presence_rate")
    review_durations = [r.review_duration_ms for r in reviews if r.review_duration_ms is not None]
    review_ms = sum(review_durations) if review_durations else (
        0 if run.settings.get("condition") == "without_intermediate_human_supervision" else None
    )
    model_values = {
        str(v) for v in (run.settings.get("model_ids") or {}).values() if v
    }
    model = next(iter(model_values)) if len(model_values) == 1 else (
        next(iter(run.models.values()), "") if run.models else ""
    )

    return {
        "run_id": run.id,
        "pair_id": run.settings.get("replicate", ""),
        "condition": _condition(run.settings.get("condition")),
        "model": model,
        "corpus_paper_count": len(paper_ids),
        "corpus_fingerprint_sha256": run.settings.get("source_snapshot_hash", ""),
        "extraction_accuracy_mean_1_to_5": _extraction_accuracy_from_reference(evaluations, references),
        "extraction_completeness_percent": round(completeness * 100, 2) if completeness is not None else None,
        "evidence_traceability_mean_1_to_5": _mean_stage_quality(
            evaluations, "extraction", ("evidence_support", "evidence_traceability", "evidence_fidelity")
        ),
        "organization_quality_mean_1_to_5": _mean_stage_quality(
            evaluations, "organization", ("overall_quality", "organization_quality", "theme_quality", "comparison_quality")
        ),
        "literature_synthesis_quality_mean_1_to_5": _mean_stage_quality(
            evaluations, "synthesis", ("overall_quality", "synthesis_quality", "factual_accuracy", "critical_analysis", "academic_coherence")
        ),
        "human_review_time_minutes": round(review_ms / 60000, 3) if review_ms is not None else None,
    }

def run_summary_csv(db: Session, run: Run) -> str:
    return _csv_text([build_run_summary(db, run)], PRIMARY_HEADERS)

def build_five_tables(db: Session, run: Run) -> dict[str, tuple[list[dict], list[str]]]:
    evaluations = db.scalars(
        select(EvaluationRecord).where(EvaluationRecord.run_id == run.id).order_by(EvaluationRecord.created_at)
    ).all()
    reviews = db.scalars(
        select(ReviewRecord).where(ReviewRecord.run_id == run.id).order_by(ReviewRecord.created_at)
    ).all()
    errors = db.scalars(
        select(ErrorAnnotation).where(ErrorAnnotation.run_id == run.id).order_by(ErrorAnnotation.created_at)
    ).all()
    events = db.scalars(
        select(Event).where(Event.run_id == run.id).order_by(Event.created_at)
    ).all()
    artifacts = db.scalars(
        select(Artifact).where(Artifact.run_id == run.id).order_by(Artifact.created_at, Artifact.version)
    ).all()
    metrics = run_metrics(db, run)

    runs = [{
        **build_run_summary(db, run),
        "research_question": db.get(Project, run.project_id).question,
        "run_status": run.status,
        "protocol_version": run.settings.get("protocol_version"),
        "temperature": run.settings.get("temperature"),
        "random_seed": run.settings.get("random_seed"),
        "source_mode": run.settings.get("source_mode"),
        "created_at_utc": run.created_at.isoformat() if run.created_at else "",
        "updated_at_utc": run.updated_at.isoformat() if run.updated_at else "",
        "input_tokens": metrics["system"].get("input_tokens"),
        "output_tokens": metrics["system"].get("output_tokens"),
        "total_tokens": metrics["system"].get("total_tokens"),
        "provider_reported_cost": metrics["system"].get("provider_reported_cost"),
        "machine_execution_seconds": (metrics["system"].get("total_agent_execution_duration_ms") or 0) / 1000,
        "wall_clock_seconds": (metrics["system"].get("total_elapsed_duration_ms") or 0) / 1000,
        "retry_count": metrics["system"].get("retry_or_return_count"),
        "failure_count": metrics["system"].get("stage_failure_count"),
    }]
    run_headers = list(runs[0].keys())

    quality = [{
        "evaluation_id": e.id, "run_id": e.run_id, "artifact_id": e.artifact_id,
        "stage": e.stage, "evaluator_id": e.evaluator_id, "rubric_version": e.rubric_version,
        "dimension": dimension, "score_1_to_5": score, "comments": e.comments,
        "evidence_references_json": json.dumps(e.evidence_references or [], ensure_ascii=False),
        "reference_paper_ids_json": json.dumps(e.reference_paper_ids or []),
        "created_at_utc": e.created_at.isoformat() if e.created_at else "",
    } for e in evaluations for dimension, score in (e.scores or {}).items()]
    quality_headers = ["evaluation_id","run_id","artifact_id","stage","evaluator_id","rubric_version","dimension","score_1_to_5","comments","evidence_references_json","reference_paper_ids_json","created_at_utc"]

    claim_rows = []
    for error in errors:
        claim_rows.append({
            "run_id": run.id, "artifact_id": error.artifact_id, "stage": error.stage,
            "evaluator_id": error.evaluator_id, "error_category": error.category,
            "severity": error.severity, "source_evidence_json": json.dumps(error.source_evidence or [], ensure_ascii=False),
            "human_correction": error.human_correction, "corrected": error.corrected,
            "propagated": error.propagated, "comments": error.comments,
        })
    claim_headers = ["run_id","artifact_id","stage","evaluator_id","error_category","severity","source_evidence_json","human_correction","corrected","propagated","comments"]

    interventions = [{
        "review_id": r.id, "run_id": r.run_id, "artifact_id": r.artifact_id,
        "stage": r.stage, "revision_number": r.revision_number, "decision": r.decision,
        "reviewer_comments": r.comments, "correction_json": json.dumps(r.correction, ensure_ascii=False),
        "review_duration_seconds": r.review_duration_ms / 1000 if r.review_duration_ms is not None else None,
        "created_at_utc": r.created_at.isoformat() if r.created_at else "",
    } for r in reviews]
    intervention_headers = ["review_id","run_id","artifact_id","stage","revision_number","decision","reviewer_comments","correction_json","review_duration_seconds","created_at_utc"]

    performance = [{
        "run_id": run.id, "input_tokens": metrics["system"].get("input_tokens"),
        "output_tokens": metrics["system"].get("output_tokens"), "total_tokens": metrics["system"].get("total_tokens"),
        "provider_reported_cost": metrics["system"].get("provider_reported_cost"),
        "cost_currency": metrics["system"].get("cost_currency"),
        "machine_execution_seconds": (metrics["system"].get("total_agent_execution_duration_ms") or 0) / 1000,
        "wall_clock_seconds": (metrics["system"].get("total_elapsed_duration_ms") or 0) / 1000,
        "human_review_minutes": build_run_summary(db, run)["human_review_time_minutes"],
        "retry_count": metrics["system"].get("retry_or_return_count"),
        "failure_count": metrics["system"].get("stage_failure_count"),
    }]
    performance_headers = ["run_id","input_tokens","output_tokens","total_tokens","provider_reported_cost","cost_currency","machine_execution_seconds","wall_clock_seconds","human_review_minutes","retry_count","failure_count"]

    return {
        "experiment_runs.csv": (runs, run_headers),
        "quality_evaluation.csv": (quality, quality_headers),
        "claim_evidence.csv": (claim_rows, claim_headers),
        "human_interventions.csv": (interventions, intervention_headers),
        "system_performance.csv": (performance, performance_headers),
    }

def run_research_tables_zip(db: Session, run: Run) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, (rows, headers) in build_five_tables(db, run).items():
            archive.writestr(name, _csv_text(rows, headers))
        archive.writestr(
            "README.txt",
            "Five linked CSV tables. Join using run_id; evaluation rows use evaluation_id/artifact_id. Missing scores remain blank. Human review decisions are not quality scores. extraction_completeness_percent is a field-presence measure, not semantic accuracy.",
        )
    return buffer.getvalue()
