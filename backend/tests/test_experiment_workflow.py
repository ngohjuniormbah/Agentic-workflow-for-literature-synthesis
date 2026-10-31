import unittest
import uuid
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Artifact, Paper, Project, Run
from app.schemas import RunCreate
from app import workflow
from app import worker
from app import main
from app.schemas import ReviewAction


class WorkflowExperimentTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine, expire_on_commit=False)()
        self.project = Project(id=str(uuid.uuid4()), title="Test", question="What does the evidence say about intervention X?", scope="", review_policy="every_stage")
        self.session.add(self.project)
        self.paper = Paper(id=str(uuid.uuid4()), project_id=self.project.id, run_id=None, title="Frozen paper", authors=[], year=2024, doi=None, url="", abstract="A sufficiently long verbatim quote supporting the primary result.", full_text="", venue="", citation_count=0)
        self.session.add(self.paper)
        self.run = Run(id=str(uuid.uuid4()), project_id=self.project.id, models={"extraction": "openai", "organization": "openai", "synthesis": "openai"}, settings={"source_mode": "saved_corpus", "max_papers": 25, "protocol_version": "1.0"}, status="running")
        self.session.add(self.run)
        self.session.commit()

    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_run_metadata_is_validated_and_bounded(self):
        spec = RunCreate(models={"extraction": "openai", "organization": "openai", "synthesis": "openai"}, source_mode="saved_corpus", experiment_label="pilot", condition="every_stage", replicate="R1", random_seed=17)
        self.assertEqual(spec.random_seed, 17)
        with self.assertRaises(ValueError):
            RunCreate(models={}, random_seed=2**32)

    def test_api_queues_run_and_review_decisions_resume_from_checkpoint(self):
        payload = RunCreate(models={"extraction": "openai", "organization": "openai", "synthesis": "openai"}, source_mode="saved_corpus", condition="every_stage", replicate="R1", temperature=0)
        with patch.object(main, "provider_config", return_value=object()):
            response = main.create_run(self.project.id, payload, self.session)
        self.assertEqual(response["status"], "queued")

        self.run.status = "awaiting_review"
        artifact = Artifact(id=str(uuid.uuid4()), run_id=self.run.id, stage="search", version=1, content={"count": 1}, status="pending_review", model="Saved corpus")
        self.session.add(artifact)
        self.session.commit()
        review = main.review(self.run.id, artifact.id, ReviewAction(action="approve", note=""), self.session)
        self.assertEqual(review["status"], "queued")
        self.assertEqual(review["settings"]["resume_from_stage"], "search")

    def test_local_run_freezes_source_snapshot_and_does_not_pull_later_uploads(self):
        search = workflow.execute_stage(self.session, self.project, self.run, "search")
        self.assertEqual(search.content["source_mode"], "saved_corpus")
        self.assertEqual(search.content["source_snapshot"][0]["paper_id"], self.paper.id)

        later = Paper(id=str(uuid.uuid4()), project_id=self.project.id, run_id=None, title="Uploaded later", authors=[], year=2025, doi=None, url="", abstract="This paper is not part of the experimental snapshot.", full_text="", venue="", citation_count=0)
        self.session.add(later)
        self.session.commit()

        captured = {}
        def fake_json_answer(_provider, prompt, _system, **_kwargs):
            captured["prompt"] = prompt
            return ({"claims": [{"claim": "Evidence for X", "paper_id": self.paper.id, "quote": "A sufficiently long verbatim quote supporting the primary result."}]}, "openai-test-model", {"usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}, "prompt_sha256": "abc123"})

        with patch.object(workflow, "json_answer", side_effect=fake_json_answer):
            extraction = workflow.execute_stage(self.session, self.project, self.run, "extraction")

        self.assertIn("Frozen paper", captured["prompt"])
        self.assertNotIn("Uploaded later", captured["prompt"])
        self.assertEqual(len(extraction.content["claims"]), 1)
        self.assertEqual(extraction.content["_telemetry"]["usage"]["total_tokens"], 20)
        self.assertEqual(extraction.content["_telemetry"]["prompt_sha256"], "abc123")
        self.assertEqual(self.run.settings["source_paper_ids"], [self.paper.id])

    def test_openalex_search_is_not_skipped_when_project_has_existing_papers(self):
        self.run.settings = {"source_mode": "openalex", "max_papers": 25}
        with patch.object(workflow, "search_sources", return_value=[self.paper]) as search_sources:
            artifact = workflow.execute_stage(self.session, self.project, self.run, "search")
        search_sources.assert_called_once()
        self.assertEqual(artifact.content["provider"], "OpenAlex search")

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
        self.session.add(Artifact(id=str(uuid.uuid4()), run_id=self.run.id, stage="extraction", version=1, content={}, status="revision_requested", model="openai"))
        self.session.commit()
        with patch.object(worker, "SessionLocal", return_value=self.session):
            worker.recover_interrupted_runs()
        self.assertEqual(self.run.status, "queued")
        self.assertEqual(self.run.settings["resume_from_stage"], "search")


if __name__ == "__main__":
    unittest.main()
