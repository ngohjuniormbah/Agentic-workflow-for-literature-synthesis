from pydantic import BaseModel, Field
from typing import Literal

ReviewPolicy = Literal["every_stage", "search_and_final", "final_only", "after_extraction"]
class ProjectCreate(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    question: str = Field(min_length=10, max_length=5000)
    scope: str = Field(default="", max_length=10000)
    review_policy: ReviewPolicy = "every_stage"
class ProjectUpdate(ProjectCreate): pass
class RunCreate(BaseModel):
    models: dict[str, str] = Field(default_factory=dict)
    max_papers: int = Field(default=25, ge=5, le=25)
    from_stage: str | None = None
    source_mode: Literal["openalex", "saved_corpus"] = "openalex"
    experiment_label: str = Field(default="", max_length=240)
    condition: str = Field(default="every_stage", min_length=2, max_length=120)
    replicate: str = Field(default="1", min_length=1, max_length=80)
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
class ReviewAction(BaseModel):
    action: Literal["approve", "retry", "return"]
    note: str = Field(default="", max_length=5000)
