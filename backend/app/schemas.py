from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

ReviewPolicy = Literal[
    "every_stage", "search_and_final", "final_only", "after_extraction", "none"
]
ExperimentCondition = Literal[
    "without_intermediate_human_supervision",
    "with_human_supervision_all_stages",
    "search_discovery",
    # Kept for reading existing runs and supporting already-created local projects.
    "full_audit",
    "targeted_retry",
    "endpoint_oversight",
    "midpoint_gating",
]


class ProjectCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    question: str = Field(min_length=10, max_length=5000)
    scope: str = Field(default="", max_length=10000)
    review_policy: ReviewPolicy = "every_stage"


class ProjectUpdate(ProjectCreate):
    pass


class RunCreate(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    max_papers: int = Field(default=25, ge=5, le=25)
    from_stage: str | None = None
    source_mode: Literal["openalex", "saved_corpus"] = "openalex"
    experiment_label: str = Field(default="", max_length=240)
    # This value selects the actual workflow review gates and is also exported with the run.
    condition: ExperimentCondition = "full_audit"
    replicate: str = Field(default="1", min_length=1, max_length=80)
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)


class ReviewRatings(BaseModel):
    relevance: int = Field(ge=1, le=5)
    evidence_accuracy: int = Field(ge=1, le=5)
    coverage: int = Field(ge=1, le=5)
    traceability: int = Field(ge=1, le=5)


class ReviewAction(BaseModel):
    action: Literal["approve", "retry", "return"]
    note: str = Field(default="", max_length=5000)
    correction: dict | None = None
    review_duration_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    source_paper_ids: list[str] | None = Field(default=None, max_length=25)
    # Workflow approval is separate from independent quality evaluation.
    ratings: ReviewRatings | None = None


class ReferenceFields(BaseModel):
    research_objective: str = Field(min_length=1, max_length=10000)
    research_problem: str = Field(default="Not reported", max_length=10000)
    research_question: str = Field(default="Not reported", max_length=10000)
    methodology: str = Field(min_length=1, max_length=10000)
    research_design: str = Field(default="Not reported", max_length=10000)
    dataset_or_sample: str = Field(min_length=1, max_length=10000)
    data_collection_methods: str = Field(default="Not reported", max_length=10000)
    analysis_methods: str = Field(default="Not reported", max_length=10000)
    findings: str = Field(min_length=1, max_length=10000)
    limitations: str = Field(min_length=1, max_length=10000)
    research_gaps: str = Field(min_length=1, max_length=10000)
    contributions: str = Field(default="Not reported", max_length=10000)
    supporting_evidence: list[dict[str, Any]] = Field(
        default_factory=list, max_length=100
    )
    page_or_section_references: list[dict[str, Any]] = Field(
        default_factory=list, max_length=100
    )


class ReferenceAnnotationCreate(BaseModel):
    paper_id: str = Field(min_length=1, max_length=36)
    annotator_id: str = Field(min_length=1, max_length=240)
    rubric_version: str = Field(min_length=1, max_length=120)
    annotations: ReferenceFields
    comments: str = Field(default="", max_length=10000)


class EvaluationCreate(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=36)
    evaluator_id: str = Field(min_length=1, max_length=240)
    rubric_version: str = Field(min_length=1, max_length=120)
    scores: dict[str, int] = Field(min_length=1, max_length=40)
    comments: str = Field(default="", max_length=10000)
    evidence_references: list[dict[str, Any]] = Field(
        default_factory=list, max_length=200
    )
    reference_paper_ids: list[str] = Field(default_factory=list, max_length=500)

    @field_validator("scores")
    @classmethod
    def score_values_are_five_point(cls, scores):
        if any(value < 1 or value > 5 for value in scores.values()):
            raise ValueError("Evaluation scores must use the 1–5 scale.")
        return scores


class ErrorAnnotationCreate(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=36)
    evaluator_id: str = Field(min_length=1, max_length=240)
    category: Literal[
        "retrieval_error",
        "missing_information",
        "factual_extraction_error",
        "evidence_attribution_error",
        "citation_error",
        "organization_error",
        "incorrect_comparison",
        "unsupported_claim",
        "fabricated_information",
        "synthesis_error",
    ]
    severity: Literal["minor", "moderate", "major", "critical"]
    source_evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    human_correction: str = Field(default="", max_length=10000)
    corrected: bool = False
    propagated: bool = False
    comments: str = Field(default="", max_length=10000)


class PairedRun(BaseModel):
    no_intermediate_run_id: str = Field(min_length=1, max_length=36)
    supervised_run_id: str = Field(min_length=1, max_length=36)


class ComparisonRequest(BaseModel):
    pairs: list[PairedRun] = Field(min_length=1, max_length=100)
