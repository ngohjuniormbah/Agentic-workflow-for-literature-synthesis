"""Descriptive, traceable metrics for human-reviewed experiment artifacts."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from statistics import mean, median, quantiles, stdev

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Artifact,
    ErrorAnnotation,
    EvaluationRecord,
    Event,
    ReferenceAnnotation,
    ReviewRecord,
    Run,
)


def describe(values: Iterable[float]) -> dict:
    data = sorted(float(value) for value in values if value is not None)
    if not data:
        return {
            "n": 0,
            "mean": None,
            "median": None,
            "standard_deviation": None,
            "minimum": None,
            "maximum": None,
            "interquartile_range": None,
        }
    q1 = q3 = None
    if len(data) > 1:
        quarters = quantiles(data, n=4, method="inclusive")
        q1, q3 = quarters[0], quarters[2]
    return {
        "n": len(data),
        "mean": mean(data),
        "median": median(data),
        "standard_deviation": stdev(data) if len(data) > 1 else None,
        "minimum": min(data),
        "maximum": max(data),
        "interquartile_range": q3 - q1 if q1 is not None and q3 is not None else None,
    }


def run_metrics(db: Session, run: Run) -> dict:
    artifacts = db.scalars(
        select(Artifact)
        .where(Artifact.run_id == run.id)
        .order_by(Artifact.created_at, Artifact.version)
    ).all()
    reviews = db.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.run_id == run.id)
        .order_by(ReviewRecord.created_at)
    ).all()
    evaluations = db.scalars(
        select(EvaluationRecord)
        .where(EvaluationRecord.run_id == run.id)
        .order_by(EvaluationRecord.created_at)
    ).all()
    errors = db.scalars(
        select(ErrorAnnotation).where(ErrorAnnotation.run_id == run.id)
    ).all()
    events = db.scalars(select(Event).where(Event.run_id == run.id)).all()
    reference_rows = (
        db.scalars(
            select(ReferenceAnnotation).where(
                ReferenceAnnotation.project_id == run.project_id,
                ReferenceAnnotation.paper_id.in_(
                    run.settings.get("source_paper_ids", [])
                ),
            )
        ).all()
        if run.settings.get("source_paper_ids")
        else []
    )

    token_totals = Counter()
    reported_costs = []
    request_durations = []
    stage_durations = defaultdict(list)
    stage_artifact_counts = Counter()
    search_precision = []
    search_recall = []
    for artifact in artifacts:
        content = artifact.content or {}
        telemetry = content.get("_telemetry", {})
        stage_artifact_counts[artifact.stage] += 1
        duration = telemetry.get("stage_duration_ms")
        if isinstance(duration, (int, float)):
            stage_durations[artifact.stage].append(float(duration))
        usage = telemetry.get("usage") or {}
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if isinstance(usage.get(field), (int, float)):
                token_totals[field] += int(usage[field])
        if usage.get("cost") is not None:
            try:
                reported_costs.append(float(usage["cost"]))
            except (TypeError, ValueError):
                pass
        req_ms = telemetry.get("request_duration_ms")
        if isinstance(req_ms, (int, float)):
            request_durations.append(float(req_ms))

    for evaluation in evaluations:
        if evaluation.stage != "search" or not evaluation.reference_paper_ids:
            continue
        search_artifact = next(
            (a for a in reversed(artifacts) if a.stage == "search"), None
        )
        if not search_artifact:
            continue
        ranked = (search_artifact.content or {}).get("ranked_paper_ids", [])
        gold = set(evaluation.reference_paper_ids)
        if not ranked or not gold:
            continue
        k = min(10, len(ranked))
        hits = len(set(ranked[:k]) & gold)
        search_precision.append(hits / k if k else None)
        search_recall.append(hits / len(gold))

    score_values = defaultdict(lambda: defaultdict(list))
    for evaluation in evaluations:
        for dimension, score in (evaluation.scores or {}).items():
            score_values[evaluation.stage][dimension].append(score)
    scores = {
        stage: {dimension: describe(values) for dimension, values in dimensions.items()}
        for stage, dimensions in score_values.items()
    }
    error_categories = Counter(error.category for error in errors)
    error_severity = Counter(error.severity for error in errors)
    review_actions = Counter(review.decision for review in reviews)
    measured_review_durations = [
        r.review_duration_ms for r in reviews if r.review_duration_ms is not None
    ]
    known_cost = sum(reported_costs) if reported_costs else None
    machine_stage_values = [
        value for values in stage_durations.values() for value in values
    ]
    elapsed_ms = None
    if run.created_at and run.updated_at:
        try:
            elapsed_ms = max(
                0.0, (run.updated_at - run.created_at).total_seconds() * 1000
            )
        except TypeError:
            elapsed_ms = max(
                0.0,
                (
                    run.updated_at.replace(tzinfo=None)
                    - run.created_at.replace(tzinfo=None)
                ).total_seconds()
                * 1000,
            )
    reference_field_counts = Counter()
    extraction = next((a for a in reversed(artifacts) if a.stage == "extraction"), None)
    latest_by_stage = {
        stage: next((a for a in reversed(artifacts) if a.stage == stage), None)
        for stage in ("search", "extraction", "organization", "synthesis")
    }
    search = latest_by_stage["search"]
    search_content = search.content or {} if search else {}
    search_review = next(
        (
            item
            for item in reversed(reviews)
            if item.stage == "search" and item.screening_decisions
        ),
        None,
    )
    screening = search_review.screening_decisions or {} if search_review else {}
    extraction_content = extraction.content or {} if extraction else {}
    extraction_rows = extraction_content.get("papers", [])
    extraction_rows = extraction_rows if isinstance(extraction_rows, list) else []
    field_names = (
        "research_objective",
        "research_problem",
        "research_question",
        "methodology",
        "research_design",
        "dataset_or_sample",
        "data_collection_methods",
        "analysis_methods",
        "findings",
        "limitations",
        "research_gaps",
        "contributions",
    )
    fields_possible = len(extraction_rows) * len(field_names)
    fields_reported = sum(
        isinstance((row.get("fields") or {}).get(name), str)
        and (row.get("fields") or {}).get(name, "").strip().casefold()
        not in ("", "not reported")
        for row in extraction_rows
        for name in field_names
    )
    evidence_rows = [
        item
        for row in extraction_rows
        for item in (row.get("evidence", []) if isinstance(row, dict) else [])
        if isinstance(item, dict)
    ]
    organization_content = (
        latest_by_stage["organization"].content or {}
        if latest_by_stage["organization"]
        else {}
    )
    themes = organization_content.get("themes", [])
    themes = themes if isinstance(themes, list) else []
    synthesis_content = (
        latest_by_stage["synthesis"].content or {}
        if latest_by_stage["synthesis"]
        else {}
    )
    generated_records = (
        {
            (row.get("paper_id")): row
            for row in (extraction.content or {}).get("papers", [])
        }
        if extraction
        else {}
    )
    comparison_fields = {
        "research_objective": "research_objective",
        "research_problem": "research_problem",
        "research_question": "research_question",
        "methodology": "methodology",
        "research_design": "research_design",
        "dataset_or_sample": "dataset_or_sample",
        "data_collection_methods": "data_collection_methods",
        "analysis_methods": "analysis_methods",
        "findings": "findings",
        "limitations": "limitations",
        "research_gaps": "research_gaps",
        "contributions": "contributions",
    }
    for reference in reference_rows:
        expected = reference.annotations or {}
        generated = (generated_records.get(reference.paper_id) or {}).get("fields", {})
        for field, generated_field in comparison_fields.items():
            reference_value = expected.get(field)
            if (
                not isinstance(reference_value, str)
                or not reference_value.strip()
                or reference_value.strip().casefold() == "not reported"
            ):
                continue
            reference_field_counts[field + "_reference_fields"] += 1
            generated_value = generated.get(generated_field)
            if (
                isinstance(generated_value, str)
                and generated_value.strip()
                and generated_value.strip().casefold() != "not reported"
            ):
                reference_field_counts[field + "_generated_field_present"] += 1

    return {
        "run_id": run.id,
        "condition": run.settings.get("condition"),
        "protocol_version": run.settings.get("protocol_version"),
        "status": run.status,
        "source_snapshot_hash": run.settings.get("source_snapshot_hash"),
        "source_paper_ids": run.settings.get("source_paper_ids", []),
        "system": {
            "machine_stage_duration_ms": describe(
                [x for values in stage_durations.values() for x in values]
            ),
            "total_agent_execution_duration_ms": sum(machine_stage_values),
            "total_elapsed_duration_ms": elapsed_ms,
            "stage_duration_ms": {
                stage: describe(values) for stage, values in stage_durations.items()
            },
            "request_duration_ms": describe(request_durations),
            "input_tokens": token_totals.get("prompt_tokens", 0),
            "output_tokens": token_totals.get("completion_tokens", 0),
            "total_tokens": token_totals.get("total_tokens", 0),
            "provider_reported_cost": known_cost,
            "provider_reported_cost_observations": len(reported_costs),
            "cost_currency": "USD" if reported_costs else None,
            "retry_or_return_count": sum(
                event.type == "review_revision_requested" for event in events
            ),
            "stage_failure_count": sum(
                event.type == "stage_failed" for event in events
            ),
        },
        "human": {
            "review_count": len(reviews),
            "review_decisions": dict(review_actions),
            "measured_review_duration_ms": describe(measured_review_durations),
            "unmeasured_review_count": sum(
                r.review_duration_ms is None for r in reviews
            ),
        },
        "stage_artifact_counts": dict(stage_artifact_counts),
        "stage_observations": {
            "search": {
                "candidate_count": search_content.get("count"),
                "retrieved_count": (search_content.get("search_provenance") or {}).get(
                    "retrieved_count"
                ),
                "duplicate_count": (search_content.get("search_provenance") or {}).get(
                    "duplicate_count"
                ),
                "included_by_reviewer_count": len(
                    screening.get("included_paper_ids", [])
                ),
                "excluded_by_reviewer_count": len(
                    screening.get("excluded_paper_ids", [])
                ),
                "precision_at_10": describe(search_precision),
                "recall_at_10": describe(search_recall),
            },
            "extraction": {
                "papers_processed": len(extraction_rows),
                "fields_reported": fields_reported,
                "fields_possible": fields_possible,
                "field_presence_rate": fields_reported / fields_possible
                if fields_possible
                else None,
                "source_verified_evidence_quotes": len(evidence_rows),
                "abstract_based_quotes": sum(
                    item.get("source_basis") == "abstract" for item in evidence_rows
                ),
                "full_text_based_quotes": sum(
                    item.get("source_basis") == "full_text" for item in evidence_rows
                ),
                "unsupported_field_count": extraction_content.get(
                    "unsupported_field_count"
                ),
            },
            "organization": {
                "themes_identified": len(themes),
                "agreement_entries": sum(
                    bool(theme.get("agreements"))
                    for theme in themes
                    if isinstance(theme, dict)
                ),
                "disagreement_entries": sum(
                    bool(theme.get("disagreements"))
                    for theme in themes
                    if isinstance(theme, dict)
                ),
                "gap_entries": sum(
                    bool(theme.get("gaps"))
                    for theme in themes
                    if isinstance(theme, dict)
                ),
            },
            "generation": {
                "word_count": synthesis_content.get("word_count"),
                "verified_source_citation_count": len(
                    synthesis_content.get("citations", [])
                ),
                "rejected_citation_count": synthesis_content.get(
                    "rejected_citation_count"
                ),
            },
        },
        "quality_evaluations": scores,
        "reference_field_presence": {
            "counts": dict(reference_field_counts),
            "annotation_count": len(reference_rows),
            "interpretation": "Availability/completeness only; field presence is not a semantic correctness score.",
        },
        "search_reference_metrics": {
            "precision_at_10": describe(search_precision),
            "recall_at_10": describe(search_recall),
            "reference_set_observations": len(search_precision),
        },
        "errors": {
            "total": len(errors),
            "categories": dict(error_categories),
            "severity": dict(error_severity),
            "corrected": sum(error.corrected for error in errors),
            "propagated": sum(error.propagated for error in errors),
        },
        "statistics_note": "Descriptive statistics only. No confidence intervals or significance tests are inferred from these data. Extraction field presence and source quote checks do not establish semantic correctness.",
    }


def paired_differences(values_by_metric: dict[str, list[float]]) -> dict:
    return {name: describe(values) for name, values in values_by_metric.items()}
