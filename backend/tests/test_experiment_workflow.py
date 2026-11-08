import io
import json
import unittest
import uuid
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main, worker, workflow
from app.db import Base
from app.models import Artifact, Event, Paper, Project, ReviewRecord, Run
from app.schemas import (
    ComparisonRequest,
    EvaluationCreate,
    ReferenceAnnotationCreate,
    ReferenceFields,
    ReviewAction,
    ReviewRatings,
    RunCreate,
)
from app.workflow import EXPERIMENT_CONDITION_POLICIES, validate_organization


class WorkflowExperimentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}
        )
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.project = Project(
            id=str(uuid.uuid4()),
            title="Test",
            question="What does the evidence say about intervention X?",
            scope="",
            review_policy="every_stage",
        )
        self.session.add(self.project)
        self.paper = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=None,
            title="Frozen paper",
            authors=[],
            year=2024,
            doi=None,
            url="",
            abstract="A sufficiently long verbatim quote supporting the primary result.",
            full_text="",
            venue="",
            citation_count=0,
        )
        self.session.add(self.paper)
        self.run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models={
                "search": "openai",
                "extraction": "openai",
                "organization": "openai",
                "synthesis": "openai",
            },
            settings={
                "source_mode": "saved_corpus",
                "max_papers": 25,
                "protocol_version": "1.0",
            },
            status="running",
        )
        self.session.add(self.run)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_run_metadata_is_validated_and_bounded(self):
        spec = RunCreate(
            models={
                "search": "openai",
                "extraction": "openai",
                "organization": "openai",
                "synthesis": "openai",
            },
            source_mode="saved_corpus",
            experiment_label="pilot",
            condition="full_audit",
            replicate="R1",
            random_seed=17,
        )
        self.assertEqual(spec.random_seed, 17)
        with self.assertRaises(ValueError):
            RunCreate(models={}, random_seed=2**32)
        self.assertIsNone(ReviewAction(action="approve").ratings)

    def test_delete_project_removes_its_saved_experiment_data_only(self):
        other = Project(
            id=str(uuid.uuid4()),
            title="Keep this project",
            question="Keep this research question?",
            scope="",
            review_policy="every_stage",
        )
        self.session.add(other)
        other_paper = Paper(
            id=str(uuid.uuid4()),
            project_id=other.id,
            run_id=None,
            title="Other project paper",
            authors=[],
            year=2025,
            doi=None,
            url="",
            abstract="",
            full_text="",
            venue="",
            citation_count=0,
        )
        self.session.add(other_paper)
        self.run.status = "completed"
        self.session.commit()

        result = main.delete_project(self.project.id, self.session)

        self.assertTrue(result["deleted"])
        self.assertIsNone(self.session.get(Project, self.project.id))
        self.assertIsNone(self.session.get(Paper, self.paper.id))
        self.assertIsNone(self.session.get(Run, self.run.id))
        self.assertIsNotNone(self.session.get(Project, other.id))
        self.assertIsNotNone(self.session.get(Paper, other_paper.id))

    def test_run_view_redacts_credentials_misconfigured_as_model_ids(self):
        self.run.settings["model_ids"] = {
            "search": "OPENROUTER_API_KEY: sk-or-v1-credential-value",
            "extraction": "gpt-4o-mini",
        }

        result = main.run_view(self.run)

        self.assertEqual(
            result["settings"]["model_ids"]["search"],
            "Invalid model ID (credential redacted)",
        )
        self.assertEqual(result["settings"]["model_ids"]["extraction"], "gpt-4o-mini")
        self.assertEqual(
            main.safe_model_id("sk-or-v1-credential-value"),
            "",
        )

    def test_run_detail_exposes_the_saved_stage_failure_for_the_dashboard(self):
        self.run.status = "failed"
        failure = Event(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=self.run.id,
            type="stage_failed",
            message="Search failed: provider rejected the model ID.",
            details={"stage": "search"},
        )
        self.session.add(failure)
        self.session.commit()

        result = main.get_run(self.run.id, self.session)

        self.assertEqual(result["failure"]["stage"], "search")
        self.assertEqual(
            result["failure"]["message"],
            "Search failed: provider rejected the model ID.",
        )

    def test_all_four_experiment_conditions_select_the_documented_review_gates(self):
        expected = {
            "without_intermediate_human_supervision": ("none", []),
            "with_human_supervision_all_stages": (
                "every_stage",
                ["search", "extraction", "organization", "synthesis"],
            ),
            "full_audit": (
                "every_stage",
                ["search", "extraction", "organization", "synthesis"],
            ),
            "targeted_retry": ("search_and_final", ["search", "synthesis"]),
            "endpoint_oversight": ("final_only", ["synthesis"]),
            "midpoint_gating": ("after_extraction", ["extraction"]),
        }
        for condition, (policy, stages) in expected.items():
            self.assertEqual(EXPERIMENT_CONDITION_POLICIES[condition], policy)
            self.assertEqual(
                [
                    stage
                    for stage in workflow.STAGES
                    if workflow.needs_review(policy, stage)
                ],
                stages,
            )
        with self.assertRaises(ValueError):
            RunCreate(condition="unregistered_condition")

    def test_search_artifact_waits_for_human_only_in_search_review_conditions(self):
        expected_review = {
            "search_discovery": True,
            "without_intermediate_human_supervision": False,
            "with_human_supervision_all_stages": True,
            "full_audit": True,
            "targeted_retry": True,
            "endpoint_oversight": False,
            "midpoint_gating": False,
        }
        for condition, policy in EXPERIMENT_CONDITION_POLICIES.items():
            run = Run(
                id=str(uuid.uuid4()),
                project_id=self.project.id,
                models={
                    "search": "openai",
                    "extraction": "openai",
                    "organization": "openai",
                    "synthesis": "openai",
                },
                settings={
                    "source_mode": "saved_corpus",
                    "max_papers": 25,
                    "condition": condition,
                    "review_policy": policy,
                },
                status="running",
            )
            self.session.add(run)
            self.session.commit()
            with patch.object(
                workflow,
                "json_answer",
                return_value=(
                    {"ranked_paper_ids": [self.paper.id]},
                    "mock-search-model",
                    {},
                ),
            ):
                artifact = workflow.execute_stage(
                    self.session, self.project, run, "search"
                )
            expected_status = (
                "pending_review" if expected_review[condition] else "approved"
            )
            self.assertEqual(artifact.status, expected_status)
            self.assertEqual(run.settings["source_paper_ids"], [self.paper.id])

    def test_api_freezes_condition_and_review_policy_per_run(self):
        models = {
            "search": "openai",
            "extraction": "openai",
            "organization": "openai",
            "synthesis": "openai",
        }
        for condition, policy in EXPERIMENT_CONDITION_POLICIES.items():
            source_mode = (
                "openalex" if condition == "search_discovery" else "saved_corpus"
            )
            payload = RunCreate(
                models=models, source_mode=source_mode, condition=condition
            )
            with patch.object(
                main,
                "provider_config",
                return_value=SimpleNamespace(model_id="unit-test-model"),
            ):
                created = main.create_run(self.project.id, payload, self.session)
            self.assertEqual(created["settings"]["condition"], condition)
            self.assertEqual(created["settings"]["scenario"], condition)
            self.assertEqual(created["settings"]["review_policy"], policy)
            self.assertIn("Search", created["settings"]["model_scope"])

        mixed_models = {**models, "organization": "qwen"}
        with self.assertRaises(main.HTTPException) as error:
            main.create_run(
                self.project.id,
                RunCreate(models=mixed_models, source_mode="saved_corpus"),
                self.session,
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_search_discovery_freezes_selected_new_candidates_and_preserves_existing_corpus(
        self,
    ):
        self.run.settings = {
            "run_type": "search_discovery",
            "condition": "search_discovery",
            "review_policy": "every_stage",
            "source_mode": "openalex",
        }
        new_selected = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=self.run.id,
            title="Selected OpenAlex result",
            authors=[],
            year=2025,
            doi=None,
            url="https://example.org/selected",
            abstract="Selected candidate",
            full_text="",
            venue="",
            citation_count=0,
            in_corpus=False,
        )
        new_excluded = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=self.run.id,
            title="Excluded OpenAlex result",
            authors=[],
            year=2025,
            doi=None,
            url="https://example.org/excluded",
            abstract="Excluded candidate",
            full_text="",
            venue="",
            citation_count=0,
            in_corpus=False,
        )
        self.session.add_all([new_selected, new_excluded])
        snapshot = [
            {
                "paper_id": new_selected.id,
                "title": new_selected.title,
                "text_sha256": "one",
            },
            {
                "paper_id": new_excluded.id,
                "title": new_excluded.title,
                "text_sha256": "two",
            },
        ]
        self.run.status = "awaiting_review"
        artifact = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="search",
            version=1,
            content={"source_snapshot": snapshot, "source_snapshot_hash": "all"},
            status="pending_review",
            model="mock/openai",
        )
        self.session.add(artifact)
        self.session.commit()

        main.review(
            self.run.id,
            artifact.id,
            ReviewAction(action="approve", source_paper_ids=[new_selected.id]),
            self.session,
        )

        self.assertTrue(self.paper.in_corpus)
        self.assertTrue(new_selected.in_corpus)
        self.assertFalse(new_excluded.in_corpus)
        self.assertEqual(self.run.settings["source_paper_ids"], [new_selected.id])

    def test_synthesis_replaces_citations_to_sources_outside_the_screened_corpus(self):
        valid_id = str(uuid.uuid4())
        unverified_id = str(uuid.uuid4())
        draft = f"Supported statement [{valid_id}]. Unsupported reference [{unverified_id}]."
        sanitized, citations, rejected = workflow.sanitize_citations(draft, [valid_id])
        self.assertIn(f"[{valid_id}]", sanitized)
        self.assertIn("[UNVERIFIED CITATION]", sanitized)
        self.assertEqual(citations, [valid_id])
        self.assertEqual(rejected, [unverified_id])

    def test_organization_drops_themes_and_comparisons_without_valid_evidence_indices(
        self,
    ):
        content = validate_organization(
            {
                "themes": [
                    {
                        "name": "Supported theme",
                        "summary": "Evidence summary",
                        "claim_indices": [0, 1, 999, True],
                        "agreements": [
                            {"text": "Two sources agree", "claim_indices": [0, 1, 999]},
                            {"text": "Untraceable", "claim_indices": [99]},
                        ],
                        "gaps": [{"text": "Unstudied factor", "claim_indices": [1]}],
                    },
                    {
                        "name": "Unsupported theme",
                        "summary": "No source",
                        "claim_indices": [],
                    },
                ]
            },
            claim_count=2,
        )
        self.assertEqual(content["themes"][0]["claim_indices"], [0, 1])
        self.assertEqual(len(content["themes"][0]["agreements"]), 1)
        self.assertEqual(content["themes"][0]["gaps"][0]["claim_indices"], [1])
        self.assertEqual(content["dropped_unreferenced_themes"], 1)
        self.assertEqual(content["dropped_unreferenced_comparisons"], 1)

    def test_api_queues_run_and_review_decisions_resume_from_checkpoint(self):
        payload = RunCreate(
            models={
                "search": "openai",
                "extraction": "openai",
                "organization": "openai",
                "synthesis": "openai",
            },
            source_mode="saved_corpus",
            condition="full_audit",
            replicate="R1",
            temperature=0,
        )
        with patch.object(
            main,
            "provider_config",
            return_value=SimpleNamespace(model_id="unit-test-model"),
        ):
            response = main.create_run(self.project.id, payload, self.session)
        self.assertEqual(response["status"], "queued")

        self.run.status = "awaiting_review"
        artifact = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="search",
            version=1,
            content={"count": 1, "source_snapshot": [{"paper_id": self.paper.id}]},
            status="pending_review",
            model="Saved corpus",
        )
        self.session.add(artifact)
        self.session.commit()
        review = main.review(
            self.run.id,
            artifact.id,
            ReviewAction(
                action="approve",
                note="",
                ratings=ReviewRatings(
                    relevance=4, evidence_accuracy=4, coverage=4, traceability=4
                ),
            ),
            self.session,
        )
        self.assertEqual(review["status"], "queued")
        self.assertEqual(review["settings"]["resume_from_stage"], "search")
        event = self.session.scalar(
            select(Event).where(
                Event.run_id == self.run.id, Event.type == "review_approved"
            )
        )
        self.assertEqual(event.details["ratings"]["evidence_accuracy"], 4)

    def test_local_run_freezes_source_snapshot_and_does_not_pull_later_uploads(self):
        with patch.object(
            workflow,
            "json_answer",
            return_value=(
                {"ranked_paper_ids": [self.paper.id]},
                "openai-search-model",
                {
                    "usage": {"total_tokens": 4},
                    "prompt_sha256": "search-hash",
                    "request_duration_ms": 5,
                },
            ),
        ):
            search = workflow.execute_stage(
                self.session, self.project, self.run, "search"
            )
        self.assertEqual(search.content["source_mode"], "saved_corpus")
        self.assertEqual(
            search.content["source_snapshot"][0]["paper_id"], self.paper.id
        )
        self.assertEqual(search.content["model"], "openai-search-model")
        self.assertEqual(search.content["_telemetry"]["usage"]["total_tokens"], 4)

        later = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=None,
            title="Uploaded later",
            authors=[],
            year=2025,
            doi=None,
            url="",
            abstract="This paper is not part of the experimental snapshot.",
            full_text="",
            venue="",
            citation_count=0,
        )
        self.session.add(later)
        self.session.commit()

        captured = {}

        def fake_json_answer(_provider, prompt, _system, **_kwargs):
            captured["prompt"] = prompt
            return (
                {
                    "claims": [
                        {
                            "claim": "Evidence for X",
                            "paper_id": self.paper.id,
                            "quote": "A sufficiently long verbatim quote supporting the primary result.",
                        }
                    ]
                },
                "openai-test-model",
                {
                    "usage": {
                        "prompt_tokens": 12,
                        "completion_tokens": 8,
                        "total_tokens": 20,
                    },
                    "prompt_sha256": "abc123",
                },
            )

        with patch.object(workflow, "json_answer", side_effect=fake_json_answer):
            extraction = workflow.execute_stage(
                self.session, self.project, self.run, "extraction"
            )

        self.assertIn("Frozen paper", captured["prompt"])
        self.assertNotIn("Uploaded later", captured["prompt"])
        self.assertEqual(len(extraction.content["claims"]), 1)
        self.assertEqual(extraction.content["_telemetry"]["usage"]["total_tokens"], 20)
        self.assertEqual(extraction.content["_telemetry"]["prompt_sha256"], "abc123")
        self.assertEqual(self.run.settings["source_paper_ids"], [self.paper.id])

    def test_extraction_splits_saved_papers_into_bounded_batches_and_aggregates_usage(
        self,
    ):
        extra_papers = [
            Paper(
                id=str(uuid.uuid4()),
                project_id=self.project.id,
                run_id=None,
                title=f"Frozen paper {index}",
                authors=[],
                year=2024,
                doi=None,
                url="",
                abstract=f"Abstract evidence for paper {index}.",
                full_text="",
                venue="",
                citation_count=0,
            )
            for index in range(2, 6)
        ]
        self.session.add_all(extra_papers)
        paper_ids = [self.paper.id, *(paper.id for paper in extra_papers)]
        self.run.settings = {**self.run.settings, "source_paper_ids": paper_ids}
        self.session.commit()
        calls = []

        def fake_json_answer(_provider, prompt, _system, **_kwargs):
            sources_json = prompt.split("\nSources: ", 1)[1]
            batch = json.loads(sources_json)
            calls.append(batch)
            return (
                {
                    "papers": [
                        {"paper_id": paper["id"], "fields": {}, "evidence": []}
                        for paper in batch
                    ]
                },
                "openai-test-model",
                {
                    "usage": {"total_tokens": 10},
                    "prompt_sha256": f"batch-{len(calls)}",
                    "request_duration_ms": 20,
                },
            )

        with patch.object(workflow, "json_answer", side_effect=fake_json_answer):
            extraction = workflow.execute_stage(
                self.session, self.project, self.run, "extraction"
            )

        self.assertEqual([len(batch) for batch in calls], [2, 2, 1])
        self.assertEqual(
            {paper["id"] for batch in calls for paper in batch}, set(paper_ids)
        )
        self.assertEqual(len(extraction.content["papers"]), 5)
        self.assertEqual(
            extraction.content["processing"],
            {
                "strategy": "bounded_batches",
                "batch_size": 2,
                "batch_count": 3,
                "paper_count": 5,
            },
        )
        self.assertEqual(extraction.content["_telemetry"]["usage"]["total_tokens"], 30)

    def test_openalex_search_is_not_skipped_when_project_has_existing_papers(self):
        self.run.settings = {"source_mode": "openalex", "max_papers": 25}
        with (
            patch.object(
                workflow, "search_sources", return_value=[self.paper]
            ) as search_sources,
            patch.object(
                workflow,
                "json_answer",
                return_value=(
                    {"ranked_paper_ids": [self.paper.id]},
                    "openai-search-model",
                    {},
                ),
            ),
        ):
            artifact = workflow.execute_stage(
                self.session, self.project, self.run, "search"
            )
        search_sources.assert_called_once()
        self.assertEqual(artifact.content["retrieval_source"], "OpenAlex")
        self.assertEqual(artifact.model, "openai-search-model")

    def test_search_llm_ranking_never_drops_or_invents_candidate_ids(self):
        second = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=None,
            title="Second paper",
            authors=[],
            year=2023,
            doi=None,
            url="",
            abstract="Another candidate abstract",
            full_text="",
            venue="",
            citation_count=0,
        )
        self.session.add(second)
        self.session.commit()
        self.run.settings = {"source_mode": "saved_corpus", "max_papers": 25}
        with patch.object(
            workflow,
            "json_answer",
            return_value=(
                {"ranked_paper_ids": [second.id, str(uuid.uuid4()), second.id]},
                "ranker",
                {},
            ),
        ):
            artifact = workflow.execute_stage(
                self.session, self.project, self.run, "search"
            )
        result_ids = [paper["id"] for paper in artifact.content["papers"]]
        self.assertEqual(result_ids, [second.id, self.paper.id])
        self.assertEqual(set(result_ids), {second.id, self.paper.id})

    def test_candidate_source_snapshot_hash_is_stable_when_model_ranking_changes(self):
        second = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=None,
            title="Second paper",
            authors=[],
            year=2023,
            doi=None,
            url="",
            abstract="Another candidate abstract",
            full_text="",
            venue="",
            citation_count=0,
        )
        self.session.add(second)
        self.session.commit()
        first_run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models=self.run.models,
            settings={"source_mode": "saved_corpus", "max_papers": 25},
            status="running",
        )
        second_run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models=self.run.models,
            settings={"source_mode": "saved_corpus", "max_papers": 25},
            status="running",
        )
        self.session.add_all([first_run, second_run])
        self.session.commit()
        with patch.object(
            workflow,
            "json_answer",
            return_value=(
                {"ranked_paper_ids": [self.paper.id, second.id]},
                "ranker-a",
                {},
            ),
        ):
            first = workflow.execute_stage(
                self.session, self.project, first_run, "search"
            )
        with patch.object(
            workflow,
            "json_answer",
            return_value=(
                {"ranked_paper_ids": [second.id, self.paper.id]},
                "ranker-b",
                {},
            ),
        ):
            second_result = workflow.execute_stage(
                self.session, self.project, second_run, "search"
            )
        self.assertNotEqual(
            first.content["ranked_paper_ids"], second_result.content["ranked_paper_ids"]
        )
        self.assertEqual(
            first.content["source_snapshot_hash"],
            second_result.content["source_snapshot_hash"],
        )

    def test_worker_claims_queued_run_and_preserves_checkpoint(self):
        self.run.status = "queued"
        self.run.settings = {**self.run.settings, "resume_from_stage": "search"}
        self.session.commit()
        with patch.object(worker, "SessionLocal", return_value=self.session):
            claimed = worker.claim_next_run()
        self.assertEqual(claimed, (self.run.id, self.project.id, "search"))
        self.assertEqual(self.run.status, "running")
        self.assertEqual(self.run.settings["resume_from_stage"], "search")

    def test_worker_recovery_keeps_retry_checkpoint(self):
        self.run.status = "running"
        self.run.settings = {**self.run.settings, "resume_from_stage": "search"}
        self.session.add(
            Artifact(
                id=str(uuid.uuid4()),
                run_id=self.run.id,
                stage="extraction",
                version=1,
                content={},
                status="revision_requested",
                model="openai",
            )
        )
        self.session.commit()
        with patch.object(worker, "SessionLocal", return_value=self.session):
            worker.recover_interrupted_runs()
        self.assertEqual(self.run.status, "queued")
        self.assertEqual(self.run.settings["resume_from_stage"], "search")

    def test_search_review_saves_reviewer_screened_sources_for_extraction(self):
        other_id = str(uuid.uuid4())
        candidates = [self.paper.id, other_id]
        self.run.status = "awaiting_review"
        self.run.settings = {**self.run.settings, "source_paper_ids": candidates}
        search = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="search",
            version=1,
            content={
                "count": 2,
                "source_snapshot_hash": "all-candidate-hash",
                "source_snapshot": [
                    {
                        "paper_id": self.paper.id,
                        "title": "Frozen paper",
                        "text_sha256": "one",
                    },
                    {
                        "paper_id": other_id,
                        "title": "Exclude this",
                        "text_sha256": "two",
                    },
                ],
            },
            status="pending_review",
            model="Saved corpus",
        )
        self.session.add(search)
        self.session.commit()

        result = main.review(
            self.run.id,
            search.id,
            ReviewAction(
                action="approve",
                source_paper_ids=[self.paper.id],
                ratings=ReviewRatings(
                    relevance=5, evidence_accuracy=4, coverage=3, traceability=5
                ),
            ),
            self.session,
        )

        self.assertEqual(result["settings"]["source_paper_ids"], [self.paper.id])
        self.assertEqual(result["settings"]["resume_from_stage"], "search")
        reviewed = next(a for a in result["artifacts"] if a["id"] == search.id)
        self.assertEqual(reviewed["content"]["included_paper_ids"], [self.paper.id])
        self.assertEqual(reviewed["content"]["excluded_paper_ids"], [other_id])

    def test_human_correction_creates_new_artifact_and_persistent_review_record(self):
        self.run.status = "awaiting_review"
        original_content = {
            "claims": [{"claim": "Original model claim", "paper_id": self.paper.id}]
        }
        artifact = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="extraction",
            version=1,
            content=original_content,
            status="pending_review",
            model="mock/openai",
        )
        self.session.add(artifact)
        self.session.commit()

        corrected_content = {
            "claims": [{"claim": "Human-corrected claim", "paper_id": self.paper.id}]
        }
        result = main.review(
            self.run.id,
            artifact.id,
            ReviewAction(
                action="approve",
                note="Corrected the claim wording.",
                correction=corrected_content,
                review_duration_ms=1234,
            ),
            self.session,
        )

        self.assertEqual(artifact.content, original_content)
        latest = self.session.scalar(
            select(Artifact)
            .where(Artifact.run_id == self.run.id, Artifact.stage == "extraction")
            .order_by(Artifact.version.desc())
        )
        self.assertNotEqual(latest.id, artifact.id)
        self.assertEqual(latest.version, 2)
        self.assertEqual(latest.content["claims"], corrected_content["claims"])
        review = self.session.scalar(
            select(ReviewRecord).where(ReviewRecord.artifact_id == artifact.id)
        )
        self.assertIsNotNone(review)
        self.assertEqual(review.corrected_artifact_id, latest.id)
        self.assertEqual(review.review_duration_ms, 1234)
        self.assertEqual(review.original_content, original_content)
        self.assertEqual(review.ratings, {})
        self.assertEqual(result["status"], "queued")

    def test_supervised_recovery_restarts_at_extraction_from_frozen_papers(self):
        frozen_ids = [self.paper.id]
        frozen_hash = "frozen-source-snapshot"
        self.run.settings = {
            "condition": "with_human_supervision_all_stages",
            "protocol_version": "2.0",
            "review_policy": "every_stage",
            "source_mode": "saved_corpus",
            "source_paper_ids": frozen_ids,
            "source_snapshot_hash": frozen_hash,
        }
        self.run.status = "awaiting_review"
        search = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="search",
            version=1,
            content={
                "source_paper_ids": frozen_ids,
                "source_snapshot_hash": frozen_hash,
            },
            status="approved",
            model="openai",
        )
        organization = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="organization",
            version=1,
            content={"themes": [{"name": "A theme"}]},
            status="pending_review",
            model="openai",
        )
        self.session.add_all([search, organization])
        self.session.commit()

        result = main.review(
            self.run.id,
            organization.id,
            ReviewAction(
                action="restart_from_extraction",
                note="Organization missed a comparison; re-check the source evidence.",
                review_duration_ms=90000,
            ),
            self.session,
        )

        self.assertEqual(result["status"], "queued")
        self.assertEqual(result["settings"]["resume_from_stage"], "search")
        self.assertEqual(result["settings"]["source_paper_ids"], frozen_ids)
        self.assertEqual(result["settings"]["source_snapshot_hash"], frozen_hash)
        self.assertEqual(result["settings"]["saved_corpus_restart_count"], 1)
        self.assertEqual(organization.status, "revision_requested")
        saved_review = self.session.scalar(
            select(ReviewRecord).where(ReviewRecord.artifact_id == organization.id)
        )
        self.assertEqual(saved_review.decision, "restart_from_extraction")
        self.assertEqual(saved_review.review_duration_ms, 90000)
        restart_event = self.session.scalar(
            select(Event).where(
                Event.run_id == self.run.id,
                Event.type == "workflow_restarted_from_saved_corpus",
            )
        )
        self.assertEqual(restart_event.details["source_snapshot_hash"], frozen_hash)

    def test_autonomous_condition_cannot_use_supervised_corpus_restart_action(self):
        self.run.settings = {
            "condition": "without_intermediate_human_supervision",
            "protocol_version": "2.0",
            "source_paper_ids": [self.paper.id],
            "source_snapshot_hash": "frozen-source-snapshot",
        }
        self.run.status = "awaiting_review"
        artifact = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="synthesis",
            version=1,
            content={"draft": "Autonomous output"},
            status="pending_review",
            model="openai",
        )
        self.session.add(artifact)
        self.session.commit()

        with self.assertRaises(main.HTTPException) as error:
            main.review(
                self.run.id,
                artifact.id,
                ReviewAction(
                    action="restart_from_extraction",
                    note="Do not alter the autonomous run.",
                ),
                self.session,
            )

        self.assertEqual(error.exception.status_code, 422)

    def test_supervised_search_records_screening_without_changing_frozen_comparison_corpus(
        self,
    ):
        second = Paper(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            run_id=None,
            title="Second frozen paper",
            authors=[],
            year=2023,
            doi=None,
            url="",
            abstract="Abstract",
            full_text="",
            venue="",
            citation_count=0,
        )
        self.session.add(second)
        frozen_ids = [self.paper.id, second.id]
        frozen_hash = "same-corpus-hash"
        self.run.settings = {
            "source_mode": "saved_corpus",
            "protocol_version": "2.0",
            "condition": "with_human_supervision_all_stages",
            "source_paper_ids": frozen_ids,
            "source_snapshot_hash": frozen_hash,
        }
        self.run.status = "awaiting_review"
        original = {
            "ranked_paper_ids": frozen_ids,
            "source_snapshot_hash": frozen_hash,
            "source_snapshot": [
                {"paper_id": pid, "title": "Paper"} for pid in frozen_ids
            ],
        }
        search = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="search",
            version=1,
            content=original,
            status="pending_review",
            model="mock/openai",
        )
        self.session.add(search)
        self.session.commit()

        main.review(
            self.run.id,
            search.id,
            ReviewAction(
                action="approve",
                source_paper_ids=[self.paper.id],
                note="Second paper looks less relevant.",
            ),
            self.session,
        )

        self.assertEqual(self.run.settings["source_paper_ids"], frozen_ids)
        self.assertEqual(self.run.settings["source_snapshot_hash"], frozen_hash)
        self.assertEqual(search.content, original)
        review = self.session.scalar(
            select(ReviewRecord).where(ReviewRecord.artifact_id == search.id)
        )
        self.assertEqual(
            review.screening_decisions,
            {"included_paper_ids": [self.paper.id], "excluded_paper_ids": [second.id]},
        )

    def test_no_intermediate_condition_advances_through_all_four_agents(self):
        run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models=self.run.models,
            settings={
                "condition": "without_intermediate_human_supervision",
                "review_policy": "none",
                "protocol_version": "2.0",
            },
            status="running",
        )
        self.session.add(run)
        self.session.commit()
        visited = []

        def fake_execute(db, project, current_run, stage):
            visited.append(stage)
            artifact = Artifact(
                id=str(uuid.uuid4()),
                run_id=current_run.id,
                stage=stage,
                version=1,
                content={"stage": stage},
                status="approved",
                model="mock/openai",
            )
            db.add(artifact)
            db.flush()
            return artifact

        with patch.object(workflow, "execute_stage", side_effect=fake_execute):
            workflow.advance(self.session, self.project, run)
        self.assertEqual(visited, workflow.STAGES)
        self.assertEqual(run.status, "completed")

    def test_resume_after_search_checkpoint_starts_at_extraction(self):
        run = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models=self.run.models,
            settings={
                "condition": "with_human_supervision_all_stages",
                "review_policy": "every_stage",
                "source_mode": "saved_corpus",
                "source_paper_ids": [self.paper.id],
                "source_snapshot_hash": "frozen",
            },
            status="running",
        )
        self.session.add(run)
        self.session.commit()
        visited = []

        def fake_execute(db, project, current_run, stage):
            visited.append(stage)
            artifact = Artifact(
                id=str(uuid.uuid4()),
                run_id=current_run.id,
                stage=stage,
                version=1,
                content={"stage": stage},
                status="approved",
                model="mock/openai",
            )
            db.add(artifact)
            db.flush()
            return artifact

        with patch.object(workflow, "execute_stage", side_effect=fake_execute):
            workflow.advance(self.session, self.project, run, from_stage="search")

        self.assertEqual(visited, ["extraction", "organization", "synthesis"])
        self.assertEqual(run.status, "completed")

    def test_independent_reference_evaluation_metrics_and_evidence_package(self):
        artifact = Artifact(
            id=str(uuid.uuid4()),
            run_id=self.run.id,
            stage="synthesis",
            version=1,
            content={"draft": "Claim supported by a source."},
            status="approved",
            model="mock/openai",
        )
        self.session.add(artifact)
        self.run.status = "completed"
        self.run.settings = {
            **self.run.settings,
            "source_paper_ids": [self.paper.id],
            "source_snapshot_hash": "frozen",
        }
        self.session.commit()
        annotation = ReferenceAnnotationCreate(
            paper_id=self.paper.id,
            annotator_id="R1",
            rubric_version="gold-v1",
            annotations=ReferenceFields(
                research_objective="Objective",
                methodology="Method",
                dataset_or_sample="Sample",
                findings="Finding",
                limitations="Limitation",
                research_gaps="Gap",
            ),
        )
        saved_reference = main.create_reference_annotation(
            self.project.id, annotation, self.session
        )
        self.assertEqual(saved_reference["annotator_id"], "R1")
        evaluation = main.create_evaluation(
            self.run.id,
            EvaluationCreate(
                artifact_id=artifact.id,
                evaluator_id="E1",
                rubric_version="quality-v1",
                scores={"factual_accuracy": 4, "citation_correctness": 5},
                comments="Checked the source.",
            ),
            self.session,
        )
        self.assertEqual(evaluation["stage"], "synthesis")
        metrics = main.get_run_metrics(self.run.id, self.session)
        self.assertEqual(
            metrics["quality_evaluations"]["synthesis"]["factual_accuracy"]["mean"], 4
        )
        self.assertEqual(metrics["reference_field_presence"]["annotation_count"], 1)
        package = main.export_evidence_package(self.run.id, self.session)
        archive = zipfile.ZipFile(io.BytesIO(package.body))
        names = archive.namelist()
        self.assertTrue(any(name.endswith("/protocol/protocol.json") for name in names))
        self.assertTrue(
            any(name.endswith("/evaluation/evaluator_scores.csv") for name in names)
        )
        self.assertTrue(
            any(name.endswith("/reports/experimental_results.md") for name in names)
        )

    def test_pair_comparison_accepts_same_frozen_corpus_in_different_search_rank_order(
        self,
    ):
        models = {
            "search": "openai",
            "extraction": "openai",
            "organization": "openai",
            "synthesis": "openai",
        }
        common = {
            "protocol_version": "2.0",
            "source_mode": "saved_corpus",
            "source_snapshot_hash": "same-hash",
            "replicate": "pair-1",
            "random_seed": 42,
            "temperature": 0.0,
            "max_papers": 25,
        }
        run_a = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models=models,
            settings={
                **common,
                "condition": "without_intermediate_human_supervision",
                "source_paper_ids": [self.paper.id, "paper-2"],
            },
            status="completed",
        )
        run_b = Run(
            id=str(uuid.uuid4()),
            project_id=self.project.id,
            models=models,
            settings={
                **common,
                "condition": "with_human_supervision_all_stages",
                "source_paper_ids": ["paper-2", self.paper.id],
            },
            status="completed",
        )
        self.session.add_all([run_a, run_b])
        self.session.commit()

        result = main.compare_paired_runs(
            self.project.id,
            ComparisonRequest(
                pairs=[
                    {"no_intermediate_run_id": run_a.id, "supervised_run_id": run_b.id}
                ]
            ),
            self.session,
        )

        self.assertEqual(result["paired_run_count"], 1)


if __name__ == "__main__":
    unittest.main()
