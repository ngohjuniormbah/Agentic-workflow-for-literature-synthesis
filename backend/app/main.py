import csv
import hashlib
import io
import json
import logging
import os
import uuid
import zipfile
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .agents import (
    AI_STAGES,
    STAGES,
    ProviderError,
    complete,
    provider_catalog,
    provider_config,
)
from .config import settings
from .db import get_db
from .evaluation import describe, run_metrics
from .models import (
    Artifact,
    ErrorAnnotation,
    EvaluationRecord,
    Event,
    Paper,
    Project,
    ReferenceAnnotation,
    ReviewRecord,
    Run,
)
from .schemas import (
    ComparisonRequest,
    ErrorAnnotationCreate,
    EvaluationCreate,
    ProjectCreate,
    ProjectUpdate,
    ReferenceAnnotationCreate,
    ReviewAction,
    RunCreate,
)
from .workflow import (
    EXPERIMENT_CONDITION_POLICIES,
    record,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def proj_or_404(db, id):
    p = db.get(Project, id)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


def run_or_404(db, id):
    r = db.get(Run, id)
    if not r:
        raise HTTPException(404, "Run not found")
    return r


def artifact_view(a):
    return {
        "id": a.id,
        "run_id": a.run_id,
        "stage": a.stage,
        "version": a.version,
        "content": a.content,
        "status": a.status,
        "model": a.model,
        "reviewer_note": a.reviewer_note,
        "created_at": a.created_at,
    }


def review_view(review):
    return {
        "id": review.id,
        "run_id": review.run_id,
        "artifact_id": review.artifact_id,
        "corrected_artifact_id": review.corrected_artifact_id,
        "stage": review.stage,
        "revision_number": review.revision_number,
        "decision": review.decision,
        "comments": review.comments,
        "correction": review.correction,
        "screening_decisions": review.screening_decisions,
        "ratings": review.ratings,
        "review_duration_ms": review.review_duration_ms,
        "original_content": review.original_content,
        "created_at": review.created_at,
    }


def reference_view(row):
    return {
        "id": row.id,
        "project_id": row.project_id,
        "paper_id": row.paper_id,
        "annotator_id": row.annotator_id,
        "rubric_version": row.rubric_version,
        "annotations": row.annotations,
        "comments": row.comments,
        "created_at": row.created_at,
    }


def evaluation_view(row):
    return {
        "id": row.id,
        "run_id": row.run_id,
        "artifact_id": row.artifact_id,
        "stage": row.stage,
        "evaluator_id": row.evaluator_id,
        "rubric_version": row.rubric_version,
        "scores": row.scores,
        "comments": row.comments,
        "evidence_references": row.evidence_references,
        "reference_paper_ids": row.reference_paper_ids,
        "created_at": row.created_at,
    }


def error_view(row):
    return {
        "id": row.id,
        "run_id": row.run_id,
        "artifact_id": row.artifact_id,
        "stage": row.stage,
        "evaluator_id": row.evaluator_id,
        "category": row.category,
        "severity": row.severity,
        "source_evidence": row.source_evidence,
        "human_correction": row.human_correction,
        "corrected": row.corrected,
        "propagated": row.propagated,
        "comments": row.comments,
        "created_at": row.created_at,
    }


def paper_view(p):
    return {
        "id": p.id,
        "title": p.title,
        "authors": p.authors,
        "year": p.year,
        "doi": p.doi,
        "url": p.url,
        "abstract": p.abstract,
        "has_full_text": bool(p.full_text),
        "in_frozen_corpus": p.in_corpus,
        "venue": p.venue,
        "citation_count": p.citation_count,
    }


def project_view(p):
    return {
        "id": p.id,
        "title": p.title,
        "question": p.question,
        "scope": p.scope,
        "review_policy": p.review_policy,
        "created_at": p.created_at,
        "run_count": len(p.runs),
    }


def run_view(r):
    return {
        "id": r.id,
        "project_id": r.project_id,
        "status": r.status,
        "current_stage": r.current_stage,
        "models": r.models,
        "settings": r.settings,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(select(1))
    except Exception as exc:
        raise HTTPException(503, "Database health check failed") from exc
    providers = provider_catalog()
    return {
        "status": "ok",
        "environment": settings.app_env,
        "dependencies": {"database": "ok"},
        "configured_models": [n for n, p in providers.items() if p.configured],
    }


@app.get("/api/v1/models")
def models():
    return {
        "providers": [
            {
                "id": p.id,
                "gateway": p.gateway,
                "configured": p.configured,
                "model": p.model_id,
            }
            for p in provider_catalog().values()
        ]
    }


@app.post("/api/v1/models/{provider_id}/check")
def check_model(provider_id: str):
    try:
        text, model, _usage = complete(
            provider_id,
            "Reply with exactly: OK",
            "You are checking that the model connection works. Reply with only OK.",
            max_tokens=16,
        )
    except ProviderError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"provider": provider_id, "model": model, "ok": True, "response": text[:100]}


@app.get("/api/v1/projects")
def projects(db: Session = Depends(get_db)):
    return [
        project_view(p)
        for p in db.scalars(select(Project).order_by(desc(Project.created_at))).all()
    ]


@app.post("/api/v1/projects", status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)):
    p = Project(
        id=str(uuid.uuid4()),
        title=body.title,
        question=body.question,
        scope=body.scope,
        review_policy=body.review_policy,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return project_view(p)


@app.patch("/api/v1/projects/{project_id}")
def update_project(project_id: str, body: ProjectUpdate, db: Session = Depends(get_db)):
    p = proj_or_404(db, project_id)
    for key, value in body.model_dump().items():
        setattr(p, key, value)
    db.commit()
    db.refresh(p)
    return project_view(p)


@app.get("/api/v1/projects/{project_id}")
def get_project(project_id: str, db: Session = Depends(get_db)):
    p = proj_or_404(db, project_id)
    return {
        **project_view(p),
        "runs": [
            run_view(r)
            for r in db.scalars(
                select(Run).where(Run.project_id == p.id).order_by(desc(Run.created_at))
            ).all()
        ],
    }


@app.get("/api/v1/projects/{project_id}/papers")
def papers(project_id: str, db: Session = Depends(get_db)):
    proj_or_404(db, project_id)
    return [
        paper_view(p)
        for p in db.scalars(
            select(Paper)
            .where(Paper.project_id == project_id)
            .order_by(desc(Paper.created_at))
        ).all()
    ]


@app.get("/api/v1/projects/{project_id}/runs/export")
def export_runs(project_id: str, db: Session = Depends(get_db)):
    project = proj_or_404(db, project_id)
    runs = db.scalars(
        select(Run).where(Run.project_id == project.id).order_by(Run.created_at)
    ).all()
    rows = []
    for run in runs:
        artifacts = db.scalars(
            select(Artifact)
            .where(Artifact.run_id == run.id)
            .order_by(Artifact.created_at)
        ).all()
        run_events = db.scalars(
            select(Event).where(Event.run_id == run.id).order_by(Event.created_at)
        ).all()
        run_reviews = db.scalars(
            select(ReviewRecord)
            .where(ReviewRecord.run_id == run.id)
            .order_by(ReviewRecord.created_at)
        ).all()
        run_evaluations = db.scalars(
            select(EvaluationRecord)
            .where(EvaluationRecord.run_id == run.id)
            .order_by(EvaluationRecord.created_at)
        ).all()
        run_errors = db.scalars(
            select(ErrorAnnotation)
            .where(ErrorAnnotation.run_id == run.id)
            .order_by(ErrorAnnotation.created_at)
        ).all()
        rows.append(
            {
                "project": {
                    "id": project.id,
                    "title": project.title,
                    "question": project.question,
                    "scope": project.scope,
                    "review_policy": project.review_policy,
                },
                "run": run_view(run),
                "artifacts": [artifact_view(a) for a in artifacts],
                "reviews": [review_view(x) for x in run_reviews],
                "evaluations": [evaluation_view(x) for x in run_evaluations],
                "errors": [error_view(x) for x in run_errors],
                "events": [
                    {
                        "id": e.id,
                        "type": e.type,
                        "message": e.message,
                        "details": e.details,
                        "created_at": e.created_at,
                    }
                    for e in run_events
                ],
            }
        )
    body = "\n".join(
        json.dumps(row, ensure_ascii=False, default=str) for row in rows
    ) + ("\n" if rows else "")
    return Response(
        body,
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f'attachment; filename="{project.id}-experiment-runs.jsonl"'
        },
    )


@app.post("/api/v1/projects/{project_id}/papers/upload", status_code=201)
async def upload_paper(
    project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)
):
    project = proj_or_404(db, project_id)
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(415, "Upload a PDF file.")
    data = await file.read(25 * 1024 * 1024 + 1)
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "PDF exceeds the 25 MB upload limit.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(415, "The uploaded file is not a valid PDF.")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise HTTPException(422, "Encrypted PDFs are not supported.")
        if len(reader.pages) > 500:
            raise HTTPException(413, "PDF exceeds the 500 page limit.")
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(422, "Could not read text from this PDF.") from exc
    if not text.strip():
        raise HTTPException(
            422, "No selectable text found. OCR scanned PDFs before upload."
        )
    title = os.path.splitext(os.path.basename(file.filename))[0][:500]
    paper = Paper(
        id=str(uuid.uuid4()),
        project_id=project.id,
        run_id=None,
        title=title,
        authors=[],
        year=None,
        doi=None,
        url="",
        abstract="",
        full_text=text[:2_000_000],
        venue="Uploaded PDF",
        citation_count=0,
    )
    db.add(paper)
    record(
        db,
        project.id,
        "library",
        "paper_uploaded",
        f"Uploaded paper: {title}",
        {"paper_id": paper.id, "characters": len(text)},
    )
    db.commit()
    db.refresh(paper)
    return paper_view(paper)


@app.get("/api/v1/projects/{project_id}/events")
def events(project_id: str, db: Session = Depends(get_db)):
    proj_or_404(db, project_id)
    return [
        {
            "id": e.id,
            "run_id": e.run_id,
            "type": e.type,
            "message": e.message,
            "details": e.details,
            "created_at": e.created_at,
        }
        for e in db.scalars(
            select(Event)
            .where(Event.project_id == project_id)
            .order_by(desc(Event.created_at))
            .limit(100)
        ).all()
    ]


@app.post("/api/v1/projects/{project_id}/runs", status_code=201)
def create_run(project_id: str, body: RunCreate, db: Session = Depends(get_db)):
    p = proj_or_404(db, project_id)
    if body.source_mode == "saved_corpus" and not db.scalar(
        select(Paper.id)
        .where(Paper.project_id == p.id, Paper.in_corpus.is_(True))
        .limit(1)
    ):
        raise HTTPException(
            422, "Upload at least one paper before starting a saved-corpus run."
        )
    if body.condition == "search_discovery" and body.source_mode != "openalex":
        raise HTTPException(422, "Search discovery must use OpenAlex retrieval.")
    missing = sorted(set(AI_STAGES) - set(body.models))
    if missing:
        raise HTTPException(
            422, f"Choose a model for every AI stage: {', '.join(missing)}"
        )
    assigned_models = {
        body.models[stage] for stage in AI_STAGES if stage in body.models
    }
    if len(assigned_models) != 1:
        raise HTTPException(
            422,
            "For a controlled model experiment, assign the same model to Search, Extraction, Organization, and Synthesis.",
        )
    for stage, provider in body.models.items():
        if stage not in AI_STAGES or provider not in provider_catalog():
            raise HTTPException(422, f"Invalid model assignment for {stage}")
        try:
            provider_config(provider)
        except ProviderError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
    review_policy = EXPERIMENT_CONDITION_POLICIES[body.condition]
    protocol_version = (
        "2.0"
        if body.condition
        in {
            "without_intermediate_human_supervision",
            "with_human_supervision_all_stages",
        }
        else "1.2"
    )
    if protocol_version == "2.0" and body.source_mode != "saved_corpus":
        raise HTTPException(
            422,
            "The controlled A/B conditions require the same frozen Saved corpus. Run and document literature search separately, then use the frozen corpus for both conditions.",
        )
    run_type = (
        "search_discovery" if body.condition == "search_discovery" else "experiment"
    )
    r = Run(
        id=str(uuid.uuid4()),
        project_id=p.id,
        models=body.models,
        settings={
            "run_type": run_type,
            "max_papers": body.max_papers,
            "source_mode": body.source_mode,
            "experiment_label": body.experiment_label,
            "condition": body.condition,
            "scenario": body.condition,
            "replicate": body.replicate,
            "random_seed": body.random_seed,
            "temperature": body.temperature,
            "protocol_version": protocol_version,
            "review_policy": review_policy,
            "model_scope": "one configured model shared by Search, Extraction, Organization, and Synthesis",
            "corpus_policy": "frozen source IDs captured by Search at run start",
        },
        status="queued",
    )
    db.add(r)
    db.flush()
    record(
        db,
        p.id,
        r.id,
        "run_queued",
        "Workflow run queued",
        {
            "run_type": run_type,
            "review_policy": review_policy,
            "condition": body.condition,
            "models": body.models,
            "experiment_label": body.experiment_label,
            "replicate": body.replicate,
            "source_mode": body.source_mode,
            "protocol_version": protocol_version,
        },
    )
    db.commit()
    db.refresh(r)
    return {**run_view(r), "artifacts": []}


@app.get("/api/v1/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    r = run_or_404(db, run_id)
    artifacts = db.scalars(
        select(Artifact).where(Artifact.run_id == r.id).order_by(Artifact.created_at)
    ).all()
    reviews = db.scalars(
        select(ReviewRecord)
        .where(ReviewRecord.run_id == r.id)
        .order_by(ReviewRecord.created_at)
    ).all()
    return {
        **run_view(r),
        "artifacts": [artifact_view(a) for a in artifacts],
        "reviews": [review_view(x) for x in reviews],
        "papers": [
            paper_view(p)
            for p in db.scalars(
                select(Paper)
                .where(Paper.project_id == r.project_id)
                .order_by(desc(Paper.created_at))
            ).all()
        ],
    }


@app.post("/api/v1/runs/{run_id}/artifacts/{artifact_id}/review")
def review(
    run_id: str, artifact_id: str, body: ReviewAction, db: Session = Depends(get_db)
):
    r = run_or_404(db, run_id)
    p = proj_or_404(db, r.project_id)
    a = db.get(Artifact, artifact_id)
    if not a or a.run_id != r.id:
        raise HTTPException(404, "Artifact not found")
    if a.status != "pending_review":
        raise HTTPException(409, "Artifact is no longer awaiting review")
    if r.status != "awaiting_review":
        raise HTTPException(409, "Run is not currently waiting for review")
    a.reviewer_note = body.note
    corrected_artifact_id = None
    next_revision = 1 + len(
        db.scalars(select(ReviewRecord).where(ReviewRecord.artifact_id == a.id)).all()
    )
    if body.correction is not None and body.action != "approve":
        raise HTTPException(
            422, "A correction can only be saved when approving the reviewed artifact."
        )
    if body.correction is not None:
        latest = db.scalar(
            select(Artifact)
            .where(Artifact.run_id == r.id, Artifact.stage == a.stage)
            .order_by(Artifact.version.desc())
        )
        corrected = Artifact(
            id=str(uuid.uuid4()),
            run_id=r.id,
            stage=a.stage,
            version=(latest.version + 1 if latest else a.version + 1),
            content={
                **{k: v for k, v in a.content.items() if k != "_telemetry"},
                **body.correction,
                "_telemetry": {
                    "stage": a.stage,
                    "origin": "human_correction",
                    "source_artifact_id": a.id,
                },
            },
            status="approved",
            model="human correction",
            reviewer_note=body.note,
        )
        db.add(corrected)
        db.flush()
        corrected_artifact_id = corrected.id
    review_ratings = body.ratings.model_dump() if body.ratings else {}
    review_record = ReviewRecord(
        id=str(uuid.uuid4()),
        run_id=r.id,
        artifact_id=a.id,
        corrected_artifact_id=corrected_artifact_id,
        stage=a.stage,
        revision_number=next_revision,
        decision=body.action,
        comments=body.note,
        correction=body.correction,
        ratings=review_ratings,
        review_duration_ms=body.review_duration_ms,
        original_content={k: v for k, v in a.content.items() if k != "_telemetry"},
    )
    db.add(review_record)
    if body.action == "approve":
        screening_details = {}
        if a.stage == "search":
            snapshot = a.content.get("source_snapshot", [])
            candidates = [
                item.get("paper_id")
                for item in snapshot
                if isinstance(item, dict) and isinstance(item.get("paper_id"), str)
            ]
            selected = list(
                dict.fromkeys(
                    body.source_paper_ids
                    if body.source_paper_ids is not None
                    else candidates
                )
            )
            if any(source_id not in candidates for source_id in selected):
                raise HTTPException(
                    422, "A selected source was not produced by this Search artifact."
                )
            included = set(selected)
            selected_snapshot = sorted(
                (
                    {key: value for key, value in item.items() if key != "rank"}
                    for item in snapshot
                    if item.get("paper_id") in included
                ),
                key=lambda item: item["paper_id"],
            )
            selected_hash = hashlib.sha256(
                json.dumps(
                    selected_snapshot,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            all_candidate_hash = a.content.get("source_snapshot_hash", "")
            screening_details = {
                "included_paper_ids": selected,
                "excluded_paper_ids": [
                    source_id for source_id in candidates if source_id not in included
                ],
            }
            review_record.screening_decisions = screening_details
            if r.settings.get("run_type") == "search_discovery":
                if not selected:
                    raise HTTPException(
                        422, "Select at least one candidate to freeze into the corpus."
                    )
                for paper in db.scalars(
                    select(Paper).where(
                        Paper.project_id == p.id, Paper.id.in_(candidates)
                    )
                ).all():
                    # Preserve papers that were already in the frozen corpus. Search
                    # discovery may add selected candidates, but must not remove old rows.
                    if paper.run_id == r.id:
                        paper.in_corpus = paper.id in included
                    elif paper.id in included:
                        paper.in_corpus = True
                r.settings = {
                    **r.settings,
                    "source_paper_ids": selected,
                    "source_snapshot_hash": selected_hash,
                    "resume_from_stage": a.stage,
                }
            elif r.settings.get("protocol_version") == "2.0":
                # Record screening judgments but retain the frozen candidate set for paired downstream comparison.
                r.settings = {
                    **r.settings,
                    "source_paper_ids": candidates,
                    "source_snapshot_hash": all_candidate_hash,
                    "resume_from_stage": a.stage,
                }
            else:
                if not selected:
                    raise HTTPException(
                        422, "Keep at least one source before approving Search."
                    )
                a.content = {
                    **a.content,
                    "search_result_snapshot_hash": all_candidate_hash,
                    "included_paper_ids": selected,
                    "excluded_paper_ids": screening_details["excluded_paper_ids"],
                    "screened_source_snapshot_hash": selected_hash,
                }
                for paper in db.scalars(
                    select(Paper).where(
                        Paper.project_id == p.id, Paper.id.in_(candidates)
                    )
                ).all():
                    paper.in_corpus = paper.id in included
                r.settings = {
                    **r.settings,
                    "source_paper_ids": selected,
                    "source_snapshot_hash": selected_hash,
                    "resume_from_stage": a.stage,
                }
        else:
            r.settings = {**r.settings, "resume_from_stage": a.stage}
        a.status = "approved"
        record(
            db,
            p.id,
            r.id,
            "review_approved",
            f"{a.stage.title()} v{a.version} approved",
            {
                "review_id": review_record.id,
                "artifact_id": a.id,
                "corrected_artifact_id": corrected_artifact_id,
                "note": body.note,
                "ratings": review_ratings,
                "review_duration_ms": body.review_duration_ms,
                **screening_details,
            },
        )
    else:
        a.status = "revision_requested"
        stage = a.stage
        if body.action == "return":
            stage = STAGES[max(0, STAGES.index(a.stage) - 1)]
        resume_from = (
            STAGES[STAGES.index(stage) - 1] if STAGES.index(stage) > 0 else None
        )
        r.settings = {
            **r.settings,
            "review_note": body.note,
            "resume_from_stage": resume_from,
        }
        record(
            db,
            p.id,
            r.id,
            "review_revision_requested",
            f"Revision requested from {stage.title()}",
            {
                "review_id": review_record.id,
                "artifact_id": a.id,
                "action": body.action,
                "note": body.note,
                "retry_stage": stage,
                "ratings": review_ratings,
                "review_duration_ms": body.review_duration_ms,
            },
        )
    r.status = "queued"
    record(
        db,
        p.id,
        r.id,
        "run_queued",
        "Run queued after reviewer decision",
        {"resume_from_stage": r.settings.get("resume_from_stage")},
    )
    db.commit()
    db.refresh(r)
    return {
        **run_view(r),
        "artifacts": [
            artifact_view(x)
            for x in db.scalars(
                select(Artifact)
                .where(Artifact.run_id == r.id)
                .order_by(Artifact.created_at)
            ).all()
        ],
    }


@app.post("/api/v1/projects/{project_id}/reference-annotations", status_code=201)
def create_reference_annotation(
    project_id: str, body: ReferenceAnnotationCreate, db: Session = Depends(get_db)
):
    project = proj_or_404(db, project_id)
    paper = db.get(Paper, body.paper_id)
    if not paper or paper.project_id != project.id:
        raise HTTPException(404, "Paper not found in this project.")
    row = ReferenceAnnotation(
        id=str(uuid.uuid4()),
        project_id=project.id,
        paper_id=paper.id,
        annotator_id=body.annotator_id.strip(),
        rubric_version=body.rubric_version.strip(),
        annotations=body.annotations.model_dump(),
        comments=body.comments,
    )
    db.add(row)
    record(
        db,
        project.id,
        "reference",
        "reference_annotation_created",
        "Independent reference annotation saved",
        {
            "annotation_id": row.id,
            "paper_id": paper.id,
            "annotator_id": row.annotator_id,
            "rubric_version": row.rubric_version,
        },
    )
    db.commit()
    db.refresh(row)
    return reference_view(row)


@app.get("/api/v1/projects/{project_id}/reference-annotations")
def list_reference_annotations(project_id: str, db: Session = Depends(get_db)):
    proj_or_404(db, project_id)
    rows = db.scalars(
        select(ReferenceAnnotation)
        .where(ReferenceAnnotation.project_id == project_id)
        .order_by(ReferenceAnnotation.created_at)
    ).all()
    return [reference_view(row) for row in rows]


@app.post("/api/v1/runs/{run_id}/evaluations", status_code=201)
def create_evaluation(
    run_id: str, body: EvaluationCreate, db: Session = Depends(get_db)
):
    run = run_or_404(db, run_id)
    artifact = db.get(Artifact, body.artifact_id)
    if not artifact or artifact.run_id != run.id:
        raise HTTPException(404, "Artifact not found in this run.")
    if any(
        not isinstance(paper_id, str)
        or not (paper := db.get(Paper, paper_id))
        or paper.project_id != run.project_id
        for paper_id in body.reference_paper_ids
    ):
        raise HTTPException(
            422,
            "Every reference-set paper ID must exist in this project's saved corpus.",
        )
    row = EvaluationRecord(
        id=str(uuid.uuid4()),
        run_id=run.id,
        artifact_id=artifact.id,
        stage=artifact.stage,
        evaluator_id=body.evaluator_id.strip(),
        rubric_version=body.rubric_version.strip(),
        scores=body.scores,
        comments=body.comments,
        evidence_references=body.evidence_references,
        reference_paper_ids=body.reference_paper_ids,
    )
    db.add(row)
    record(
        db,
        run.project_id,
        run.id,
        "independent_evaluation_created",
        "Independent human evaluation saved",
        {
            "evaluation_id": row.id,
            "artifact_id": artifact.id,
            "stage": artifact.stage,
            "evaluator_id": row.evaluator_id,
            "rubric_version": row.rubric_version,
        },
    )
    db.commit()
    db.refresh(row)
    return evaluation_view(row)


@app.get("/api/v1/runs/{run_id}/evaluations")
def list_evaluations(run_id: str, db: Session = Depends(get_db)):
    run = run_or_404(db, run_id)
    rows = db.scalars(
        select(EvaluationRecord)
        .where(EvaluationRecord.run_id == run.id)
        .order_by(EvaluationRecord.created_at)
    ).all()
    return [evaluation_view(row) for row in rows]


@app.post("/api/v1/runs/{run_id}/errors", status_code=201)
def create_error_annotation(
    run_id: str, body: ErrorAnnotationCreate, db: Session = Depends(get_db)
):
    run = run_or_404(db, run_id)
    artifact = db.get(Artifact, body.artifact_id)
    if not artifact or artifact.run_id != run.id:
        raise HTTPException(404, "Artifact not found in this run.")
    row = ErrorAnnotation(
        id=str(uuid.uuid4()),
        run_id=run.id,
        artifact_id=artifact.id,
        stage=artifact.stage,
        evaluator_id=body.evaluator_id.strip(),
        category=body.category,
        severity=body.severity,
        source_evidence=body.source_evidence,
        human_correction=body.human_correction,
        corrected=body.corrected,
        propagated=body.propagated,
        comments=body.comments,
    )
    db.add(row)
    record(
        db,
        run.project_id,
        run.id,
        "error_annotation_created",
        "Independent error annotation saved",
        {
            "error_id": row.id,
            "artifact_id": artifact.id,
            "stage": artifact.stage,
            "category": row.category,
            "severity": row.severity,
            "corrected": row.corrected,
            "propagated": row.propagated,
        },
    )
    db.commit()
    db.refresh(row)
    return error_view(row)


@app.get("/api/v1/runs/{run_id}/errors")
def list_error_annotations(run_id: str, db: Session = Depends(get_db)):
    run = run_or_404(db, run_id)
    rows = db.scalars(
        select(ErrorAnnotation)
        .where(ErrorAnnotation.run_id == run.id)
        .order_by(ErrorAnnotation.created_at)
    ).all()
    return [error_view(row) for row in rows]


@app.get("/api/v1/runs/{run_id}/metrics")
def get_run_metrics(run_id: str, db: Session = Depends(get_db)):
    return run_metrics(db, run_or_404(db, run_id))


@app.post("/api/v1/projects/{project_id}/compare")
def compare_paired_runs(
    project_id: str, body: ComparisonRequest, db: Session = Depends(get_db)
):
    project = proj_or_404(db, project_id)
    metric_values = {
        "without_intermediate": {},
        "supervised": {},
        "supervised_minus_without": {},
    }
    comparisons = []
    for pair in body.pairs:
        no_review = run_or_404(db, pair.no_intermediate_run_id)
        supervised = run_or_404(db, pair.supervised_run_id)
        if no_review.project_id != project.id or supervised.project_id != project.id:
            raise HTTPException(
                422, "Both paired runs must belong to the selected project."
            )
        if (
            no_review.settings.get("condition")
            != "without_intermediate_human_supervision"
            or supervised.settings.get("condition")
            != "with_human_supervision_all_stages"
        ):
            raise HTTPException(
                422,
                "Pair order must be no-intermediate first and all-stage-supervision second.",
            )
        if no_review.status != "completed" or supervised.status != "completed":
            raise HTTPException(422, "Both runs must be complete before comparison.")
        no_metrics = run_metrics(db, no_review)
        yes_metrics = run_metrics(db, supervised)
        no_source_ids = set(no_metrics.get("source_paper_ids") or [])
        yes_source_ids = set(yes_metrics.get("source_paper_ids") or [])
        if (
            not no_metrics.get("source_snapshot_hash")
            or no_metrics.get("source_snapshot_hash")
            != yes_metrics.get("source_snapshot_hash")
            or no_source_ids != yes_source_ids
        ):
            raise HTTPException(
                422,
                "Paired runs must use the identical frozen corpus and Search snapshot.",
            )
        if (
            no_review.settings.get("protocol_version") != "2.0"
            or supervised.settings.get("protocol_version") != "2.0"
            or no_review.settings.get("source_mode") != "saved_corpus"
            or supervised.settings.get("source_mode") != "saved_corpus"
        ):
            raise HTTPException(
                422, "Only protocol 2.0 runs on the fixed saved corpus can be paired."
            )
        matching_settings = (
            "temperature",
            "replicate",
            "random_seed",
            "max_papers",
            "source_mode",
        )
        if no_review.models != supervised.models or any(
            no_review.settings.get(key) != supervised.settings.get(key)
            for key in matching_settings
        ):
            raise HTTPException(
                422,
                "Paired runs must match model assignments, temperature, replicate ID, seed, and source settings; only the review condition may differ.",
            )
        flat_no = _flatten_metrics(no_metrics)
        flat_yes = _flatten_metrics(yes_metrics)
        shared = set(flat_no) & set(flat_yes)
        differences = {
            key: flat_yes[key] - flat_no[key]
            for key in shared
            if isinstance(flat_no[key], (int, float))
            and isinstance(flat_yes[key], (int, float))
        }
        for group, values in (
            ("without_intermediate", flat_no),
            ("supervised", flat_yes),
            ("supervised_minus_without", differences),
        ):
            for key, value in values.items():
                if isinstance(value, (int, float)):
                    metric_values[group].setdefault(key, []).append(float(value))
        comparisons.append(
            {
                "replicate": no_review.settings.get("replicate"),
                "without_intermediate_run_id": no_review.id,
                "supervised_run_id": supervised.id,
                "without_intermediate": no_metrics,
                "supervised": yes_metrics,
                "differences_supervised_minus_without": differences,
            }
        )
    return {
        "project_id": project.id,
        "paired_run_count": len(comparisons),
        "pairs": comparisons,
        "descriptive_summary": {
            group: {key: describe(values) for key, values in metrics.items()}
            for group, metrics in metric_values.items()
        },
        "statistics_note": "Descriptive paired differences only. No significance test or confidence interval is reported.",
    }


def _flatten_metrics(metrics):
    flat = {
        "total_tokens": metrics["system"]["total_tokens"],
        "input_tokens": metrics["system"]["input_tokens"],
        "output_tokens": metrics["system"]["output_tokens"],
        "machine_execution_duration_ms": metrics["system"][
            "total_agent_execution_duration_ms"
        ],
        "total_elapsed_duration_ms": metrics["system"]["total_elapsed_duration_ms"],
        "measured_human_review_time_ms": metrics["human"][
            "measured_review_duration_ms"
        ]["mean"],
        "total_review_count": metrics["human"]["review_count"],
        "error_count": metrics["errors"]["total"],
        "corrected_error_count": metrics["errors"]["corrected"],
        "propagated_error_count": metrics["errors"]["propagated"],
    }
    for stage, dimensions in metrics["quality_evaluations"].items():
        for dimension, stats in dimensions.items():
            flat[f"quality/{stage}/{dimension}"] = stats["mean"]
    if metrics["system"]["provider_reported_cost"] is not None:
        flat["provider_reported_cost"] = metrics["system"]["provider_reported_cost"]
    return flat


@app.get("/api/v1/runs/{run_id}/evidence-package")
def export_evidence_package(run_id: str, db: Session = Depends(get_db)):
    run = run_or_404(db, run_id)
    project = proj_or_404(db, run.project_id)
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
        select(ErrorAnnotation)
        .where(ErrorAnnotation.run_id == run.id)
        .order_by(ErrorAnnotation.created_at)
    ).all()
    events = db.scalars(
        select(Event).where(Event.run_id == run.id).order_by(Event.created_at)
    ).all()
    paper_ids = run.settings.get("source_paper_ids", [])
    source_papers = (
        db.scalars(
            select(Paper).where(Paper.project_id == project.id, Paper.id.in_(paper_ids))
        ).all()
        if paper_ids
        else []
    )
    refs = (
        db.scalars(
            select(ReferenceAnnotation).where(
                ReferenceAnnotation.project_id == project.id,
                ReferenceAnnotation.paper_id.in_(paper_ids),
            )
        ).all()
        if paper_ids
        else []
    )
    metrics = run_metrics(db, run)
    condition = run.settings.get("condition", "unassigned")
    folder = (
        "condition_a"
        if condition == "without_intermediate_human_supervision"
        else "condition_b"
        if condition == "with_human_supervision_all_stages"
        else "legacy"
    )
    root = f"{run.id}/{folder}"
    buffer = io.BytesIO()

    def csv_text(rows, headers):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:

        def put_json(path, obj):
            archive.writestr(
                path, json.dumps(obj, ensure_ascii=False, indent=2, default=str)
            )

        put_json(
            f"{run.id}/protocol/protocol.json",
            {
                "title": "Agentic Workflow for Literature Synthesis",
                "protocol_version": run.settings.get("protocol_version"),
                "condition": condition,
                "review_policy": run.settings.get("review_policy"),
                "research_question": project.question,
                "scope": project.scope,
                "human_review_policy": "No intermediate review"
                if condition == "without_intermediate_human_supervision"
                else "Review all four stages"
                if condition == "with_human_supervision_all_stages"
                else "Legacy/other condition",
                "statistics_note": metrics["statistics_note"],
            },
        )
        put_json(
            f"{run.id}/protocol/configuration.json",
            {
                "run": run_view(run),
                "project_id": project.id,
                "created_at": run.created_at,
                "updated_at": run.updated_at,
            },
        )
        put_json(
            f"{run.id}/corpus/corpus_manifest.json",
            {
                "source_snapshot_hash": run.settings.get("source_snapshot_hash"),
                "source_paper_ids": paper_ids,
                "count": len(source_papers),
                "policy": run.settings.get("corpus_policy"),
                "papers": [paper_view(p) for p in source_papers],
            },
        )
        archive.writestr(
            f"{run.id}/corpus/papers.csv",
            csv_text(
                [paper_view(p) for p in source_papers],
                [
                    "id",
                    "title",
                    "authors",
                    "year",
                    "doi",
                    "url",
                    "abstract",
                    "has_full_text",
                    "venue",
                    "citation_count",
                ],
            ),
        )
        for artifact in artifacts:
            data = {
                **artifact_view(artifact),
                "content": {k: v for k, v in (artifact.content or {}).items()},
            }
            stage_dir = (
                "generation" if artifact.stage == "synthesis" else artifact.stage
            )
            put_json(f"{root}/{stage_dir}/artifact-v{artifact.version}.json", data)
        put_json(
            f"{root}/human_reviews/reviews.json", [review_view(x) for x in reviews]
        )
        put_json(
            f"{run.id}/ground_truth/reference_annotations.json",
            [reference_view(x) for x in refs],
        )
        put_json(
            f"{run.id}/evaluation/evaluations.json",
            [evaluation_view(x) for x in evaluations],
        )
        eval_rows = [
            {
                "evaluation_id": row.id,
                "artifact_id": row.artifact_id,
                "stage": row.stage,
                "evaluator_id": row.evaluator_id,
                "rubric_version": row.rubric_version,
                "dimension": dimension,
                "score": score,
                "comments": row.comments,
                "evidence_references": json.dumps(
                    row.evidence_references, ensure_ascii=False
                ),
                "reference_paper_ids": json.dumps(row.reference_paper_ids),
            }
            for row in evaluations
            for dimension, score in row.scores.items()
        ]
        archive.writestr(
            f"{run.id}/evaluation/evaluator_scores.csv",
            csv_text(
                eval_rows,
                [
                    "evaluation_id",
                    "artifact_id",
                    "stage",
                    "evaluator_id",
                    "rubric_version",
                    "dimension",
                    "score",
                    "comments",
                    "evidence_references",
                    "reference_paper_ids",
                ],
            ),
        )
        error_rows = [error_view(x) for x in errors]
        archive.writestr(
            f"{run.id}/evaluation/error_annotations.csv",
            csv_text(
                error_rows,
                [
                    "id",
                    "run_id",
                    "artifact_id",
                    "stage",
                    "evaluator_id",
                    "category",
                    "severity",
                    "source_evidence",
                    "human_correction",
                    "corrected",
                    "propagated",
                    "comments",
                    "created_at",
                ],
            ),
        )
        put_json(f"{run.id}/metrics/agent_metrics.json", metrics)
        metric_rows = [
            {"group": group, "metric": metric, "value": value}
            for group, values in metrics.items()
            if isinstance(values, dict)
            for metric, value in values.items()
            if not isinstance(value, dict)
        ]
        archive.writestr(
            f"{run.id}/metrics/metrics.csv",
            csv_text(metric_rows, ["group", "metric", "value"]),
        )
        put_json(
            f"{root}/execution_logs/events.json",
            [
                {
                    "id": e.id,
                    "type": e.type,
                    "message": e.message,
                    "details": e.details,
                    "created_at": e.created_at,
                }
                for e in events
            ],
        )
        report = f"# Experimental run {run.id}\n\n- Condition: `{condition}`\n- Status at export: `{run.status}`\n- Model IDs: `{json.dumps(run.models, ensure_ascii=False)}`\n- Frozen source count: {len(source_papers)}\n- Source snapshot hash: `{run.settings.get('source_snapshot_hash')}`\n- Protocol version: `{run.settings.get('protocol_version')}`\n\nThis report contains measured records only. Missing values are not imputed. Independent evaluation and reference annotations are stored separately from workflow approval decisions.\n\n## Metrics\n\n```json\n{json.dumps(metrics, ensure_ascii=False, indent=2, default=str)}\n```\n\nThis export is {'partial because the run is not complete' if run.status != 'completed' else 'a completed-run evidence package'}. No inferential statistical result is generated.\n"
        archive.writestr(f"{run.id}/reports/experimental_results.md", report)
        archive.writestr(
            f"{run.id}/README.txt",
            "Raw artifacts, review records, independent scores, error annotations, corpus manifest, and traceable descriptive metrics. Values are not fabricated or imputed. JSON/CSV/Markdown are provided; PDF and DOCX rendering are not implemented in this release.\n",
        )
    buffer.seek(0)
    return Response(
        buffer.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{run.id}-evidence-package.zip"'
        },
    )


@app.get("/")
def root():
    return {"service": "Synthesis API", "docs": "/docs", "health": "/health"}
