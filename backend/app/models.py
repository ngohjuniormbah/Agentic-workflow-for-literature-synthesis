from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def now():
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    question: Mapped[str] = mapped_column(Text)
    scope: Mapped[str] = mapped_column(Text, default="")
    review_policy: Mapped[str] = mapped_column(String(32), default="every_stage")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    runs: Mapped[list["Run"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued")
    current_stage: Mapped[str] = mapped_column(String(32), default="search")
    models: Mapped[dict] = mapped_column(JSON, default=dict)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=now, onupdate=now
    )
    project: Mapped[Project] = relationship(back_populates="runs")
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Paper(Base):
    __tablename__ = "papers"
    __table_args__ = (UniqueConstraint("project_id", "doi", name="uq_project_doi"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(Text)
    authors: Mapped[list] = mapped_column(JSON, default=list)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(500), nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    abstract: Mapped[str] = mapped_column(Text, default="")
    full_text: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[str] = mapped_column(String(500), default="")
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    in_corpus: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24), default="pending_review")
    model: Mapped[str] = mapped_column(String(100), default="")
    reviewer_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    run: Mapped[Run] = relationship(back_populates="artifacts")


class ReviewRecord(Base):
    __tablename__ = "review_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    corrected_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    revision_number: Mapped[int] = mapped_column(Integer, default=1)
    decision: Mapped[str] = mapped_column(String(24))
    comments: Mapped[str] = mapped_column(Text, default="")
    correction: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    screening_decisions: Mapped[dict] = mapped_column(JSON, default=dict)
    ratings: Mapped[dict] = mapped_column(JSON, default=dict)
    review_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    original_content: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ReferenceAnnotation(Base):
    __tablename__ = "reference_annotations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), index=True
    )
    annotator_id: Mapped[str] = mapped_column(String(240))
    rubric_version: Mapped[str] = mapped_column(String(120))
    annotations: Mapped[dict] = mapped_column(JSON)
    comments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EvaluationRecord(Base):
    __tablename__ = "evaluation_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    evaluator_id: Mapped[str] = mapped_column(String(240))
    rubric_version: Mapped[str] = mapped_column(String(120))
    scores: Mapped[dict] = mapped_column(JSON)
    comments: Mapped[str] = mapped_column(Text, default="")
    evidence_references: Mapped[list] = mapped_column(JSON, default=list)
    reference_paper_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ErrorAnnotation(Base):
    __tablename__ = "error_annotations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), index=True)
    evaluator_id: Mapped[str] = mapped_column(String(240))
    category: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(24))
    source_evidence: Mapped[list] = mapped_column(JSON, default=list)
    human_correction: Mapped[str] = mapped_column(Text, default="")
    corrected: Mapped[bool] = mapped_column(default=False)
    propagated: Mapped[bool] = mapped_column(default=False)
    comments: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    run_id: Mapped[str] = mapped_column(String(36), index=True)
    type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
